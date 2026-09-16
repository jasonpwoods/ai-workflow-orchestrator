"""The orchestrator: ingest -> classify -> apply rules -> act -> audit.

Operational behaviour that makes this a pipeline rather than a script:

* idempotency - a repeated item is recognised by fingerprint and its actions are skipped;
* isolation - one item that fails classification is recorded and the batch continues;
* audit - one JSON line per item with the decision, the rules that fired and the results;
* metrics - counts, latency percentiles and spend, exposed for a dashboard or a log line.
"""

from __future__ import annotations

import json
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from .actions import live, run_actions
from .classify import Classifier
from .prioritize import Rules
from .schema import AuditRecord, Classification, Decision, Item, Usage

AUDIT_PATH = Path(__file__).resolve().parents[2] / ".cache" / "audit.jsonl"


class Orchestrator:
    def __init__(
        self,
        classifier: Classifier | None = None,
        rules: Rules | None = None,
        audit_path: Path | None = None,
    ) -> None:
        self.classifier = classifier or Classifier()
        self.rules = rules or Rules()
        self.audit_path = audit_path or AUDIT_PATH
        self.seen_actions: set[str] = set()
        self.seen_items: set[str] = set()
        self.latencies: list[int] = []
        self.counts: Counter[str] = Counter()
        self.cost_usd = 0.0
        self.tokens = 0

    def process(self, item: Item) -> AuditRecord:
        started = time.perf_counter()
        fingerprint = item.fingerprint()
        duplicate = fingerprint in self.seen_items
        self.seen_items.add(fingerprint)

        try:
            classification, usage = self.classifier.classify(item)
        except Exception as error:  # noqa: BLE001 - a failed classification is a human's problem, not a crash
            classification = Classification(
                category="how_to",
                urgency="normal",
                confidence=0.0,
                rationale=f"classification failed: {error}"[:400],
                classifier="fallback",
            )
            usage = Usage()
            self.counts["classification_failures"] += 1

        decision: Decision = self.rules.decide(item, classification)
        if duplicate:
            decision.actions = []
        results = run_actions(item, decision, self.seen_actions)

        record = AuditRecord(
            item_id=item.id,
            fingerprint=fingerprint,
            decision=decision,
            results=results,
            usage=usage,
            duplicate=duplicate,
        )
        self._record(record, int((time.perf_counter() - started) * 1000))
        return record

    def process_batch(self, items: list[Item]) -> list[AuditRecord]:
        return [self.process(item) for item in items]

    def _record(self, record: AuditRecord, latency_ms: int) -> None:
        self.latencies.append(latency_ms)
        self.counts["items"] += 1
        self.counts[f"priority_{record.decision.priority}"] += 1
        self.counts[f"category_{record.decision.classification.category}"] += 1
        if record.duplicate:
            self.counts["duplicates"] += 1
        if record.decision.needs_human:
            self.counts["needs_human"] += 1
        self.tokens += record.usage.prompt_tokens + record.usage.completion_tokens
        self.cost_usd = round(self.cost_usd + record.usage.cost_usd, 6)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json() + "\n")

    def metrics(self) -> dict:
        ordered = sorted(self.latencies)

        def percentile(p: float) -> int:
            if not ordered:
                return 0
            return ordered[min(len(ordered) - 1, int(round(p * (len(ordered) - 1))))]

        return {
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "mode": "live" if live() else "dry_run",
            "items": self.counts["items"],
            "duplicates": self.counts["duplicates"],
            "needs_human": self.counts["needs_human"],
            "classification_failures": self.counts["classification_failures"],
            "by_priority": {k.split("_")[1]: v for k, v in self.counts.items() if k.startswith("priority_")},
            "by_category": {k.split("_", 1)[1]: v for k, v in self.counts.items() if k.startswith("category_")},
            "tokens": self.tokens,
            "cost_usd": self.cost_usd,
            "latency_ms_p50": percentile(0.5),
            "latency_ms_p95": percentile(0.95),
            "provider": f"{self.classifier.provider.name}:{self.classifier.provider.model}",
            "prompt_version": self.classifier.prompt_version,
            "rules_version": self.rules.version,
        }

    def metrics_json(self) -> str:
        return json.dumps(self.metrics(), indent=2)
