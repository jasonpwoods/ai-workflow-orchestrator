"""The rules engine: deterministic, explainable, and owned by the business.

Score = category base + urgency weight + signal points. Every signal that fires is
recorded with its points and the reason, so a priority can be explained to the person
whose ticket it was - and changed by editing rules.yaml, not by editing a prompt.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .schema import Classification, Decision, Item, RuleHit, sla_due


class Rules:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(__file__).resolve().parents[2] / "rules.yaml"
        self.data = yaml.safe_load(self.path.read_text(encoding="utf-8"))

    @property
    def version(self) -> str:
        return str(self.data.get("version", "unversioned"))

    def _signals(self, item: Item) -> list[RuleHit]:
        text = item.text.lower()
        domain = item.requester.split("@")[-1].lower() if "@" in item.requester else ""
        hits: list[RuleHit] = []
        for signal in self.data.get("signals", []):
            matched = any(phrase.lower() in text for phrase in signal.get("any_of", []))
            if not matched and domain and domain in [d.lower() for d in signal.get("requester_domains", [])]:
                matched = True
            if matched:
                hits.append(RuleHit(rule_id=signal["id"], points=int(signal["points"]), why=signal["why"]))
        return hits

    def decide(self, item: Item, classification: Classification) -> Decision:
        base = int(self.data["base_points"].get(classification.category, 0))
        urgency = int(self.data["urgency_points"].get(classification.urgency, 0))
        hits = [
            RuleHit(rule_id=f"category:{classification.category}", points=base, why="category base score"),
            RuleHit(rule_id=f"urgency:{classification.urgency}", points=urgency, why="urgency weight"),
            *self._signals(item),
        ]
        score = max(0, sum(hit.points for hit in hits))

        priority = 5
        for band in sorted(self.data["priority_bands"], key=lambda b: -int(b["min_score"])):
            if score >= int(band["min_score"]):
                priority = int(band["priority"])
                break

        review = self.data.get("human_review", {})
        needs_human = (
            classification.confidence < float(review.get("min_confidence", 0.0))
            or classification.category in set(review.get("always_for_categories", []))
            or priority in set(review.get("always_for_priority", []))
        )

        return Decision(
            item_id=item.id,
            classification=classification,
            priority=priority,
            score=score,
            sla_due=sla_due(item.received_at, int(self.data["sla_hours"][priority])),
            rule_hits=hits,
            actions=list(self.data["routing"].get(priority, [])),
            needs_human=needs_human,
        )
