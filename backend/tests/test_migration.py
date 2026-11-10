import importlib.util
from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect

from app.db import Base
from app import models


def load(name):
    path = Path(__file__).parents[1] / "alembic/versions" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_upgrade_matches_models_and_downgrades(database_engine):
    revisions = [load("0001_registration_storage.py"), load("0002_pickup_return_code.py")]
    engine = database_engine
    with engine.begin() as connection:
        context = MigrationContext.configure(connection)
        with Operations.context(context):
            for revision in revisions:
                revision.upgrade()
            assert compare_metadata(context, Base.metadata) == []
            for revision in reversed(revisions):
                revision.downgrade()
            assert inspect(connection).get_table_names() == []
