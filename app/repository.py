"""Tenant-scoped data access layer.

All application code reads/writes tenant-owned tables through OrgRepo, which
is bound to a single organization_id. Every SELECT is filtered by
organization_id and every INSERT is stamped with it here — a route handler
that forgets a filter cannot leak another tenant's data, because there is no
unscoped query path.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models


# Tables OrgRepo is allowed to touch. Organization/User are account-level and
# handled separately in auth code.
_SCOPED_MODELS = (
    models.BusinessProfile,
    models.CatalogItem,
    models.Customer,
    models.Quote,
    models.QuoteItem,
    models.QuoteVersion,
    models.AISettings,
    models.AITaskLog,
)


class OrgRepo:
    def __init__(self, db: Session, organization_id: int):
        self.db = db
        self.organization_id = organization_id

    def _check(self, model):
        if model not in _SCOPED_MODELS:
            raise ValueError(f"{model.__name__} is not an org-scoped model")

    def select(self, model):
        """A SELECT statement pre-filtered to this organization."""
        self._check(model)
        return select(model).where(model.organization_id == self.organization_id)

    def list(self, model, *where, order_by=None):
        stmt = self.select(model)
        if where:
            stmt = stmt.where(*where)
        if order_by is not None:
            stmt = stmt.order_by(order_by)
        return list(self.db.scalars(stmt))

    def get(self, model, id: int):
        """Fetch by primary key, returning None if it belongs to another org."""
        self._check(model)
        obj = self.db.get(model, id)
        if obj is None or obj.organization_id != self.organization_id:
            return None
        return obj

    def first(self, model, *where):
        stmt = self.select(model)
        if where:
            stmt = stmt.where(*where)
        return self.db.scalars(stmt).first()

    def add(self, obj):
        """Stamp the object with this org and stage it for insert."""
        self._check(type(obj))
        obj.organization_id = self.organization_id
        self.db.add(obj)
        return obj

    def delete(self, obj):
        self._check(type(obj))
        if obj.organization_id != self.organization_id:
            raise ValueError("cross-tenant delete blocked")
        self.db.delete(obj)

    def commit(self):
        self.db.commit()


def next_quote_number(repo: OrgRepo) -> str:
    """Sequential per-organization quote number: Q-0001, Q-0002, ..."""
    quotes = repo.list(models.Quote)
    max_n = 0
    for q in quotes:
        try:
            max_n = max(max_n, int(q.quote_number.split("-")[-1]))
        except ValueError:
            continue
    return f"Q-{max_n + 1:04d}"
