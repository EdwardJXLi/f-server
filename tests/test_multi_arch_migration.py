from datetime import datetime, timezone

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import Session

from f_server.config import Settings
from f_server.models import Version, nativecode_identity


def test_multi_arch_migration_preserves_existing_versions(tmp_path, monkeypatch) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'migration.db'}", _env_file=None)
    monkeypatch.setattr("f_server.config.get_settings", lambda: settings)
    config = Config()
    config.set_main_option("script_location", "alembic")
    command.upgrade(config, "0002_registry_settings")
    engine = sa.create_engine(settings.database_url)
    old_tables = sa.MetaData()
    old_tables.reflect(bind=engine)
    now = datetime.now(timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            old_tables.tables["apps"]
            .insert()
            .values(
                package_name="com.example.migration", categories=[], added=now, last_updated=now
            )
        )
        for version_code, nativecode in [(1, []), (2, ["x86_64", "arm64-v8a", "x86_64"])]:
            name = f"legacy_{version_code}.apk"
            connection.execute(
                old_tables.tables["versions"]
                .insert()
                .values(
                    id=version_code,
                    package_name="com.example.migration",
                    version_code=version_code,
                    version_name="1.0",
                    apk_name=name,
                    storage_key=f"repo/{name}",
                    size=3,
                    sha256_hash=str(version_code) * 64,
                    nativecode=nativecode,
                    permissions=[],
                    signer_fingerprint="a" * 64,
                    release_channel="release",
                    added_at=now,
                )
            )

    command.upgrade(config, "head")
    with Session(engine) as session:
        original = session.get(Version, 1)
        assert original.apk_name == "legacy_1.apk"
        assert original.storage_key == "repo/legacy_1.apk"
        assert original.sha256_hash == "1" * 64
        assert original.nativecode_key == nativecode_identity([])
        multi = session.get(Version, 2)
        assert multi.apk_name == "legacy_2.apk"
        assert multi.nativecode == ["x86_64", "arm64-v8a", "x86_64"]
        assert multi.nativecode_key == nativecode_identity(["arm64-v8a", "x86_64"])
        variant = Version(
            package_name=original.package_name,
            version_code=1,
            apk_name="arm64.apk",
            storage_key="repo/arm64.apk",
            size=4,
            sha256_hash="c" * 64,
            nativecode=["arm64-v8a"],
            signer_fingerprint=original.signer_fingerprint,
        )
        session.add(variant)
        session.commit()
        variant_id = variant.id
        assert variant.nativecode_key == nativecode_identity(["arm64-v8a"])

        duplicate = Version(
            package_name=original.package_name,
            version_code=2,
            apk_name="duplicate.apk",
            storage_key="repo/duplicate.apk",
            size=4,
            sha256_hash="d" * 64,
            nativecode=["arm64-v8a", "x86_64"],
            signer_fingerprint=original.signer_fingerprint,
        )
        session.add(duplicate)
        with pytest.raises(sa.exc.IntegrityError):
            session.commit()
        session.rollback()

    with pytest.raises(RuntimeError, match="additional architecture variants"):
        command.downgrade(config, "0002_registry_settings")
    with Session(engine) as session:
        variant = session.get(Version, variant_id)
        assert variant.nativecode == ["arm64-v8a"]
        session.delete(variant)
        session.commit()
    command.downgrade(config, "0002_registry_settings")
    command.upgrade(config, "head")
    with Session(engine) as session:
        assert session.get(Version, 1).apk_name == "legacy_1.apk"
        assert session.get(Version, 2).nativecode_key == nativecode_identity(
            ["arm64-v8a", "x86_64"]
        )
    engine.dispose()
