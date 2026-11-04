from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints, field_validator

from f_server.models import App, Version, utcnow

Locale = Annotated[str, StringConstraints(pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")]


def _utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


class LocalizedMetadata(BaseModel):
    name: str | None = None
    summary: str | None = None
    description: str | None = None


class AppMetadata(BaseModel):
    name: str | None = None
    summary: str | None = None
    description: str | None = None
    categories: list[str] = Field(default_factory=list)
    license: str | None = None
    author: str | None = None
    web_url: str | None = None
    source_url: str | None = None
    issue_url: str | None = None
    translation_url: str | None = None
    localized: dict[Locale, LocalizedMetadata | None] = Field(default_factory=dict)
    added: datetime | None = None

    @field_validator("added")
    @classmethod
    def added_date_utc(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class VersionMetadata(BaseModel):
    release_channel: str = "release"
    changelog: str | None = None
    whats_new: dict[Locale, str | None] = Field(default_factory=dict)
    released_at: datetime | None = None

    @field_validator("released_at")
    @classmethod
    def release_date_utc(cls, value: datetime | None) -> datetime | None:
        return _utc(value)


class UploadMetadata(AppMetadata, VersionMetadata):
    pass


def apply_app_metadata(app: App, metadata: AppMetadata) -> bool:
    changed = False
    for field in (
        "name", "summary", "description", "categories", "license", "author",
        "web_url", "source_url", "issue_url", "translation_url", "added",
    ):
        if field not in metadata.model_fields_set:
            continue
        value = getattr(metadata, field)
        if field == "added" and value is None:
            continue
        previous = getattr(app, field)
        if field == "added":
            previous = _utc(previous)
        if previous != value:
            setattr(app, field, value)
            changed = True
    localized = dict(app.localized or {})
    for locale, update in metadata.localized.items():
        if update is None:
            localized.pop(locale, None)
            continue
        text = dict(localized.get(locale, {}))
        for field in update.model_fields_set:
            value = getattr(update, field)
            if value is None:
                text.pop(field, None)
            else:
                text[field] = value
        if text:
            localized[locale] = text
        else:
            localized.pop(locale, None)
    if localized != (app.localized or {}):
        app.localized = localized
        changed = True
    if changed:
        app.last_updated = utcnow()
    return changed


def apply_version_metadata(version: Version, metadata: VersionMetadata) -> bool:
    changed = False
    if "release_channel" in metadata.model_fields_set and version.release_channel != metadata.release_channel:
        version.release_channel = metadata.release_channel
        changed = True
    if metadata.released_at is not None and _utc(version.added_at) != metadata.released_at:
        version.added_at = metadata.released_at
        changed = True
    notes = dict(version.whats_new or {})
    if "changelog" in metadata.model_fields_set:
        if metadata.changelog is None:
            notes.pop("en-US", None)
        else:
            notes["en-US"] = metadata.changelog
    for locale, text in metadata.whats_new.items():
        if text is None:
            notes.pop(locale, None)
        else:
            notes[locale] = text
    if notes != (version.whats_new or {}):
        version.whats_new = notes
        changed = True
    return changed
