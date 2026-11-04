from __future__ import annotations

import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from f_server.auth.api_keys import create_api_key
from f_server.config import RepoConfig, Settings, StorageConfig, UploadsConfig
from f_server.db import Base, get_session
from f_server.main import create_app
from f_server.fdroid.parse import parse_apk
from f_server.models import AllowedSigningKey, App, AuditLog, Version, nativecode_identity
from f_server.storage.local import LocalStorage
from tests.apk_factory import android_tools_available, build_signed_apk


pytestmark = pytest.mark.skipif(not android_tools_available(), reason="Android SDK/JBR tools are unavailable")


def test_real_apk_upload_tofu_idempotency_scope_and_cert_mismatch(tmp_path, monkeypatch) -> None:
    storage = LocalStorage(tmp_path / "storage")
    settings = Settings(
        database_url="sqlite:///:memory:",
        repo=RepoConfig(),
        storage=StorageConfig(),
    )
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def override_session():
        with testing_session() as session:
            yield session

    monkeypatch.setattr("f_server.routers.upload.get_storage", lambda: storage)
    monkeypatch.setattr("f_server.routers.repo.get_storage", lambda: storage)
    monkeypatch.setattr("f_server.services.rebuild.get_storage", lambda: storage)
    monkeypatch.setattr("f_server.services.rebuild.get_settings", lambda: settings)
    monkeypatch.setattr("f_server.main.create_all", lambda: None)

    app = create_app()
    app.dependency_overrides[get_session] = override_session

    package_name = "com.example.fixture"
    apk_v1 = build_signed_apk(tmp_path / "apks", package_name, 1, "1.0", "signer-one")
    apk_v2_wrong_signer = build_signed_apk(tmp_path / "apks", package_name, 2, "2.0", "signer-two")

    with testing_session() as session:
        upload_key = create_api_key(session, "fixture", [package_name]).secret
        wrong_scope_key = create_api_key(session, "wrong", ["org.other.*"]).secret

    with TestClient(app) as client:
        missing_key = _upload(client, apk_v1, None)
        assert missing_key.status_code == 401

        wrong_scope = _upload(client, apk_v1, wrong_scope_key)
        assert wrong_scope.status_code == 403
        assert wrong_scope.json()["detail"] == "package is outside API key scope"

        created = _upload(
            client,
            apk_v1,
            upload_key,
            {"summary": "Fixture summary", "categories": ["Tools"]},
            screenshots=[("screen.png", b"not-a-real-png")],
        )
        assert created.status_code == 200
        created_json = created.json()
        assert created_json["status"] == "created"
        assert created_json["packageName"] == package_name
        assert created_json["versionCode"] == 1
        assert created_json["indexFiles"] == ["repo/index-v2.json", "repo/entry.json", "repo/index-v1.json"]

        idempotent = _upload(client, apk_v1, upload_key)
        assert idempotent.status_code == 200
        assert idempotent.json()["status"] == "exists"

        mismatch = _upload(client, apk_v2_wrong_signer, upload_key)
        assert mismatch.status_code == 422
        assert "signing certificate" in mismatch.json()["detail"]

        package = client.get(f"/api/v1/packages/{package_name}")
        assert package.status_code == 200
        assert package.json()["versions"][0]["versionCode"] == 1

        index = client.get("/repo/index-v2.json")
        assert index.status_code == 200
        index_json = index.json()
        assert package_name in index_json["packages"]

    with testing_session() as session:
        assert session.get(App, package_name) is not None
        assert session.scalar(select(AllowedSigningKey).where(AllowedSigningKey.package_name == package_name))
        assert session.scalar(select(Version).where(Version.package_name == package_name)).version_code == 1
        assert storage.exists(f"repo/{package_name}/en-US/phoneScreenshots/01.png")
        assert session.scalar(select(AuditLog).where(AuditLog.result == "422-cert"))


