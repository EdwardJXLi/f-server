# Client parser validation

The script requires a JDK, Kotlin 2.2.20, and `curl`.

```sh
tests/validate_client_indexes.sh /path/to/generated/repo
```

The directory must contain `index-v1.json`, `index-v2.json`, and `entry.json`.
The script uses [F-Droid client revision `5809dc397da52b66725e87355bc4918d6f519d36`](https://gitlab.com/fdroid/fdroidclient/-/tree/5809dc397da52b66725e87355bc4918d6f519d36/libs/index).
It parses both indexes and the entry file, converts v1 to v2, and compares package identities and compatibility metadata.
Android installation and older clients are not covered.

Sources and serialization 1.9.0 are cached outside the project. Set `FSERVER_CLIENT_VALIDATION_CACHE` to choose the cache directory.
Set `JAVA_HOME` and `KOTLINC` if the tools are outside `PATH`.
