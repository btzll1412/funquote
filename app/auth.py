import hashlib
import hmac
import secrets

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.repository import OrgRepo

_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _ITERATIONS)
    return f"pbkdf2${_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iterations, salt, expected = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), int(iterations)
        )
        return hmac.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


class AuthRequired(Exception):
    """Raised when an unauthenticated request hits a protected page."""


def get_current_user(request: Request, db: Session = Depends(get_db)) -> models.User:
    user_id = request.session.get("user_id")
    if user_id is None:
        raise AuthRequired()
    user = db.get(models.User, user_id)
    if user is None:
        request.session.clear()
        raise AuthRequired()
    return user


def get_repo(
    user: models.User = Depends(get_current_user), db: Session = Depends(get_db)
) -> OrgRepo:
    return OrgRepo(db, user.organization_id)


def require_admin(user: models.User = Depends(get_current_user)) -> models.User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin access required")
    return user


def find_user_by_email(db: Session, email: str) -> models.User | None:
    return db.scalars(
        select(models.User).where(models.User.email == email.strip().lower())
    ).first()
