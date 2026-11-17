import importlib.util
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect
from sqlalchemy import insert, select, table, column, Uuid, JSON
from uuid import uuid4

from app.db import Base
from app import models


def test_migration_upgrade_matches_models_and_downgrades(database_engine):
    directory = Path(__file__).parents[1] / "alembic/versions"
    migrations = []
    for path in sorted(directory.glob("0*.py")):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        assert migration.down_revision == (migrations[-1].revision if migrations else None)
        migrations.append(migration)
    engine = database_engine
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            for migration in migrations:
                migration.upgrade()
            assert compare_metadata(context, Base.metadata) == []
            for migration in reversed(migrations):
                migration.downgrade()
            assert inspect(connection).get_table_names() == []


def test_legacy_json_is_preserved_and_backfilled_without_promoting_evidence(database_engine):
    directory = Path(__file__).parents[1] / 'alembic/versions'
    migrations = []
    for path in sorted(directory.glob('0*.py')):
        spec = importlib.util.spec_from_file_location(path.stem, path)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        migrations.append(migration)
    from app.models import Kiosk, Registration
    old = {'data': {'category': 'pencil_case', 'color': ['black'], 'material': 'fabric'},
           'ownership_evidence': [{'verified': True, 'value': 'old unchecked data'}]}
    with database_engine.begin() as connection:
        with Operations.context(MigrationContext.configure(connection)):
            for migration in migrations[:-1]:
                migration.upgrade()
            kiosk, reg = uuid4(), uuid4()
            connection.execute(insert(Kiosk), [{'id': kiosk, 'name': 'legacy'}])
            registrations = table('registrations', column('id', Uuid()), column('kiosk_id', Uuid()),
                                  column('created_at'), column('status'), column('analysis_status'),
                                  column('analysis_features', JSON()))
            connection.execute(registrations.insert(), {'id': reg, 'kiosk_id': kiosk, 'created_at': models.now(),
                'status': 'stored', 'analysis_status': 'succeeded', 'analysis_features': old})
            migrations[-1].upgrade()
            saved = connection.execute(select(Registration.__table__).where(Registration.id == reg)).mappings().one()
            assert saved['analysis_features'] == old
            assert saved['analysis_metadata'] is None and saved['ownership_evidence'] == []
            assert saved['search_features'] == {'category': ['pencilcase'], 'color': ['black']}
