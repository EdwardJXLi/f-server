# f-server

`f-server` is a self-hosted F-Droid-compatible repository service for many independent Android projects. CI jobs upload APKs with scoped API keys; the server verifies package scope, pins APK signing certificates on first upload, stores binaries, and republishes F-Droid index files.

## Quick start

```sh
uv sync
uv run f-server init --keystore ./repo-keystore.p12
uv run f-server keys create --label first-ci --scope '*'
uv run uvicorn f_server.main:app --reload
```

The admin panel is at `http://localhost:8000/admin`, the public repository is under `http://localhost:8000/repo`, and health checks use `GET /health`.

Repository downloads are public by default. Use the admin panel to set the registry password and lock or unlock downloads.

## Configuration

Configuration is read from `config.yaml`, `.env`, and `FS_`-prefixed environment variables. Nested values use `__`, for example `FS_STORAGE__BACKEND=s3`.

```yaml
database_url: sqlite:///./f-server.db
storage:
  backend: local
  local_path: ./data/storage
  # for S3/min.io, endpoint is used by the server and public_base_url is used in presigned redirects
  # endpoint: http://minio:9000
  # public_base_url: http://127.0.0.1:9000
repo:
  name: f-server
  description: Private F-Droid repository
  url: http://localhost:8000/repo
  keystore_path: ./repo-keystore.p12
  keystore_pass: change-me
  key_alias: f-server
  key_pass: change-me
admin_auth:
  mode: none
  session_secret: change-me
uploads:
  onboarding: tofu_scoped
  verify_signing_keys: true
notifications:
  # optional Discord webhook for newly published app versions
  # discord_webhook_url: https://discord.com/api/webhooks/...
  discord_username: f-server
  discord_timeout_seconds: 5.0
```

### Configuration reference

The YAML keys below can be set in `config.yaml`. The equivalent environment variable is shown for deployments that prefer `.env` or process environment overrides. Environment variables start with `FS_`, and nested YAML keys use `__`.