def test_upload_can_disable_signing_key_verification(tmp_path, monkeypatch) -> None:
    storage = LocalStorage(tmp_path / "storage")
    settings = Settings(
        database_url="sqlite:///:memory:",
        repo=RepoConfig(),
        storage=StorageConfig(),
        uploads=UploadsConfig(verify_signing_keys=False),
    )
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)

    def override_session():
        with testing_session() as session:
            yield session

    monkeypatch.setattr("f_server.routers.upload.get_settings", lambda: settings)
    monkeypatch.setattr("f_server.routers.upload.get_storage", lambda: storage)
    monkeypatch.setattr("f_server.routers.repo.get_storage", lambda: storage)
    monkeypatch.setattr("f_server.services.rebuild.get_storage", lambda: storage)
    monkeypatch.setattr("f_server.services.rebuild.get_settings", lambda: settings)
    monkeypatch.setattr("f_server.main.create_all", lambda: None)

    app = create_app()
    app.dependency_overrides[get_session] = override_session

    package_name = "com.example.unpinned"
    apk_v1 = build_signed_apk(tmp_path / "apks", package_name, 1, "1.0", "signer-one")
    apk_v2_wrong_signer = build_signed_apk(tmp_path / "apks", package_name, 2, "2.0", "signer-two")

    with testing_session() as session:
        upload_key = create_api_key(session, "fixture", [package_name]).secret

    with TestClient(app) as client:
        created = _upload(client, apk_v1, upload_key)
        assert created.status_code == 200
        assert created.json()["status"] == "created"

        signer_change = _upload(client, apk_v2_wrong_signer, upload_key)
        assert signer_change.status_code == 200
        assert signer_change.json()["status"] == "created"
        assert signer_change.json()["versionCode"] == 2

    with testing_session() as session:
        versions = session.scalars(select(Version).where(Version.package_name == package_name)).all()
        assert len(versions) == 2
        assert not session.scalar(select(AllowedSigningKey).where(AllowedSigningKey.package_name == package_name))
        assert not session.scalar(select(AuditLog).where(AuditLog.result == "422-cert"))


