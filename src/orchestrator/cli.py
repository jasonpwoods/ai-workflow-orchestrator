"""Command line.

    python -m orchestrator.cli run sample_data            # triage every sample item (dry run)
    python -m orchestrator.cli one "checkout is down for all customers"
    python -m orchestrator.cli metrics sample_data
"""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from .actions import live
from .ingest import load_all
from .pipeline import Orchestrator
from .schema import Item


def _print_record(record) -> None:
    decision = record.decision
    flag = " [needs human]" if decision.needs_human else ""
    dup = " [duplicate]" if record.duplicate else ""
    print(f"P{decision.priority}  {decision.classification.category:<15} {record.item_id}{flag}{dup}")
    print(f"      {decision.explain()}")
    if record.results:
        print("      actions: " + ", ".join(f"{r.action}={r.status}" for r in record.results))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Triage incoming work.")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="ingest a directory and triage everything in it")
    run.add_argument("directory", type=Path)

    one = sub.add_parser("one", help="triage a single piece of text")
    one.add_argument("text", nargs="+")
    one.add_argument("--subject", default="")
    one.add_argument("--requester", default="")

    metrics = sub.add_parser("metrics", help="triage a directory and print metrics only")
    metrics.add_argument("directory", type=Path)

    args = parser.parse_args(argv)
    orchestrator = Orchestrator()
    mode = "LIVE" if live() else "dry run"

    if args.command == "one":
        item = Item(
            id="cli-1",
            source="ticket",
            subject=args.subject,
            body=" ".join(args.text),
            received_at=datetime.now(UTC),
            requester=args.requester,
        )
        _print_record(orchestrator.process(item))
        return 0

    items = load_all(args.directory)
    if not items:
        print(f"no items found in {args.directory}")
        return 1
    records = orchestrator.process_batch(items)

    if args.command == "run":
        print(f"{len(records)} items ({mode})\n")
        for record in sorted(records, key=lambda r: r.decision.priority):
            _print_record(record)
        print()
    print(json.dumps(orchestrator.metrics(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
