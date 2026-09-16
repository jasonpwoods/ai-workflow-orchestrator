"""Classification: model call in, validated Classification out.

The prompt is a versioned file. The model's JSON is parsed, repaired once if it came
back wrapped in prose, then validated against the label vocabulary. An invented label
or an out-of-range confidence is a validation error, never a silent pass-through into
the rules engine.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from .providers import Completion, Provider, build_provider
from .schema import Classification, Item, Usage

JSON_BLOCK = re.compile(r"\{.*\}", re.S)
CATEGORIES = {"outage", "bug", "access_request", "billing", "how_to", "feature_request", "security", "spam"}
URGENCIES = {"critical", "high", "normal", "low"}


def load_prompt(version: str = "classify.v3", prompt_dir: Path | None = None) -> dict:
    root = prompt_dir or Path(__file__).resolve().parents[2] / "prompts"
    return yaml.safe_load((root / f"{version}.yaml").read_text(encoding="utf-8"))


def parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = JSON_BLOCK.search(text)
        if not match:
            raise
        return json.loads(match.group(0))


class Classifier:
    def __init__(self, provider: Provider | None = None, prompt_version: str = "classify.v3") -> None:
        self.provider = provider or build_provider()
        self.prompt = load_prompt(prompt_version)
        self.prompt_version = self.prompt.get("version", prompt_version)

    def _system(self) -> str:
        shots = "\n\n".join(
            f"Item:\n{shot['text']}\nJSON:\n{shot['answer']}" for shot in self.prompt.get("few_shot", [])
        )
        return f"{self.prompt['system']}\n\nWorked examples:\n{shots}" if shots else self.prompt["system"]

    def classify(self, item: Item) -> tuple[Classification, Usage]:
        completion = self._complete(item)
        try:
            payload = parse_json(completion.text)
        except json.JSONDecodeError:
            completion = self._complete(item, strict=True)
            payload = parse_json(completion.text)

        category = str(payload.get("category", "")).strip().lower()
        urgency = str(payload.get("urgency", "")).strip().lower()
        if category not in CATEGORIES or urgency not in URGENCIES:
            raise ValueError(f"model returned an unknown label: category={category!r} urgency={urgency!r}")

        try:
            classification = Classification(
                category=category,  # type: ignore[arg-type]
                urgency=urgency,  # type: ignore[arg-type]
                confidence=float(payload.get("confidence", 0.5)),
                rationale=str(payload.get("reasoning") or payload.get("rationale") or "")[:400],
                entities={k: str(v) for k, v in (payload.get("entities") or {}).items() if v},
                classifier=f"{completion.provider}:{completion.model}",
                prompt_version=self.prompt_version,
            )
        except ValidationError as error:
            raise ValueError(f"classification failed validation: {error}") from error

        usage = Usage(
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            cost_usd=completion.cost_usd,
            latency_ms=completion.latency_ms,
            provider=completion.provider,
            model=completion.model,
        )
        return classification, usage

    def _complete(self, item: Item, strict: bool = False) -> Completion:
        system = self._system() + ("\n\nReturn JSON only. No prose, no code fences." if strict else "")
        return self.provider.complete(system=system, user=item.text[:4000])
