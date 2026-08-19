"""AI task interface.

The single entry point is `ai_task(...)`. Each task type is a narrow,
stateless, single-purpose function with a fixed input shape and a fixed JSON
output schema. Nothing else in the application calls the AI provider.

Guarantees enforced here:
- every call is made on behalf of exactly one organization
- every call is logged to ai_task_log (input, raw output, model, status)
- output is schema-checked before it is returned to the caller
- the AI never sees pricing and never touches the database
"""

import json

from app import models
from app.ai.provider import AIProviderError, get_provider
from app.repository import OrgRepo


class AITaskError(Exception):
    pass


_EXTRACT_QUOTE_ITEMS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "sku": {"type": ["string", "null"]},
                    "name_guess": {"type": "string"},
                    "quantity": {"type": "number"},
                    "notes": {"type": "string"},
                },
                "required": ["sku", "name_guess", "quantity", "notes"],
                "additionalProperties": False,
            },
        },
        "unmatched_notes": {"type": "string"},
    },
    "required": ["items", "unmatched_notes"],
    "additionalProperties": False,
}

_IMPORT_CATALOG_ITEMS_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "sku": {"type": ["string", "null"]},
                    "cost_price": {"type": ["number", "null"]},
                    "sell_price": {"type": ["number", "null"]},
                    "category": {"type": ["string", "null"]},
                    "confidence_notes": {"type": "string"},
                },
                "required": ["name", "sku", "cost_price", "sell_price",
                             "category", "confidence_notes"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

_CLASSIFY_QUOTE_REQUEST_SCHEMA = {
    "type": "object",
    "properties": {
        "is_quote_request": {"type": "boolean"},
        "confidence": {"type": "number"},
    },
    "required": ["is_quote_request", "confidence"],
    "additionalProperties": False,
}

_TASKS = {
    "extract_quote_items": {
        "system": (
            "You extract requested line items from a customer's message for a "
            "quoting system. You are given the company's product catalog "
            "(sku, name, description, category — no pricing). Map each "
            "requested item to a catalog sku when confident; otherwise set sku "
            "to null and describe it in name_guess. Never invent skus. Never "
            "output prices. Put anything you could not map into "
            "unmatched_notes."
        ),
        "schema": _EXTRACT_QUOTE_ITEMS_SCHEMA,
        "required_input": ("customer_text", "catalog_summary"),
    },
    "import_catalog_items": {
        "system": (
            "You parse a raw pasted product list into structured catalog "
            "items for review by a human. Follow the user's parsing/pricing "
            "instructions if given. Use null for anything not present in the "
            "source text — never invent skus, prices, or categories. Note any "
            "uncertainty in confidence_notes."
        ),
        "schema": _IMPORT_CATALOG_ITEMS_SCHEMA,
        "required_input": ("raw_pasted_text", "user_instructions"),
    },
    # Phase 2 (email intake). Defined so the interface is complete; no intake
    # channel calls it yet.
    "classify_quote_request": {
        "system": (
            "Classify whether an email is a request for a price quote. Return "
            "is_quote_request and a 0-1 confidence. Do nothing else."
        ),
        "schema": _CLASSIFY_QUOTE_REQUEST_SCHEMA,
        "required_input": ("email_text", "email_subject"),
    },
}


def _validate(value, schema, path="$"):
    t = schema.get("type")
    types = t if isinstance(t, list) else [t]
    ok = False
    for typ in types:
        if typ == "object" and isinstance(value, dict):
            ok = True
            for key in schema.get("required", []):
                if key not in value:
                    raise AITaskError(f"AI output missing {path}.{key}")
            props = schema.get("properties", {})
            for key, sub in props.items():
                if key in value:
                    _validate(value[key], sub, f"{path}.{key}")
            if schema.get("additionalProperties") is False:
                extra = set(value) - set(props)
                if extra:
                    raise AITaskError(f"AI output has unexpected keys at {path}: {extra}")
        elif typ == "array" and isinstance(value, list):
            ok = True
            for i, item in enumerate(value):
                _validate(item, schema["items"], f"{path}[{i}]")
        elif typ == "string" and isinstance(value, str):
            ok = True
        elif typ == "number" and isinstance(value, (int, float)) and not isinstance(value, bool):
            ok = True
        elif typ == "boolean" and isinstance(value, bool):
            ok = True
        elif typ == "null" and value is None:
            ok = True
    if not ok:
        raise AITaskError(f"AI output at {path} is not of type {t}")


def _log(repo: OrgRepo, task_type: str, payload: dict, output: dict | None,
         model_used: str, status: str,
         linked_entity_type: str | None = None, linked_entity_id: int | None = None):
    repo.add(models.AITaskLog(
        task_type=task_type,
        input_payload_json=json.dumps(payload, default=str),
        output_payload_json=json.dumps(output, default=str) if output is not None else "",
        model_used=model_used,
        status=status,
        linked_entity_type=linked_entity_type,
        linked_entity_id=linked_entity_id,
    ))
    repo.commit()


def ai_task(task_type: str, payload: dict, *, repo: OrgRepo) -> dict:
    """Run one AI task for one organization. Returns validated JSON output.

    Raises AITaskError on provider failure or schema-invalid output; the
    attempt is logged either way.
    """
    if task_type not in _TASKS:
        raise AITaskError(f"Unknown AI task type: {task_type}")
    task = _TASKS[task_type]
    missing = [k for k in task["required_input"] if k not in payload]
    if missing:
        raise AITaskError(f"Missing input fields for {task_type}: {missing}")

    org = repo.db.get(models.Organization, repo.organization_id)
    if org is None or not org.ai_enabled:
        raise AITaskError("AI assist is disabled for this organization")

    provider = get_provider()
    model_used = getattr(provider, "model", provider.name)

    try:
        output = provider.complete_structured(
            system=task["system"],
            user=json.dumps(payload),
            json_schema=task["schema"],
        )
    except AIProviderError as e:
        _log(repo, task_type, payload, {"error": str(e)}, model_used, "error")
        raise AITaskError(str(e)) from e

    try:
        _validate(output, task["schema"])
    except AITaskError:
        _log(repo, task_type, payload, output, model_used, "rejected")
        raise

    _log(repo, task_type, payload, output, model_used, "success")
    return output


def build_catalog_summary(repo: OrgRepo) -> list[dict]:
    """Catalog context for extract_quote_items — this org only, no pricing."""
    items = repo.list(models.CatalogItem, models.CatalogItem.is_active == True)  # noqa: E712
    return [
        {"sku": i.sku, "name": i.name, "description": i.description, "category": i.category}
        for i in items
    ]
