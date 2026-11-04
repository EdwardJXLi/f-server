from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

from f_server.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


JsonType = JSON().with_variant(JSONB, "postgresql")


def nativecode_identity(nativecode: Iterable[str]) -> str:
    encoded = json.dumps(sorted(set(nativecode)), separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


class App(Base):
    __tablename__ = "apps"

    package_name: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(255))
    summary: Mapped[str | None] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(255))
    license: Mapped[str | None] = mapped_column(String(128))
    web_url: Mapped[str | None] = mapped_column(String(1024))
    source_url: Mapped[str | None] = mapped_column(String(1024))
    issue_url: Mapped[str | None] = mapped_column(String(1024))
    categories: Mapped[list[str]] = mapped_column(MutableList.as_mutable(JsonType), default=list)
    icon_ref: Mapped[str | None] = mapped_column(String(1024))
    added: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    suggested_version_code: Mapped[int | None] = mapped_column(Integer)

    signing_keys: Mapped[list[AllowedSigningKey]] = relationship(
        back_populates="app", cascade="all, delete-orphan"
    )
    versions: Mapped[list[Version]] = relationship(back_populates="app", cascade="all, delete-orphan")
    assets: Mapped[list[Asset]] = relationship(back_populates="app", cascade="all, delete-orphan")


class AllowedSigningKey(Base):
    __tablename__ = "allowed_signing_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_name: Mapped[str] = mapped_column(ForeignKey("apps.package_name", ondelete="CASCADE"))
    sha256_fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    added_by: Mapped[str | None] = mapped_column(String(255))
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    app: Mapped[App] = relationship(back_populates="signing_keys")

    __table_args__ = (UniqueConstraint("package_name", "sha256_fingerprint"),)


class Version(Base):
    __tablename__ = "versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_name: Mapped[str] = mapped_column(ForeignKey("apps.package_name", ondelete="CASCADE"))
    version_name: Mapped[str | None] = mapped_column(String(255))
    version_code: Mapped[int] = mapped_column(Integer)
    apk_name: Mapped[str] = mapped_column(String(512))
    storage_key: Mapped[str] = mapped_column(String(1024))
    size: Mapped[int] = mapped_column(Integer)
    sha256_hash: Mapped[str] = mapped_column(String(64))
    min_sdk: Mapped[int | None] = mapped_column(Integer)
    target_sdk: Mapped[int | None] = mapped_column(Integer)
    max_sdk: Mapped[int | None] = mapped_column(Integer)
    nativecode: Mapped[list[str]] = mapped_column(MutableList.as_mutable(JsonType), default=list)
    nativecode_key: Mapped[str] = mapped_column(
        String(64),
        default=lambda context: nativecode_identity(
            context.get_current_parameters().get("nativecode") or []
        ),
    )
    permissions: Mapped[list[str]] = mapped_column(MutableList.as_mutable(JsonType), default=list)
    permission_details: Mapped[list[dict[str, str | int]]] = mapped_column(
        MutableList.as_mutable(JsonType), default=list
    )
    permissions_sdk23: Mapped[list[dict[str, str | int]]] = mapped_column(
        MutableList.as_mutable(JsonType), default=list
    )
    features: Mapped[list[str]] = mapped_column(MutableList.as_mutable(JsonType), default=list)
    signer_fingerprint: Mapped[str] = mapped_column(String(64))
    release_channel: Mapped[str] = mapped_column(String(64), default="release")
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    app: Mapped[App] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint(
            "package_name",
            "version_code",
            "signer_fingerprint",
            "nativecode_key",
            name="uq_version_signer_nativecode",
        ),
    )


class Asset(Base):
    __tablename__ = "assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    package_name: Mapped[str] = mapped_column(ForeignKey("apps.package_name", ondelete="CASCADE"))
    locale: Mapped[str] = mapped_column(String(16), default="en-US")
    kind: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(1024))

    app: Mapped[App] = relationship(back_populates="assets")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[int] = mapped_column(primary_key=True)
    hashed_secret: Mapped[str] = mapped_column(String(512), unique=True)
    label: Mapped[str] = mapped_column(String(255))
    allowed_package_globs: Mapped[list[str]] = mapped_column(MutableList.as_mutable(JsonType), default=list)
    permissions: Mapped[list[str]] = mapped_column(MutableList.as_mutable(JsonType), default=list)
    created_by: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class AdminUser(Base):
    __tablename__ = "admin_users"

    id: Mapped[int] = mapped_column(primary_key=True)
    subject: Mapped[str] = mapped_column(String(255), unique=True)
    username: Mapped[str | None] = mapped_column(String(255))
    hashed_password: Mapped[str | None] = mapped_column(String(512))
    roles: Mapped[list[str]] = mapped_column(MutableList.as_mutable(JsonType), default=list)


class RegistrySettings(Base):
    __tablename__ = "registry_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    downloads_locked: Mapped[bool] = mapped_column(Boolean, default=False)
    username: Mapped[str | None] = mapped_column(String(255))
    hashed_password: Mapped[str | None] = mapped_column(String(512))


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str | None] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(128))
    package_name: Mapped[str | None] = mapped_column(String(255), index=True)
    version_code: Mapped[int | None] = mapped_column(Integer)
    ip: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(64))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
