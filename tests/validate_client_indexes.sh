#!/usr/bin/env bash
set -euo pipefail

if [[ $# -eq 0 ]]; then
  echo "Usage: tests/validate_client_indexes.sh REPO_DIRECTORY [REPO_DIRECTORY ...]" >&2
  exit 2
fi

revision=5809dc397da52b66725e87355bc4918d6f519d36
serialization_version=1.9.0
cache_root="${FSERVER_CLIENT_VALIDATION_CACHE:-${XDG_CACHE_HOME:-${HOME}/.cache}/f-server-client-validation}"
validation_dir="${cache_root}/${revision}"
mkdir -p "${validation_dir}/sources" "${validation_dir}/lib"

fetch() {
  local url="$1" target="$2"
  if [[ ! -s "$target" ]]; then
    curl --fail --silent --show-error --location "$url" -o "${target}.tmp"
    mv "${target}.tmp" "$target"
  fi
}

source_root="https://gitlab.com/fdroid/fdroidclient/-/raw/${revision}"
sources=(
  libs/core/src/commonMain/kotlin/org/fdroid/IndexFile.kt
  libs/index/src/commonMain/kotlin/org/fdroid/index/IndexParser.kt
  libs/index/src/commonMain/kotlin/org/fdroid/index/IndexConverter.kt
  libs/index/src/commonMain/kotlin/org/fdroid/index/v1/AppV1.kt
  libs/index/src/commonMain/kotlin/org/fdroid/index/v1/IndexV1.kt
  libs/index/src/commonMain/kotlin/org/fdroid/index/v1/PackageV1.kt
  libs/index/src/commonMain/kotlin/org/fdroid/index/v2/IndexV2.kt
  libs/index/src/commonMain/kotlin/org/fdroid/index/v2/PackageV2.kt
)
for source in "${sources[@]}"; do
  fetch "${source_root}/${source}" "${validation_dir}/sources/${source##*/}"
done

for module in core json; do
  artifact="kotlinx-serialization-${module}-jvm"
  fetch \
    "https://repo.maven.apache.org/maven2/org/jetbrains/kotlinx/${artifact}/${serialization_version}/${artifact}-${serialization_version}.jar" \
    "${validation_dir}/lib/${artifact}.jar"
done
classpath="${validation_dir}/lib/kotlinx-serialization-core-jvm.jar:${validation_dir}/lib/kotlinx-serialization-json-jvm.jar"

if [[ ! -s "${validation_dir}/parser.jar" || "$0" -nt "${validation_dir}/parser.jar" ]]; then
  compiler="$(command -v "${KOTLINC:-kotlinc}")"
  compiler_dir="$(dirname "$(readlink -f "$compiler")")"
  plugin="${KOTLIN_SERIALIZATION_PLUGIN:-${compiler_dir}/../lib/kotlinx-serialization-compiler-plugin.jar}"
  cat > "${validation_dir}/Main.kt" <<'KOTLIN'
import java.io.File
import org.fdroid.index.IndexConverter
import org.fdroid.index.IndexParser

fun main(args: Array<String>) {
    for (path in args) {
        val dir = File(path)
        val v1 = IndexParser.parseV1(File(dir, "index-v1.json").readText())
        val v2 = IndexParser.parseV2(File(dir, "index-v2.json").readText())
        val entry = IndexParser.parseEntry(File(dir, "entry.json").readText())
        val converted = IndexConverter().toIndexV2(v1)
        check(v1.apps.map { it.packageName }.toSet() == v2.packages.keys)
        check(converted.packages.keys == v2.packages.keys)
        check(entry.index.numPackages == v2.packages.size)
        for ((name, versions) in v1.packages) {
            check(versions.map { it.hash }.toSet() == v2.packages.getValue(name).versions.keys)
            for (version in versions) {
                val modern = v2.packages.getValue(name).versions.getValue(version.hash)
                val adapted = converted.packages.getValue(name).versions.getValue(version.hash)
                check(version.packageName == name)
                check(version.signer == modern.manifest.signer?.sha256?.first())
                check(version.versionCode == modern.versionCode)
                check(version.versionName == modern.versionName)
                check(adapted.manifest.maxSdkVersion == modern.manifest.maxSdkVersion)
                check(adapted.manifest.nativecode == modern.manifest.nativecode)
                check(adapted.manifest.features == modern.manifest.features)
                check(adapted.manifest.usesPermission == modern.manifest.usesPermission)
                check(adapted.manifest.usesPermissionSdk23 == modern.manifest.usesPermissionSdk23)
            }
        }
        println("Upstream F-Droid v1/v2/entry parsing and v1 conversion passed: $path")
    }
}
KOTLIN
  "$compiler" -cp "$classpath" -Xplugin="$plugin" -include-runtime \
    -d "${validation_dir}/parser.jar" "${validation_dir}/Main.kt" "${validation_dir}"/sources/*.kt
fi

exec "${JAVA_HOME:+${JAVA_HOME}/bin/}java" \
  -cp "${validation_dir}/parser.jar:${classpath}" MainKt "$@"
