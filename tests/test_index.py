import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from f_server.config import Settings
from f_server.db import Base
from f_server.fdroid.index import build_index_payload, write_unsigned_indexes
from f_server.models import AllowedSigningKey, App, Version


def test_build_index_payload_contains_versions(tmp_path) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        app = App(package_name="com.example.app", name="Example", summary="Summary", categories=["Tools"])
        session.add(app)
        session.flush()
        session.add(AllowedSigningKey(package_name=app.package_name, sha256_fingerprint="a" * 64))
        session.add(
            Version(
                package_name=app.package_name,
                version_name="1.0",
                version_code=1,
                apk_name="com.example.app_1_aaaaaaa.apk",
                storage_key="repo/com.example.app_1_aaaaaaa.apk",
                size=3,
                sha256_hash="b" * 64,
                min_sdk=23,
                target_sdk=35,
                max_sdk=None,
                nativecode=[],
                permissions=["android.permission.INTERNET"],
                signer_fingerprint="a" * 64,
                release_channel="release",
            )
        )
        session.commit()

        payload = build_index_payload(session, Settings())
        package = payload["packages"]["com.example.app"]
        assert package["metadata"]["name"]["en-US"] == "Example"
        assert package["versions"]["b" * 64]["manifest"]["versionCode"] == 1

        files = write_unsigned_indexes(session, Settings(), tmp_path / "repo")
        assert {path.name for path in files} == {"entry.json", "index-v1.json", "index-v2.json"}
        v1 = json.loads((tmp_path / "repo" / "index-v1.json").read_text())
        assert isinstance(v1["repo"]["name"], str)
        assert isinstance(v1["repo"]["description"], str)
        assert isinstance(v1["repo"]["icon"], str)
        assert v1["repo"]["version"] == 20002
        assert v1["apps"][0]["license"] == "Unknown"
        assert v1["packages"][app.package_name][0]["packageName"] == app.package_name
        assert v1["packages"][app.package_name][0]["signer"] == "a" * 64


@pytest.mark.parametrize(
    ("channels", "stable_code", "beta_code"),
    [
        (["release", "release"], 2, 2),
        (["release", "Beta", "release", "Beta"], 3, 4),
        (["Beta", "Beta"], 0, 2),
        (["release", "Beta", "Nightly"], 1, 2),
        ([], 0, 0),
    ],
)
def test_release_channels_control_suggested_versions(
    tmp_path, channels, stable_code, beta_code
) -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    package_name = "com.example.channels"
    with Session(engine) as session:
        session.add(App(
            package_name=package_name,
            name="Channels",
            categories=[],
            suggested_version_code=999,
        ))
        session.flush()
        for code, channel in enumerate(channels, start=1):
            name = f"{package_name}_{code}.apk"
            session.add(Version(
                package_name=package_name,
                version_code=code,
                version_name=str(code),
                apk_name=name,
                storage_key=f"repo/{name}",
                size=code,
                sha256_hash=f"{code:064x}",
                signer_fingerprint="a" * 64,
                release_channel=channel,
            ))
        session.commit()
        repodir = tmp_path / "repo"
        write_unsigned_indexes(session, Settings(), repodir)

    v2 = json.loads((repodir / "index-v2.json").read_text())
    versions = v2["packages"][package_name]["versions"].values()
    for version in versions:
        code = version["manifest"]["versionCode"]
        channel = channels[code - 1]
        assert version.get("releaseChannels", []) == ([] if channel == "release" else [channel])
        if channel != "release":
            assert v2["repo"]["releaseChannels"][channel] == {"name": {"en-US": channel}}
    stable = [v for v in versions if not v.get("releaseChannels")]
    with_beta = [
        v for v in versions
        if not v.get("releaseChannels") or "Beta" in v["releaseChannels"]
    ]
    assert max((v["manifest"]["versionCode"] for v in stable), default=0) == stable_code
    assert max((v["manifest"]["versionCode"] for v in with_beta), default=0) == beta_code
    v1 = json.loads((repodir / "index-v1.json").read_text())
    assert v1["apps"][0]["suggestedVersionCode"] == str(stable_code)
