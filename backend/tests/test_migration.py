import importlib.util
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

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
