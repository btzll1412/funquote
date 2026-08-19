"""Per-tenant AI provider configuration (bring-your-own-AI)."""

from sqlalchemy import select

from app import models
from app.database import SessionLocal
from app.secret_store import decrypt_secret, encrypt_secret, mask_secret


def test_secret_roundtrip_and_masking():
    ct = encrypt_secret("sk-test-1234567890abcdef")
    assert ct != "" and "sk-test" not in ct
    assert decrypt_secret(ct) == "sk-test-1234567890abcdef"
    assert mask_secret("sk-test-1234567890abcdef").endswith("cdef")
    assert "sk-test" not in mask_secret("sk-test-1234567890abcdef")
    assert decrypt_secret("garbage") == ""


def test_ai_settings_admin_only(org_a):
    assert org_a.get("/settings/ai").status_code == 200
    org_a.post("/settings/users", data={
        "name": "Staff", "email": "staff2@alpha.test",
        "password": "password123", "role": "staff"}, follow_redirects=False)
    from fastapi.testclient import TestClient
    from app.main import app
    staff = TestClient(app)
    staff.post("/login", data={"email": "staff2@alpha.test",
                               "password": "password123"})
    assert staff.get("/settings/ai").status_code == 403


def test_save_settings_encrypts_key_and_masks_display(org_a):
    r = org_a.post("/settings/ai", data={
        "provider": "anthropic", "model": "claude-sonnet-5",
        "api_key": "sk-ant-secret-key-9876", "base_url": "",
    }, follow_redirects=False)
    assert r.status_code == 303

    db = SessionLocal()
    settings = db.scalars(select(models.AISettings)).one()
    assert settings.provider == "anthropic"
    assert "sk-ant-secret-key-9876" not in settings.api_key_encrypted
    assert decrypt_secret(settings.api_key_encrypted) == "sk-ant-secret-key-9876"
    db.close()

    page = org_a.get("/settings/ai").text
    assert "sk-ant-secret-key-9876" not in page  # never echoed back
    assert "9876" in page                        # masked tail shown
    assert "Anthropic" in page

    # blank key on re-save keeps the stored key
    org_a.post("/settings/ai", data={
        "provider": "anthropic", "model": "claude-haiku-4-5",
        "api_key": "", "base_url": ""})
    db = SessionLocal()
    settings = db.scalars(select(models.AISettings)).one()
    assert decrypt_secret(settings.api_key_encrypted) == "sk-ant-secret-key-9876"
    assert settings.model == "claude-haiku-4-5"
    db.close()


def test_custom_server_requires_base_url(org_a):
    r = org_a.post("/settings/ai", data={
        "provider": "openai_compatible", "model": "llama3.1",
        "api_key": "", "base_url": "",
    }, follow_redirects=True)
    assert "base url is required" in r.text.lower()
    db = SessionLocal()
    assert db.scalars(select(models.AISettings)).first() is None
    db.close()


def test_org_provider_overrides_env_fallback(org_a):
    """With no org settings the env mock works; once the org connects its own
    (unreachable) server, AI calls go there and fail cleanly + get logged."""
    r = org_a.post("/quotes/ai-assist",
                   data={"customer_text": "2 things", "customer_id": ""})
    assert r.status_code == 200  # mock fallback works

    org_a.post("/settings/ai", data={
        "provider": "openai_compatible", "model": "llama3.1",
        "api_key": "", "base_url": "http://127.0.0.1:1/v1"})
    r = org_a.post("/quotes/ai-assist",
                   data={"customer_text": "2 things", "customer_id": ""},
                   follow_redirects=True)
    assert "AI assist failed" in r.text

    db = SessionLocal()
    logs = db.scalars(select(models.AITaskLog)
                      .order_by(models.AITaskLog.id)).all()
    assert logs[-1].status == "error"
    assert logs[-1].model_used.startswith("openai_compatible:")
    db.close()

    # removing the config falls back to the env mock again
    org_a.post("/settings/ai/clear")
    r = org_a.post("/quotes/ai-assist",
                   data={"customer_text": "2 things", "customer_id": ""})
    assert r.status_code == 200


def test_connection_test_is_logged(org_a):
    r = org_a.post("/settings/ai/test", follow_redirects=True)
    assert "Connected" in r.text  # env mock fallback answers the ping
    db = SessionLocal()
    logs = db.scalars(select(models.AITaskLog)).all()
    assert any(l.task_type == "connection_test" and l.status == "success"
               for l in logs)
    db.close()


def test_ai_settings_are_tenant_isolated(org_a, org_b):
    org_a.post("/settings/ai", data={
        "provider": "openai", "model": "gpt-4o-mini",
        "api_key": "sk-alpha-only", "base_url": ""})
    page_b = org_b.get("/settings/ai").text
    assert "No provider connected" in page_b
    assert "alpha" not in page_b
