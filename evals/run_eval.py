"""Evaluate the classifier and the rules together, and write evals/REPORT.md.

Reported:

* accuracy and macro-F1 over the labelled set (macro-F1 because the classes are
  unbalanced and accuracy alone hides a category the model never predicts);
* a confusion matrix, so a systematic mix-up is visible rather than averaged away;
* priority compliance - how often the rules land at or above the expected urgency band;
* latency p50/p95 and cost per 1,000 items.

    python evals/run_eval.py                 # offline classifier, no key needed
    ORCH_PROVIDER=openai python evals/run_eval.py
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from orchestrator.classify import Classifier  # noqa: E402
from orchestrator.prioritize import Rules  # noqa: E402
from orchestrator.schema import Item  # noqa: E402

LABELS = ["outage", "bug", "access_request", "billing", "how_to", "feature_request", "security", "spam"]


def macro_f1(truth: list[str], predicted: list[str]) -> tuple[float, dict[str, dict[str, float]]]:
    per_label: dict[str, dict[str, float]] = {}
    scores = []
    for label in LABELS:
        tp = sum(1 for t, p in zip(truth, predicted) if t == label and p == label)
        fp = sum(1 for t, p in zip(truth, predicted) if t != label and p == label)
        fn = sum(1 for t, p in zip(truth, predicted) if t == label and p != label)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        support = sum(1 for t in truth if t == label)
        if support:
            scores.append(f1)
        per_label[label] = {"precision": round(precision, 3), "recall": round(recall, 3), "f1": round(f1, 3), "support": support}
    return (round(sum(scores) / len(scores), 3) if scores else 0.0), per_label


def run() -> dict:
    data = yaml.safe_load((ROOT / "evals" / "dataset.yaml").read_text(encoding="utf-8"))
    classifier = Classifier()
    rules = Rules()

    truth: list[str] = []
    predicted: list[str] = []
    latencies: list[int] = []
    confusion: dict[str, Counter[str]] = defaultdict(Counter)
    priority_ok = 0
    cost = 0.0
    failures: list[dict] = []

    for index, case in enumerate(data["items"]):
        item = Item(
            id=f"eval-{index:03d}",
            source="ticket",
            subject="",
            body=case["text"],
            received_at=datetime.now(UTC),
        )
        started = time.perf_counter()
        classification, usage = classifier.classify(item)
        decision = rules.decide(item, classification)
        latencies.append(int((time.perf_counter() - started) * 1000))
        cost += usage.cost_usd

        truth.append(case["category"])
        predicted.append(classification.category)
        confusion[case["category"]][classification.category] += 1
        if decision.priority <= int(case["max_priority"]):
            priority_ok += 1
        if classification.category != case["category"] or decision.priority > int(case["max_priority"]):
            failures.append(
                {
                    "text": case["text"][:90],
                    "expected": case["category"],
                    "got": classification.category,
                    "priority": decision.priority,
                    "max_priority": case["max_priority"],
                }
            )

    total = len(truth)
    accuracy = sum(1 for t, p in zip(truth, predicted) if t == p) / total
    f1, per_label = macro_f1(truth, predicted)
    return {
        "provider": f"{classifier.provider.name}:{classifier.provider.model}",
        "prompt_version": classifier.prompt_version,
        "rules_version": rules.version,
        "items": total,
        "accuracy": round(accuracy, 3),
        "macro_f1": f1,
        "priority_compliance": round(priority_ok / total, 3),
        "latency_ms_p50": int(statistics.median(latencies)),
        "latency_ms_p95": sorted(latencies)[max(0, int(0.95 * (len(latencies) - 1)))],
        "cost_usd_per_1000_items": round(cost / total * 1000, 4),
        "per_label": per_label,
        "confusion": {k: dict(v) for k, v in confusion.items()},
        "failures": failures,
    }


def write_report(result: dict) -> Path:
    path = ROOT / "evals" / "REPORT.md"
    lines = [
        "# Evaluation report",
        "",
        f"`python evals/run_eval.py` over {result['items']} labelled items.",
        "",
        f"- classifier: `{result['provider']}`  prompt: `{result['prompt_version']}`  rules: `{result['rules_version']}`",
        "",
        "| Metric | Result |",
        "| --- | --- |",
        f"| Accuracy | {result['accuracy']:.0%} |",
        f"| Macro-F1 | {result['macro_f1']:.2f} |",
        f"| Priority compliance | {result['priority_compliance']:.0%} |",
        f"| Latency p50 | {result['latency_ms_p50']} ms |",
        f"| Latency p95 | {result['latency_ms_p95']} ms |",
        f"| Cost per 1,000 items | ${result['cost_usd_per_1000_items']:.4f} |",
        "",
        "## Per label",
        "",
        "| Label | Precision | Recall | F1 | Support |",
        "| --- | --- | --- | --- | --- |",
    ]
    for label, scores in result["per_label"].items():
        if scores["support"]:
            lines.append(
                f"| {label} | {scores['precision']:.2f} | {scores['recall']:.2f} | {scores['f1']:.2f} | {int(scores['support'])} |"
            )
    if result["failures"]:
        lines += [
            "",
            "## Misses",
            "",
            "| Item | Expected | Got | Priority | Max |",
            "| --- | --- | --- | --- | --- |",
            *[
                f"| {f['text'].replace('|', '/')} | {f['expected']} | {f['got']} | {f['priority']} | {f['max_priority']} |"
                for f in result["failures"]
            ],
        ]
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


if __name__ == "__main__":
    result = run()
    print(json.dumps({k: v for k, v in result.items() if k not in {"failures", "confusion", "per_label"}}, indent=2))
    print(f"misses: {len(result['failures'])}")
    print(f"report: {write_report(result)}")
