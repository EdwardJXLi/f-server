from __future__ import annotations

import os
import subprocess
from zipfile import ZipFile

import pytest
from fastapi.testclient import TestClient
from fdroidserver import common
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from f_server.auth.api_keys import create_api_key
from f_server.config import RepoConfig, Settings, UploadsConfig
from f_server.db import Base, get_session
from f_server.fdroid.parse import ApkVerificationUnavailable, parse_apk, verify_apk_signature
from f_server.main import create_app
from f_server.models import Version
from f_server.storage.local import LocalStorage
from tests.apk_factory import BUILD_TOOLS, JBR, android_tools_available, build_signed_apk


@pytest.fixture
def signed_apk(tmp_path, monkeypatch):
    if not android_tools_available():
        pytest.skip("Android SDK/JBR tools are unavailable")
    monkeypatch.setenv("PATH", f"{BUILD_TOOLS}:{JBR / 'bin'}:{os.environ.get('PATH', '')}")
    monkeypatch.setenv("JAVA_HOME", str(JBR))
    return build_signed_apk(tmp_path / "apks", "com.example.signature", 1, "1.0", "signer")


def test_parse_rejects_tampered_apk_with_unchanged_certificate(signed_apk) -> None:
    original = parse_apk(signed_apk)
    with ZipFile(signed_apk, "a") as archive:
        archive.writestr("assets/tampered.txt", b"added after signing")
    certificate = common.get_first_signer_certificate(str(signed_apk))
    assert common.signer_fingerprint(certificate) == original.signer_fingerprint
    with pytest.raises(ValueError, match="APK signature verification failed"):
        parse_apk(signed_apk)


@pytest.mark.parametrize("verify_signing_keys", [True, False])
def test_upload_rejects_tampering_before_idempotency(
    signed_apk, tmp_path, monkeypatch, verify_signing_keys
) -> None:
    settings = Settings(
        database_url="sqlite:///:memory:",
        repo=RepoConfig(),
        uploads=UploadsConfig(verify_signing_keys=verify_signing_keys),
        _env_file=None,
    )
    storage = LocalStorage(tmp_path / "storage")
    engine = create_engine(
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
    with sessions() as session:
        key = create_api_key(session, "signature", ["com.example.signature"]).secret
    headers = {"Authorization": f"Bearer {key}"}
    original_bytes = signed_apk.read_bytes()
    with TestClient(app) as client:
        valid = client.post(
            "/api/v1/upload", headers=headers, files={"apk": ("app.apk", original_bytes)}
        )
        assert valid.status_code == 200
        with ZipFile(signed_apk, "a") as archive:
            archive.writestr("assets/tampered.txt", b"added after signing")
        rejected = client.post(
            "/api/v1/upload", headers=headers, files={"apk": ("app.apk", signed_apk.read_bytes())}
        )
        assert rejected.status_code == 422
        assert "APK signature verification failed" in rejected.json()["detail"]
    with sessions() as session:
        versions = session.scalars(select(Version)).all()
        assert len(versions) == 1
        assert versions[0].sha256_hash == valid.json()["sha256"]
        with storage.open_stream(versions[0].storage_key) as stream:
            assert stream.read() == original_bytes
    engine.dispose()


def test_signature_verification_requires_apksigner(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("f_server.fdroid.parse.shutil.which", lambda command: None)
    with pytest.raises(ApkVerificationUnavailable, match="apksigner is required"):
        verify_apk_signature(tmp_path / "app.apk")


def test_signature_verification_reports_tool_timeout(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("f_server.fdroid.parse.shutil.which", lambda command: "/tool/apksigner")

    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr("f_server.fdroid.parse.subprocess.run", timed_out)
    with pytest.raises(ApkVerificationUnavailable, match="could not complete"):
        verify_apk_signature(tmp_path / "app.apk")
