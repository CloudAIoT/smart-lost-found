"""Record photo analysis progress so a slow or abandoned run is visible instead of silent.

The column keeps the stage, the model attempt, the start and deadline, and the failure code of the
most recent analysis. Before it, a worker that died left `analysis_status='running'` with no way to
tell a running analysis from a lost one, and no way to report why one failed after the response.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0006_analysis_progress"
down_revision = "0005_missing_items"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("registrations", sa.Column(
        "analysis_progress", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True))


def downgrade():
    op.drop_column("registrations", "analysis_progress")
