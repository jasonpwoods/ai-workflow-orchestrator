"""Actions: the part that touches the outside world, so the part that is most careful.

Rules that hold here:

* dry run is the default. Nothing leaves this process unless ORCH_LIVE=1 is set.
* every action carries an idempotency key derived from the item fingerprint and the
  action name, so replaying a queue cannot double-page an on-call engineer.
* an action that fails is recorded as failed and does not stop the remaining actions.
* actions are registered by name; rules.yaml routes to those names and can only reach
  what is registered here.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from .schema import ActionResult, Decision, Item

Handler = Callable[[Item, Decision, str], ActionResult]
_REGISTRY: dict[str, Handler] = {}

OUTBOX = Path(os.getenv("ORCH_OUTBOX", Path(__file__).resolve().parents[2] / ".cache" / "outbox.jsonl"))


def live() -> bool:
    return os.getenv("ORCH_LIVE") == "1"


def register(name: str) -> Callable[[Handler], Handler]:
    def decorator(handler: Handler) -> Handler:
        _REGISTRY[name] = handler
        return handler

    return decorator


def registered() -> list[str]:
    return sorted(_REGISTRY)


def _write_outbox(payload: dict) -> None:
    OUTBOX.parent.mkdir(parents=True, exist_ok=True)
    with OUTBOX.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"at": datetime.now(UTC).isoformat(), **payload}, ensure_ascii=False) + "\n")


def _record(action: str, item: Item, decision: Decision, key: str, detail: str) -> ActionResult:
    """Dry run writes the exact payload it would have sent, so the diff is reviewable."""
    _write_outbox(
        {
            "action": action,
            "item_id": item.id,
            "priority": decision.priority,
            "category": decision.classification.category,
            "idempotency_key": key,
            "detail": detail,
            "mode": "live" if live() else "dry_run",
        }
    )
    return ActionResult(action=action, status="sent" if live() else "dry_run", detail=detail, idempotency_key=key)


@register("page_oncall")
def page_oncall(item: Item, decision: Decision, key: str) -> ActionResult:
    detail = f"P{decision.priority} {decision.classification.category}: {item.subject[:80]}"
    if live():
        url = os.getenv("ORCH_PAGER_WEBHOOK")
        if not url:
            return ActionResult(action="page_oncall", status="skipped", detail="ORCH_PAGER_WEBHOOK not set", idempotency_key=key)
        import httpx

        try:
            httpx.post(url, json={"summary": detail, "dedup_key": key, "severity": "critical"}, timeout=15.0).raise_for_status()
        except Exception as error:  # noqa: BLE001 - one failed action must not stop the rest
            return ActionResult(action="page_oncall", status="failed", detail=str(error)[:200], idempotency_key=key)
    return _record("page_oncall", item, decision, key, detail)


@register("open_incident")
def open_incident(item: Item, decision: Decision, key: str) -> ActionResult:
    detail = f"incident for {item.id}: {decision.explain()}"
    return _record("open_incident", item, decision, key, detail)


@register("create_ticket")
def create_ticket(item: Item, decision: Decision, key: str) -> ActionResult:
    detail = f"ticket P{decision.priority} due {decision.sla_due.isoformat()}"
    return _record("create_ticket", item, decision, key, detail)


@register("notify_requester")
def notify_requester(item: Item, decision: Decision, key: str) -> ActionResult:
    who = item.requester or "requester"
    detail = f"acknowledgement to {who}, SLA {decision.sla_due.isoformat()}"
    return _record("notify_requester", item, decision, key, detail)


@register("archive")
def archive(item: Item, decision: Decision, key: str) -> ActionResult:
    return _record("archive", item, decision, key, "filed without action")


def run_actions(item: Item, decision: Decision, seen: set[str] | None = None) -> list[ActionResult]:
    results: list[ActionResult] = []
    for name in decision.actions:
        handler = _REGISTRY.get(name)
        key = f"{item.fingerprint()}:{name}"
        if handler is None:
            results.append(ActionResult(action=name, status="failed", detail="no handler registered", idempotency_key=key))
            continue
        if seen is not None and key in seen:
            results.append(ActionResult(action=name, status="skipped", detail="already executed", idempotency_key=key))
            continue
        try:
            result = handler(item, decision, key)
        except Exception as error:  # noqa: BLE001
            result = ActionResult(action=name, status="failed", detail=str(error)[:200], idempotency_key=key)
        if seen is not None and result.status in {"sent", "dry_run"}:
            seen.add(key)
        results.append(result)
    return results