def test_architecture_variants_preserve_legacy_uploads_and_downloads(tmp_path, monkeypatch) -> None:
    settings = Settings(database_url="sqlite:///:memory:", repo=RepoConfig())
    storage = LocalStorage(tmp_path / "storage")
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_session():
        with testing_session() as session:
            yield session

    monkeypatch.setattr("f_server.routers.upload.get_settings", lambda: settings)
    monkeypatch.setattr("f_server.routers.upload.get_storage", lambda: storage)
    monkeypatch.setattr("f_server.routers.repo.get_storage", lambda: storage)
    monkeypatch.setattr("f_server.routers.admin.get_storage", lambda: storage)
    monkeypatch.setattr("f_server.services.rebuild.get_storage", lambda: storage)
    monkeypatch.setattr("f_server.services.rebuild.get_settings", lambda: settings)
    monkeypatch.setattr("f_server.main.create_all", lambda: None)
    notifications = []
    monkeypatch.setattr(
        "f_server.routers.upload.notify_app_published",
        lambda app, version, *args: notifications.append(version.id),
    )
    app = create_app()
    app.dependency_overrides[get_session] = override_session

    package_name = "com.example.architectures"
    legacy_apk = build_signed_apk(
        tmp_path / "apks", package_name, 1, "1.0", "signer-one", ("arm64-v8a",)
    )
    info = parse_apk(legacy_apk)
    legacy_name = f"{package_name}_1_{info.signer_fingerprint[:7]}.apk"
    legacy_digest = hashlib.sha256(legacy_apk.read_bytes()).hexdigest()
    with legacy_apk.open("rb") as fp:
        storage.put(f"repo/{legacy_name}", fp, "application/vnd.android.package-archive")
    with testing_session() as session:
        session.add(App(package_name=package_name, name="Architectures", categories=[]))
        session.flush()
        session.add(AllowedSigningKey(
            package_name=package_name, sha256_fingerprint=info.signer_fingerprint
        ))
        session.add(Version(
            package_name=package_name,
            version_code=1,
            version_name="1.0",
            apk_name=legacy_name,
            storage_key=f"repo/{legacy_name}",
            size=legacy_apk.stat().st_size,
            sha256_hash=legacy_digest,
            signer_fingerprint=info.signer_fingerprint,
            nativecode=["androidx.window.extensions"],
        ))
        key = create_api_key(session, "architectures", [package_name]).secret

    variants = [(), ("x86_64",), ("arm64-v8a", "x86_64")]
    apks = [
        build_signed_apk(tmp_path / "apks", package_name, 1, "1.0", "signer-one", abis)
        for abis in variants
    ]
    with TestClient(app) as client:
        legacy = _upload(client, legacy_apk, key)
        assert legacy.status_code == 200
        assert legacy.json()["status"] == "exists"
        assert legacy.json()["apkName"] == legacy_name
        assert legacy.json()["nativecode"] == ["arm64-v8a"]
        assert legacy.json()["indexFiles"]
        expected = {legacy_digest: (["arm64-v8a"], legacy_name, legacy_apk)}
        for abis, apk in zip(variants, apks, strict=True):
            created = _upload(client, apk, key)
            assert created.status_code == 200
            body = created.json()
            assert body["status"] == "created"
            assert body["nativecode"] == list(abis)
            expected[body["sha256"]] = (list(abis), body["apkName"], apk)
            repeated = _upload(client, apk, key)
            assert repeated.json()["status"] == "exists"
            assert repeated.json()["apkName"] == body["apkName"]
            assert repeated.json()["indexFiles"] == []
        assert len(notifications) == 3
        assert len({name for _, name, _ in expected.values()}) == 4
        rebuilt_apk = build_signed_apk(
            tmp_path / "apks", package_name, 1, "rebuilt", "signer-one", ("x86_64", "arm64-v8a")
        )
        rebuilt = _upload(client, rebuilt_apk, key)
        assert rebuilt.json()["status"] == "exists"
        assert rebuilt.json()["versionName"] == "1.0"
        assert rebuilt.json()["sha256"] in expected
        assert rebuilt.json()["indexFiles"] == []
        assert len(notifications) == 3

        package = client.get(f"/api/v1/packages/{package_name}").json()
        assert len(package["versions"]) == 4
        assert {v["sha256"]: v["nativecode"] for v in package["versions"]} == {
            digest: abis for digest, (abis, _, _) in expected.items()
        }
        v2 = client.get("/repo/index-v2.json").json()["packages"][package_name]["versions"]
        v1 = client.get("/repo/index-v1.json").json()["packages"][package_name]
        assert set(v2) == set(expected)
        assert {v["hash"]: v["nativecode"] for v in v1} == {
            digest: abis for digest, (abis, _, _) in expected.items()
        }
        for digest, (abis, name, apk) in expected.items():
            assert v2[digest]["manifest"]["versionCode"] == 1
            assert v2[digest]["manifest"]["nativecode"] == abis
            assert v2[digest]["file"]["name"] == f"/{name}"
            downloaded = client.get(f"/repo/{name}")
            assert downloaded.status_code == 200
            assert downloaded.content == apk.read_bytes()

        rejected_apk = build_signed_apk(
            tmp_path / "apks", package_name, 1, "1.0", "signer-two", ("armeabi-v7a",)
        )
        assert _upload(client, rejected_apk, key).status_code == 422

        with testing_session() as session:
            versions = session.scalars(select(Version)).all()
            assert len(versions) == 4
            legacy_version = next(v for v in versions if v.apk_name == legacy_name)
            assert legacy_version.nativecode_key == nativecode_identity(["arm64-v8a"])
            x86_version = next(v for v in versions if v.nativecode == ["x86_64"])
            deleted_name = x86_version.apk_name
            deleted_id = x86_version.id
        deleted = client.post(f"/admin/versions/{deleted_id}/delete", follow_redirects=False)
        assert deleted.status_code == 303
        assert client.get(f"/repo/{deleted_name}").status_code == 404
        assert client.get(f"/repo/{legacy_name}").content == legacy_apk.read_bytes()
        assert len(client.get(f"/api/v1/packages/{package_name}").json()["versions"]) == 3


def _upload(
    client: TestClient,
    apk_path,
    key: str | None,
    metadata: dict | None = None,
    screenshots: list[tuple[str, bytes]] | None = None,
):
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    data = {}
    if metadata is not None:
        data["metadata"] = json.dumps(metadata)
    with apk_path.open("rb") as fp:
        files = [("apk", (apk_path.name, fp, "application/vnd.android.package-archive"))]
        for filename, content in screenshots or []:
            files.append(("screenshots", (filename, content, "image/png")))
        return client.post(
            "/api/v1/upload",
            headers=headers,
            data=data,
            files=files,
        )
