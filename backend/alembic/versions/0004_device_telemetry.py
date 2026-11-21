"""Store device and locker state and the alerts received over MQTT."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_device_telemetry"
down_revision = "0003_ownership_attempts"
branch_labels = None
depends_on = None


def upgrade():
    # No foreign key to kiosks: telemetry comes from outside this service, and an unknown kiosk id
    # must be recorded or dropped rather than raise inside the subscriber thread.
    op.create_table(
        "device_states",
        sa.Column("kiosk_id", sa.Uuid(), primary_key=True),
        sa.Column("device", sa.String(20), primary_key=True),
        sa.Column("locker_number", sa.Integer(), primary_key=True),
        sa.Column("connection", sa.String(20), nullable=True),
        sa.Column("door", sa.String(10), nullable=True),
        sa.Column("weight_g", sa.Float(), nullable=True),
        sa.Column("item", sa.String(10), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("device IN ('capture','locker')", name="device_state_device"),
        sa.CheckConstraint("locker_number >= 0", name="device_state_target"),
        sa.CheckConstraint("connection IS NULL OR connection IN ('online','offline')", name="device_state_connection"),
        sa.CheckConstraint("door IS NULL OR door IN ('open','closed')", name="device_state_door"),
        sa.CheckConstraint("item IS NULL OR item IN ('present','empty')", name="device_state_item"),
    )
    op.create_table(
        "device_alerts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kiosk_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("device", sa.String(20), nullable=False),
        sa.Column("locker_number", sa.Integer(), nullable=True),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("detail", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("severity IN ('alarm','warning')", name="device_alert_severity"),
        sa.CheckConstraint("device IN ('capture','locker')", name="device_alert_device"),
        sa.CheckConstraint("locker_number IS NULL OR locker_number > 0", name="device_alert_locker"),
        # A persistent subscriber session can redeliver a queued alert; the replay is dropped here.
        sa.UniqueConstraint("kiosk_id", "code", "locker_number", "reported_at", name="uq_device_alert_report"),
    )
    op.create_index("ix_device_alerts_recent", "device_alerts", ["kiosk_id", "reported_at"])


def downgrade():
    op.drop_index("ix_device_alerts_recent", table_name="device_alerts")
    op.drop_table("device_alerts")
    op.drop_table("device_states")
