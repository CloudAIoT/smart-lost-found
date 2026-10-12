"""Registration and storage foundation."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_registration_storage"
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('categories',
    sa.Column('code', sa.String(length=100), nullable=False),
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('kiosks',
    sa.Column('name', sa.String(length=200), nullable=False),
    sa.Column('location_description', sa.String(), nullable=True),
    sa.Column('latitude', sa.Float(), nullable=True),
    sa.Column('longitude', sa.Float(), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('(latitude IS NULL) = (longitude IS NULL)', name='coordinates_pair'),
    sa.CheckConstraint('latitude BETWEEN -90 AND 90', name='latitude_range'),
    sa.CheckConstraint('longitude BETWEEN -180 AND 180', name='longitude_range'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('lockers',
    sa.Column('kiosk_id', sa.Uuid(), nullable=False),
    sa.Column('locker_number', sa.Integer(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('locker_number > 0', name='positive_locker_number'),
    sa.ForeignKeyConstraint(['kiosk_id'], ['kiosks.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('kiosk_id', 'locker_number')
    )
    op.create_table('registrations',
    sa.Column('kiosk_id', sa.Uuid(), nullable=False),
    sa.Column('primary_category_id', sa.Uuid(), nullable=True),
    sa.Column('secondary_description', sa.String(), nullable=True),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('analysis_status', sa.String(), nullable=False),
    sa.Column('analysis_input_image_ids', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True),
    sa.Column('analysis_features', sa.JSON().with_variant(postgresql.JSONB(), 'postgresql'), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("analysis_status IN ('pending','running','succeeded','failed')", name='analysis_status'),
    sa.CheckConstraint("status IN ('draft','capturing','analyzing','ready','opening','awaiting_deposit','stored','blocked')", name='registration_status'),
    sa.ForeignKeyConstraint(['kiosk_id'], ['kiosks.id'], ),
    sa.ForeignKeyConstraint(['primary_category_id'], ['categories.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('found_items',
    sa.Column('registration_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('awaiting_deposit','stored')", name='item_status'),
    sa.ForeignKeyConstraint(['registration_id'], ['registrations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('registration_id')
    )
    op.create_table('storage_assignments',
    sa.Column('found_item_id', sa.Uuid(), nullable=False),
    sa.Column('locker_id', sa.Uuid(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('deposited_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("(status = 'cancelled') = (ended_at IS NOT NULL)", name='assignment_end'),
    sa.CheckConstraint("status IN ('reserved','awaiting_deposit','stored','cancelled')", name='assignment_status'),
    sa.ForeignKeyConstraint(['found_item_id'], ['found_items.id'], ),
    sa.ForeignKeyConstraint(['locker_id'], ['lockers.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('uq_active_item', 'storage_assignments', ['found_item_id'], unique=True, postgresql_where=sa.text('ended_at IS NULL'), sqlite_where=sa.text('ended_at IS NULL'))
    op.create_index('uq_active_locker', 'storage_assignments', ['locker_id'], unique=True, postgresql_where=sa.text('ended_at IS NULL'), sqlite_where=sa.text('ended_at IS NULL'))
    op.create_table('device_commands',
    sa.Column('command_id', sa.String(), nullable=False),
    sa.Column('registration_id', sa.Uuid(), nullable=False),
    sa.Column('action', sa.String(), nullable=False),
    sa.Column('shot_order', sa.Integer(), nullable=True),
    sa.Column('storage_assignment_id', sa.Uuid(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('error_code', sa.String(), nullable=True),
    sa.CheckConstraint("(action = 'capture' AND shot_order IN (1,2) AND shot_order IS NOT NULL AND storage_assignment_id IS NULL) OR (action = 'open' AND shot_order IS NULL AND storage_assignment_id IS NOT NULL)", name='command_target'),
    sa.CheckConstraint("status IN ('sent','succeeded','failed','unknown')", name='command_status'),
    sa.ForeignKeyConstraint(['registration_id'], ['registrations.id'], ),
    sa.ForeignKeyConstraint(['storage_assignment_id'], ['storage_assignments.id'], ),
    sa.PrimaryKeyConstraint('command_id'),
    sa.UniqueConstraint('registration_id', 'shot_order')
    )
    op.create_table('item_images',
    sa.Column('registration_id', sa.Uuid(), nullable=False),
    sa.Column('capture_command_id', sa.String(), nullable=False),
    sa.Column('shot_order', sa.Integer(), nullable=False),
    sa.Column('object_key', sa.String(), nullable=False),
    sa.Column('content_type', sa.String(), nullable=False),
    sa.Column('byte_size', sa.Integer(), nullable=False),
    sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("content_type = 'image/jpeg'", name='image_type'),
    sa.CheckConstraint('byte_size > 0', name='image_size'),
    sa.CheckConstraint('shot_order IN (1,2)', name='image_order'),
    sa.ForeignKeyConstraint(['capture_command_id'], ['device_commands.command_id'], ),
    sa.ForeignKeyConstraint(['registration_id'], ['registrations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('capture_command_id'),
    sa.UniqueConstraint('object_key'),
    sa.UniqueConstraint('registration_id', 'shot_order')
    )
    # ### end Alembic commands ###

def downgrade():
    op.drop_table('item_images')
    op.drop_table('device_commands')
    op.drop_index('uq_active_locker', table_name='storage_assignments', postgresql_where=sa.text('ended_at IS NULL'), sqlite_where=sa.text('ended_at IS NULL'))
    op.drop_index('uq_active_item', table_name='storage_assignments', postgresql_where=sa.text('ended_at IS NULL'), sqlite_where=sa.text('ended_at IS NULL'))
    op.drop_table('storage_assignments')
    op.drop_table('found_items')
    op.drop_table('registrations')
    op.drop_table('lockers')
    op.drop_table('kiosks')
    op.drop_table('categories')
    # ### end Alembic commands ###
