import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app import models
from app.ai.tasks import test_connection
from app.auth import find_user_by_email, get_repo, hash_password, require_admin
from app.emailer import external_url, invite_email_body, send_email, smtp_configured
from app.repository import OrgRepo
from app.secret_store import decrypt_secret, encrypt_secret, mask_secret
from app.templating import flash, render
from app.tokens import make_invite_token

router = APIRouter(prefix="/settings")


@router.get("/ai")
def ai_settings(
    request: Request,
    user: models.User = Depends(require_admin),
    repo: OrgRepo = Depends(get_repo),
):
    settings = repo.first(models.AISettings)
    masked = mask_secret(decrypt_secret(settings.api_key_encrypted)) if settings else ""
    return render(request, "settings/ai.html", user=user, settings=settings,
                  masked_key=masked, providers=models.AI_PROVIDER_CHOICES)


@router.post("/ai")
def save_ai_settings(
    request: Request,
    provider: str = Form(...),
    model: str = Form(""),
    api_key: str = Form(""),
    base_url: str = Form(""),
    user: models.User = Depends(require_admin),
    repo: OrgRepo = Depends(get_repo),
):
    valid = {key for key, _ in models.AI_PROVIDER_CHOICES}
    if provider not in valid:
        flash(request, "Unknown provider.", "error")
        return RedirectResponse("/settings/ai", status_code=303)
    if provider == "openai_compatible" and not base_url.strip():
        flash(request, "A base URL is required for a local/custom server "
                       "(e.g. http://192.168.1.50:11434/v1).", "error")
        return RedirectResponse("/settings/ai", status_code=303)

    settings = repo.first(models.AISettings)
    if settings is None:
        settings = repo.add(models.AISettings(provider=provider))
    settings.provider = provider
    settings.model = model.strip()
    settings.base_url = base_url.strip()
    # Blank key field means "keep the stored key" so admins can edit other
    # fields without re-entering the credential.
    if api_key.strip():
        settings.api_key_encrypted = encrypt_secret(api_key.strip())
    repo.commit()
    flash(request, "AI provider saved. Use “Test connection” to verify it works.",
          "success")
    return RedirectResponse("/settings/ai", status_code=303)


@router.post("/ai/test")
def test_ai_settings(
    request: Request,
    user: models.User = Depends(require_admin),
    repo: OrgRepo = Depends(get_repo),
):
    settings = repo.first(models.AISettings)
    ok, message = test_connection(repo, settings)
    flash(request, ("✓ " if ok else "✗ Connection failed: ") + message,
          "success" if ok else "error")
    return RedirectResponse("/settings/ai", status_code=303)


@router.post("/ai/clear")
def clear_ai_settings(
    request: Request,
    user: models.User = Depends(require_admin),
    repo: OrgRepo = Depends(get_repo),
):
    settings = repo.first(models.AISettings)
    if settings is not None:
        repo.delete(settings)
        repo.commit()
    flash(request, "AI provider configuration removed.", "success")
    return RedirectResponse("/settings/ai", status_code=303)


@router.get("")
def org_settings(
    request: Request,
    user: models.User = Depends(require_admin),
    repo: OrgRepo = Depends(get_repo),
):
    org = repo.db.get(models.Organization, repo.organization_id)
    return render(request, "settings/org.html", user=user, org=org)


@router.post("")
def save_org_settings(
    request: Request,
    org_name: str = Form(...),
    ai_enabled: str = Form(""),
    user: models.User = Depends(require_admin),
    repo: OrgRepo = Depends(get_repo),
):
    org = repo.db.get(models.Organization, repo.organization_id)
    org.name = org_name.strip()
    org.ai_enabled = ai_enabled == "on"
    repo.db.commit()
    flash(request, "Organization settings saved.", "success")
    return RedirectResponse("/settings", status_code=303)


@router.get("/users")
def list_users(
    request: Request,
    user: models.User = Depends(require_admin),
    repo: OrgRepo = Depends(get_repo),
):
    users = list(repo.db.scalars(
        select(models.User)
        .where(models.User.organization_id == repo.organization_id)
        .order_by(models.User.name)
    ))
    invites = [
        {"invite": inv,
         "link": external_url(request, f"/invite/{make_invite_token(inv.id)}")}
        for inv in repo.list(models.UserInvite,
                             order_by=models.UserInvite.created_at.desc())
    ]
    return render(request, "settings/users.html", user=user, users=users,
                  invites=invites, smtp_on=smtp_configured())


