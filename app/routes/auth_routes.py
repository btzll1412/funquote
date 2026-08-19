from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import models
from app.auth import find_user_by_email, get_current_user, hash_password, verify_password
from app.database import get_db
from app.emailer import external_url, reset_email_body, send_email
from app.templating import flash, render
from app.tokens import make_reset_token, read_invite_token, read_reset_token

router = APIRouter()


@router.get("/signup")
def signup_form(request: Request):
    return render(request, "auth/signup.html")


@router.post("/signup")
def signup(
    request: Request,
    org_name: str = Form(...),
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    if len(password) < 8:
        flash(request, "Password must be at least 8 characters.", "error")
        return render(request, "auth/signup.html", org_name=org_name, name=name, email=email)
    if find_user_by_email(db, email):
        flash(request, "That email is already registered.", "error")
        return render(request, "auth/signup.html", org_name=org_name, name=name)

    org = models.Organization(name=org_name.strip())
    db.add(org)
    db.flush()
    user = models.User(
        organization_id=org.id, name=name.strip(), email=email,
        password_hash=hash_password(password), role="admin",
    )
    db.add(user)
    db.add(models.BusinessProfile(organization_id=org.id, company_name=org.name))
    db.commit()

    request.session["user_id"] = user.id
    flash(request, f"Welcome! Organization “{org.name}” created.", "success")
    return RedirectResponse("/", status_code=303)


@router.get("/login")
def login_form(request: Request):
    return render(request, "auth/login.html")


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = find_user_by_email(db, email)
    if user is None or not verify_password(password, user.password_hash):
        flash(request, "Invalid email or password.", "error")
        return render(request, "auth/login.html", email=email)
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


# ---------- Accept an email invitation ----------

def _load_invite(token: str, db: Session) -> models.UserInvite | None:
    invite_id = read_invite_token(token)
    if invite_id is None:
        return None
    return db.get(models.UserInvite, invite_id)


@router.get("/invite/{token}")
def accept_invite_form(request: Request, token: str, db: Session = Depends(get_db)):
    invite = _load_invite(token, db)
    if invite is None:
        flash(request, "This invitation link is invalid or has expired. "
                       "Ask your admin to send a new one.", "error")
        return RedirectResponse("/login", status_code=303)
    org = db.get(models.Organization, invite.organization_id)
    return render(request, "auth/accept_invite.html", invite=invite,
                  org=org, token=token)


@router.post("/invite/{token}")
def accept_invite(
    request: Request,
    token: str,
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    invite = _load_invite(token, db)
    if invite is None:
        flash(request, "This invitation link is invalid or has expired. "
                       "Ask your admin to send a new one.", "error")
        return RedirectResponse("/login", status_code=303)
    if len(password) < 8:
        flash(request, "Password must be at least 8 characters.", "error")
        return RedirectResponse(f"/invite/{token}", status_code=303)
    if find_user_by_email(db, invite.email):
        flash(request, "An account with this email already exists — just log in.",
              "error")
        db.delete(invite)
        db.commit()
        return RedirectResponse("/login", status_code=303)

    user = models.User(
        organization_id=invite.organization_id,
        name=invite.name, email=invite.email.strip().lower(),
        password_hash=hash_password(password), role=invite.role,
    )
    db.add(user)
    db.delete(invite)
    db.commit()
    request.session.clear()
    request.session["user_id"] = user.id
    flash(request, f"Welcome, {user.name}! Your account is ready.", "success")
    return RedirectResponse("/", status_code=303)


# ---------- Forgot / reset password ----------

@router.get("/forgot-password")
def forgot_password_form(request: Request):
    return render(request, "auth/forgot_password.html")


@router.post("/forgot-password")
def forgot_password(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    user = find_user_by_email(db, email)
    if user is not None:
        link = external_url(request, f"/reset-password/{make_reset_token(user)}")
        send_email(user.email, "Reset your FunQuote password",
                   reset_email_body(user.name, link))
    # Same message either way, so this form can't be used to probe which
    # emails are registered.
    flash(request, "If that email is registered, a password reset link has "
                   "been sent to it. The link is valid for 2 hours.", "success")
    return RedirectResponse("/login", status_code=303)


@router.get("/reset-password/{token}")
def reset_password_form(request: Request, token: str, db: Session = Depends(get_db)):
    payload = read_reset_token(token)
    user = db.get(models.User, payload["user_id"]) if payload else None
    if user is None or not user.password_hash.endswith(payload["ph"]):
        flash(request, "This reset link is invalid, expired, or already used. "
                       "Request a new one below.", "error")
        return RedirectResponse("/forgot-password", status_code=303)
    return render(request, "auth/reset_password.html", token=token, email=user.email)


@router.post("/reset-password/{token}")
def reset_password(
    request: Request,
    token: str,
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    payload = read_reset_token(token)
    user = db.get(models.User, payload["user_id"]) if payload else None
    if user is None or not user.password_hash.endswith(payload["ph"]):
        flash(request, "This reset link is invalid, expired, or already used. "
                       "Request a new one below.", "error")
        return RedirectResponse("/forgot-password", status_code=303)
    if len(password) < 8:
        flash(request, "Password must be at least 8 characters.", "error")
        return RedirectResponse(f"/reset-password/{token}", status_code=303)
    user.password_hash = hash_password(password)
    db.commit()
    request.session.clear()
    flash(request, "Password updated — log in with your new password.", "success")
    return RedirectResponse("/login", status_code=303)


# ---------- Change my own password ----------

@router.get("/account")
def account_page(request: Request, user: models.User = Depends(get_current_user)):
    return render(request, "auth/account.html", user=user)


@router.post("/account/password")
def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not verify_password(current_password, user.password_hash):
        flash(request, "Your current password was incorrect.", "error")
        return RedirectResponse("/account", status_code=303)
    if len(new_password) < 8:
        flash(request, "New password must be at least 8 characters.", "error")
        return RedirectResponse("/account", status_code=303)
    user.password_hash = hash_password(new_password)
    db.add(user)
    db.commit()
    flash(request, "Password changed.", "success")
    return RedirectResponse("/account", status_code=303)
