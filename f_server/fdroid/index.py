from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from f_server.config import Settings
from f_server.models import App


def _epoch_ms(dt: datetime | None) -> int:
    if dt is None:
        return 0
    return int(dt.timestamp() * 1000)


def _file_entry(path: Path, name: str | None = None) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "name": name or f"/{path.name}",
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }


def _repo(settings: Settings, timestamp: int) -> dict[str, Any]:
    return {
        "name": {"en-US": settings.repo.name},
        "description": {"en-US": settings.repo.description},
        "address": settings.repo.url.rstrip("/"),
        "timestamp": timestamp,
    }


def build_index_payload(session: Session, settings: Settings, timestamp: int | None = None) -> dict[str, Any]:
    ts = timestamp or int(time.time() * 1000)
    repo = _repo(settings, ts)
    apps = session.scalars(
        select(App).options(selectinload(App.versions), selectinload(App.signing_keys)).order_by(App.package_name)
    ).all()
    packages: dict[str, Any] = {}
    for app in apps:
        versions = {}
        for version in sorted(app.versions, key=lambda v: v.version_code, reverse=True):
            versions[version.sha256_hash] = {
                "added": _epoch_ms(version.added_at),
                "file": {
                    "name": f"/{version.apk_name}",
                    "sha256": version.sha256_hash,
                    "size": version.size,
                },
                "manifest": {
                    "versionCode": version.version_code,
                    "versionName": version.version_name or str(version.version_code),
                    "usesSdk": {
                        "minSdkVersion": version.min_sdk or 1,
                        "targetSdkVersion": version.target_sdk or version.min_sdk or 1,
                    },
                    "signer": {"sha256": [version.signer_fingerprint]},
                    "usesPermission": [{"name": permission} for permission in version.permissions],
                    "nativecode": version.nativecode,
                },
            }
            if version.release_channel != "release":
                channel = version.release_channel
                versions[version.sha256_hash]["releaseChannels"] = [channel]
                repo.setdefault("releaseChannels", {})[channel] = {"name": {"en-US": channel}}
        metadata: dict[str, Any] = {
            "name": {"en-US": app.name or app.package_name},
            "summary": {"en-US": app.summary or ""},
            "description": {"en-US": app.description or app.summary or ""},
            "added": _epoch_ms(app.added),
            "lastUpdated": _epoch_ms(app.last_updated),
            "categories": app.categories or [],
        }
        if app.license:
            metadata["license"] = app.license
        if app.web_url:
            metadata["webSite"] = app.web_url
        if app.source_url:
            metadata["sourceCode"] = app.source_url
        if app.issue_url:
            metadata["issueTracker"] = app.issue_url
        if app.signing_keys:
            metadata["preferredSigner"] = app.signing_keys[0].sha256_fingerprint
        packages[app.package_name] = {"metadata": metadata, "versions": versions}
    return {"repo": repo, "packages": packages}


def write_unsigned_indexes(session: Session, settings: Settings, repodir: Path) -> list[Path]:
    repodir.mkdir(parents=True, exist_ok=True)
    (repodir / "diff").mkdir(exist_ok=True)
    payload = build_index_payload(session, settings)
    index_path = repodir / "index-v2.json"
    index_path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    entry = {
        "timestamp": payload["repo"]["timestamp"],
        "version": 20002,
        "index": _file_entry(index_path, "/index-v2.json") | {"numPackages": len(payload["packages"])},
        "diffs": {},
    }
    entry_path = repodir / "entry.json"
    entry_path.write_text(json.dumps(entry, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    v1_apps = []
    v1_packages: dict[str, list[dict[str, Any]]] = {}
    for package_name, item in payload["packages"].items():
        meta = item["metadata"]
        v1_apps.append(
            {
                "packageName": package_name,
                "name": meta.get("name", {}).get("en-US", package_name),
                "summary": meta.get("summary", {}).get("en-US", ""),
                "description": meta.get("description", {}).get("en-US", ""),
                "license": meta.get("license", "Unknown"),
                "added": meta["added"],
                "lastUpdated": meta["lastUpdated"],
                "suggestedVersionCode": str(
                    max(
                        (
                            v["manifest"]["versionCode"]
                            for v in item["versions"].values()
                            if not v.get("releaseChannels")
                        ),
                        default=0,
                    )
                ),
            }
        )
        if not item["versions"]:
            continue
        v1_packages[package_name] = []
        for version in item["versions"].values():
            manifest = version["manifest"]
            v1_packages[package_name].append(
                {
                    "apkName": version["file"]["name"].lstrip("/"),
                    "packageName": package_name,
                    "hash": version["file"]["sha256"],
                    "hashType": "sha256",
                    "size": version["file"]["size"],
                    "versionCode": manifest["versionCode"],
                    "versionName": manifest["versionName"],
                    "minSdkVersion": manifest["usesSdk"]["minSdkVersion"],
                    "targetSdkVersion": manifest["usesSdk"]["targetSdkVersion"],
                    "nativecode": manifest["nativecode"],
                    "signer": manifest["signer"]["sha256"][0],
                }
            )
    v1_path = repodir / "index-v1.json"
    v1_path.write_text(
        json.dumps(
            {
                "repo": {
                    "timestamp": payload["repo"]["timestamp"],
                    "version": 20002,
                    "name": settings.repo.name,
                    "description": settings.repo.description,
                    "address": settings.repo.url.rstrip("/"),
                    "icon": settings.repo.icon or "",
                },
                "requests": {"install": [], "uninstall": []},
                "apps": v1_apps,
                "packages": v1_packages,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    return [index_path, entry_path, v1_path]
