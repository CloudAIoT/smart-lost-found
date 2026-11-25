"""Keep operator resolutions; existing alarms remain unresolved until reviewed."""

from alembic import op
import sqlalchemy as sa

revision = "0007_alert_resolution"
down_revision = "0006_analysis_progress"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("device_alerts", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("device_alerts", sa.Column("resolved_by", sa.String(100), nullable=True))
    op.add_column("device_alerts", sa.Column("resolution_note", sa.String(2000), nullable=True))
    op.create_index("ix_device_alerts_unresolved", "device_alerts", ["kiosk_id", "severity", "resolved_at"])


def downgrade():
    op.drop_index("ix_device_alerts_unresolved", table_name="device_alerts")
    op.drop_column("device_alerts", "resolution_note")
    op.drop_column("device_alerts", "resolved_by")
    op.drop_column("device_alerts", "resolved_at")
