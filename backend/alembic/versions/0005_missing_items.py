"""Represent a stored item lost after forced locker entry.

SQLite cannot alter CHECK constraints in place, so both tables are rebuilt through Alembic batch
mode just as in revision 0002. A downgrade maps incident rows to the nearest old terminal states
before restoring the narrower constraints.
"""

from alembic import op
import sqlalchemy as sa


revision = "0005_missing_items"
down_revision = "0004_device_telemetry"
branch_labels = None
depends_on = None

OLD_ITEM_STATUS = "status IN ('awaiting_deposit','stored','retrieved')"
NEW_ITEM_STATUS = "status IN ('awaiting_deposit','stored','retrieved','missing')"
OLD_ASSIGNMENT_STATUS = "status IN ('reserved','awaiting_deposit','stored','awaiting_pickup','retrieved','cancelled')"
NEW_ASSIGNMENT_STATUS = "status IN ('reserved','awaiting_deposit','stored','awaiting_pickup','retrieved','cancelled','missing')"
OLD_ASSIGNMENT_END = "(status IN ('cancelled','retrieved')) = (ended_at IS NOT NULL)"
NEW_ASSIGNMENT_END = "(status IN ('cancelled','retrieved','missing')) = (ended_at IS NOT NULL)"


def found_items(item_status: str) -> sa.Table:
    return sa.Table(
        "found_items", sa.MetaData(),
        sa.Column("registration_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(item_status, name="item_status"),
        sa.ForeignKeyConstraint(["registration_id"], ["registrations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("registration_id"),
    )


def storage_assignments(assignment_status: str, assignment_end: str) -> sa.Table:
    table = sa.Table(
        "storage_assignments", sa.MetaData(),
        sa.Column("found_item_id", sa.Uuid(), nullable=False),
        sa.Column("locker_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("deposited_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(assignment_end, name="assignment_end"),
        sa.CheckConstraint(assignment_status, name="assignment_status"),
        sa.ForeignKeyConstraint(["found_item_id"], ["found_items.id"]),
        sa.ForeignKeyConstraint(["locker_id"], ["lockers.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    active = sa.text("ended_at IS NULL")
    sa.Index("uq_active_item", table.c.found_item_id, unique=True,
             postgresql_where=active, sqlite_where=active)
    sa.Index("uq_active_locker", table.c.locker_id, unique=True,
             postgresql_where=active, sqlite_where=active)
    return table


def replace_item_status(old: str, new: str) -> None:
    with op.batch_alter_table("found_items", copy_from=found_items(old)) as batch:
        batch.drop_constraint("item_status", type_="check")
        batch.create_check_constraint("item_status", new)


def replace_assignment_status(old_status: str, old_end: str,
                              new_status: str, new_end: str) -> None:
    old = storage_assignments(old_status, old_end)
    with op.batch_alter_table("storage_assignments", copy_from=old) as batch:
        batch.drop_constraint("assignment_status", type_="check")
        batch.drop_constraint("assignment_end", type_="check")
        batch.create_check_constraint("assignment_status", new_status)
        batch.create_check_constraint("assignment_end", new_end)


def upgrade():
    replace_item_status(OLD_ITEM_STATUS, NEW_ITEM_STATUS)
    replace_assignment_status(OLD_ASSIGNMENT_STATUS, OLD_ASSIGNMENT_END,
                              NEW_ASSIGNMENT_STATUS, NEW_ASSIGNMENT_END)


def downgrade():
    # Preserve terminal semantics while converting values the previous revision does not know.
    op.execute(sa.text("UPDATE storage_assignments SET status = 'cancelled' WHERE status = 'missing'"))
    op.execute(sa.text("UPDATE found_items SET status = 'retrieved' WHERE status = 'missing'"))
    replace_assignment_status(NEW_ASSIGNMENT_STATUS, NEW_ASSIGNMENT_END,
                              OLD_ASSIGNMENT_STATUS, OLD_ASSIGNMENT_END)
    replace_item_status(NEW_ITEM_STATUS, OLD_ITEM_STATUS)
