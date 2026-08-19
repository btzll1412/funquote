"""Signed, expiring tokens for invite and password-reset links."""

import itsdangerous

from app import config, models

INVITE_MAX_AGE = 7 * 24 * 3600   # 7 days
RESET_MAX_AGE = 2 * 3600         # 2 hours


def _serializer(salt: str) -> itsdangerous.URLSafeTimedSerializer:
    return itsdangerous.URLSafeTimedSerializer(config.SECRET_KEY, salt=salt)


def make_invite_token(invite_id: int) -> str:
    return _serializer("invite-v1").dumps({"invite_id": invite_id})


def read_invite_token(token: str) -> int | None:
    try:
        payload = _serializer("invite-v1").loads(token, max_age=INVITE_MAX_AGE)
        return int(payload["invite_id"])
    except (itsdangerous.BadSignature, KeyError, ValueError, TypeError):
        return None


def make_reset_token(user: models.User) -> str:
    # A fragment of the current password hash is baked in, so the token
    # stops working the moment the password changes (single effective use).
    return _serializer("password-reset-v1").dumps(
        {"user_id": user.id, "ph": user.password_hash[-12:]}
    )


def read_reset_token(token: str) -> dict | None:
    try:
        payload = _serializer("password-reset-v1").loads(token, max_age=RESET_MAX_AGE)
        return {"user_id": int(payload["user_id"]), "ph": str(payload["ph"])}
    except (itsdangerous.BadSignature, KeyError, ValueError, TypeError):
        return None
