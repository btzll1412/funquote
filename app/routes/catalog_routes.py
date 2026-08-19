import difflib
import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import models
from app.ai.tasks import AITaskError, ai_task
from app.auth import get_current_user, get_repo
from app.repository import OrgRepo
from app.templating import flash, render

router = APIRouter(prefix="/catalog")


def _parse_price(v: str | None) -> float | None:
    if v is None or str(v).strip() == "":
        return None
    return round(float(str(v).replace("$", "").replace(",", "")), 2)


def _apply_pricing(item: models.CatalogItem, cost, sell, markup) -> None:
    """Store what was given; compute the missing side when possible."""
    item.cost_price = cost
    if sell is None and markup is not None and cost is not None:
        sell = round(cost * (1 + markup / 100), 2)
    if markup is None and sell is not None and cost:
        markup = round((sell / cost - 1) * 100, 2)
    item.sell_price = sell
    item.markup_percent = markup


def _parse_custom_fields(keys: list[str], values: list[str]) -> str:
    fields = {}
    for k, v in zip(keys, values):
        k = k.strip()
        if k:
            fields[k] = v.strip()
    return json.dumps(fields)


@router.get("")
def list_items(
    request: Request,
    q: str = "",
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    items = repo.list(models.CatalogItem, models.CatalogItem.is_active == True,  # noqa: E712
                      order_by=models.CatalogItem.name)
    if q:
        ql = q.lower()
        items = [
            i for i in items
            if ql in i.name.lower() or ql in (i.sku or "").lower()
            or ql in i.category.lower()
        ]
    return render(request, "catalog/list.html", user=user, items=items, q=q)


@router.get("/new")
def new_form(request: Request, user: models.User = Depends(get_current_user)):
    return render(request, "catalog/form.html", user=user, item=None, custom_fields={})


@router.get("/{item_id}/edit")
def edit_form(
    request: Request,
    item_id: int,
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    item = repo.get(models.CatalogItem, item_id)
    if item is None:
        raise HTTPException(404)
    try:
        custom_fields = json.loads(item.custom_fields_json or "{}")
    except json.JSONDecodeError:
        custom_fields = {}
    return render(request, "catalog/form.html", user=user, item=item,
                  custom_fields=custom_fields)


@router.post("/save")
async def save(
    request: Request,
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    form = await request.form()
    item_id = form.get("item_id")
    sku = (form.get("sku") or "").strip() or None
    name = (form.get("name") or "").strip()
    if not name:
        flash(request, "Name is required.", "error")
        return RedirectResponse("/catalog/new", status_code=303)

    if item_id:
        item = repo.get(models.CatalogItem, int(item_id))
        if item is None:
            raise HTTPException(404)
    else:
        item = repo.add(models.CatalogItem(name=name))

    if sku:
        dup = repo.first(models.CatalogItem, models.CatalogItem.sku == sku,
                         models.CatalogItem.id != (item.id or 0),
                         models.CatalogItem.is_active == True)  # noqa: E712
        if dup:
            flash(request, f"SKU “{sku}” already exists on “{dup.name}”.", "error")
            target = f"/catalog/{item.id}/edit" if item_id else "/catalog/new"
            repo.db.rollback()
            return RedirectResponse(target, status_code=303)

    item.sku = sku
    item.name = name
    item.description = (form.get("description") or "").strip()
    item.category = (form.get("category") or "").strip()
    try:
        _apply_pricing(item, _parse_price(form.get("cost_price")),
                       _parse_price(form.get("sell_price")),
                       _parse_price(form.get("markup_percent")))
    except ValueError:
        flash(request, "Prices must be numbers.", "error")
        repo.db.rollback()
        return RedirectResponse("/catalog", status_code=303)
    item.custom_fields_json = _parse_custom_fields(
        form.getlist("cf_key"), form.getlist("cf_value"))
    repo.commit()
    flash(request, "Catalog item saved.", "success")
    return RedirectResponse("/catalog", status_code=303)


@router.post("/{item_id}/delete")
def delete(
    request: Request,
    item_id: int,
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    item = repo.get(models.CatalogItem, item_id)
    if item is None:
        raise HTTPException(404)
    # Soft delete so quote snapshots and history stay intact.
    item.is_active = False
    repo.commit()
    flash(request, "Catalog item deleted.", "success")
    return RedirectResponse("/catalog", status_code=303)


# ---------- AI bulk import: paste → AI parse → human review → confirm ----------

@router.get("/import")
def import_form(
    request: Request,
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    org = repo.db.get(models.Organization, repo.organization_id)
    return render(request, "catalog/import.html", user=user, org=org)


@router.post("/import/parse")
def import_parse(
    request: Request,
    raw_pasted_text: str = Form(...),
    user_instructions: str = Form(""),
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    try:
        result = ai_task(
            "import_catalog_items",
            {"raw_pasted_text": raw_pasted_text, "user_instructions": user_instructions},
            repo=repo,
        )
    except AITaskError as e:
        flash(request, f"AI parse failed: {e}", "error")
        return RedirectResponse("/catalog/import", status_code=303)

    existing = repo.list(models.CatalogItem, models.CatalogItem.is_active == True)  # noqa: E712
    existing_skus = {(i.sku or "").lower(): i for i in existing if i.sku}

    rows = []
    for parsed in result["items"]:
        dup = None
        sku = (parsed.get("sku") or "").strip()
        if sku and sku.lower() in existing_skus:
            dup = existing_skus[sku.lower()]
        else:
            name = parsed.get("name") or ""
            best = difflib.get_close_matches(
                name.lower(), [i.name.lower() for i in existing], n=1, cutoff=0.85)
            if best:
                dup = next(i for i in existing if i.name.lower() == best[0])
        rows.append({"parsed": parsed, "duplicate": dup})

    return render(request, "catalog/import_review.html", user=user, rows=rows)


@router.post("/import/confirm")
async def import_confirm(
    request: Request,
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    form = await request.form()
    include = form.getlist("include")
    names = form.getlist("name")
    skus = form.getlist("sku")
    costs = form.getlist("cost_price")
    sells = form.getlist("sell_price")
    categories = form.getlist("category")

    created, skipped = 0, 0
    for idx_str in include:
        i = int(idx_str)
        name = names[i].strip()
        if not name:
            skipped += 1
            continue
        sku = skus[i].strip() or None
        if sku and repo.first(models.CatalogItem, models.CatalogItem.sku == sku,
                              models.CatalogItem.is_active == True):  # noqa: E712
            skipped += 1
            continue
        item = repo.add(models.CatalogItem(name=name, sku=sku,
                                           category=categories[i].strip()))
        try:
            _apply_pricing(item, _parse_price(costs[i]), _parse_price(sells[i]), None)
        except ValueError:
            _apply_pricing(item, None, None, None)
        created += 1
    repo.commit()
    flash(request, f"Imported {created} item(s), skipped {skipped}.", "success")
    return RedirectResponse("/catalog", status_code=303)
