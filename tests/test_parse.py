from __future__ import annotations

from f_server.fdroid.parse import parse_apk


class FakeApk:
    def get_min_sdk_version(self):
        return "23"

    def get_target_sdk_version(self):
        return "35"

    def get_max_sdk_version(self):
        return None

    def get_permissions(self):
        return ["android.permission.INTERNET"]

    def get_libraries(self):
        return ["androidx.window.extensions", "androidx.window.sidecar"]

    def get_app_name(self):
        return "Fixture"


def test_parse_apk_uses_native_abis_not_android_libraries(monkeypatch, tmp_path) -> None:
    apk_path = tmp_path / "fixture.apk"
    apk_path.write_bytes(b"apk")

    monkeypatch.setattr("f_server.fdroid.parse.verify_apk_signature", lambda path: None)
    monkeypatch.setattr("f_server.fdroid.parse.common.get_apk_id", lambda path: ("com.example.app", 1, "1.0"))
    monkeypatch.setattr("f_server.fdroid.parse.common.get_first_signer_certificate", lambda path: b"cert")
    monkeypatch.setattr("f_server.fdroid.parse.common.signer_fingerprint", lambda cert: "a" * 64)
    monkeypatch.setattr("f_server.fdroid.parse.common.get_androguard_APK", lambda path: FakeApk())
    monkeypatch.setattr(
        "f_server.fdroid.parse.common.get_native_code",
        lambda path: ["arm64-v8a", "x86_64"],
    )

    info = parse_apk(apk_path)

    assert info.nativecode == ("arm64-v8a", "x86_64")
