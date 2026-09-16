"""Typed contracts for the triage pipeline.

One item shape in, one decision shape out, one audit record per item. Anything a
model produces is validated into these before it can trigger an action - a model
that invents a category or an urgency level fails here, not at the webhook.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator

Category = Literal[
    "outage",
    "bug",
    "access_request",
    "billing",
    "how_to",
    "feature_request",
    "security",
    "spam",
]
Urgency = Literal["critical", "high", "normal", "low"]
Source = Literal["ticket", "email", "log"]


class Item(BaseModel):
    """A normalised unit of work, whatever it arrived as."""

    id: str
    source: Source
    subject: str = ""
    body: str
    received_at: datetime
    requester: str = ""
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("body")
    @classmethod
    def _body_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("body must not be empty")
        return value.strip()

    @property
    def text(self) -> str:
        return f"{self.subject}\n{self.body}".strip()

    def fingerprint(self) -> str:
        """Idempotency key: the same content never triggers the same action twice."""
        material = f"{self.source}|{self.subject}|{' '.join(self.body.split())}".lower()
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


class Classification(BaseModel):
    category: Category
    urgency: Urgency
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = ""
    entities: dict[str, str] = Field(default_factory=dict)
    classifier: str = "rules-v1"
    prompt_version: str = "unversioned"


class RuleHit(BaseModel):
    rule_id: str
    points: int
    why: str


class Decision(BaseModel):
    item_id: str
    classification: Classification
    priority: int = Field(ge=1, le=5)
    score: int
    sla_due: datetime
    rule_hits: list[RuleHit] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    needs_human: bool = False

    def explain(self) -> str:
        parts = [f"{hit.rule_id} ({hit.points:+d}: {hit.why})" for hit in self.rule_hits]
        return f"P{self.priority} score {self.score} <- " + "; ".join(parts)


class ActionResult(BaseModel):
    action: str
    status: Literal["sent", "dry_run", "skipped", "failed"]
    detail: str = ""
    idempotency_key: str = ""


class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    provider: str = "offline"
    model: str = "rules-v1"


class AuditRecord(BaseModel):
    """What ran, why, and what it did. One JSON line per item."""

    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    item_id: str
    fingerprint: str
    decision: Decision
    results: list[ActionResult] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    duplicate: bool = False


def sla_due(received_at: datetime, hours: int) -> datetime:
    return received_at + timedelta(hours=hours)
