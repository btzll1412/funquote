import json
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["money"] = lambda v: f"${float(v or 0):,.2f}"
templates.env.filters["tojson_attr"] = lambda v: json.dumps(v)


def flash(request: Request, message: str, category: str = "info") -> None:
    # Reassign (not mutate in place) so SessionMiddleware sees the session as
    # modified and persists the cookie — nested mutations are not tracked.
    flashes = list(request.session.get("_flashes", []))
    flashes.append([category, message])
    request.session["_flashes"] = flashes


def render(request: Request, template_name: str, user=None, **context):
    flashes = request.session.pop("_flashes", [])
    return templates.TemplateResponse(
        request, template_name, {"user": user, "flashes": flashes, **context}
    )
