from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import models
from app.auth import get_current_user, get_repo
from app.repository import OrgRepo
from app.templating import flash, render

router = APIRouter(prefix="/customers")


@router.get("")
def list_customers(
    request: Request,
    q: str = "",
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    customers = repo.list(models.Customer, order_by=models.Customer.name)
    if q:
        ql = q.lower()
        customers = [
            c for c in customers
            if ql in c.name.lower() or ql in c.company.lower() or ql in c.email.lower()
        ]
    return render(request, "customers/list.html", user=user, customers=customers, q=q)


@router.get("/new")
def new_form(request: Request, user: models.User = Depends(get_current_user)):
    return render(request, "customers/form.html", user=user, customer=None)


@router.get("/{customer_id}/edit")
def edit_form(
    request: Request,
    customer_id: int,
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    customer = repo.get(models.Customer, customer_id)
    if customer is None:
        raise HTTPException(404)
    return render(request, "customers/form.html", user=user, customer=customer)


@router.post("/save")
def save(
    request: Request,
    customer_id: int | None = Form(None),
    name: str = Form(...),
    company: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    address: str = Form(""),
    notes: str = Form(""),
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    if customer_id:
        customer = repo.get(models.Customer, customer_id)
        if customer is None:
            raise HTTPException(404)
    else:
        customer = repo.add(models.Customer(name=""))
    customer.name = name.strip()
    customer.company = company.strip()
    customer.phone = phone.strip()
    customer.email = email.strip()
    customer.address = address.strip()
    customer.notes = notes.strip()
    repo.commit()
    flash(request, "Customer saved.", "success")
    return RedirectResponse("/customers", status_code=303)


@router.post("/{customer_id}/delete")
def delete(
    request: Request,
    customer_id: int,
    user: models.User = Depends(get_current_user),
    repo: OrgRepo = Depends(get_repo),
):
    customer = repo.get(models.Customer, customer_id)
    if customer is None:
        raise HTTPException(404)
    in_use = repo.first(models.Quote, models.Quote.customer_id == customer.id)
    if in_use:
        flash(request, "Customer has quotes and can’t be deleted.", "error")
    else:
        repo.delete(customer)
        repo.commit()
        flash(request, "Customer deleted.", "success")
    return RedirectResponse("/customers", status_code=303)
