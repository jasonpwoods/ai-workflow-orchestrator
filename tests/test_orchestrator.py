"""Behaviour tests. No keys, no network, no writes outside tmp_path."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from orchestrator.actions import registered, run_actions
from orchestrator.classify import Classifier, parse_json
from orchestrator.ingest import from_emails, from_logs, from_tickets, load_all
from orchestrator.pipeline import Orchestrator
from orchestrator.prioritize import Rules
from orchestrator.schema import Item

SAMPLES = Path(__file__).resolve().parents[1] / "sample_data"


def make_item(body: str, subject: str = "", requester: str = "") -> Item:
    return Item(id="t1", source="ticket", subject=subject, body=body, received_at=datetime.now(UTC), requester=requester)


@pytest.fixture()
def orchestrator(tmp_path: Path) -> Orchestrator:
    return Orchestrator(audit_path=tmp_path / "audit.jsonl")


def test_ingest_reads_all_three_sources():
    assert len(from_tickets(SAMPLES / "tickets.json")) == 12
    assert len(from_emails(SAMPLES / "emails")) == 2
    logs = from_logs(SAMPLES / "app.log")
    assert len(logs) == 3 and all(item.source == "log" for item in logs)
    assert len(load_all(SAMPLES)) == 17


def test_outage_is_classified_and_becomes_priority_one():
    item = make_item("Production checkout is down, every customer sees an error and cannot complete an order.")
    classification, _ = Classifier().classify(item)
    decision = Rules().decide(item, classification)
    assert classification.category == "outage"
    assert decision.priority == 1
    assert "page_oncall" in decision.actions


def test_spam_lands_at_the_bottom():
    item = make_item("Join our webinar and start a free trial promotion. Unsubscribe any time.")
    classification, _ = Classifier().classify(item)
    decision = Rules().decide(item, classification)
    assert classification.category == "spam"
    assert decision.priority == 5
    assert decision.actions == ["archive"]


def test_rules_explain_every_point_they_add():
    item = make_item("Production outage, data loss confirmed, all users affected.")
    classification, _ = Classifier().classify(item)
    decision = Rules().decide(item, classification)
    assert decision.score == sum(hit.points for hit in decision.rule_hits)
    assert {"production_impact", "data_loss", "many_users"} <= {hit.rule_id for hit in decision.rule_hits}
    assert "P1" in decision.explain()


def test_workaround_reduces_the_score():
    with_workaround = make_item("Export throws an exception. There is a workaround, the CSV download still works.")
    without = make_item("Export throws an exception and nothing else works.")
    rules, classifier = Rules(), Classifier()
    a = rules.decide(with_workaround, classifier.classify(with_workaround)[0])
    b = rules.decide(without, classifier.classify(without)[0])
    assert a.score < b.score


def test_security_always_needs_a_human():
    item = make_item("A credential may have leaked, we see unauthorized access attempts.")
    classification, _ = Classifier().classify(item)
    decision = Rules().decide(item, classification)
    assert classification.category == "security"
    assert decision.needs_human is True


def test_low_confidence_routes_to_a_human():
    item = make_item("Please look at this when you can, thanks.")
    classification, _ = Classifier().classify(item)
    decision = Rules().decide(item, classification)
    assert classification.confidence < 0.55
    assert decision.needs_human is True


def test_actions_are_dry_run_by_default(orchestrator: Orchestrator):
    record = orchestrator.process(make_item("Production is down for all users, customers cannot order."))
    assert record.results and all(result.status == "dry_run" for result in record.results)


def test_duplicate_item_runs_no_actions(orchestrator: Orchestrator):
    text = "Production checkout is down, all users affected."
    first = orchestrator.process(make_item(text))
    second = orchestrator.process(make_item(text))
    assert first.duplicate is False and second.duplicate is True
    assert second.results == []


def test_idempotency_key_blocks_a_replayed_action():
    item = make_item("Production is down for all users.")
    classification, _ = Classifier().classify(item)
    decision = Rules().decide(item, classification)
    seen: set[str] = set()
    first = run_actions(item, decision, seen)
    second = run_actions(item, decision, seen)
    assert all(result.status == "dry_run" for result in first)
    assert all(result.status == "skipped" for result in second)


def test_unknown_action_is_recorded_not_raised():
    item = make_item("anything at all that is long enough")
    classification, _ = Classifier().classify(item)
    decision = Rules().decide(item, classification)
    decision.actions = ["does_not_exist"]
    result = run_actions(item, decision, set())[0]
    assert result.status == "failed" and "no handler" in result.detail


def test_registry_lists_the_handlers():
    assert {"page_oncall", "create_ticket", "archive"} <= set(registered())


def test_audit_log_is_one_json_line_per_item(orchestrator: Orchestrator, tmp_path: Path):
    orchestrator.process(make_item("Invoice 4471 was charged twice, please refund."))
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["decision"]["classification"]["category"] == "billing"
    assert record["decision"]["rule_hits"]


def test_metrics_report_counts_latency_and_versions(orchestrator: Orchestrator):
    orchestrator.process_batch(load_all(SAMPLES))
    metrics = orchestrator.metrics()
    assert metrics["items"] == 17
    assert metrics["duplicates"] >= 1
    assert metrics["latency_ms_p95"] >= 0
    assert metrics["mode"] == "dry_run"
    assert metrics["prompt_version"].startswith("classify.")


def test_classifier_rejects_an_invented_label(monkeypatch: pytest.MonkeyPatch):
    classifier = Classifier()

    class Rogue:
        name, model = "rogue", "test"

        def complete(self, *, system: str, user: str):
            from orchestrator.providers import Completion

            return Completion('{"category": "banana", "urgency": "high", "confidence": 0.9}', 1, 1, 0.0, 1, "rogue", "test")

    classifier.provider = Rogue()
    with pytest.raises(ValueError, match="unknown label"):
        classifier.classify(make_item("something happened in production"))


def test_json_wrapped_in_prose_is_recovered():
    assert parse_json('Here you go:\n```json\n{"category": "bug"}\n```')["category"] == "bug"


def test_classification_failure_does_not_stop_the_batch(orchestrator: Orchestrator):
    class Broken:
        name, model = "broken", "test"

        def complete(self, *, system: str, user: str):
            raise RuntimeError("provider exploded")

    orchestrator.classifier.provider = Broken()
    record = orchestrator.process(make_item("Production is down for everyone right now."))
    assert record.decision.needs_human is True
    assert orchestrator.metrics()["classification_failures"] == 1
