"""AI provider abstraction — bring-your-own-AI per tenant.

Each organization configures its own provider (its own OpenAI/Anthropic/
Google account, or its own local/external server) under Settings → AI
provider. This module speaks each vendor's standard API:

- OpenAI:            Chat Completions + Structured Outputs (json_schema)
- Anthropic:         Messages API with a forced tool call (input_schema)
- Google Gemini:     generateContent with JSON response mode
- OpenAI-compatible: Chat Completions against any base URL (Ollama,
                     LM Studio, vLLM, LocalAI, proxies, …) — prompt-embedded
                     schema, since json_schema support varies by server

Everything above this module talks to `AIProvider.complete_structured()`.
Providers return raw JSON only. They never see the database, prices, or any
other tenant, and they never perform side effects. Output is additionally
schema-validated by the caller (app/ai/tasks.py) before use.
"""

import json
import re
from abc import ABC, abstractmethod

import httpx

from app import config, models
from app.secret_store import decrypt_secret

_TIMEOUT = 90


class AIProviderError(Exception):
    pass


class AIProvider(ABC):
    name: str = "base"
    model: str = ""

    @abstractmethod
    def complete_structured(self, *, system: str, user: str, json_schema: dict) -> dict:
        """Return a dict conforming to json_schema, or raise AIProviderError."""


def _post_json(url: str, headers: dict, body: dict) -> dict:
    try:
        resp = httpx.post(url, headers=headers, json=body, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        detail = e.response.text[:300]
        raise AIProviderError(
            f"provider returned HTTP {e.response.status_code}: {detail}") from e
    except httpx.HTTPError as e:
        raise AIProviderError(f"could not reach provider: {e}") from e
    except json.JSONDecodeError as e:
        raise AIProviderError(f"provider returned non-JSON response: {e}") from e


def _parse_json_text(text: str) -> dict:
    """Parse model output as JSON, tolerating markdown code fences."""
    text = text.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise AIProviderError(f"model did not return valid JSON: {e}") from e


def _schema_prompt(system: str, json_schema: dict) -> str:
    return (
        f"{system}\n\nRespond with a single JSON object (no prose, no markdown) "
        f"that conforms exactly to this JSON Schema:\n{json.dumps(json_schema)}"
    )


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str, base_url: str = ""):
        if not api_key:
            raise AIProviderError("OpenAI API key is not configured")
        self.api_key = api_key
        self.model = model or "gpt-4o-mini"
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")

    def complete_structured(self, *, system: str, user: str, json_schema: dict) -> dict:
        data = _post_json(
            f"{self.base_url}/chat/completions",
            {"Authorization": f"Bearer {self.api_key}"},
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "task_output", "strict": True,
                                    "schema": json_schema},
                },
            },
        )
        try:
            return _parse_json_text(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as e:
            raise AIProviderError(f"unexpected OpenAI response shape: {e}") from e


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, base_url: str = ""):
        if not api_key:
            raise AIProviderError("Anthropic API key is not configured")
        self.api_key = api_key
        self.model = model or "claude-sonnet-5"
        self.base_url = (base_url or "https://api.anthropic.com").rstrip("/")

    def complete_structured(self, *, system: str, user: str, json_schema: dict) -> dict:
        data = _post_json(
            f"{self.base_url}/v1/messages",
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            {
                "model": self.model,
                "max_tokens": 4096,
                "system": system,
                "messages": [{"role": "user", "content": user}],
                "tools": [{
                    "name": "task_output",
                    "description": "Report the structured result of the task.",
                    "input_schema": json_schema,
                }],
                "tool_choice": {"type": "tool", "name": "task_output"},
            },
        )
        for block in data.get("content", []):
            if block.get("type") == "tool_use":
                return block.get("input", {})
        raise AIProviderError("Anthropic response contained no tool_use block")


