from __future__ import annotations

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from f_server.auth.api_keys import audit_event, package_allowed, require_api_key
from f_server.config import get_settings
from f_server.db import get_session
from f_server.fdroid.parse import ApkInfo, ApkVerificationUnavailable, parse_apk
from f_server.models import ApiKey, App, Asset, Version, nativecode_identity, utcnow
from f_server.security.pinning import pin_first_signer, signer_allowed
from f_server.services.notifications import notify_app_published
from f_server.services.rebuild import rebuild_repo
from f_server.storage import get_storage

router = APIRouter(prefix="/api/v1", tags=["upload"])


class UploadMetadata(BaseModel):
    name: str | None = None
    summary: str | None = None
    description: str | None = None
    categories: list[str] = Field(default_factory=list)
    changelog: str | None = None
    release_channel: str = "release"


class UploadResponse(BaseModel):
    status: str
    packageName: str
    versionCode: int
    versionName: str | None
    signer: str
    apkName: str
    sha256: str
    size: int
    nativecode: list[str] = Field(default_factory=list)
    indexFiles: list[str] = Field(default_factory=list)


@router.post("/upload", response_model=UploadResponse)
def upload_apk(
    request: Request,
    apk: Annotated[UploadFile, File()],
    metadata: Annotated[str | None, Form()] = None,
    icon: Annotated[UploadFile | None, File()] = None,
    screenshots: Annotated[list[UploadFile] | None, File()] = None,
    locale: Annotated[str, Form(pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")] = "en-US",
    api_key: ApiKey = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> UploadResponse:
    parsed_metadata = _parse_metadata(metadata)
    settings = get_settings()
    verify_signing_keys = settings.uploads.verify_signing_keys
    with tempfile.TemporaryDirectory(prefix="f-server-upload-") as tmp:
        tmp_apk = Path(tmp) / "upload.apk"
        sha256 = hashlib.sha256()
        size = 0
        with tmp_apk.open("wb") as out:
            while chunk := apk.file.read(1024 * 1024):
                size += len(chunk)
                sha256.update(chunk)
                out.write(chunk)
        try:
            info = parse_apk(tmp_apk)
        except ApkVerificationUnavailable as exc:
            raise HTTPException(status_code=503, detail="APK signature verification is unavailable") from exc
        except Exception as exc:
            audit_event(session, request, api_key.label, "upload", None, None, "422-unparsable")
            raise HTTPException(status_code=422, detail=f"unparsable APK: {exc}") from exc

        _authorize_package(session, request, api_key, info.package_name, info.version_code)
        digest = sha256.hexdigest()
        nativecode_key = nativecode_identity(info.nativecode)
        existing = session.scalar(
            select(Version)
            .where(
                Version.package_name == info.package_name,
                Version.version_code == info.version_code,
                Version.signer_fingerprint == info.signer_fingerprint,
                or_(Version.nativecode_key == nativecode_key, Version.sha256_hash == digest),
            )
            .order_by((Version.sha256_hash == digest).desc())
            .limit(1)
        )
        if existing:
            metadata_changed = False
            if existing.sha256_hash == digest:
                metadata_changed = _apply_apk_info(existing, info)
                if (
                    "release_channel" in parsed_metadata.model_fields_set
                    and existing.release_channel != parsed_metadata.release_channel
                ):
                    existing.release_channel = parsed_metadata.release_channel
                    existing.app.last_updated = utcnow()
                    metadata_changed = True
            if metadata_changed:
                session.flush()
                index_files = rebuild_repo(session)
            else:
                index_files = []
            session.commit()
            audit_event(
                session,
                request,
                api_key.label,
                "upload",
                info.package_name,
                info.version_code,
                "200-idempotent",
            )
            return _response("exists", existing, index_files)

        app = session.get(App, info.package_name)
        if app is None:
            if "create" not in api_key.permissions:
                audit_event(session, request, api_key.label, "upload", info.package_name, info.version_code, "403-create")
                raise HTTPException(status_code=403, detail="API key cannot create new packages")
            app = App(
                package_name=info.package_name,
                name=parsed_metadata.name or info.label or info.package_name,
                summary=parsed_metadata.summary,
                description=parsed_metadata.description,
                categories=parsed_metadata.categories,
            )
            session.add(app)
            session.flush()
            if verify_signing_keys:
                pin_first_signer(session, app, info.signer_fingerprint, api_key.label)
        elif verify_signing_keys and not signer_allowed(session, info.package_name, info.signer_fingerprint):
            audit_event(session, request, api_key.label, "upload", info.package_name, info.version_code, "422-cert")
            raise HTTPException(status_code=422, detail="APK signing certificate does not match pinned package key")
        else:
            _update_app_metadata(app, parsed_metadata)

        apk_name = f"{info.package_name}_{info.version_code}_{digest}.apk"
        storage_key = f"repo/{apk_name}"
        with tmp_apk.open("rb") as fp:
            get_storage().put(storage_key, fp, "application/vnd.android.package-archive")

        version = Version(
            package_name=info.package_name,
            version_name=info.version_name,
            version_code=info.version_code,
            apk_name=apk_name,
            storage_key=storage_key,
            size=size,
            sha256_hash=digest,
            min_sdk=info.min_sdk,
            target_sdk=info.target_sdk,
            max_sdk=info.max_sdk,
            nativecode=list(info.nativecode),
            nativecode_key=nativecode_key,
            permissions=list(info.permissions),
            permission_details=list(info.permission_details),
            permissions_sdk23=list(info.permissions_sdk23),
            features=list(info.features),
            signer_fingerprint=info.signer_fingerprint,
            release_channel=parsed_metadata.release_channel,
        )
        session.add(version)
        app.last_updated = utcnow()
        app.suggested_version_code = max(app.suggested_version_code or 0, info.version_code)
        if icon is not None:
            _store_icon(session, icon, app.package_name, locale)
        if screenshots:
            _store_screenshots(session, screenshots, app.package_name, locale)
        session.commit()
        session.refresh(version)
        index_files = rebuild_repo(session)
        audit_event(session, request, api_key.label, "upload", info.package_name, info.version_code, "201")
        notify_app_published(app, version, settings.repo, settings.notifications)
        return _response("created", version, index_files)


@router.post("/packages/{package_name}/artwork")
def upload_artwork(
    package_name: str,
    request: Request,
    icon: Annotated[UploadFile | None, File()] = None,
    screenshots: Annotated[list[UploadFile] | None, File()] = None,
    locale: Annotated[str, Form(pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")] = "en-US",
    api_key: ApiKey = Depends(require_api_key),
    session: Session = Depends(get_session),
) -> dict:
    _authorize_package(session, request, api_key, package_name)
    app = session.get(App, package_name)
    if app is None:
        raise HTTPException(status_code=404, detail="package not found")
    if icon is None and not screenshots:
        raise HTTPException(status_code=422, detail="provide icon or screenshots")
    if icon is not None:
        _store_icon(session, icon, package_name, locale)
    if screenshots:
        _store_screenshots(session, screenshots, package_name, locale)
    app.last_updated = utcnow()
    session.commit()
    index_files = rebuild_repo(session)
    audit_event(session, request, api_key.label, "artwork", package_name, None, "200")
    return {"status": "updated", "packageName": package_name, "indexFiles": index_files}


@router.get("/packages/{package_name}")
def get_package(package_name: str, session: Session = Depends(get_session)) -> dict:
    app = session.get(App, package_name)
    if app is None:
        raise HTTPException(status_code=404, detail="package not found")
    versions = session.scalars(
        select(Version).where(Version.package_name == package_name).order_by(Version.version_code.desc())
    ).all()
    return {
        "packageName": app.package_name,
        "name": app.name,
        "summary": app.summary,
        "description": app.description,
        "categories": app.categories,
        "versions": [
            {
                "versionCode": v.version_code,
                "versionName": v.version_name,
                "apkName": v.apk_name,
                "sha256": v.sha256_hash,
                "size": v.size,
                "signer": v.signer_fingerprint,
                "releaseChannel": v.release_channel,
                "nativecode": v.nativecode,
            }
            for v in versions
        ],
    }


def _parse_metadata(raw: str | None) -> UploadMetadata:
    if not raw:
        return UploadMetadata()
    try:
        return UploadMetadata.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid metadata JSON: {exc}") from exc


def _authorize_package(
    session: Session, request: Request, api_key: ApiKey,
    package_name: str, version_code: int | None = None,
) -> None:
    if "upload" not in api_key.permissions:
        audit_event(session, request, api_key.label, "upload", package_name, version_code, "403-permission")
        raise HTTPException(status_code=403, detail="API key cannot upload")
    if not package_allowed(api_key, package_name):
        audit_event(session, request, api_key.label, "upload", package_name, version_code, "403-scope")
        raise HTTPException(status_code=403, detail="package is outside API key scope")


def _update_app_metadata(app: App, metadata: UploadMetadata) -> None:
    if metadata.name:
        app.name = metadata.name
    if metadata.summary:
        app.summary = metadata.summary
    if metadata.description:
        app.description = metadata.description
    if metadata.categories:
        app.categories = metadata.categories


def _apply_apk_info(version: Version, info: ApkInfo) -> bool:
    changed = False
    fields = {
        "version_name": info.version_name,
        "min_sdk": info.min_sdk,
        "target_sdk": info.target_sdk,
        "max_sdk": info.max_sdk,
        "nativecode": list(info.nativecode),
        "permissions": list(info.permissions),
        "permission_details": list(info.permission_details)
        or [{"name": name} for name in info.permissions],
        "permissions_sdk23": list(info.permissions_sdk23),
        "features": list(info.features),
    }
    for field, value in fields.items():
        previous = getattr(version, field)
        if previous is None and field == "permission_details":
            previous = [{"name": name} for name in version.permissions]
        elif previous is None and field in {"permissions_sdk23", "features"}:
            previous = []
        if previous != value:
            setattr(version, field, value)
            if field == "nativecode":
                version.nativecode_key = nativecode_identity(value)
            changed = True
    return changed


def _store_icon(session: Session, icon: UploadFile, package_name: str, locale: str = "en-US") -> None:
    suffix = Path(icon.filename or "icon.png").suffix or ".png"
    key = f"repo/{package_name}/{locale}/icon{suffix}"
    _store_asset(session, icon, package_name, locale, "icon", key)


def _store_screenshots(
    session: Session, screenshots: list[UploadFile], package_name: str, locale: str = "en-US"
) -> None:
    for index, screenshot in enumerate(screenshots, start=1):
        suffix = Path(screenshot.filename or "screenshot.png").suffix or ".png"
        key = f"repo/{package_name}/{locale}/phoneScreenshots/{index:02d}{suffix}"
        _store_asset(session, screenshot, package_name, locale, "screenshot", key)


def _store_asset(
    session: Session, upload: UploadFile, package_name: str, locale: str, kind: str, key: str
) -> None:
    digest = hashlib.sha256()
    size = 0
    with tempfile.SpooledTemporaryFile(max_size=1024 * 1024 * 8) as tmp:
        while chunk := upload.file.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
            tmp.write(chunk)
        tmp.seek(0)
        get_storage().put(key, tmp, upload.content_type or "application/octet-stream")
    criteria = [Asset.package_name == package_name, Asset.locale == locale, Asset.kind == kind]
    if kind != "icon":
        criteria.append(Asset.storage_key == key)
    matches = session.scalars(
        select(Asset).where(*criteria).order_by(Asset.id.desc())
    ).all()
    asset = matches[0] if matches else Asset(
        package_name=package_name, locale=locale, kind=kind, storage_key=key
    )
    for duplicate in matches[1:]:
        session.delete(duplicate)
    asset.storage_key = key
    asset.sha256_hash = digest.hexdigest()
    asset.size = size
    session.add(asset)


def _response(status_text: str, version: Version, index_files: list[str]) -> UploadResponse:
    return UploadResponse(
        status=status_text,
        packageName=version.package_name,
        versionCode=version.version_code,
        versionName=version.version_name,
        signer=version.signer_fingerprint,
        apkName=version.apk_name,
        sha256=version.sha256_hash,
        size=version.size,
        nativecode=version.nativecode,
        indexFiles=index_files,
    )
