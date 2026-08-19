import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="funquote-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["AI_PROVIDER"] = "mock"
os.environ["SECRET_KEY"] = "test-secret"
os.environ["UPLOAD_DIR"] = f"{_tmp}/uploads"

import pytest
from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def client():
    return TestClient(app)


def signup(client: TestClient, org: str, email: str) -> None:
    r = client.post("/signup", data={
        "org_name": org, "name": f"{org} Admin", "email": email,
        "password": "password123",
    }, follow_redirects=False)
    assert r.status_code == 303, r.text


@pytest.fixture
def org_a(client):
    signup(client, "Alpha Security", "admin@alpha.test")
    return client


@pytest.fixture
def org_b():
    c = TestClient(app)
    signup(c, "Bravo Plumbing", "admin@bravo.test")
    return c


def add_catalog_item(client, name, sku, cost="50", sell="100", **extra):
    data = {"name": name, "sku": sku, "cost_price": cost, "sell_price": sell,
            "category": extra.get("category", ""), "description": extra.get("description", ""),
            "markup_percent": "", "cf_key": "", "cf_value": ""}
    r = client.post("/catalog/save", data=data, follow_redirects=False)
    assert r.status_code == 303, r.text


def add_customer(client, name="Acme Corp"):
    r = client.post("/customers/save", data={"name": name}, follow_redirects=False)
    assert r.status_code == 303, r.text
