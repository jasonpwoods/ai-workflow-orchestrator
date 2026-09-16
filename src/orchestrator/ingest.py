"""Ingest adapters: tickets, emails and log lines become the same Item.

Each adapter is small and total: malformed input is skipped with a reason rather than
raising halfway through a batch, because a triage pipeline that dies on one bad record
is not a pipeline.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from pathlib import Path

from .schema import Item

LOG_LINE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})\s+(?P<level>[A-Z]+)\s+(?P<service>[\w.-]+)\s+(?P<message>.+)$"
)


def _parse_dt(value: str | None) -> datetime:
    if not value:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        return datetime.now(UTC)


def from_tickets(path: Path) -> list[Item]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    items: list[Item] = []
    for row in rows:
        try:
            items.append(
                Item(
                    id=str(row["id"]),
                    source="ticket",
                    subject=str(row.get("subject", "")),
                    body=str(row.get("body", "")),
                    received_at=_parse_dt(row.get("received_at")),
                    requester=str(row.get("requester", "")),
                    metadata={k: str(v) for k, v in (row.get("metadata") or {}).items()},
                )
            )
        except Exception:  # noqa: BLE001 - skip the bad record, keep the batch
            continue
    return items


def from_emails(directory: Path) -> list[Item]:
    items: list[Item] = []
    for path in sorted(directory.glob("*.eml")):
        try:
            message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
            body = message.get_body(preferencelist=("plain",))
            items.append(
                Item(
                    id=path.stem,
                    source="email",
                    subject=str(message.get("subject", "")),
                    body=(body.get_content() if body else "").strip() or "(no body)",
                    received_at=_parse_dt(message.get("date")),
                    requester=str(message.get("from", "")),
                )
            )
        except Exception:  # noqa: BLE001
            continue
    return items


def from_logs(path: Path, *, levels: tuple[str, ...] = ("ERROR", "CRITICAL", "FATAL")) -> list[Item]:
    """Only error-level lines become work; everything else is noise for this pipeline."""
    items: list[Item] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = LOG_LINE.match(line.strip())
        if not match or match.group("level") not in levels:
            continue
        items.append(
            Item(
                id=f"{path.stem}-{number:04d}",
                source="log",
                subject=f"{match.group('level')} in {match.group('service')}",
                body=match.group("message"),
                received_at=_parse_dt(match.group("ts")),
                metadata={"service": match.group("service"), "level": match.group("level")},
            )
        )
    return items


def load_all(sample_dir: Path) -> list[Item]:
    items: list[Item] = []
    tickets = sample_dir / "tickets.json"
    emails = sample_dir / "emails"
    logs = sample_dir / "app.log"
    if tickets.is_file():
        items += from_tickets(tickets)
    if emails.is_dir():
        items += from_emails(emails)
    if logs.is_file():
        items += from_logs(logs)
    return items
