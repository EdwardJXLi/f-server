from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fdroidserver import common


class ApkVerificationUnavailable(RuntimeError):
    """Raised when APK signature verification cannot run."""


def verify_apk_signature(path: str | Path) -> None:
    apksigner = shutil.which("apksigner")
    if apksigner is None:
        raise ApkVerificationUnavailable("apksigner is required to verify APK signatures")
    try:
        result = subprocess.run(
            [apksigner, "verify", str(path)],
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ApkVerificationUnavailable("could not complete APK signature verification") from exc
    if result.returncode != 0:
        raise ValueError("APK signature verification failed")


@dataclass(frozen=True)
class ApkInfo:
    package_name: str
    version_code: int
    version_name: str | None
    signer_fingerprint: str
    min_sdk: int | None = None
    target_sdk: int | None = None
    max_sdk: int | None = None
    permissions: tuple[str, ...] = ()
    permission_details: tuple[dict[str, Any], ...] = ()
    permissions_sdk23: tuple[dict[str, Any], ...] = ()
    features: tuple[str, ...] = ()
    nativecode: tuple[str, ...] = ()
    label: str | None = None


def _safe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


def _permission(name: str, maximum: Any = None) -> dict[str, Any]:
    value: dict[str, Any] = {"name": name}
    bound = _safe_int(maximum)
    if bound is not None:
        value["maxSdkVersion"] = bound
    return value


def _manifest_permissions(manifest: Any, tag: str) -> list[dict[str, Any]]:
    if manifest is None:
        return []
    return [
        _permission(name, item.attrib.get(ANDROID_NS + "maxSdkVersion"))
        for item in manifest.findall(tag)
        if (name := item.attrib.get(ANDROID_NS + "name"))
    ]


def parse_apk(path: str | Path) -> ApkInfo:
    apk_path = str(path)
    verify_apk_signature(apk_path)
    package_name, version_code, version_name = common.get_apk_id(apk_path)
    cert = common.get_first_signer_certificate(apk_path)
    if not cert:
        raise ValueError("APK has no readable signing certificate")
    signer = common.signer_fingerprint(cert)

    apk = common.get_androguard_APK(apk_path)
    min_sdk = _safe_int(getattr(apk, "get_min_sdk_version", lambda: None)())
    target_sdk = _safe_int(getattr(apk, "get_target_sdk_version", lambda: None)())
    max_sdk = _safe_int(getattr(apk, "get_max_sdk_version", lambda: None)())
    manifest = getattr(apk, "get_android_manifest_xml", lambda: None)()
    if manifest is None:
        permission_details = [
            _permission(name) for name in getattr(apk, "get_permissions", lambda: [])() or []
        ]
    else:
        permission_details = _manifest_permissions(manifest, "uses-permission")
    for name, maximum in getattr(apk, "get_uses_implied_permission_list", lambda: [])() or []:
        permission = _permission(name, maximum)
        if permission not in permission_details:
            permission_details.append(permission)
    permissions_sdk23 = _manifest_permissions(manifest, "uses-permission-sdk-23")
    permissions = tuple(sorted({item["name"] for item in permission_details}))
    features = ()
    if manifest is not None:
        features = tuple(sorted({
            name
            for item in manifest.findall("uses-feature")
            if (name := item.attrib.get(ANDROID_NS + "name"))
            and item.attrib.get(ANDROID_NS + "required", "true").lower() not in {"false", "0"}
        }))
    nativecode = tuple(common.get_native_code(apk_path))
    label = getattr(apk, "get_app_name", lambda: None)()

    return ApkInfo(
        package_name=package_name,
        version_code=int(version_code),
        version_name=version_name or None,
        signer_fingerprint=signer,
        min_sdk=min_sdk,
        target_sdk=target_sdk,
        max_sdk=max_sdk,
        permissions=permissions,
        permission_details=tuple(permission_details),
        permissions_sdk23=tuple(permissions_sdk23),
        features=features,
        nativecode=nativecode,
        label=label,
    )
