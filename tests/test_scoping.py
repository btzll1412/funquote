"""Tenant isolation: one org can never see or touch another org's data."""

from sqlalchemy import select

from app import models
from app.database import SessionLocal
from app.repository import OrgRepo
from tests.conftest import add_catalog_item, add_customer


def _first_quote_id(client) -> int:
    r = client.get("/quotes")
    assert r.status_code == 200
    # create one quote to look up
    r = client.post("/quotes/save", data={
        "created_by": "manual", "tax_rate": "0", "notes": "",
        "line_catalog_id": [""], "line_sku": ["X"], "line_desc": ["Widget"],
        "line_qty": ["1"], "line_price": ["10"],
    }, follow_redirects=False)
    assert r.status_code == 303
    return int(r.headers["location"].rsplit("/", 1)[-1])


def test_cross_tenant_pages_404(org_a, org_b):
    add_catalog_item(org_a, "Camera", "CAM-1")
    add_customer(org_a)
    quote_id = _first_quote_id(org_a)

    db = SessionLocal()
    item = db.scalars(select(models.CatalogItem)).first()
    customer = db.scalars(select(models.Customer)).first()
    db.close()

    # Org B (a different tenant) gets 404s on all of org A's records
    assert org_b.get(f"/quotes/{quote_id}").status_code == 404
    assert org_b.get(f"/quotes/{quote_id}/pdf").status_code == 404
    assert org_b.get(f"/quotes/{quote_id}/edit").status_code == 404
    assert org_b.get(f"/catalog/{item.id}/edit").status_code == 404
    assert org_b.get(f"/customers/{customer.id}/edit").status_code == 404
    assert org_b.post(f"/quotes/{quote_id}/delete").status_code == 404
    # Org A still sees its own quote
    assert org_a.get(f"/quotes/{quote_id}").status_code == 200


def test_repo_layer_scopes_everything(org_a, org_b):
    add_catalog_item(org_a, "Camera", "CAM-1")
    db = SessionLocal()
    orgs = {o.name: o.id for o in db.scalars(select(models.Organization))}
    item = db.scalars(select(models.CatalogItem)).first()

    repo_b = OrgRepo(db, orgs["Bravo Plumbing"])
    assert repo_b.get(models.CatalogItem, item.id) is None
    assert repo_b.list(models.CatalogItem) == []

    repo_a = OrgRepo(db, orgs["Alpha Security"])
    assert repo_a.get(models.CatalogItem, item.id) is not None
    db.close()


def test_repo_rejects_unscoped_models():
    db = SessionLocal()
    repo = OrgRepo(db, 1)
    try:
        repo.select(models.User)
        assert False, "should have raised"
    except ValueError:
        pass
    finally:
        db.close()


def test_unauthenticated_redirects_to_login(client):
    for path in ("/", "/quotes", "/catalog", "/customers", "/profile"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303
        assert r.headers["location"] == "/login"
