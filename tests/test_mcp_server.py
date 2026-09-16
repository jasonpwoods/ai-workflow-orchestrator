"""The MCP surface: protocol shape and the safety rule that guards writes."""

from __future__ import annotations

import io
import json

from orchestrator.mcp_server import MCPServer, serve


def call(server: MCPServer, name: str, arguments: dict) -> dict:
    response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": arguments}})
    return json.loads(response["result"]["content"][0]["text"])


def test_initialize_reports_protocol_and_server():
    server = MCPServer()
    result = server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize"})["result"]
    assert result["protocolVersion"]
    assert result["serverInfo"]["name"] == "ai-workflow-orchestrator"


def test_tools_are_declared_with_schemas():
    tools = MCPServer().handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    names = {tool["name"] for tool in tools}
    assert names == {"triage_item", "list_actions", "execute_actions"}
    assert all("inputSchema" in tool for tool in tools)


def test_triage_tool_returns_a_decision():
    payload = call(MCPServer(), "triage_item", {"body": "Production checkout is down, all users affected."})
    assert payload["category"] == "outage"
    assert payload["priority"] == 1
    assert payload["would_run"]
    assert "explanation" in payload


def test_execute_refuses_outside_live_mode():
    payload = call(MCPServer(), "execute_actions", {"body": "Production is down for everyone."})
    assert payload["executed"] is False
    assert "dry-run" in payload["reason"]


def test_unknown_tool_is_a_protocol_error():
    response = MCPServer().handle({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "rm_rf", "arguments": {}}})
    assert response["error"]["code"] == -32601


def test_bad_arguments_return_an_error_not_a_traceback():
    response = MCPServer().handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "triage_item", "arguments": {}}})
    assert response["error"]["code"] == -32000


def test_notifications_get_no_reply():
    assert MCPServer().handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_serve_speaks_line_delimited_json():
    stdin = io.StringIO(json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"}) + "\n")
    stdout = io.StringIO()
    serve(stdin, stdout)
    response = json.loads(stdout.getvalue().strip())
    assert response["id"] == 7 and response["result"]["tools"]


def test_malformed_line_returns_parse_error():
    stdin = io.StringIO("{not json}\n")
    stdout = io.StringIO()
    serve(stdin, stdout)
    assert json.loads(stdout.getvalue().strip())["error"]["code"] == -32700
