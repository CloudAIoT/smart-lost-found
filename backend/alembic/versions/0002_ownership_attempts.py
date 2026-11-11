"""Store the reported found location and the ownership workflow state."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0002_ownership_attempts"
down_revision = "0001_registration_storage"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("registrations", sa.Column("found_location", sa.String(200), nullable=True))
    op.create_table(
        "ownership_attempts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False),
    )
    op.create_index("ix_ownership_attempts_expires_at", "ownership_attempts", ["expires_at"])


def downgrade():
    op.drop_table("ownership_attempts")
    op.drop_column("registrations", "found_location")