def _send_invite(request: Request, org_name: str, inviter: str,
                 invite: models.UserInvite) -> None:
    link = external_url(request, f"/invite/{make_invite_token(invite.id)}")
    sent, detail = send_email(
        invite.email, f"You're invited to {org_name} on FunQuote",
        invite_email_body(org_name, inviter, link))
    if sent:
        flash(request, f"Invitation emailed to {invite.email}. It expires in 7 days.",
              "success")
    else:
        flash(request, f"Invitation created, but {detail}. Copy the invite "
                       f"link from the pending invitations list below and "
                       f"send it to {invite.email} yourself.", "info")


@router.post("/users/invite")
def invite_user(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    role: str = Form("staff"),
    user: models.User = Depends(require_admin),
    repo: OrgRepo = Depends(get_repo),
):
    email = email.strip().lower()
    if find_user_by_email(repo.db, email):
        flash(request, "That email already has an account.", "error")
        return RedirectResponse("/settings/users", status_code=303)
    invite = repo.first(models.UserInvite, models.UserInvite.email == email)
    if invite is None:
        invite = repo.add(models.UserInvite(email=email, name=""))
    invite.name = name.strip()
    invite.role = role if role in ("admin", "staff") else "staff"
    invite.invited_by_user_id = user.id
    repo.commit()
    org = repo.db.get(models.Organization, repo.organization_id)
    _send_invite(request, org.name, user.name, invite)
    return RedirectResponse("/settings/users", status_code=303)


@router.post("/users/invites/{invite_id}/resend")
def resend_invite(
    request: Request,
    invite_id: int,
    user: models.User = Depends(require_admin),
    repo: OrgRepo = Depends(get_repo),
):
    invite = repo.get(models.UserInvite, invite_id)
    if invite is None:
        raise HTTPException(404)
    org = repo.db.get(models.Organization, repo.organization_id)
    _send_invite(request, org.name, user.name, invite)
    return RedirectResponse("/settings/users", status_code=303)


@router.post("/users/invites/{invite_id}/cancel")
def cancel_invite(
    request: Request,
    invite_id: int,
    user: models.User = Depends(require_admin),
    repo: OrgRepo = Depends(get_repo),
):
    invite = repo.get(models.UserInvite, invite_id)
    if invite is None:
        raise HTTPException(404)
    repo.delete(invite)
    repo.commit()
    flash(request, "Invitation cancelled — its link no longer works.", "success")
    return RedirectResponse("/settings/users", status_code=303)


@router.post("/users")
def create_user(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    role: str = Form("staff"),
    user: models.User = Depends(require_admin),
    repo: OrgRepo = Depends(get_repo),
):
    email = email.strip().lower()
    if len(password) < 8:
        flash(request, "Password must be at least 8 characters.", "error")
    elif find_user_by_email(repo.db, email):
        flash(request, "That email is already registered.", "error")
    else:
        repo.db.add(models.User(
            organization_id=repo.organization_id, name=name.strip(), email=email,
            password_hash=hash_password(password),
            role=role if role in ("admin", "staff") else "staff",
        ))
        repo.db.commit()
        flash(request, f"User {name} added.", "success")
    return RedirectResponse("/settings/users", status_code=303)


@router.post("/users/{user_id}/delete")
def delete_user(
    request: Request,
    user_id: int,
    user: models.User = Depends(require_admin),
    repo: OrgRepo = Depends(get_repo),
):
    target = repo.db.get(models.User, user_id)
    if target is None or target.organization_id != repo.organization_id:
        raise HTTPException(404)
    if target.id == user.id:
        flash(request, "You can’t delete your own account.", "error")
    else:
        repo.db.delete(target)
        repo.db.commit()
        flash(request, "User removed.", "success")
    return RedirectResponse("/settings/users", status_code=303)


@router.get("/ai-log")
def ai_log(
    request: Request,
    user: models.User = Depends(require_admin),
    repo: OrgRepo = Depends(get_repo),
):
    entries = repo.list(models.AITaskLog,
                        order_by=models.AITaskLog.created_at.desc())[:200]
    def pretty(s: str) -> str:
        try:
            return json.dumps(json.loads(s), indent=2)
        except (json.JSONDecodeError, TypeError):
            return s or ""
    rows = [{"e": e, "input": pretty(e.input_payload_json),
             "output": pretty(e.output_payload_json)} for e in entries]
    return render(request, "settings/ai_log.html", user=user, rows=rows)
