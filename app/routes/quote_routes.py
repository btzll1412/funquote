import json
from datetime import date, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app import models
from app.ai.tasks import AITaskError, ai_task, build_catalog_summary
from app.auth import get_current_user, get_repo
from app.pdf import render_quote_pdf
from app.repository import OrgRepo, next_quote_number
from app.templating import flash, render

router = APIRouter(prefix="/quotes")


def _builder_context(repo: OrgRepo) -> dict:
    catalog = repo.list(models.CatalogItem, models.CatalogItem.is_active == True,  # noqa: E712
                        order_by=models.CatalogItem.name)
    customers = repo.list(models.Customer, order_by=models.Customer.name)
    catalog_json = [
        {"id": c.id, "sku": c.sku or "", "name": c.name,
         "description": c.description, "price": float(c.sell_price or 0)}
        for c in catalog
    ]
    return {"catalog": catalog, "customers": customers, "catalog_json": catalog_json}


def _snapshot(quote: models.Quote) -> str:
    return json.dumps({
        "quote_number": quote.quote_number,
        "status": quote.status,
        "customer_id": quote.customer_id,
        "expiration_date": quote.expiration_date.isoformat() if quote.expiration_date else None,
        "subtotal": float(quote.subtotal), "tax_rate": float(quote.tax_rate),
        "tax": float(quote.tax), "total": float(quote.total),
        "notes": quote.notes,
        "items": [
            {"catalog_item_id": i.catalog_item_id, "sku": i.sku_snapshot,
             "description": i.description_snapshot, "quantity": float(i.quantity),
             "unit_price": float(i.unit_price), "line_total": float(i.line_total)}
            for i in quote.items
        ],
    })


def _record_version(repo: OrgRepo, quote: models.Quote) -> None:
    last = repo.list(models.QuoteVersion, models.QuoteVersion.quote_id == quote.id)
    n = max((v.version_number for v in last), default=0) + 1
    repo.add(models.QuoteVersion(quote_id=quote.id, version_number=n,
                                 snapshot_json=_snapshot(quote)))


