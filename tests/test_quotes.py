"""Manual quote flow: build, totals, snapshots, PDF, status."""

from sqlalchemy import select

from app import models
from app.database import SessionLocal
from tests.conftest import add_catalog_item, add_customer


def _create_quote(client, tax_rate="8.25", org_name=None):
    db = SessionLocal()
    item_q = select(models.CatalogItem)
    cust_q = select(models.Customer)
    if org_name:
        org = db.scalars(select(models.Organization)
                         .where(models.Organization.name == org_name)).one()
        item_q = item_q.where(models.CatalogItem.organization_id == org.id)
        cust_q = cust_q.where(models.Customer.organization_id == org.id)
    item = db.scalars(item_q).first()
    customer = db.scalars(cust_q).first()
    db.close()
    r = client.post("/quotes/save", data={
        "created_by": "manual", "customer_id": str(customer.id),
        "tax_rate": tax_rate, "notes": "Install included",
        "line_catalog_id": [str(item.id), ""],
        "line_sku": [item.sku, ""],
        "line_desc": [item.name, "Custom labor"],
        "line_qty": ["4", "2"],
        "line_price": ["100", "75"],
    }, follow_redirects=False)
    assert r.status_code == 303, r.text
    return int(r.headers["location"].rsplit("/", 1)[-1])


def test_quote_totals_and_snapshots(org_a):
    add_catalog_item(org_a, "Turret Camera", "CAM-4MP", cost="60", sell="100")
    add_customer(org_a)
    quote_id = _create_quote(org_a)

    db = SessionLocal()
    quote = db.get(models.Quote, quote_id)
    assert quote.quote_number == "Q-0001"
    assert float(quote.subtotal) == 550.0          # 4*100 + 2*75
    assert float(quote.tax) == round(550 * 0.0825, 2)
    assert float(quote.total) == float(quote.subtotal) + float(quote.tax)
    assert len(quote.items) == 2
    catalog_line = quote.items[0]
    assert catalog_line.catalog_item_id is not None
    assert catalog_line.sku_snapshot == "CAM-4MP"
    # a version snapshot was recorded
    versions = db.scalars(select(models.QuoteVersion)).all()
    assert len(versions) == 1 and versions[0].version_number == 1
    db.close()


def test_quote_number_increments_per_org(org_a, org_b):
    add_catalog_item(org_a, "Camera", "CAM-1")
    add_customer(org_a)
    add_catalog_item(org_b, "Pipe", "PIPE-1")
    add_customer(org_b, "Bravo Customer")
    _create_quote(org_a, org_name="Alpha Security")
    _create_quote(org_a, org_name="Alpha Security")
    b_id = _create_quote(org_b, org_name="Bravo Plumbing")
    db = SessionLocal()
    b_quote = db.get(models.Quote, b_id)
    assert b_quote.quote_number == "Q-0001"  # org B's sequence is independent
    db.close()


def test_pdf_download(org_a):
    add_catalog_item(org_a, "Camera", "CAM-1")
    add_customer(org_a)
    quote_id = _create_quote(org_a)
    r = org_a.get(f"/quotes/{quote_id}/pdf")
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")


def test_status_transitions(org_a):
    add_catalog_item(org_a, "Camera", "CAM-1")
    add_customer(org_a)
    quote_id = _create_quote(org_a)
    r = org_a.post(f"/quotes/{quote_id}/status", data={"status": "sent"},
                   follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    assert db.get(models.Quote, quote_id).status == "sent"
    db.close()
    r = org_a.post(f"/quotes/{quote_id}/status", data={"status": "bogus"})
    assert r.status_code == 400


def test_markup_computed_from_cost(org_a):
    r = org_a.post("/catalog/save", data={
        "name": "NVR", "sku": "NVR-16", "cost_price": "200",
        "sell_price": "", "markup_percent": "50", "category": "",
        "description": "", "cf_key": "", "cf_value": "",
    }, follow_redirects=False)
    assert r.status_code == 303
    db = SessionLocal()
    item = db.scalars(select(models.CatalogItem)).first()
    assert float(item.sell_price) == 300.0
    db.close()
