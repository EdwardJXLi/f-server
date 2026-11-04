# F-Droid client parser validation

Use `validate_client_indexes.sh` to check a generated repository with the upstream F-Droid client parsers.
The script uses unchanged source files from [fdroidclient revision `5809dc397da52b66725e87355bc4918d6f519d36`](https://gitlab.com/fdroid/fdroidclient/-/tree/5809dc397da52b66725e87355bc4918d6f519d36/libs/index).
It calls `IndexParser.parseV1`, `parseV2`, `parseEntry`, and `IndexConverter.toIndexV2`.
It also compares package identities and compatibility metadata between the parsed formats.

Install a JDK, Kotlin 2.2.20, and `curl` before you run the script.
The script downloads upstream sources and serialization 1.9.0 into an external cache.
Set `FSERVER_CLIENT_VALIDATION_CACHE` to change its cache directory.
It adds no project dependencies and runs independently of pytest.

Pass directories that contain `index-v1.json`, `index-v2.json`, and `entry.json`:

```sh
tests/validate_client_indexes.sh /path/to/generated/repo
```

For the local Android Studio tools, use:

```sh
JAVA_HOME=/opt/android-studio/jbr \
KOTLINC=/opt/android-studio/plugins/Kotlin/kotlinc/bin/kotlinc \
tests/validate_client_indexes.sh /path/to/generated/repo
```

Successful parsing checks the current client contract. It does not test Android installation or older client versions.
