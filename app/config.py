import os
from pathlib import Path


def _load_dotenv() -> None:
    """Tiny .env loader so we don't need python-dotenv. Real env vars win."""
    path = Path(".env")
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-secret")
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./data/funquote.db")
AI_PROVIDER = os.environ.get("AI_PROVIDER", "mock")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", "./data/uploads"))

# Outbound email (invites, password resets). With SMTP_HOST unset the app
# runs in "console mode": emails are printed to the server log instead, and
# invite links are additionally shown to the admin in the UI to copy.
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("SMTP_FROM", "")
SMTP_SECURITY = os.environ.get("SMTP_SECURITY", "starttls")  # starttls / ssl / none

# Public base URL used in emailed links (e.g. https://quotes.example.com).
# Defaults to the URL the request came in on.
APP_BASE_URL = os.environ.get("APP_BASE_URL", "")
