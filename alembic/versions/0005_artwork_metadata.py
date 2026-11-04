from alembic import op
import sqlalchemy as sa

revision = "0005_artwork_metadata"
down_revision = "0004_compatibility_metadata"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assets", sa.Column("sha256_hash", sa.String(64), nullable=True))
    op.add_column("assets", sa.Column("size", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("assets") as batch:
        batch.drop_column("size")
        batch.drop_column("sha256_hash")
