# f-server

`f-server` hosts an F-Droid repository for APKs published from CI. It supports signed v1/v2 indexes and local or S3 storage.

## Local setup

Local setup requires Python 3.12+, `uv`, Java, and `apksigner`. Repository signing also requires `keytool` and `jarsigner`.

```sh
uv sync
uv run f-server init --keystore ./repo-keystore.p12
uv run f-server keys create --label ci-app --scope com.example.app
uv run uvicorn f_server.main:app --reload
```

The admin panel is at `/admin`, the repository at `/repo`, and the health endpoint at `/health`.
See [Uploads and CI](docs/pushing-to-f-server.md) for publishing APKs.

## Configuration

Settings come from `config.yaml`, `.env`, or `FS_` environment variables. Nested keys use `__`, for example `FS_STORAGE__BACKEND=s3`.
SQLite and local file storage are the defaults. See [.env.example](.env.example) and [config.py](f_server/config.py) for available settings.

For signed indexes, set all four signing values in `config.yaml`:

```yaml
repo:
  url: https://f-server.example.com/repo
  keystore_path: ./repo-keystore.p12
  keystore_pass: change-me
  key_alias: f-server
  key_pass: change-me
```

Partial signing configuration blocks publication. Back up the repository keystore and password to preserve the repository identity.

Admin authentication supports `none` (default), `basic`, and `oidc`. Downloads are public until locked in the admin panel.
Uploads require a scoped API key and a valid APK signature. The first upload pins the app's signing certificate.
Setting `uploads.verify_signing_keys=false` disables certificate pinning. APK signature verification remains required.

## Containers

The Compose stack includes PostgreSQL and MinIO. Set the credentials and public repository URL in `.env`.

```sh
cp .env.example .env
podman compose up -d --build
podman compose exec f-server f-server init \
  --keystore /var/lib/f-server/keystore/repo-keystore.p12 \
  --alias f-server
```

Use the configured repository key password when prompted.
