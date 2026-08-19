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
