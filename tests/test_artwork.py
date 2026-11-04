import hashlib
import json
from io import BytesIO

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from f_server.auth.api_keys import create_api_key
from f_server.config import Settings
from f_server.db import Base, get_session
from f_server.fdroid.index import write_unsigned_indexes
from f_server.main import create_app
from f_server.models import App, Asset
from f_server.storage.local import LocalStorage


def test_localized_artwork_is_published_with_download_integrity(tmp_path) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    storage = LocalStorage(tmp_path / "storage")
    package = "com.example.artwork"
    files = {
        f"repo/{package}/en-US/icon.png": b"english icon",
        f"repo/{package}/de/icon.png": b"german icon",
        f"repo/{package}/de/phoneScreenshots/01.png": b"german screenshot",
    }
    with Session(engine) as session:
        session.add(App(package_name=package, categories=[]))
        session.flush()
        for key, content in files.items():
            storage.put(key, BytesIO(content), "image/png")
            session.add(Asset(
                package_name=package,
                locale="en-US" if "/en-US/" in key else "de",
                kind="screenshot" if "phoneScreenshots" in key else "icon",
                storage_key=key,
            ))
        session.commit()
        repodir = tmp_path / "repo"
        write_unsigned_indexes(session, Settings(), repodir, storage=storage)
        session.commit()
        v2 = json.loads((repodir / "index-v2.json").read_text())
        metadata = v2["packages"][package]["metadata"]
        entries = list(metadata["icon"].values()) + metadata["screenshots"]["phone"]["de"]
        assert len(entries) == 3
        for entry in entries:
            key = "repo" + entry["name"]
            with storage.open_stream(key) as stream:
                downloaded = stream.read()
            assert entry["sha256"] == hashlib.sha256(downloaded).hexdigest()
            assert entry["size"] == len(downloaded)
        v1 = json.loads((repodir / "index-v1.json").read_text())
        assert v1["apps"][0]["localized"]["de"]["icon"] == "icon.png"
        assert v1["apps"][0]["localized"]["de"]["phoneScreenshots"] == ["01.png"]

        def unexpected_read(key):
            raise AssertionError(f"cached artwork reread: {key}")

        storage.open_stream = unexpected_read
        write_unsigned_indexes(session, Settings(), repodir, storage=storage)
    engine.dispose()


def test_artwork_upload_without_apk_requires_package_scope(tmp_path, monkeypatch) -> None:
    settings = Settings(database_url="sqlite:///:memory:", _env_file=None)
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
    monkeypatch.setattr("f_server.routers.upload.get_storage", lambda: storage)
    monkeypatch.setattr("f_server.routers.repo.get_storage", lambda: storage)
    monkeypatch.setattr("f_server.services.rebuild.get_storage", lambda: storage)
    monkeypatch.setattr("f_server.services.rebuild.get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_session] = override_session
    package = "com.example.artwork"
    with sessions() as session:
        session.add(App(package_name=package, categories=[]))
        valid = create_api_key(session, "artwork", [package]).secret
        other = create_api_key(session, "other", ["com.example.other"]).secret
    url = f"/api/v1/packages/{package}/artwork"
    with TestClient(app) as client:
        assert client.post(url, files={"icon": ("icon.png", b"icon")}).status_code == 401
        assert client.post(
            url, headers={"Authorization": f"Bearer {other}"}, files={"icon": ("icon.png", b"icon")}
        ).status_code == 403
        headers = {"Authorization": f"Bearer {valid}"}
        response = client.post(
            url, headers=headers, data={"locale": "de"},
            files=[("icon", ("icon.png", b"german icon")), ("screenshots", ("screen.png", b"screen"))],
        )
        assert response.status_code == 200
        assert response.json()["indexFiles"]
        metadata = client.get("/repo/index-v2.json").json()["packages"][package]["metadata"]
        icon = metadata["icon"]["de"]
        downloaded = client.get("/repo" + icon["name"])
        assert downloaded.content == b"german icon"
        assert hashlib.sha256(downloaded.content).hexdigest() == icon["sha256"]
        assert icon["size"] == len(downloaded.content)
        assert client.post(
            url, headers=headers, data={"locale": "../../escape"}, files={"icon": ("icon.png", b"bad")}
        ).status_code == 422
    engine.dispose()
