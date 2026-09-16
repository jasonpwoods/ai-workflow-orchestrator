"""Model providers with the operational parts attached.

Same contract for every provider: text in, text plus Usage out. Retries cover
transport failures and retryable status codes with exponential backoff and jitter;
token counts come from the provider when it reports them and from a local count when
it does not; cost is computed from a published price table.

The offline provider is a real classifier, not a stub: weighted keyword evidence over
the label vocabulary, which gives the pipeline a deterministic baseline that runs in
CI with no key and gives the hosted model something to beat.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import time
from dataclasses import dataclass
from typing import Protocol

WORD = re.compile(r"[a-z0-9]+")

PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-sonnet-4": (3.00, 15.00),
    "rules-v1": (0.0, 0.0),
}

# Evidence used by the offline classifier. Kept next to the prompt examples on purpose:
# when the hosted model is unavailable the system still has to pick a defensible label.
EVIDENCE: dict[str, dict[str, float]] = {
    "outage": {
        "down": 3, "outage": 3, "unavailable": 2, "503": 2, "timeout": 1.5, "cannot access": 2, "offline": 1.5,
        "cannot complete": 2.5, "every customer": 2.5, "customers cannot": 2.5, "nothing loads": 2.5, "failing": 1.5,
    },
    "security": {"breach": 3, "phishing": 3, "credential": 2.5, "unauthorized": 2.5, "malware": 3, "cve": 2, "leaked": 2},
    "bug": {"error": 2, "exception": 2.5, "broken": 2, "crash": 2.5, "stack trace": 3, "wrong": 1.2, "bug": 3},
    "access_request": {"access": 2.5, "permission": 2.5, "account": 1.5, "licence": 2, "license": 2, "onboard": 2, "reset": 1.5},
    "billing": {"invoice": 3, "billing": 3, "charge": 2, "refund": 2.5, "payment": 2.5, "purchase order": 2},
    "how_to": {"how do i": 3, "how to": 2.5, "question": 1.5, "where is": 2, "documentation": 1.5, "guide": 1.5},
    "feature_request": {"feature": 2.5, "would be nice": 3, "request": 1.2, "enhancement": 3, "roadmap": 2},
    "spam": {"unsubscribe": 3, "webinar": 2.5, "promotion": 2.5, "free trial": 2.5, "newsletter": 2},
}

URGENCY_EVIDENCE: dict[str, dict[str, float]] = {
    "critical": {"down": 2.5, "outage": 2.5, "all users": 2, "data loss": 3, "breach": 3, "urgent": 1.5, "immediately": 1.5},
    "high": {"blocked": 2, "cannot work": 2, "deadline": 1.5, "escalate": 1.5, "sla": 1.5},
    "low": {"whenever": 2, "no rush": 2.5, "nice to have": 2.5, "unsubscribe": 2},
}


def count_tokens(text: str) -> int:
    try:
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text))
    except Exception:  # noqa: BLE001
        return max(1, round(len(text) / 4))


def estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    for key, (prompt_price, completion_price) in PRICES.items():
        if model.startswith(key):
            return round((prompt_tokens * prompt_price + completion_tokens * completion_price) / 1_000_000, 6)
    return 0.0


@dataclass
class Completion:
    text: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    provider: str
    model: str


class Provider(Protocol):
    name: str
    model: str

    def complete(self, *, system: str, user: str) -> Completion: ...


class OfflineClassifier:
    """Deterministic weighted-evidence classifier. No key, no network, same answer every run."""

    name = "offline"

    def __init__(self, model: str = "rules-v1") -> None:
        self.model = model

    @staticmethod
    def _score(text: str, evidence: dict[str, dict[str, float]]) -> dict[str, float]:
        """Whole-word matching only: substring counting scored "download" as an outage ("down")."""
        lowered = text.lower()
        scores: dict[str, float] = {}
        for label, terms in evidence.items():
            total = 0.0
            for term, weight in terms.items():
                # Whole word, tolerating regular inflection ("crash" matches "crashes"),
                # without letting "down" match "download".
                hits = len(re.findall(rf"\b{re.escape(term)}(?:s|es|ed|ing)?\b", lowered))
                if hits:
                    total += weight * (1 + math.log(hits))
            scores[label] = total
        return scores

    def complete(self, *, system: str, user: str) -> Completion:
        started = time.perf_counter()
        category_scores = self._score(user, EVIDENCE)
        best_category = max(category_scores, key=lambda key: category_scores[key])
        ranked = sorted(category_scores.values(), reverse=True)
        margin = ranked[0] - (ranked[1] if len(ranked) > 1 else 0.0)
        if category_scores[best_category] == 0.0:
            best_category = "how_to"  # an unlabelled request is a question until proven otherwise

        urgency_scores = self._score(user, URGENCY_EVIDENCE)
        best_urgency = max(urgency_scores, key=lambda key: urgency_scores[key])
        if urgency_scores[best_urgency] == 0.0:
            best_urgency = "normal"

        # Confidence from separation between the top two labels, capped into a sane band.
        confidence = round(min(0.95, 0.4 + 0.12 * margin), 3) if category_scores[best_category] else 0.35
        payload = {
            "category": best_category,
            "urgency": best_urgency,
            "confidence": confidence,
            "rationale": f"weighted keyword evidence: {best_category} scored {category_scores[best_category]:.1f}",
        }
        text = json.dumps(payload)
        return Completion(
            text=text,
            prompt_tokens=count_tokens(system + user),
            completion_tokens=count_tokens(text),
            cost_usd=0.0,
            latency_ms=int((time.perf_counter() - started) * 1000),
            provider=self.name,
            model=self.model,
        )


class _HttpProvider:
    name = "http"

    def __init__(self, model: str, timeout: float = 30.0, max_retries: int = 3) -> None:
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    def _post(self, url: str, headers: dict[str, str], payload: dict) -> dict:
        import httpx

        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = httpx.post(url, headers=headers, json=payload, timeout=self.timeout)
                if response.status_code in (429, 500, 502, 503, 504):
                    raise httpx.HTTPStatusError("retryable", request=response.request, response=response)
                response.raise_for_status()
                return response.json()
            except Exception as error:  # noqa: BLE001
                last = error
                if attempt == self.max_retries - 1:
                    break
                time.sleep((2**attempt) * 0.5 + random.uniform(0, 0.25))
        raise RuntimeError(f"{self.name} request failed after {self.max_retries} attempts: {last}")


class OpenAIProvider(_HttpProvider):
    name = "openai"

    def complete(self, *, system: str, user: str) -> Completion:
        started = time.perf_counter()
        body = self._post(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            {
                "model": self.model,
                "temperature": 0,
                "response_format": {"type": "json_object"},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            },
        )
        text = body["choices"][0]["message"]["content"]
        usage = body.get("usage", {})
        prompt_tokens = int(usage.get("prompt_tokens", count_tokens(system + user)))
        completion_tokens = int(usage.get("completion_tokens", count_tokens(text)))
        return Completion(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=estimate_cost(self.model, prompt_tokens, completion_tokens),
            latency_ms=int((time.perf_counter() - started) * 1000),
            provider=self.name,
            model=self.model,
        )


class AnthropicProvider(_HttpProvider):
    name = "anthropic"

    def complete(self, *, system: str, user: str) -> Completion:
        started = time.perf_counter()
        body = self._post(
            "https://api.anthropic.com/v1/messages",
            {"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01"},
            {
                "model": self.model,
                "max_tokens": 500,
                "temperature": 0,
                "system": system,
                "messages": [{"role": "user", "content": user + "\n\nReply with JSON only."}],
            },
        )
        text = "".join(part.get("text", "") for part in body.get("content", []))
        usage = body.get("usage", {})
        prompt_tokens = int(usage.get("input_tokens", count_tokens(system + user)))
        completion_tokens = int(usage.get("output_tokens", count_tokens(text)))
        return Completion(
            text=text,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=estimate_cost(self.model, prompt_tokens, completion_tokens),
            latency_ms=int((time.perf_counter() - started) * 1000),
            provider=self.name,
            model=self.model,
        )


def build_provider(name: str | None = None, model: str | None = None) -> Provider:
    name = (name or os.getenv("ORCH_PROVIDER") or "").lower()
    if name == "openai" and os.getenv("OPENAI_API_KEY"):
        return OpenAIProvider(model or os.getenv("ORCH_MODEL", "gpt-4o-mini"))
    if name == "anthropic" and os.getenv("ANTHROPIC_API_KEY"):
        return AnthropicProvider(model or os.getenv("ORCH_MODEL", "claude-3-5-haiku"))
    if not name:
        if os.getenv("OPENAI_API_KEY"):
            return OpenAIProvider(model or os.getenv("ORCH_MODEL", "gpt-4o-mini"))
        if os.getenv("ANTHROPIC_API_KEY"):
            return AnthropicProvider(model or os.getenv("ORCH_MODEL", "claude-3-5-haiku"))
    return OfflineClassifier()