| YAML key | Environment override | Default | Meaning |
| --- | --- | --- | --- |
| `database_url` | `FS_DATABASE_URL` | `sqlite:///./f-server.db` | SQLAlchemy database URL. Use SQLite for a single local instance or Postgres for container/server deployments. |
| `storage.backend` | `FS_STORAGE__BACKEND` | `local` | Storage backend for APKs and generated repo files. Supported values are `local` and `s3`. |
| `storage.local_path` | `FS_STORAGE__LOCAL_PATH` | `./data/storage` | Directory used by the `local` storage backend. |
| `storage.endpoint` | `FS_STORAGE__ENDPOINT` | unset | S3-compatible endpoint used by the server, for example `http://minio:9000`. Leave unset for AWS S3's standard endpoint. |
| `storage.public_base_url` | `FS_STORAGE__PUBLIC_BASE_URL` | unset | Public endpoint used when generating download URLs. For MinIO in compose this is usually a host-reachable URL such as `http://127.0.0.1:9000`. |
| `storage.bucket` | `FS_STORAGE__BUCKET` | `f-server` | S3 bucket that stores APKs and repo files. |
| `storage.region` | `FS_STORAGE__REGION` | `us-east-1` | S3 region. MinIO commonly uses `us-east-1`. |
| `storage.access_key` | `FS_STORAGE__ACCESS_KEY` | unset | S3 access key. Required for MinIO unless credentials are supplied by the runtime environment. |
| `storage.secret_key` | `FS_STORAGE__SECRET_KEY` | unset | S3 secret key. Required for MinIO unless credentials are supplied by the runtime environment. |
| `repo.name` | `FS_REPO__NAME` | `f-server` | Repository name written into F-Droid index metadata. |
| `repo.description` | `FS_REPO__DESCRIPTION` | `Private F-Droid repository` | Repository description written into F-Droid index metadata. |
| `repo.url` | `FS_REPO__URL` | `http://localhost:8000/repo` | Public URL of the repository as Android clients should see it. This should normally end in `/repo`. |
| `repo.icon` | `FS_REPO__ICON` | unset | Optional repository icon setting. |
| `repo.keystore_path` | `FS_REPO__KEYSTORE_PATH` | unset | Path to the repository signing keystore. Required for signed F-Droid indexes. |
| `repo.keystore_pass` | `FS_REPO__KEYSTORE_PASS` | unset | Password for the repository signing keystore. Required when `repo.keystore_path` is set. |
| `repo.key_alias` | `FS_REPO__KEY_ALIAS` | unset | Alias of the repository signing key inside the keystore. Required for signing. |
| `repo.key_pass` | `FS_REPO__KEY_PASS` | unset | Password for the repository signing key. Required for signing. |
| `admin_auth.mode` | `FS_ADMIN_AUTH__MODE` | `none` | Admin UI authentication mode. Supported values are `none`, `basic`, and `oidc`. |
| `admin_auth.session_secret` | `FS_ADMIN_AUTH__SESSION_SECRET` | `change-me` | Secret used to sign admin session cookies. Change this in any shared or public deployment. |
| `admin_auth.username` | `FS_ADMIN_AUTH__USERNAME` | `admin` | Username for `basic` admin authentication. |
| `admin_auth.password` | `FS_ADMIN_AUTH__PASSWORD` | unset | Password for `basic` admin authentication. Required when `admin_auth.mode` is `basic`. |
| `admin_auth.issuer` | `FS_ADMIN_AUTH__ISSUER` | unset | OIDC issuer URL. Required when `admin_auth.mode` is `oidc`. |
| `admin_auth.client_id` | `FS_ADMIN_AUTH__CLIENT_ID` | unset | OIDC client ID. Required for OIDC admin login. |
| `admin_auth.client_secret` | `FS_ADMIN_AUTH__CLIENT_SECRET` | unset | OIDC client secret. Required for OIDC admin login. |
| `admin_auth.redirect_url` | `FS_ADMIN_AUTH__REDIRECT_URL` | unset | Optional OIDC redirect URL setting. |
| `admin_auth.scopes` | `FS_ADMIN_AUTH__SCOPES` | `openid profile email` | OIDC scopes requested during admin login. |
| `uploads.onboarding` | `FS_UPLOADS__ONBOARDING` | `tofu_scoped` | Upload onboarding policy. The current supported value pins the first signing certificate for an in-scope package when signing-key verification is enabled. |
| `uploads.verify_signing_keys` | `FS_UPLOADS__VERIFY_SIGNING_KEYS` | `true` | Whether uploads must match pinned APK signing certificates. Set to `false` to skip first-upload pinning and later signing-key mismatch rejection. |
| `notifications.discord_webhook_url` | `FS_NOTIFICATIONS__DISCORD_WEBHOOK_URL` | unset | Optional Discord webhook URL. When set, f-server sends a message after each newly published app version. Idempotent re-uploads do not send duplicate messages. |
| `notifications.discord_username` | `FS_NOTIFICATIONS__DISCORD_USERNAME` | `f-server` | Display username used by Discord webhook messages. |
| `notifications.discord_timeout_seconds` | `FS_NOTIFICATIONS__DISCORD_TIMEOUT_SECONDS` | `5.0` | Timeout for sending Discord webhook messages. Notification failures are logged and do not fail the upload. |

The example compose stack also uses container-specific variables that are not read directly by f-server:

| Environment variable | Used by | Meaning |
| --- | --- | --- |
| `POSTGRES_DB` | Postgres container | Database created at first startup. Must match the database name in `database_url`. |
| `POSTGRES_USER` | Postgres container | Database user created at first startup. Must match the user in `database_url`. |
| `POSTGRES_PASSWORD` | Postgres container | Database password created at first startup. Must match the password in `database_url`. |
| `MINIO_ROOT_USER` | MinIO container | MinIO admin/access key used by the sample stack. Must match `storage.access_key` unless you create separate credentials. |
| `MINIO_ROOT_PASSWORD` | MinIO container | MinIO admin/secret key used by the sample stack. Must match `storage.secret_key` unless you create separate credentials. |

