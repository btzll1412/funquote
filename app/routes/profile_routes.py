from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse

from app import config, models
from app.auth import get_current_user, get_repo
from app.repository import OrgRepo
from app.templating import flash, render

router = APIRouter(prefix="/profile")

_ALLOWED_LOGO_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def _get_profile(repo: OrgRepo) -> models.BusinessProfile:
    profile = repo.first(models.BusinessProfile)
    if profile is None:
        profile = repo.add(models.BusinessProfile())
        repo.commit()
    return profile


@router.get("")
def edit_form(
    request: Request,
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    return render(request, "profile/edit.html", user=user, profile=_get_profile(repo))


@router.post("")
async def save(
    request: Request,
    company_name: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    website: str = Form(""),
    quote_footer_text: str = Form(""),
    logo: UploadFile | None = File(None),
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    profile = _get_profile(repo)
    profile.company_name = company_name.strip()
    profile.phone = phone.strip()
    profile.email = email.strip()
    profile.address = address.strip()
    profile.website = website.strip()
    profile.quote_footer_text = quote_footer_text.strip()

    if logo is not None and logo.filename:
        ext = Path(logo.filename).suffix.lower()
        if ext not in _ALLOWED_LOGO_EXT:
            flash(request, "Logo must be an image file (png/jpg/gif/webp).", "error")
            return RedirectResponse("/profile", status_code=303)
        org_dir = config.UPLOAD_DIR / f"org_{repo.organization_id}"
        org_dir.mkdir(parents=True, exist_ok=True)
        dest = org_dir / f"logo{ext}"
        dest.write_bytes(await logo.read())
        profile.logo_path = str(dest)

    repo.commit()
    flash(request, "Business profile saved.", "success")
    return RedirectResponse("/profile", status_code=303)
