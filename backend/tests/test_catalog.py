from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import Base, get_session
from app.main import app
from app.models import Kiosk


@pytest.fixture
def catalog(database_engine):
    Base.metadata.create_all(database_engine)
    with Session(database_engine) as session:
        kiosk = Kiosk(name="Catalog test kiosk")
        session.add(kiosk)
        session.commit()
        kiosk_id = str(kiosk.id)
    settings = Settings(_env_file=None, postgres_db="test", postgres_user="test",
                        postgres_password="test", local_development=True)
    def sessions():
        with Session(database_engine) as session:
            yield session
    app.dependency_overrides.update({get_session: sessions, get_settings: lambda: settings})
    try:
        with TestClient(app) as client:
            yield SimpleNamespace(client=client, settings=settings, kiosk_id=kiosk_id)
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("local_mode", [True, False])
def test_catalog_and_optional_registration(catalog, local_mode):
    catalog.settings.local_development = local_mode
    catalog.settings.business_api_enabled = not local_mode
    assert catalog.client.get("/api/kiosks").json()[0]["id"] == catalog.kiosk_id
    assert catalog.client.get("/api/categories").json() == []
    response = catalog.client.post("/api/registrations", json={"kiosk_id": catalog.kiosk_id})
    assert response.status_code == 201
    value = response.json()
    assert value["primary_category_id"] is None
    assert value["secondary_description"] is None
    assert value["status"] == "draft"
    assert catalog.client.get(f'/api/registrations/{value["id"]}').json() == value


def test_registration_rejects_unknown_references(catalog):
    assert catalog.client.post("/api/registrations", json={"kiosk_id": str(uuid4())}).status_code == 404
    assert catalog.client.post("/api/registrations", json={
        "kiosk_id": catalog.kiosk_id, "primary_category_id": str(uuid4()),
    }).status_code == 404
    assert catalog.client.post("/api/registrations", json={"kiosk_id": "invalid"}).status_code == 422


def test_business_api_disabled_without_either_switch(catalog):
    catalog.settings.local_development = False
    assert catalog.client.get("/api/kiosks").status_code == 503
    assert catalog.client.post("/api/registrations", json={"kiosk_id": catalog.kiosk_id}).status_code == 503
    assert catalog.client.get("/health").status_code == 200
