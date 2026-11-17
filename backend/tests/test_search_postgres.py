"""Real planner evidence; SQLite cannot verify a PostgreSQL GIN index."""
import json
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb
from sqlalchemy import insert

from app.db import Base
from app.models import Kiosk, Registration, FoundItem, Locker, StorageAssignment
from app.ai_ownership_verification.search import candidate_query


def test_selective_search_uses_gin_without_loading_inventory(database_engine):
    if database_engine.dialect.name != 'postgresql':
        pytest.skip('PostgreSQL planner required')
    Base.metadata.create_all(database_engine)
    kiosk = uuid4()
    reg_ids, item_ids, locker_ids = ([uuid4() for _ in range(5000)] for _ in range(3))
    with database_engine.begin() as connection:
        connection.execute(insert(Kiosk), [{'id': kiosk, 'name': 'index benchmark'}])
        connection.execute(insert(Registration), [
            {'id': reg_id, 'kiosk_id': kiosk, 'status': 'stored', 'analysis_status': 'succeeded',
             'search_features': {'visible_text': ['needle' if n < 3 else f'label{n}']}}
            for n, reg_id in enumerate(reg_ids)])
        connection.execute(insert(FoundItem), [
            {'id': item, 'registration_id': reg, 'status': 'stored'} for item, reg in zip(item_ids, reg_ids)])
        connection.execute(insert(Locker), [
            {'id': locker, 'kiosk_id': kiosk, 'locker_number': n + 1} for n, locker in enumerate(locker_ids)])
        connection.execute(insert(StorageAssignment), [
            {'found_item_id': item, 'locker_id': locker, 'status': 'stored'} for item, locker in zip(item_ids, locker_ids)])
        connection.exec_driver_sql("SELECT gin_clean_pending_list('ix_registrations_search_features')")
        for table in ('registrations', 'found_items', 'storage_assignments'):
            connection.exec_driver_sql('ANALYZE ' + table)
        query = candidate_query({'visible_text': 'needle'}, 'postgresql')
        compiled = query.compile(dialect=connection.dialect)
        parameters = {key: Jsonb(value) if isinstance(value, dict) else value for key, value in compiled.params.items()}
        plan = connection.exec_driver_sql('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) ' + str(compiled), parameters).scalar_one()
        assert 'ix_registrations_search_features' in json.dumps(plan)
        assert plan[0]['Plan']['Actual Rows'] == 3
        print(f"5000 stored items, 3 matches: {plan[0]['Execution Time']} ms; GIN index used")
