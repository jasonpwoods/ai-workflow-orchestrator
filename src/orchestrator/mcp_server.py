"""A Model Context Protocol server over stdio.

MCP is how a model reaches a tool without the tool handing over its keys or its
database. This server exposes the triage pipeline as three named tools with declared
JSON schemas, so an MCP client (Claude Desktop, an agent runtime, an IDE) can call
them and can only do what is declared here.

Safety choices worth noting:

* the write-capable tool refuses unless ORCH_LIVE=1, so a model cannot page an
  on-call engineer from a chat window by default;
* every tool validates its arguments into the pydantic contract before running;
* errors come back as JSON-RPC errors, never as a stack trace on stdout, because
  stdout is the protocol channel.

    python -m orchestrator.mcp_server        # speaks JSON-RPC 2.0 on stdin/stdout
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import Any

from .actions import live, registered
from .pipeline import Orchestrator
from .schema import Item

PROTOCOL_VERSION = "2024-11-05"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "triage_item",
        "description": "Classify one piece of incoming work and return its priority, SLA and the rules that fired. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "requester": {"type": "string"},
                "source": {"type": "string", "enum": ["ticket", "email", "log"]},
            },
            "required": ["body"],
        },
    },
    {
        "name": "list_actions",
        "description": "List the action handlers this deployment can run, and whether live mode is enabled.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "execute_actions",
        "description": "Run the routed actions for an item. Refuses unless the deployment is in live mode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "body": {"type": "string"},
                "requester": {"type": "string"},
            },
            "required": ["body"],
        },
    },
]


class MCPServer:
    def __init__(self, orchestrator: Orchestrator | None = None) -> None:
        self.orchestrator = orchestrator or Orchestrator()

    # ---- tools -----------------------------------------------------------------
    def _item(self, args: dict[str, Any], item_id: str) -> Item:
        return Item(
            id=item_id,
            source=args.get("source", "ticket"),
            subject=str(args.get("subject", "")),
            body=str(args["body"]),
            received_at=datetime.now(UTC),
            requester=str(args.get("requester", "")),
        )

    def triage_item(self, args: dict[str, Any]) -> dict:
        item = self._item(args, "mcp-triage")
        classification, usage = self.orchestrator.classifier.classify(item)
        decision = self.orchestrator.rules.decide(item, classification)
        return {
            "category": decision.classification.category,
            "urgency": decision.classification.urgency,
            "confidence": decision.classification.confidence,
            "priority": decision.priority,
            "score": decision.score,
            "sla_due": decision.sla_due.isoformat(),
            "needs_human": decision.needs_human,
            "explanation": decision.explain(),
            "would_run": decision.actions,
            "usage": usage.model_dump(),
        }

    def list_actions(self, _: dict[str, Any]) -> dict:
        return {"actions": registered(), "live": live(), "rules_version": self.orchestrator.rules.version}

    def execute_actions(self, args: dict[str, Any]) -> dict:
        if not live():
            return {
                "executed": False,
                "reason": "this deployment is in dry-run mode; set ORCH_LIVE=1 to allow writes",
            }
        record = self.orchestrator.process(self._item(args, "mcp-execute"))
        return {"executed": True, "results": [r.model_dump() for r in record.results]}

    # ---- protocol --------------------------------------------------------------
    def handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            result = {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "ai-workflow-orchestrator", "version": "1.0.0"},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            params = request.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            handler = {
                "triage_item": self.triage_item,
                "list_actions": self.list_actions,
                "execute_actions": self.execute_actions,
            }.get(name)
            if handler is None:
                return self._error(request_id, -32601, f"unknown tool: {name}")
            try:
                payload = handler(arguments)
            except Exception as error:  # noqa: BLE001 - protocol errors, not tracebacks
                return self._error(request_id, -32000, str(error)[:300])
            result = {"content": [{"type": "text", "text": json.dumps(payload)}], "isError": False}
        elif method in {"notifications/initialized", "initialized"}:
            return None  # notification: no reply
        else:
            return self._error(request_id, -32601, f"unknown method: {method}")
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve(stdin=None, stdout=None) -> None:
    server = MCPServer()
    source = stdin or sys.stdin
    sink = stdout or sys.stdout
    for line in source:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            sink.write(json.dumps(MCPServer._error(None, -32700, "parse error")) + "\n")
            sink.flush()
            continue
        response = server.handle(request)
        if response is not None:
            sink.write(json.dumps(response) + "\n")
            sink.flush()


if __name__ == "__main__":
    serve()
