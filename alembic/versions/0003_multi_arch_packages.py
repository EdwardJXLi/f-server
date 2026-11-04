from __future__ import annotations

import hashlib
import json

from alembic import op
import sqlalchemy as sa

revision = "0003_multi_arch_packages"
down_revision = "0002_registry_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("versions", sa.Column("nativecode_key", sa.String(64), nullable=True))
    versions = sa.table(
        "versions",
        sa.column("id", sa.Integer),
        sa.column("nativecode", sa.JSON),
        sa.column("nativecode_key", sa.String(64)),
    )
    connection = op.get_bind()
    update = (
        versions.update()
        .where(versions.c.id == sa.bindparam("version_id"))
        .values(nativecode_key=sa.bindparam("abi_key"))
    )
    query = sa.select(versions.c.id, versions.c.nativecode).execution_options(yield_per=1000)
    with connection.execute(query) as rows:
        for batch_rows in rows.partitions(1000):
            updates = []
            for row in batch_rows:
                encoded = json.dumps(sorted(set(row.nativecode or [])), separators=(",", ":"))
                key = hashlib.sha256(encoded.encode()).hexdigest()
                updates.append({"version_id": row.id, "abi_key": key})
            connection.execute(update, updates)
    with op.batch_alter_table("versions") as batch:
        batch.alter_column("nativecode_key", existing_type=sa.String(64), nullable=False)
        batch.drop_constraint("uq_version_signer", type_="unique")
        batch.create_unique_constraint(
            "uq_version_signer_nativecode",
            ["package_name", "version_code", "signer_fingerprint", "nativecode_key"],
        )


def downgrade() -> None:
    versions = sa.table(
        "versions",
        sa.column("package_name"),
        sa.column("version_code"),
        sa.column("signer_fingerprint"),
    )
    duplicate = (
        op.get_bind()
        .execute(
            sa.select(versions.c.package_name)
            .group_by(
                versions.c.package_name, versions.c.version_code, versions.c.signer_fingerprint
            )
            .having(sa.func.count() > 1)
            .limit(1)
        )
        .first()
    )
    if duplicate:
        raise RuntimeError("Remove additional architecture variants before downgrading")
    with op.batch_alter_table("versions") as batch:
        batch.drop_constraint("uq_version_signer_nativecode", type_="unique")
        batch.create_unique_constraint(
            "uq_version_signer", ["package_name", "version_code", "signer_fingerprint"]
        )
        batch.drop_column("nativecode_key")
