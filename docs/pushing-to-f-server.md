# Uploads and CI

Create a scoped key in the admin panel or with `f-server keys create --label ci-app --scope com.example.app`.
Store it as the CI secret `FSERVER_API_KEY`.

## Upload API

`POST /api/v1/upload` accepts multipart form data and an `Authorization: Bearer <FSERVER_API_KEY>` header.

| Field | Value |
| --- | --- |
| `apk` | Required APK file. |
| `metadata` | Optional JSON string with the fields below. |
| `icon` | Optional icon file. |
| `screenshots` | Optional repeated phone screenshot files. |
| `locale` | Artwork locale, such as `de`. Defaults to `en-US`. |

Every APK must pass `apksigner verify` and the key's package scope check. Later uploads must match a pinned signing certificate when pinning is enabled.

Upload each architecture APK separately. Different ABI sets can share a package name, version code, and signer.
The server detects ABIs from the APK. Upload responses and package entries expose them as `nativecode`.

Re-uploading the same package/version/signer/ABI set returns the stored APK with `"status":"exists"`.
Identical binaries can refresh parsed metadata and apply metadata changes. A different binary for that identity leaves the stored APK and metadata unchanged.
New uploads return `"status":"created"`. The response includes `sha256`, `apkName`, and `indexFiles`.

## Metadata

| Scope | Fields |
| --- | --- |
| App text | `name`, `summary`, `description`, `license`, `author` |
| App links | `web_url`, `source_url`, `issue_url`, `translation_url` |
| App categories | `categories`: array of strings |
| App translations | `localized`: locale map with `name`, `summary`, and `description` |
| App publication date | `added` |
| Version channel | `release_channel`: `release` (default), `Beta`, or another channel name |
| Version notes | `changelog`: English notes, or `whats_new`: locale-to-text map |
| Version release date | `released_at` |

Dates accept ISO 8601 values or Unix timestamps in milliseconds. Dates without a timezone use UTC.

`whats_new["en-US"]` takes precedence over `changelog`. The v2 index preserves notes per APK, while v1 publishes the suggested stable version's notes.

Use the exact name `Beta` for F-Droid's beta channel. Named channels appear in the v2 repository and version metadata.
Stable versions omit `releaseChannels`. The v1 `suggestedVersionCode` is the highest stable code as a string, or `"0"`.

## Updates without an APK

These endpoints require an upload key scoped to the existing package:

| Endpoint | Body |
| --- | --- |
| `PATCH /api/v1/packages/{packageName}/metadata` | JSON app fields |
| `PATCH /api/v1/packages/{packageName}/versions/{sha256}/metadata` | JSON version fields |
| `POST /api/v1/packages/{packageName}/artwork` | Multipart `icon`, `screenshots`, and `locale` |

Find APK hashes with `GET /api/v1/packages/{packageName}`.

Omitted metadata fields stay unchanged, including `release_channel`. Localized updates merge by locale and field.
Use `null` to clear optional text or localized entries. A supplied categories array replaces the previous categories.
PATCH responses use `updated` after rebuilding indexes, or `unchanged` with an empty `indexFiles` array.

The v2 artwork entries include paths, SHA-256 hashes, and byte sizes.

## GitHub Actions

The build job must provide an artifact named `app-release-apk`.

```yaml
jobs:
  publish-fserver:
    runs-on: ubuntu-latest
    needs: build
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: app-release-apk
          path: dist
      - name: Upload to f-server
        env:
          FSERVER_URL: https://f-server.example.com
          FSERVER_API_KEY: ${{ secrets.FSERVER_API_KEY }}
        run: |
          mapfile -d '' apks < <(find dist -type f -name '*.apk' -print0)
          test "${#apks[@]}" -gt 0
          for apk in "${apks[@]}"; do
            curl --fail-with-body \
              -H "Authorization: Bearer ${FSERVER_API_KEY}" \
              -F "apk=@${apk};type=application/vnd.android.package-archive" \
              -F 'metadata={"release_channel":"release"}' \
              "${FSERVER_URL}/api/v1/upload"
          done
```

## Errors

Errors use the JSON `detail` field.

| HTTP | Cause |
| --- | --- |
| 401 | Missing, invalid, or revoked API key. |
| 403 | Package outside key scope, missing upload permission, or missing create permission for a new package. |
| 404 | Package or APK not found. |
| 422 | Invalid APK, signature, metadata, or artwork locale. Also covers pinned-certificate mismatches. |
| 503 | APK signature verification unavailable. Check Java and `apksigner` on the server. |
