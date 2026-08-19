import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from app import models
from app.auth import find_user_by_email, get_repo, hash_password, require_admin
from app.repository import OrgRepo
from app.templating import flash, render

router = APIRouter(prefix="/settings")


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
    return render(request, "settings/users.html", user=user, users=users)


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
