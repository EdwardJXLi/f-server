# Pushing APKs to f-server

f-server is a private F-Droid repository server. A project CI job uploads each APK to `POST /api/v1/upload`; f-server authenticates the project with `FSERVER_API_KEY`, verifies the APK package name is in that key's scope, pins the APK signing certificate on first upload, and publishes accepted versions to the repo index. The only prerequisite is a repository secret named `FSERVER_API_KEY` provided by the human/admin; it is minted in the admin panel under API Keys or with `f-server keys create --label ci-app --scope com.example.app`.

## Upload contract

Endpoint:

```text
POST https://f-server.example.com/api/v1/upload
Authorization: Bearer <FSERVER_API_KEY>
Content-Type: multipart/form-data
```

Multipart fields:

- `apk` required file. The Android APK to publish.
- `metadata` optional JSON string. Supported fields:

```json
{
  "name": "Example App",
  "summary": "Short app summary",
  "description": "Longer F-Droid description",
  "categories": ["Internet", "Security"],
  "changelog": "What changed in this build",
  "release_channel": "release"
}
```

- `icon` optional file. Stored as the app icon asset.
- `screenshots` optional repeated file field. Stored as localized phone screenshot assets.
- `locale` optional artwork locale, such as `en-US` or `de`. The default is `en-US`.

To upload artwork without an APK, send the same artwork fields to `POST /api/v1/packages/{packageName}/artwork`.
This endpoint requires an upload key scoped to the existing package. Both indexes reference the uploaded icons and screenshots.
The v2 localized file entries include repository paths, SHA-256 hashes, and byte sizes.

`release_channel` defaults to `release` for new APKs. Use `Beta` for beta releases, with this exact capitalization.
Other channel names are published as supplied. Stable APKs have no version-level `releaseChannels` in `index-v2.json`.
Each named channel appears in the repository channel definitions and on its versions.
The v1 `suggestedVersionCode` is the highest stable version code as a string, or `"0"` if there are no stable APKs.

## Rich metadata and partial updates

Upload metadata also accepts these app fields: `license`, `author`, `web_url`, `source_url`, `issue_url`, `translation_url`, `localized`, and `added`.
The `localized` map contains locale keys and optional `name`, `summary`, and `description` values.
Use `translation_url` for the app's translation service link.

Version fields are `release_channel`, `changelog`, `whats_new`, and `released_at`.
The `whats_new` map contains localized release notes. The existing `changelog` field supplies English (`en-US`) notes.
An explicit `whats_new["en-US"]` takes precedence over `changelog`.
The v2 index preserves notes for each APK. The v1 index publishes localized notes for the suggested stable version.

Use `added` for the app's original publication date and `released_at` for the APK's original release date.
Both accept ISO 8601 dates or Unix timestamps in milliseconds. Dates without a timezone use UTC.

These JSON endpoints update existing records without an APK:

```text
PATCH /api/v1/packages/{packageName}/metadata
PATCH /api/v1/packages/{packageName}/versions/{sha256}/metadata
```

Both require a bearer key with upload permission for the package. The version endpoint selects one APK by its SHA-256 hash.
Send app fields to the first endpoint and version fields to the second.
For example, an app metadata update can contain:

```json
{
  "license": "GPL-3.0-only",
  "author": "Example Author",
  "source_url": "https://example.com/source",
  "localized": {"de": {"summary": "Eine kurze Beschreibung"}},
  "added": "2020-01-02T00:00:00Z"
}
```

Omitted fields stay unchanged. Localized updates merge by locale and field.
Use `null` to clear optional text or a localized entry. A supplied categories array replaces the previous categories.
Identical updates return `"status":"unchanged"` with an empty `indexFiles` array.
Changed updates return `"status":"updated"` after rebuilding the indexes. Neither endpoint replaces stored APK bytes.

## Architecture variants

Upload each architecture APK in a separate request to the same endpoint. The server reads the supported ABIs from each APK.
No additional multipart fields are required. APKs with different ABI sets can share a package name, version code, and signer.
An APK that contains several ABIs is one variant. An APK with no native libraries is a universal variant.

Upload responses and package version entries include a `nativecode` array, for example `["arm64-v8a"]` or `[]` for universal APKs.
Both F-Droid indexes include this array for each APK. Existing APK names and download URLs remain valid.

## GitHub Actions job

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

## Raw curl

```sh
curl --fail-with-body \
  -H "Authorization: Bearer ${FSERVER_API_KEY}" \
  -F "apk=@app-release.apk;type=application/vnd.android.package-archive" \
  -F 'metadata={"summary":"Nightly build","categories":["Tools"]}' \
  https://f-server.example.com/api/v1/upload
```

## Behavior agents must understand

- First push uses scoped trust-on-first-use. If the package is new and the API key scope allows it, f-server creates the app and pins the APK signing certificate fingerprint.
- Every APK must pass `apksigner verify` before metadata is accepted. Disabling signing-key pinning does not disable signature verification.
- Later pushes must be signed by a pinned certificate. A leaked API key cannot publish a differently signed APK.
- Re-uploading the same `(packageName, versionCode, signer, ABI set)` is idempotent and returns the stored APK. ABI order does not affect identity.
- Re-uploading an identical APK can refresh parsed metadata. A different binary for an existing variant leaves the stored APK and its metadata unchanged.
- For an identical APK, an explicit `release_channel` changes its channel and rebuilds the indexes. Omitting the field preserves its current channel.
- The Android signing key is never sent to f-server. Only the APK's public signing certificate fingerprint is extracted.

## Responses and failures

| HTTP | Result | JSON/detail shape | Remediation |
| --- | --- | --- | --- |
| 200 | Existing version | `{"status":"exists","packageName":"...","versionCode":1,"signer":"...","apkName":"...","sha256":"..."}` | Treat as success; the upload was already published. |
| 201/200 | Created version | `{"status":"created","packageName":"...","versionCode":1,"versionName":"...","signer":"...","apkName":"...","sha256":"...","size":123,"indexFiles":["repo/index-v2.json"]}` | Treat as success. |
| 401 | Bad or missing key | `{"detail":"missing bearer token"}` or `{"detail":"invalid API key"}` | Check `FSERVER_API_KEY`, secret name, and bearer header. Ask an admin to mint a new key if revoked. |
| 403 | Out of scope | `{"detail":"package is outside API key scope"}` | Ask an admin for a key scoped to the APK package name or fix the app ID being built. |
| 403 | Missing permission | `{"detail":"API key cannot upload"}` or `{"detail":"API key cannot create new packages"}` | Ask an admin to mint a key with upload/create permission. |
| 409 | Version exists with incompatible policy | Reserved for future strict duplicate handling. | Current server returns idempotent success for same signer; for other conflicts inspect package history. |
| 422 | Cert mismatch | `{"detail":"APK signing certificate does not match pinned package key"}` | Ensure CI used the same Android signing key. For legitimate rotation, ask an admin to add the new fingerprint. |
| 422 | Unparsable APK | `{"detail":"unparsable APK: ..."}` | Verify the artifact is an APK, not an AAB, ZIP, or unsigned/corrupt file. |
| 422 | Bad metadata | `{"detail":"invalid metadata JSON: ..."}` | Send valid JSON in the `metadata` form field. |
