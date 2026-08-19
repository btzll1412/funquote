"""Email invites, forgot/reset password, and change-password flows.

Tests run without SMTP (console mode): invite links are scraped from the
admin UI, reset tokens are minted directly via app.tokens.
"""

import re

from fastapi.testclient import TestClient
from sqlalchemy import select

from app import models
from app.database import SessionLocal
from app.main import app
from app.tokens import make_invite_token, make_reset_token


def _invite(org_a, email="new@alpha.test", name="Newbie", role="staff") -> str:
    r = org_a.post("/settings/users/invite",
                   data={"name": name, "email": email, "role": role},
                   follow_redirects=True)
    assert r.status_code == 200
    links = re.findall(r'value="(http://[^"]*/invite/[^"]+)"', r.text)
    assert links, "invite link not shown on users page"
    return links[-1].replace("http://testserver", "")


def test_invite_accept_flow(org_a):
    path = _invite(org_a)

    # invite exists, user does not yet
    db = SessionLocal()
    assert db.scalars(select(models.UserInvite)).one().email == "new@alpha.test"
    assert len(db.scalars(select(models.User)).all()) == 1
    db.close()

    invitee = TestClient(app)
    r = invitee.get(path)
    assert r.status_code == 200 and "Join Alpha Security" in r.text

    r = invitee.post(path, data={"password": "newpassword1"}, follow_redirects=True)
    assert "Your account is ready" in r.text

    db = SessionLocal()
    new_user = next(u for u in db.scalars(select(models.User)) if u.email == "new@alpha.test")
    org = db.scalars(select(models.Organization)).one()
    assert new_user.organization_id == org.id and new_user.role == "staff"
    assert db.scalars(select(models.UserInvite)).all() == []  # consumed
    db.close()

    # invitee is signed in and can work; used link is dead
    assert invitee.get("/quotes").status_code == 200
    r = invitee.get(path, follow_redirects=True)
    assert "invalid or has expired" in r.text


def test_invite_link_tampering_and_cancel(org_a):
    path = _invite(org_a, email="other@alpha.test")
    bad = TestClient(app)
    r = bad.get("/invite/not-a-real-token", follow_redirects=True)
    assert "invalid or has expired" in r.text

    db = SessionLocal()
    invite_id = db.scalars(select(models.UserInvite)).one().id
    db.close()
    r = org_a.post(f"/settings/users/invites/{invite_id}/cancel", follow_redirects=True)
    assert "cancelled" in r.text
    r = bad.get(path, follow_redirects=True)
    assert "invalid or has expired" in r.text  # cancelled link is dead


def test_invite_existing_email_rejected(org_a):
    r = org_a.post("/settings/users/invite",
                   data={"name": "Dup", "email": "admin@alpha.test", "role": "staff"},
                   follow_redirects=True)
    assert "already has an account" in r.text
    db = SessionLocal()
    assert db.scalars(select(models.UserInvite)).all() == []
    db.close()


def test_invites_admin_only_and_tenant_scoped(org_a, org_b):
    _invite(org_a, email="scoped@alpha.test")
    db = SessionLocal()
    invite_id = db.scalars(select(models.UserInvite)).one().id
    db.close()
    # org B admin can't see or touch org A's invite
    assert "scoped@alpha.test" not in org_b.get("/settings/users").text
    assert org_b.post(f"/settings/users/invites/{invite_id}/cancel").status_code == 404


def test_forgot_password_flow(org_a):
    # neutral response for unknown email
    r = org_a.post("/forgot-password", data={"email": "nobody@nowhere.test"},
                   follow_redirects=True)
    assert "If that email is registered" in r.text

    db = SessionLocal()
    user = db.scalars(select(models.User)).one()
    token = make_reset_token(user)
    db.close()

    c = TestClient(app)
    r = c.get(f"/reset-password/{token}")
    assert r.status_code == 200 and "admin@alpha.test" in r.text
    r = c.post(f"/reset-password/{token}", data={"password": "brandnewpass1"},
               follow_redirects=True)
    assert "Password updated" in r.text

    # old password dead, new one works
    r = c.post("/login", data={"email": "admin@alpha.test", "password": "password123"},
               follow_redirects=True)
    assert "Invalid email or password" in r.text
    r = c.post("/login", data={"email": "admin@alpha.test", "password": "brandnewpass1"},
               follow_redirects=False)
    assert r.status_code == 303

    # token is single-use: password hash changed, so it no longer validates
    r = c.get(f"/reset-password/{token}", follow_redirects=True)
    assert "invalid, expired, or already used" in r.text


def test_reset_with_garbage_token(client):
    r = client.get("/reset-password/garbage", follow_redirects=True)
    assert "invalid, expired, or already used" in r.text


def test_change_password(org_a):
    assert org_a.get("/account").status_code == 200
    r = org_a.post("/account/password", data={
        "current_password": "wrongwrong", "new_password": "anothernewpw1"},
        follow_redirects=True)
    assert "current password was incorrect" in r.text
    r = org_a.post("/account/password", data={
        "current_password": "password123", "new_password": "anothernewpw1"},
        follow_redirects=True)
    assert "Password changed" in r.text
    c = TestClient(app)
    r = c.post("/login", data={"email": "admin@alpha.test",
                               "password": "anothernewpw1"}, follow_redirects=False)
    assert r.status_code == 303


def test_invite_token_cross_check():
    # a reset token can't be used as an invite token and vice versa
    from app.tokens import read_invite_token, read_reset_token
    assert read_invite_token("junk") is None
    assert read_reset_token(make_invite_token(1)) is None