Upload clients and CI jobs use non-`FS_` variables because they are not configuring the server process:

| Environment variable | Used by | Meaning |
| --- | --- | --- |
| `FSERVER_URL` | Upload client or CI job | Base URL of the f-server instance, for example `https://f-server.example.com`. |
| `FSERVER_API_KEY` | Upload client or CI job | Bearer token created in f-server for a specific project/package scope. Store this as a CI secret. |

### Repository signing key

f-server signs the generated F-Droid repository indexes with a repository signing key. This is separate from Android app signing keys: app signing keys stay in each app's build system, while the repository key belongs to the f-server deployment and should remain stable for the life of the repository.

Create a new PKCS12 repository keystore with:

```sh
uv run f-server init \
  --keystore ./repo-keystore.p12 \
  --alias f-server
```

The command prompts for the keystore password, creates a 4096-bit RSA key, and prints the repository key fingerprint. Configure f-server to use the generated key:

```yaml
repo:
  keystore_path: ./repo-keystore.p12
  keystore_pass: change-me
  key_alias: f-server
  key_pass: change-me
```

For the compose deployment, keep the keystore in the app volume and use the in-container path from `.env.example`:

```sh
podman compose exec f-server f-server init \
  --keystore /var/lib/f-server/keystore/repo-keystore.p12 \
  --alias f-server
```

```yaml
repo:
  keystore_path: /var/lib/f-server/keystore/repo-keystore.p12
  keystore_pass: change-me
  key_alias: f-server
  key_pass: change-me
```

Back up the keystore and its password. Losing them prevents f-server from signing future updates with the same repository identity, and rotating the repository key requires clients to trust the new repository key. Do not commit the keystore or real passwords to source control.

The equivalent environment overrides are `FS_REPO__KEYSTORE_PATH`, `FS_REPO__KEYSTORE_PASS`, `FS_REPO__KEY_ALIAS`, and `FS_REPO__KEY_PASS`. All four values must be configured for signed indexes; if any are missing, f-server publishes unsigned index files. Repo signing requires `keytool`, `jarsigner`, and `apksigner` on the host or container image.

## Upload security

Every upload must pass both checks:

- API key scope: the bearer token must allow the APK package name, using exact names or globs such as `com.example.*`.
- APK signing pin: the first upload for a new package pins its signing certificate fingerprint. Later uploads must match a pinned fingerprint unless an admin adds a rotated key.

Set `uploads.verify_signing_keys=false` or `FS_UPLOADS__VERIFY_SIGNING_KEYS=false` only if package scope is sufficient for your deployment. With verification disabled, f-server still records each APK signer but does not reject signer changes.

Every uploaded APK must pass `apksigner verify`. Uploads require `apksigner` and Java, including deployments that do not sign repository indexes.
The Android signing key is never uploaded to f-server.

## CI integration

See [docs/pushing-to-f-server.md](docs/pushing-to-f-server.md) for the full upload contract, GitHub Actions job, curl snippet, and error remediation table.

Upload architecture-specific APKs separately. Different ABI sets can share a package name, version code, and signer.
The server detects ABIs from each APK and publishes each variant in both F-Droid indexes.

## Podman deployment

```sh
cp .env.example .env
podman compose up --build
```

Generate the repo signing keystore in the app volume before publishing APKs:

```sh
podman compose exec f-server f-server init \
  --keystore /var/lib/f-server/keystore/repo-keystore.p12 \
  --alias f-server \
  --password change-me
```

For rootless Podman bind mounts, prefer named volumes. If binding host directories for keystores or local storage, use SELinux relabeling such as `:Z` or keep user IDs aligned with `--userns=keep-id`.
