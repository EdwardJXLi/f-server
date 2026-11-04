from __future__ import annotations

import os
import subprocess
from pathlib import Path
from zipfile import ZipFile

ANDROID_SDK = Path("/home/hydra/Code/Android/SDK")
JBR = Path("/opt/android-studio/jbr")
BUILD_TOOLS = ANDROID_SDK / "build-tools" / "36.1.0"
PLATFORM_JAR = ANDROID_SDK / "platforms" / "android-36.1" / "android.jar"


def android_tools_available() -> bool:
    return all(
        path.exists()
        for path in [
            BUILD_TOOLS / "aapt",
            BUILD_TOOLS / "apksigner",
            PLATFORM_JAR,
            JBR / "bin" / "keytool",
            JBR / "bin" / "java",
        ]
    )


def build_signed_apk(
    workdir: Path,
    package_name: str,
    version_code: int,
    version_name: str,
    signer_name: str,
    nativecode: tuple[str, ...] = (),
    max_sdk: int | None = None,
    manifest_elements: str = "",
) -> Path:
    variant = "_".join(nativecode) or "universal"
    apk_dir = workdir / f"{package_name}-{version_code}-{signer_name}-{variant}"
    signer_dir = workdir / signer_name
    apk_dir.mkdir(parents=True, exist_ok=True)
    signer_dir.mkdir(parents=True, exist_ok=True)
    manifest = apk_dir / "AndroidManifest.xml"
    max_sdk_attribute = f' android:maxSdkVersion="{max_sdk}"' if max_sdk is not None else ""
    manifest.write_text(
        f"""<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{package_name}" android:versionCode="{version_code}" android:versionName="{version_name}">
    <uses-sdk android:minSdkVersion="23" android:targetSdkVersion="35"{max_sdk_attribute} />
    <uses-permission android:name="android.permission.INTERNET" />
    {manifest_elements}
    <application android:label="Fixture" />
</manifest>
""",
        encoding="utf-8",
    )
    unsigned = apk_dir / "unsigned.apk"
    signed = apk_dir / f"{package_name}-{version_code}-{signer_name}.apk"
    keystore = signer_dir / "key.p12"

    subprocess.run(
        [
            str(BUILD_TOOLS / "aapt"),
            "package",
            "-f",
            "-M",
            str(manifest),
            "-I",
            str(PLATFORM_JAR),
            "-F",
            str(unsigned),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    with ZipFile(unsigned, "a") as archive:
        for abi in nativecode:
            archive.writestr(f"lib/{abi}/libfixture.so", b"fixture native library")
    if not keystore.exists():
        subprocess.run(
            [
                str(JBR / "bin" / "keytool"),
                "-genkeypair",
                "-storetype",
                "PKCS12",
                "-keystore",
                str(keystore),
                "-storepass",
                "changeit",
                "-keypass",
                "changeit",
                "-alias",
                signer_name,
                "-keyalg",
                "RSA",
                "-keysize",
                "2048",
                "-validity",
                "3650",
                "-dname",
                f"CN={signer_name}",
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    env = os.environ.copy()
    env["PATH"] = f"{JBR / 'bin'}:{env.get('PATH', '')}"
    subprocess.run(
        [
            str(BUILD_TOOLS / "apksigner"),
            "sign",
            "--ks",
            str(keystore),
            "--ks-pass",
            "pass:changeit",
            "--key-pass",
            "pass:changeit",
            "--ks-key-alias",
            signer_name,
            "--out",
            str(signed),
            str(unsigned),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    return signed
