"""AI provider abstraction.

Everything above this module talks to `AIProvider.complete_structured()`;
swapping OpenAI for another vendor (e.g. Anthropic) means adding one class
here and changing the AI_PROVIDER env var — no calling code changes.

Providers return raw JSON only. They never see the database, prices, or any
other tenant, and they never perform side effects.
"""

import json
from abc import ABC, abstractmethod

import httpx

from app import config


class AIProviderError(Exception):
    pass


class AIProvider(ABC):
    name: str = "base"

    @abstractmethod
    def complete_structured(self, *, system: str, user: str, json_schema: dict) -> dict:
        """Return a dict conforming to json_schema, or raise AIProviderError."""


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise AIProviderError("OPENAI_API_KEY is not set")
        self.api_key = api_key
        self.model = model

    def complete_structured(self, *, system: str, user: str, json_schema: dict) -> dict:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "task_output",
                    "strict": True,
                    "schema": json_schema,
                },
            },
        }
        try:
            resp = httpx.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=body,
                timeout=60,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return json.loads(content)
        except (httpx.HTTPError, KeyError, IndexError, json.JSONDecodeError) as e:
            raise AIProviderError(f"OpenAI call failed: {e}") from e


class MockProvider(AIProvider):
    """Deterministic offline provider for dev and tests.

    Does a naive line-based parse so the AI-assist UI is exercisable without
    an API key. Quality is intentionally basic — it exists so the app never
    depends on an external service to function.
    """

    name = "mock"

    def complete_structured(self, *, system: str, user: str, json_schema: dict) -> dict:
        payload = json.loads(user)
        if "customer_text" in payload:
            return self._extract_quote_items(payload)
        if "raw_pasted_text" in payload:
            return self._import_catalog_items(payload)
        if "email_text" in payload:
            return {"is_quote_request": False, "confidence": 0.0}
        raise AIProviderError("MockProvider: unrecognized payload shape")

    def _extract_quote_items(self, payload: dict) -> dict:
        import re

        catalog = payload.get("catalog_summary", [])
        text = payload.get("customer_text", "")
        items, unmatched = [], []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            qty = 1
            m = re.match(r"^(\d+)\s*[xX]?\s+(.*)$", line)
            rest = line
            if m:
                qty, rest = int(m.group(1)), m.group(2)
            match = None
            rest_low = rest.lower()
            for c in catalog:
                name = (c.get("name") or "").lower()
                sku = (c.get("sku") or "").lower()
                if (sku and sku in rest_low) or (name and (name in rest_low or rest_low in name)):
                    match = c
                    break
            if match:
                items.append(
                    {"sku": match.get("sku"), "name_guess": match.get("name") or rest,
                     "quantity": qty, "notes": ""}
                )
            else:
                items.append({"sku": None, "name_guess": rest, "quantity": qty, "notes": ""})
                unmatched.append(rest)
        return {
            "items": items,
            "unmatched_notes": "; ".join(unmatched) if unmatched else "",
        }

    def _import_catalog_items(self, payload: dict) -> dict:
        import re

        items = []
        for line in payload.get("raw_pasted_text", "").splitlines():
            line = line.strip()
            if not line:
                continue
            parts = [p.strip() for p in re.split(r"[,\t|]", line) if p.strip()]
            if not parts:
                continue
            name, sku, cost, sell = parts[0], None, None, None
            prices = []
            for p in parts[1:]:
                pm = re.match(r"^\$?(\d+(?:\.\d{1,2})?)$", p)
                if pm:
                    prices.append(float(pm.group(1)))
                elif sku is None:
                    sku = p
            if len(prices) >= 2:
                cost, sell = prices[0], prices[1]
            elif len(prices) == 1:
                cost = prices[0]
            items.append(
                {"name": name, "sku": sku, "cost_price": cost, "sell_price": sell,
                 "category": None, "confidence_notes": "parsed offline (mock provider)"}
            )
        return {"items": items}


def get_provider() -> AIProvider:
    if config.AI_PROVIDER == "openai":
        return OpenAIProvider(config.OPENAI_API_KEY, config.OPENAI_MODEL)
    return MockProvider()
