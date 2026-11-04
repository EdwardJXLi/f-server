from alembic import op
import sqlalchemy as sa

revision = "0006_rich_metadata"
down_revision = "0005_artwork_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("apps", sa.Column("translation_url", sa.String(1024), nullable=True))
    for table, name in (("apps", "localized"), ("versions", "whats_new")):
        op.add_column(table, sa.Column(name, sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        with op.batch_alter_table(table) as batch:
            batch.alter_column(name, existing_type=sa.JSON(), server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("versions") as batch:
        batch.drop_column("whats_new")
    with op.batch_alter_table("apps") as batch:
        batch.drop_column("localized")
        batch.drop_column("translation_url")
