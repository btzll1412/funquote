from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app import models
from app.auth import find_user_by_email, hash_password, verify_password
from app.database import get_db
from app.templating import flash, render

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
