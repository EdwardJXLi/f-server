from __future__ import annotations

import hashlib

from f_server.models import App, Asset
from f_server.storage.base import StorageBackend


SCREENSHOT_TYPES = {
    "screenshot": "phone",
    "phoneScreenshots": "phone",
    "sevenInchScreenshots": "sevenInch",
    "tenInchScreenshots": "tenInch",
    "tvScreenshots": "tv",
    "wearScreenshots": "wear",
}


def _file_entry(asset: Asset, storage: StorageBackend) -> dict:
    if asset.sha256_hash is None or asset.size is None:
        digest = hashlib.sha256()
        size = 0
        with storage.open_stream(asset.storage_key) as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
        asset.sha256_hash = digest.hexdigest()
        asset.size = size
    return {
        "name": "/" + asset.storage_key.removeprefix("repo/"),
        "sha256": asset.sha256_hash,
        "size": asset.size,
    }


def localized_artwork(app: App, storage: StorageBackend) -> dict:
    artwork = {}
    seen = set()
    for asset in sorted(app.assets, key=lambda item: item.id, reverse=True):
        identity = (asset.locale, asset.kind, asset.storage_key)
        if identity in seen:
            continue
        seen.add(identity)
        if asset.kind in {"icon", "featureGraphic", "promoGraphic", "tvBanner"}:
            localized = artwork.setdefault(asset.kind, {})
            if asset.locale not in localized:
                localized[asset.locale] = _file_entry(asset, storage)
        elif asset.kind in SCREENSHOT_TYPES:
            screenshots = artwork.setdefault("screenshots", {})
            images = screenshots.setdefault(SCREENSHOT_TYPES[asset.kind], {}).setdefault(asset.locale, [])
            images.append(_file_entry(asset, storage))
    for localized in artwork.get("screenshots", {}).values():
        for images in localized.values():
            images.sort(key=lambda entry: entry["name"])
    return artwork
