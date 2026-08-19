"""Encryption-at-rest for tenant AI API keys.

Keys are encrypted with Fernet using a key derived from SECRET_KEY, so the
database alone never contains a usable credential. Changing SECRET_KEY makes
previously stored keys unreadable — tenants would re-enter them.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app import config

_SALT = b"funquote-ai-credentials-v1"


def _fernet() -> Fernet:
    derived = hashlib.pbkdf2_hmac(
        "sha256", config.SECRET_KEY.encode(), _SALT, 100_000, dklen=32
    )
    return Fernet(base64.urlsafe_b64encode(derived))


def encrypt_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken:
        return ""


def mask_secret(plaintext: str) -> str:
    if not plaintext:
        return ""
    tail = plaintext[-4:] if len(plaintext) > 8 else ""
    return "••••••••" + tail
