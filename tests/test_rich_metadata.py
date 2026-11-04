import hashlib
import json
import os
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from f_server.auth.api_keys import create_api_key
from f_server.config import Settings, UploadsConfig
from f_server.db import Base, get_session
from f_server.main import create_app
from f_server.models import App, Version
from f_server.storage.local import LocalStorage
from tests.apk_factory import BUILD_TOOLS, JBR, android_tools_available, build_signed_apk


@pytest.fixture
def metadata_repo(tmp_path, monkeypatch):
    settings = Settings(
        database_url="sqlite:///:memory:", _env_file=None,
        uploads=UploadsConfig(verify_signing_keys=False),
    )
    engine = create_engine(
        settings.database_url, connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    storage = LocalStorage(tmp_path / "storage")

    def override_session():
        with sessions() as session:
            yield session

    monkeypatch.setattr("f_server.main.create_all", lambda: None)
    monkeypatch.setattr("f_server.routers.upload.get_settings", lambda: settings)
    monkeypatch.setattr("f_server.routers.upload.get_storage", lambda: storage)
    monkeypatch.setattr("f_server.routers.repo.get_storage", lambda: storage)
    monkeypatch.setattr("f_server.services.rebuild.get_storage", lambda: storage)
    monkeypatch.setattr("f_server.services.rebuild.get_settings", lambda: settings)
    monkeypatch.setattr("f_server.routers.upload.notify_app_published", lambda *args: None)
    app = create_app()
    app.dependency_overrides[get_session] = override_session
    package = "com.example.metadata"
    hashes = []
    with sessions() as session:
        session.add(App(package_name=package, name="Metadata", categories=["Tools"]))
        session.flush()
        for code, channel in [(1, "release"), (2, "Beta")]:
            data = f"original APK {code}".encode()
            digest = hashlib.sha256(data).hexdigest()
            hashes.append(digest)
            storage.put(f"repo/{code}.apk", BytesIO(data), "application/vnd.android.package-archive")
            session.add(Version(
                package_name=package, version_code=code, apk_name=f"{code}.apk",
                storage_key=f"repo/{code}.apk", size=len(data), sha256_hash=digest,
                signer_fingerprint="a" * 64, release_channel=channel,
            ))
        valid = create_api_key(session, "metadata", [package], permissions=["upload"]).secret
        wrong_scope = create_api_key(session, "other", ["org.other.*"]).secret
        read_only = create_api_key(session, "read", [package], permissions=["read"]).secret
    with TestClient(app) as client:
        yield client, sessions, storage, package, hashes, (valid, wrong_scope, read_only)
    engine.dispose()


def test_metadata_only_updates_publish_translations_dates_and_release_notes(metadata_repo) -> None:
    client, sessions, storage, package, hashes, keys = metadata_repo
    headers = {"Authorization": f"Bearer {keys[0]}"}
    app_url = f"/api/v1/packages/{package}/metadata"
    payload = {
        "license": "GPL-3.0-only", "author": "Example Author",
        "web_url": "https://example.com", "source_url": "https://example.com/source",
        "issue_url": "https://example.com/issues", "translation_url": "https://example.com/translate",
        "added": "2020-01-02T00:00:00Z",
        "localized": {"de": {"name": "Metadaten", "summary": "Zusammenfassung"}},
    }
    updated = client.patch(app_url, headers=headers, json=payload)
    assert updated.status_code == 200
    assert updated.json()["indexFiles"]
    repeated = client.patch(app_url, headers=headers, json=payload)
    assert repeated.json()["status"] == "unchanged"
    assert repeated.json()["indexFiles"] == []
    partial = client.patch(app_url, headers=headers, json={"localized": {"de": {"description": "Beschreibung"}}})
    assert partial.status_code == 200
    version_url = f"/api/v1/packages/{package}/versions/{hashes[1]}/metadata"
    release = {
        "release_channel": "release", "changelog": "Fixed the upload.",
        "whats_new": {"de": "Upload korrigiert."}, "released_at": 1580601600000,
    }
    updated = client.patch(version_url, headers=headers, json=release)
    assert updated.status_code == 200
    assert updated.json()["sha256"] == hashes[1]
    assert updated.json()["indexFiles"]
    assert client.patch(version_url, headers=headers, json=release).json()["status"] == "unchanged"
    assert client.patch(version_url, headers=headers, json={}).json()["indexFiles"] == []

    v2 = client.get("/repo/index-v2.json").json()
    metadata = v2["packages"][package]["metadata"]
    assert metadata["license"] == "GPL-3.0-only"
    assert metadata["authorName"] == "Example Author"
    assert metadata["translation"] == payload["translation_url"]
    assert metadata["sourceCode"] == payload["source_url"]
    assert metadata["issueTracker"] == payload["issue_url"]
    assert metadata["name"]["de"] == "Metadaten"
    assert metadata["summary"]["de"] == "Zusammenfassung"
    assert metadata["description"]["de"] == "Beschreibung"
    assert metadata["added"] == 1577923200000
    versions = v2["packages"][package]["versions"]
    assert versions[hashes[1]]["whatsNew"] == {"en-US": "Fixed the upload.", "de": "Upload korrigiert."}
    assert versions[hashes[1]]["added"] == 1580601600000
    assert versions[hashes[0]].get("whatsNew", {}) == {}
    v1 = client.get("/repo/index-v1.json").json()["apps"][0]
    assert v1["suggestedVersionCode"] == "2"
    assert v1["authorName"] == "Example Author"
    assert v1["localized"]["de"]["whatsNew"] == "Upload korrigiert."
    package_info = client.get(f"/api/v1/packages/{package}").json()
    assert package_info["license"] == "GPL-3.0-only"
    assert package_info["localized"]["de"]["name"] == "Metadaten"
    assert package_info["versions"][0]["whatsNew"]["de"] == "Upload korrigiert."
    for code, digest in enumerate(hashes, start=1):
        assert hashlib.sha256(client.get(f"/repo/{code}.apk").content).hexdigest() == digest
    with sessions() as session:
        assert len(session.scalars(select(Version)).all()) == 2


def test_metadata_updates_check_scope_permissions_and_patch_semantics(metadata_repo) -> None:
    client, sessions, storage, package, hashes, keys = metadata_repo
    app_url = f"/api/v1/packages/{package}/metadata"
    version_url = f"/api/v1/packages/{package}/versions/{hashes[1]}/metadata"
    for url in (app_url, version_url):
        assert client.patch(url, json={}).status_code == 401
        for key in keys[1:]:
            assert client.patch(url, headers={"Authorization": f"Bearer {key}"}, json={}).status_code == 403
    headers = {"Authorization": f"Bearer {keys[0]}"}
    assert client.patch(app_url, headers=headers, json={"license": "MIT", "categories": []}).status_code == 200
    assert client.patch(app_url, headers=headers, json={"summary": "Only this field"}).status_code == 200
    info = client.get(f"/api/v1/packages/{package}").json()
    assert info["license"] == "MIT"
    assert info["categories"] == []
    assert client.patch(app_url, headers=headers, json={"license": None}).status_code == 200
    assert client.get(f"/api/v1/packages/{package}").json()["license"] is None
    assert client.patch(app_url, headers=headers, json={"localized": {"../invalid": {"name": "bad"}}}).status_code == 422
    assert client.patch(app_url, headers=headers, json={"added": "invalid"}).status_code == 422
    assert client.patch(version_url.replace(hashes[1], "f" * 64), headers=headers, json={}).status_code == 404


@pytest.mark.skipif(not android_tools_available(), reason="Android SDK/JBR tools are unavailable")
def test_apk_upload_publishes_changelog_and_rich_metadata(metadata_repo, tmp_path, monkeypatch) -> None:
    client, sessions, storage, package, hashes, keys = metadata_repo
    monkeypatch.setenv("PATH", f"{BUILD_TOOLS}:{JBR / 'bin'}:{os.environ.get('PATH', '')}")
    apk = build_signed_apk(tmp_path / "apks", package, 3, "3.0", "signer")
    headers = {"Authorization": f"Bearer {keys[0]}"}
    payload = {"license": "MIT", "changelog": "Published notes", "localized": {"de": {"name": "Deutsch"}}}
    uploaded = client.post(
        "/api/v1/upload", headers=headers,
        files={"apk": ("app.apk", apk.read_bytes())}, data={"metadata": json.dumps(payload)},
    )
    assert uploaded.status_code == 200
    digest = uploaded.json()["sha256"]
    v2 = client.get("/repo/index-v2.json").json()["packages"][package]
    assert v2["metadata"]["license"] == "MIT"
    assert v2["metadata"]["name"]["de"] == "Deutsch"
    assert v2["versions"][digest]["whatsNew"] == {"en-US": "Published notes"}
    payload["changelog"] = "Corrected notes"
    repeated = client.post(
        "/api/v1/upload", headers=headers,
        files={"apk": ("app.apk", apk.read_bytes())}, data={"metadata": json.dumps(payload)},
    )
    assert repeated.json()["status"] == "exists"
    assert repeated.json()["indexFiles"]
    v2 = client.get("/repo/index-v2.json").json()["packages"][package]
    assert v2["versions"][digest]["whatsNew"] == {"en-US": "Corrected notes"}