@router.get("")
def list_quotes(
    request: Request,
    status: str = "",
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    quotes = repo.list(models.Quote, order_by=models.Quote.created_at.desc())
    if status:
        quotes = [q for q in quotes if q.status == status]
    return render(request, "quotes/list.html", user=user, quotes=quotes,
                  status_filter=status, statuses=models.QUOTE_STATUSES)


@router.get("/new")
def new_form(
    request: Request,
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    return render(request, "quotes/builder.html", user=user, quote=None,
                  prefill_lines=None, ai_notes=None, created_by="manual",
                  **_builder_context(repo))


@router.get("/{quote_id}/edit")
def edit_form(
    request: Request,
    quote_id: int,
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    quote = repo.get(models.Quote, quote_id)
    if quote is None:
        raise HTTPException(404)
    return render(request, "quotes/builder.html", user=user, quote=quote,
                  prefill_lines=None, ai_notes=None, created_by=quote.created_by,
                  **_builder_context(repo))


@router.post("/save")
async def save(
    request: Request,
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    form = await request.form()
    quote_id = form.get("quote_id")
    if quote_id:
        quote = repo.get(models.Quote, int(quote_id))
        if quote is None:
            raise HTTPException(404)
    else:
        quote = repo.add(models.Quote(quote_number=next_quote_number(repo)))
        created_by = form.get("created_by", "manual")
        quote.created_by = created_by if created_by in ("manual", "ai_assisted") else "manual"

    customer_id = form.get("customer_id")
    if customer_id:
        customer = repo.get(models.Customer, int(customer_id))
        if customer is None:
            raise HTTPException(400, "Unknown customer")
        quote.customer_id = customer.id
    else:
        quote.customer_id = None

    exp = (form.get("expiration_date") or "").strip()
    quote.expiration_date = date.fromisoformat(exp) if exp else None
    quote.notes = (form.get("notes") or "").strip()
    try:
        quote.tax_rate = float(form.get("tax_rate") or 0)
    except ValueError:
        quote.tax_rate = 0

    quote.items.clear()
    catalog_ids = form.getlist("line_catalog_id")
    skus = form.getlist("line_sku")
    descs = form.getlist("line_desc")
    qtys = form.getlist("line_qty")
    prices = form.getlist("line_price")

    subtotal = 0.0
    for cid, sku, desc, qty_s, price_s in zip(catalog_ids, skus, descs, qtys, prices):
        desc = desc.strip()
        if not desc:
            continue
        try:
            qty = float(qty_s or 1)
            price = float(str(price_s or 0).replace("$", "").replace(",", ""))
        except ValueError:
            continue
        catalog_item = None
        if cid.strip():
            catalog_item = repo.get(models.CatalogItem, int(cid))  # None if other org
        line_total = round(qty * price, 2)
        subtotal += line_total
        quote.items.append(models.QuoteItem(
            organization_id=repo.organization_id,
            catalog_item_id=catalog_item.id if catalog_item else None,
            description_snapshot=desc,
            sku_snapshot=sku.strip(),
            quantity=qty, unit_price=price, line_total=line_total,
        ))

    quote.subtotal = round(subtotal, 2)
    quote.tax = round(subtotal * float(quote.tax_rate) / 100, 2)
    quote.total = round(float(quote.subtotal) + float(quote.tax), 2)
    quote.updated_at = datetime.utcnow()
    repo.db.flush()
    _record_version(repo, quote)
    repo.commit()
    flash(request, f"Quote {quote.quote_number} saved.", "success")
    return RedirectResponse(f"/quotes/{quote.id}", status_code=303)


# ---------- AI assist: paste text → extract → editable draft in the builder ----------

@router.get("/ai-assist")
def ai_assist_form(
    request: Request,
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    org = repo.db.get(models.Organization, repo.organization_id)
    customers = repo.list(models.Customer, order_by=models.Customer.name)
    return render(request, "quotes/ai_assist.html", user=user, org=org,
                  customers=customers)


@router.post("/ai-assist")
def ai_assist_run(
    request: Request,
    customer_text: str = Form(...),
    customer_id: str = Form(""),
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    try:
        result = ai_task(
            "extract_quote_items",
            {"customer_text": customer_text,
             "catalog_summary": build_catalog_summary(repo)},
            repo=repo,
        )
    except AITaskError as e:
        flash(request, f"AI assist failed: {e}", "error")
        return RedirectResponse("/quotes/ai-assist", status_code=303)

    # Validate the AI's SKUs against the real catalog; pricing always from DB.
    prefill_lines, unresolved = [], []
    for item in result["items"]:
        sku = (item.get("sku") or "").strip()
        catalog_item = None
        if sku:
            catalog_item = repo.first(
                models.CatalogItem, models.CatalogItem.sku == sku,
                models.CatalogItem.is_active == True)  # noqa: E712
        qty = max(float(item.get("quantity") or 1), 0) or 1
        if catalog_item:
            prefill_lines.append({
                "catalog_id": catalog_item.id, "sku": catalog_item.sku or "",
                "desc": catalog_item.name,
                "qty": qty, "price": float(catalog_item.sell_price or 0),
                "matched": True, "notes": item.get("notes", ""),
            })
        else:
            prefill_lines.append({
                "catalog_id": "", "sku": "", "desc": item.get("name_guess", ""),
                "qty": qty, "price": 0.0, "matched": False,
                "notes": item.get("notes", ""),
            })
            unresolved.append(item.get("name_guess", ""))

    ai_notes = {
        "unmatched_notes": result.get("unmatched_notes", ""),
        "unresolved": unresolved,
    }
    sel_customer = int(customer_id) if customer_id.strip() else None
    return render(request, "quotes/builder.html", user=user, quote=None,
                  prefill_lines=prefill_lines, ai_notes=ai_notes,
                  created_by="ai_assisted", preselected_customer=sel_customer,
                  **_builder_context(repo))


@router.get("/{quote_id}")
def detail(
    request: Request,
    quote_id: int,
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    quote = repo.get(models.Quote, quote_id)
    if quote is None:
        raise HTTPException(404)
    return render(request, "quotes/detail.html", user=user, quote=quote,
                  statuses=models.QUOTE_STATUSES)


@router.post("/{quote_id}/status")
def set_status(
    request: Request,
    quote_id: int,
    status: str = Form(...),
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    quote = repo.get(models.Quote, quote_id)
    if quote is None:
        raise HTTPException(404)
    if status not in models.QUOTE_STATUSES:
        raise HTTPException(400, "Invalid status")
    quote.status = status
    quote.updated_at = datetime.utcnow()
    repo.commit()
    flash(request, f"Quote marked {status}.", "success")
    return RedirectResponse(f"/quotes/{quote_id}", status_code=303)


@router.post("/{quote_id}/delete")
def delete(
    request: Request,
    quote_id: int,
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    quote = repo.get(models.Quote, quote_id)
    if quote is None:
        raise HTTPException(404)
    for v in repo.list(models.QuoteVersion, models.QuoteVersion.quote_id == quote.id):
        repo.delete(v)
    repo.delete(quote)
    repo.commit()
    flash(request, "Quote deleted.", "success")
    return RedirectResponse("/quotes", status_code=303)


@router.get("/{quote_id}/pdf")
def download_pdf(
    quote_id: int,
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    quote = repo.get(models.Quote, quote_id)
    if quote is None:
        raise HTTPException(404)
    profile = repo.first(models.BusinessProfile)
    customer = repo.get(models.Customer, quote.customer_id) if quote.customer_id else None
    pdf_bytes = render_quote_pdf(quote, profile, customer)
    return Response(
        pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition":
                 f'attachment; filename="quote-{quote.quote_number}.pdf"'},
    )
