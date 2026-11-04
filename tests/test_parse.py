from __future__ import annotations

from xml.etree.ElementTree import fromstring

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
    assert info.permission_details == ({"name": "android.permission.INTERNET"},)


def test_parse_preserves_permission_bounds_and_required_features(monkeypatch, tmp_path) -> None:
    class ManifestApk(FakeApk):
        def get_android_manifest_xml(self):
            return fromstring('''
                <manifest xmlns:android="http://schemas.android.com/apk/res/android">
                  <uses-permission android:name="android.permission.INTERNET" />
                  <uses-permission android:name="android.permission.WRITE_EXTERNAL_STORAGE" android:maxSdkVersion="18" />
                  <uses-permission-sdk-23 android:name="android.permission.ACCESS_FINE_LOCATION" android:maxSdkVersion="28" />
                  <uses-permission-sdk-23 android:name="android.permission.BODY_SENSORS" />
                  <uses-feature android:name="android.hardware.camera" />
                  <uses-feature android:name="android.software.leanback" android:required="true" />
                  <uses-feature android:name="android.hardware.bluetooth" android:required="false" />
                  <uses-feature android:glEsVersion="0x00020000" android:required="true" />
                </manifest>
            ''')

        def get_uses_implied_permission_list(self):
            return [["android.permission.READ_EXTERNAL_STORAGE", 18]]

    monkeypatch.setattr("f_server.fdroid.parse.verify_apk_signature", lambda path: None)
    monkeypatch.setattr("f_server.fdroid.parse.common.get_apk_id", lambda path: ("com.example.app", 1, "1.0"))
    monkeypatch.setattr("f_server.fdroid.parse.common.get_first_signer_certificate", lambda path: b"cert")
    monkeypatch.setattr("f_server.fdroid.parse.common.signer_fingerprint", lambda cert: "a" * 64)
    monkeypatch.setattr("f_server.fdroid.parse.common.get_androguard_APK", lambda path: ManifestApk())
    monkeypatch.setattr("f_server.fdroid.parse.common.get_native_code", lambda path: [])

    info = parse_apk(tmp_path / "fixture.apk")

    assert info.permission_details == (
        {"name": "android.permission.INTERNET"},
        {"name": "android.permission.WRITE_EXTERNAL_STORAGE", "maxSdkVersion": 18},
        {"name": "android.permission.READ_EXTERNAL_STORAGE", "maxSdkVersion": 18},
    )
    assert info.permissions_sdk23 == (
        {"name": "android.permission.ACCESS_FINE_LOCATION", "maxSdkVersion": 28},
        {"name": "android.permission.BODY_SENSORS"},
    )
    assert info.features == ("android.hardware.camera", "android.software.leanback")
