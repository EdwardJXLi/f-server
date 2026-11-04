from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from f_server.auth.api_keys import create_api_key
from f_server.config import RepoConfig, Settings
from f_server.db import Base, get_session
from f_server.main import create_app
from f_server.models import Version, nativecode_identity
from f_server.storage.local import LocalStorage
from tests.apk_factory import BUILD_TOOLS, JBR, android_tools_available, build_signed_apk


@pytest.mark.skipif(not android_tools_available(), reason="Android SDK/JBR tools are unavailable")
def test_upload_publishes_and_refreshes_compatibility_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PATH", f"{BUILD_TOOLS}:{JBR / 'bin'}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("JAVA_HOME", str(JBR))
    settings = Settings(database_url="sqlite:///:memory:", repo=RepoConfig(), _env_file=None)
    storage = LocalStorage(tmp_path / "storage")
    engine = sa.create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_session():
        with sessions() as session:
            yield session

    monkeypatch.setattr("f_server.routers.upload.get_settings", lambda: settings)
    monkeypatch.setattr("f_server.routers.upload.get_storage", lambda: storage)
    monkeypatch.setattr("f_server.services.rebuild.get_storage", lambda: storage)
    monkeypatch.setattr("f_server.services.rebuild.get_settings", lambda: settings)
    monkeypatch.setattr("f_server.main.create_all", lambda: None)
    app = create_app()
    app.dependency_overrides[get_session] = override_session
    package_name = "com.example.compatibility"
    apk = build_signed_apk(
        tmp_path / "apks", package_name, 1, "1.0", "signer",
        max_sdk=30,
        manifest_elements='''
          <uses-permission android:name="android.permission.WRITE_EXTERNAL_STORAGE" android:maxSdkVersion="18" />
          <uses-permission-sdk-23 android:name="android.permission.ACCESS_FINE_LOCATION" android:maxSdkVersion="28" />
          <uses-permission-sdk-23 android:name="android.permission.BODY_SENSORS" />
          <uses-feature android:name="android.hardware.camera" />
          <uses-feature android:name="android.software.leanback" android:required="true" />
          <uses-feature android:name="android.hardware.bluetooth" android:required="false" />
        ''',
    )
    with sessions() as session:
        key = create_api_key(session, "compatibility", [package_name]).secret
    headers = {"Authorization": f"Bearer {key}"}
    with TestClient(app) as client:
        uploaded = client.post(
            "/api/v1/upload", headers=headers, files={"apk": ("app.apk", apk.read_bytes())}
        )
        assert uploaded.status_code == 200
        with sessions() as session:
            version = session.scalar(sa.select(Version))
            assert version.max_sdk == 30
            assert version.features == ["android.hardware.camera", "android.software.leanback"]
            assert version.permission_details == [
                {"name": "android.permission.INTERNET"},
                {"name": "android.permission.WRITE_EXTERNAL_STORAGE", "maxSdkVersion": 18},
                {"name": "android.permission.READ_EXTERNAL_STORAGE", "maxSdkVersion": 18},
            ]
            version.max_sdk = None
            version.features = []
            version.permissions_sdk23 = []
            version.permission_details = [{"name": name} for name in version.permissions]
            session.commit()
        refreshed = client.post(
            "/api/v1/upload", headers=headers, files={"apk": ("app.apk", apk.read_bytes())}
        )
        assert refreshed.status_code == 200
        assert refreshed.json()["status"] == "exists"
        assert refreshed.json()["indexFiles"]
        assert refreshed.json()["sha256"] == uploaded.json()["sha256"]

    with storage.open_stream("repo/index-v2.json") as stream:
        v2 = json.load(stream)
    manifest = v2["packages"][package_name]["versions"][uploaded.json()["sha256"]]["manifest"]
    assert manifest["maxSdkVersion"] == 30
    assert manifest["features"] == [
        {"name": "android.hardware.camera"}, {"name": "android.software.leanback"}
    ]
    assert manifest["usesPermissionSdk23"] == [
        {"name": "android.permission.ACCESS_FINE_LOCATION", "maxSdkVersion": 28},
        {"name": "android.permission.BODY_SENSORS"},
    ]
    with storage.open_stream("repo/index-v1.json") as stream:
        v1 = json.load(stream)
    legacy = v1["packages"][package_name][0]
    assert legacy["maxSdkVersion"] == 30
    assert legacy["uses-permission"] == [
        ["android.permission.INTERNET", None],
        ["android.permission.WRITE_EXTERNAL_STORAGE", 18],
        ["android.permission.READ_EXTERNAL_STORAGE", 18],
    ]
    assert legacy["uses-permission-sdk-23"] == [
        ["android.permission.ACCESS_FINE_LOCATION", 28], ["android.permission.BODY_SENSORS", None]
    ]
    assert legacy["features"] == ["android.hardware.camera", "android.software.leanback"]
    with sessions() as session:
        assert len(session.scalars(sa.select(Version)).all()) == 1
    engine.dispose()


def test_compatibility_migration_preserves_legacy_permission_names(tmp_path, monkeypatch) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'migration.db'}", _env_file=None)
    monkeypatch.setattr("f_server.config.get_settings", lambda: settings)
    config = Config()
    config.set_main_option("script_location", "alembic")
    command.upgrade(config, "0003_multi_arch_packages")
    engine = sa.create_engine(settings.database_url)
    tables = sa.MetaData()
    tables.reflect(bind=engine)
    now = datetime.now(timezone.utc)
    permissions = ["android.permission.INTERNET", "android.permission.WRITE_EXTERNAL_STORAGE"]
    with engine.begin() as connection:
        connection.execute(tables.tables["apps"].insert().values(
            package_name="com.example.legacy", categories=[], added=now, last_updated=now,
        ))
        connection.execute(tables.tables["versions"].insert().values(
            id=1,
            package_name="com.example.legacy",
            version_code=1,
            apk_name="legacy.apk",
            storage_key="repo/legacy.apk",
            size=3,
            sha256_hash="a" * 64,
            nativecode=[],
            nativecode_key=nativecode_identity([]),
            permissions=permissions,
            signer_fingerprint="b" * 64,
            release_channel="release",
            added_at=now,
        ))
    command.upgrade(config, "0004_compatibility_metadata")
    with Session(engine) as session:
        version = session.get(Version, 1)
        assert version.permissions == permissions
        assert version.permission_details == [{"name": name} for name in permissions]
        assert version.features == []
        assert version.permissions_sdk23 == []
        assert version.storage_key == "repo/legacy.apk"
    command.downgrade(config, "0003_multi_arch_packages")
    command.upgrade(config, "0004_compatibility_metadata")
    with Session(engine) as session:
        assert session.get(Version, 1).permissions == permissions
    engine.dispose()