class GoogleGeminiProvider(AIProvider):
    name = "google"

    def __init__(self, api_key: str, model: str, base_url: str = ""):
        if not api_key:
            raise AIProviderError("Google API key is not configured")
        self.api_key = api_key
        self.model = model or "gemini-2.0-flash"
        self.base_url = (base_url or
                         "https://generativelanguage.googleapis.com").rstrip("/")

    def complete_structured(self, *, system: str, user: str, json_schema: dict) -> dict:
        data = _post_json(
            f"{self.base_url}/v1beta/models/{self.model}:generateContent",
            {"x-goog-api-key": self.api_key},
            {
                "system_instruction": {"parts": [{"text": _schema_prompt(system, json_schema)}]},
                "contents": [{"role": "user", "parts": [{"text": user}]}],
                "generationConfig": {"responseMimeType": "application/json"},
            },
        )
        try:
            text = data["candidates"][0]["content"]["parts"][0]["text"]
        except (KeyError, IndexError, TypeError) as e:
            raise AIProviderError(f"unexpected Gemini response shape: {e}") from e
        return _parse_json_text(text)


class OpenAICompatibleProvider(AIProvider):
    """Any server speaking the OpenAI chat-completions format: Ollama,
    LM Studio, vLLM, LocalAI, gateways/proxies, or a hosted vendor with an
    OpenAI-compatible endpoint. Schema goes in the prompt because native
    json_schema support varies by server; output is fence-tolerant parsed
    and then validated upstream."""

    name = "openai_compatible"

    def __init__(self, api_key: str, model: str, base_url: str):
        if not base_url:
            raise AIProviderError("Base URL is required for a local/custom AI server")
        self.api_key = api_key
        self.model = model or "llama3.1"
        self.base_url = base_url.rstrip("/")

    def complete_structured(self, *, system: str, user: str, json_schema: dict) -> dict:
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data = _post_json(
            f"{self.base_url}/chat/completions",
            headers,
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": _schema_prompt(system, json_schema)},
                    {"role": "user", "content": user},
                ],
            },
        )
        try:
            return _parse_json_text(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as e:
            raise AIProviderError(f"unexpected response shape from server: {e}") from e


class MockProvider(AIProvider):
    """Deterministic offline provider for dev and tests.

    Does a naive line-based parse so the AI-assist UI is exercisable without
    an API key. Quality is intentionally basic — it exists so the app never
    depends on an external service to function.
    """

    name = "mock"
    model = "mock"

    def complete_structured(self, *, system: str, user: str, json_schema: dict) -> dict:
        payload = json.loads(user)
        if "customer_text" in payload:
            return self._extract_quote_items(payload)
        if "raw_pasted_text" in payload:
            return self._import_catalog_items(payload)
        if "email_text" in payload:
            return {"is_quote_request": False, "confidence": 0.0}
        if "ping" in payload:
            return {"ok": True}
        raise AIProviderError("MockProvider: unrecognized payload shape")

    def _extract_quote_items(self, payload: dict) -> dict:
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


def build_provider(settings: "models.AISettings | None") -> AIProvider:
    """Resolve the provider for one organization.

    An org's own configuration always wins. With no org configuration, the
    server-wide AI_PROVIDER env fallback applies (mock for dev/tests, openai
    for a single-tenant install with a shared key, none to require per-tenant
    setup — the recommended multi-tenant default).
    """
    if settings is not None:
        api_key = decrypt_secret(settings.api_key_encrypted)
        classes = {
            "openai": OpenAIProvider,
            "anthropic": AnthropicProvider,
            "google": GoogleGeminiProvider,
            "openai_compatible": OpenAICompatibleProvider,
        }
        cls = classes.get(settings.provider)
        if cls is None:
            raise AIProviderError(f"Unknown AI provider: {settings.provider}")
        return cls(api_key, settings.model, settings.base_url)

    if config.AI_PROVIDER == "mock":
        return MockProvider()
    if config.AI_PROVIDER == "openai" and config.OPENAI_API_KEY:
        return OpenAIProvider(config.OPENAI_API_KEY, config.OPENAI_MODEL)
    raise AIProviderError(
        "No AI provider configured for your organization. An admin can "
        "connect one under Settings → AI provider."
    )
