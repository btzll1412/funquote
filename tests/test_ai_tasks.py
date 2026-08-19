"""AI-assist flows: extraction validation, catalog import review, logging,
and the org-level AI toggle. Uses the deterministic mock provider."""

from sqlalchemy import select

from app import models
from app.database import SessionLocal
from tests.conftest import add_catalog_item


def test_ai_assist_matches_catalog_and_logs(org_a):
    add_catalog_item(org_a, "4MP Turret Camera", "CAM-4MP", sell="95")
    add_catalog_item(org_a, "16ch NVR", "NVR-16", sell="450")

    r = org_a.post("/quotes/ai-assist", data={
        "customer_text": "2x CAM-4MP\n1 NVR-16\n500ft of mystery cable",
        "customer_id": "",
    })
    assert r.status_code == 200
    # matched items are prefilled with DB pricing; unmatched surfaced
    assert "CAM-4MP" in r.text
    assert "mystery cable" in r.text

    db = SessionLocal()
    logs = db.scalars(select(models.AITaskLog)).all()
    assert len(logs) == 1
    log = logs[0]
    assert log.task_type == "extract_quote_items"
    assert log.status == "success"
    assert log.organization_id is not None
    # pricing is never sent to the AI
    assert "sell_price" not in log.input_payload_json
    assert "95" not in log.input_payload_json
    db.close()


def test_ai_disabled_blocks_ai_but_not_manual(org_a):
    r = org_a.post("/settings", data={"org_name": "Alpha Security"},
                   follow_redirects=False)  # checkbox omitted -> AI off
    assert r.status_code == 303
    r = org_a.post("/quotes/ai-assist",
                   data={"customer_text": "2 cameras", "customer_id": ""},
                   follow_redirects=True)
    assert "AI assist failed" in r.text or "disabled" in r.text
    # manual quoting still works
    r = org_a.post("/quotes/save", data={
        "created_by": "manual", "tax_rate": "0", "notes": "",
        "line_catalog_id": [""], "line_sku": [""], "line_desc": ["Manual line"],
        "line_qty": ["1"], "line_price": ["10"],
    }, follow_redirects=False)
    assert r.status_code == 303


def test_catalog_import_requires_confirmation(org_a):
    r = org_a.post("/catalog/import/parse", data={
        "raw_pasted_text": "Dome Camera, DC-100, $40, $80\nPatch Panel, PP-24, $25, $55",
        "user_instructions": "first price cost, second sell",
    })
    assert r.status_code == 200
    assert "Dome Camera" in r.text

    # nothing was written by the parse step
    db = SessionLocal()
    assert db.scalars(select(models.CatalogItem)).all() == []
    db.close()

    # confirm only the first row
    r = org_a.post("/catalog/import/confirm", data={
        "include": ["0"],
        "name": ["Dome Camera", "Patch Panel"],
        "sku": ["DC-100", "PP-24"],
        "cost_price": ["40", "25"],
        "sell_price": ["80", "55"],
        "category": ["", ""],
    }, follow_redirects=False)
    assert r.status_code == 303

    db = SessionLocal()
    items = db.scalars(select(models.CatalogItem)).all()
    assert len(items) == 1
    assert items[0].sku == "DC-100"
    assert float(items[0].sell_price) == 80.0
    db.close()


def test_import_flags_duplicates(org_a):
    add_catalog_item(org_a, "Dome Camera", "DC-100")
    r = org_a.post("/catalog/import/parse", data={
        "raw_pasted_text": "Dome Camera, DC-100, $40, $80",
        "user_instructions": "",
    })
    assert r.status_code == 200
    assert "Possible duplicate" in r.text


def test_ai_log_viewer_admin_only(org_a):
    assert org_a.get("/settings/ai-log").status_code == 200
    # add a staff user, log in as them, verify 403
    r = org_a.post("/settings/users", data={
        "name": "Staff", "email": "staff@alpha.test",
        "password": "password123", "role": "staff",
    }, follow_redirects=False)
    assert r.status_code == 303
    from fastapi.testclient import TestClient
    from app.main import app
    staff = TestClient(app)
    r = staff.post("/login", data={"email": "staff@alpha.test",
                                   "password": "password123"},
                   follow_redirects=False)
    assert r.status_code == 303
    assert staff.get("/settings/ai-log").status_code == 403
