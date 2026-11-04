from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0004_compatibility_metadata"
down_revision = "0003_multi_arch_packages"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("permission_details", "permissions_sdk23", "features"):
        op.add_column(
            "versions",
            sa.Column(name, sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
        )
    versions = sa.table(
        "versions",
        sa.column("id", sa.Integer),
        sa.column("permissions", sa.JSON),
        sa.column("permission_details", sa.JSON),
    )
    update = (
        versions.update()
        .where(versions.c.id == sa.bindparam("version_id"))
        .values(permission_details=sa.bindparam("details"))
    )
    connection = op.get_bind()
    query = sa.select(versions.c.id, versions.c.permissions).execution_options(yield_per=1000)
    with connection.execute(query) as rows:
        for batch_rows in rows.partitions(1000):
            connection.execute(update, [
                {"version_id": row.id, "details": [{"name": name} for name in row.permissions]}
                for row in batch_rows
            ])
    with op.batch_alter_table("versions") as batch:
        for name in ("permission_details", "permissions_sdk23", "features"):
            batch.alter_column(name, existing_type=sa.JSON(), server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("versions") as batch:
        for name in ("features", "permissions_sdk23", "permission_details"):
            batch.drop_column(name)
