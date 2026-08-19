from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select

from app import models
from app.auth import get_current_user, get_repo
from app.repository import OrgRepo
from app.templating import render

router = APIRouter()


@router.get("/")
def dashboard(
    request: Request,
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    def count(model, *where):
        stmt = select(func.count()).select_from(model).where(
            model.organization_id == repo.organization_id, *where
        )
        return repo.db.scalar(stmt) or 0

    recent_quotes = repo.list(
        models.Quote, order_by=models.Quote.created_at.desc()
    )[:8]
    org = repo.db.get(models.Organization, repo.organization_id)
    return render(
        request, "dashboard.html", user=user,
        org=org,
        n_catalog=count(models.CatalogItem, models.CatalogItem.is_active == True),  # noqa: E712
        n_customers=count(models.Customer),
        n_quotes=count(models.Quote),
        n_draft=count(models.Quote, models.Quote.status == "draft"),
        recent_quotes=recent_quotes,
    )
