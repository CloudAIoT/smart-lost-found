"""Separate image observations, review evidence and indexed search features."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from app.search_features import project

revision = '0004_image_analysis_contract'
down_revision = '0003_ownership_attempts'
branch_labels = depends_on = None


def upgrade():
    kind = sa.JSON().with_variant(JSONB(), 'postgresql')
    op.add_column('registrations', sa.Column('analysis_metadata', kind, nullable=True))
    op.add_column('registrations', sa.Column('ownership_evidence', kind, nullable=False, server_default=sa.text("'[]'")))
    op.add_column('registrations', sa.Column('search_features', kind, nullable=False, server_default=sa.text("'{}'")))
    registrations = sa.table('registrations', sa.column('id', sa.Uuid()), sa.column('analysis_features', kind),
        sa.column('analysis_status'), sa.column('primary_category_id', sa.Uuid()), sa.column('found_location'),
        sa.column('search_features', kind))
    categories = sa.table('categories', sa.column('id', sa.Uuid()), sa.column('name'))
    connection = op.get_bind()
    # One-time, paged backfill. Legacy raw JSON and unreviewed evidence are preserved.
    last = None
    while True:
        query = sa.select(registrations.c.id, registrations.c.analysis_features, registrations.c.found_location,
                          categories.c.name).select_from(registrations.outerjoin(categories,
                          registrations.c.primary_category_id == categories.c.id)).where(
                          registrations.c.analysis_status == 'succeeded').order_by(registrations.c.id).limit(200)
        if last is not None:
            query = query.where(registrations.c.id > last)
        rows = connection.execute(query).all()
        if not rows:
            break
        for row in rows:
            connection.execute(registrations.update().where(registrations.c.id == row.id).values(
                search_features=project(row.analysis_features, category=row.name, location=row.found_location, legacy=True)))
        last = rows[-1].id
    op.create_index('ix_registrations_search_features', 'registrations', ['search_features'],
                    postgresql_using='gin', postgresql_ops={'search_features': 'jsonb_path_ops'})


def downgrade():
    op.drop_index('ix_registrations_search_features', table_name='registrations')
    for name in ('search_features', 'ownership_evidence', 'analysis_metadata'):
        op.drop_column('registrations', name)
