"""Return code issuance and kiosk pickup.

SQLite cannot alter a CHECK constraint in place, so the three affected tables are
rebuilt through batch mode with an explicit definition of their current shape.
Downgrade assumes no pickup has completed yet: rows left in the statuses this
revision adds would violate the narrower constraints it restores.
"""
from alembic import op
import sqlalchemy as sa

revision = "0002_pickup_return_code"
down_revision = "0001_registration_storage"
branch_labels = None
depends_on = None

OLD_ITEM_STATUS = "status IN ('awaiting_deposit','stored')"
NEW_ITEM_STATUS = "status IN ('awaiting_deposit','stored','retrieved')"
OLD_ASSIGNMENT_STATUS = "status IN ('reserved','awaiting_deposit','stored','cancelled')"
NEW_ASSIGNMENT_STATUS = "status IN ('reserved','awaiting_deposit','stored','awaiting_pickup','retrieved','cancelled')"
OLD_ASSIGNMENT_END = "(status = 'cancelled') = (ended_at IS NOT NULL)"
NEW_ASSIGNMENT_END = "(status IN ('cancelled','retrieved')) = (ended_at IS NOT NULL)"
OLD_COMMAND_TARGET = ("(action = 'capture' AND shot_order IN (1,2) AND shot_order IS NOT NULL AND storage_assignment_id IS NULL)"
                      " OR (action = 'open' AND shot_order IS NULL AND storage_assignment_id IS NOT NULL)")
NEW_COMMAND_TARGET = ("(action = 'capture' AND shot_order IN (1,2) AND shot_order IS NOT NULL AND storage_assignment_id IS NULL)"
                      " OR (action IN ('open','pickup_open') AND shot_order IS NULL AND storage_assignment_id IS NOT NULL)")


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
    # Carried on the definition so a table rebuild restores them.
    where = sa.text("ended_at IS NULL")
    sa.Index("uq_active_item", table.c.found_item_id, unique=True, postgresql_where=where, sqlite_where=where)
    sa.Index("uq_active_locker", table.c.locker_id, unique=True, postgresql_where=where, sqlite_where=where)
    return table


def device_commands(command_target: str) -> sa.Table:
    return sa.Table(
        "device_commands", sa.MetaData(),
        sa.Column("command_id", sa.String(), nullable=False),
        sa.Column("registration_id", sa.Uuid(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("shot_order", sa.Integer(), nullable=True),
        sa.Column("storage_assignment_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_code", sa.String(), nullable=True),
        sa.CheckConstraint(command_target, name="command_target"),
        sa.CheckConstraint("status IN ('sent','succeeded','failed','unknown')", name="command_status"),
        sa.ForeignKeyConstraint(["registration_id"], ["registrations.id"]),
        sa.ForeignKeyConstraint(["storage_assignment_id"], ["storage_assignments.id"]),
        sa.PrimaryKeyConstraint("command_id"),
        sa.UniqueConstraint("registration_id", "shot_order"),
    )


def replace_check(table: sa.Table, name: str, expression: str) -> None:
    with op.batch_alter_table(table.name, copy_from=table) as batch:
        batch.drop_constraint(name, type_="check")
        batch.create_check_constraint(name, expression)


def upgrade():
    replace_check(found_items(OLD_ITEM_STATUS), "item_status", NEW_ITEM_STATUS)
    old = storage_assignments(OLD_ASSIGNMENT_STATUS, OLD_ASSIGNMENT_END)
    with op.batch_alter_table("storage_assignments", copy_from=old) as batch:
        batch.drop_constraint("assignment_status", type_="check")
        batch.drop_constraint("assignment_end", type_="check")
        batch.create_check_constraint("assignment_status", NEW_ASSIGNMENT_STATUS)
        batch.create_check_constraint("assignment_end", NEW_ASSIGNMENT_END)
    replace_check(device_commands(OLD_COMMAND_TARGET), "command_target", NEW_COMMAND_TARGET)
    op.create_table(
        "return_codes",
        sa.Column("found_item_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("(status = 'used') = (used_at IS NOT NULL)", name="return_code_use"),
        sa.CheckConstraint("failed_attempts >= 0", name="return_code_attempts"),
        sa.CheckConstraint("status IN ('active','opening','used','locked','revoked')", name="return_code_status"),
        sa.ForeignKeyConstraint(["found_item_id"], ["found_items.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
    usable = sa.text("status IN ('active','opening')")
    op.create_index("uq_open_return_code", "return_codes", ["found_item_id"], unique=True,
                    postgresql_where=usable, sqlite_where=usable)


def downgrade():
    op.drop_index("uq_open_return_code", table_name="return_codes")
    op.drop_table("return_codes")
    replace_check(device_commands(NEW_COMMAND_TARGET), "command_target", OLD_COMMAND_TARGET)
    new = storage_assignments(NEW_ASSIGNMENT_STATUS, NEW_ASSIGNMENT_END)
    with op.batch_alter_table("storage_assignments", copy_from=new) as batch:
        batch.drop_constraint("assignment_status", type_="check")
        batch.drop_constraint("assignment_end", type_="check")
        batch.create_check_constraint("assignment_status", OLD_ASSIGNMENT_STATUS)
        batch.create_check_constraint("assignment_end", OLD_ASSIGNMENT_END)
    replace_check(found_items(NEW_ITEM_STATUS), "item_status", OLD_ITEM_STATUS)
