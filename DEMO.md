# Demo

Real output, captured by running the commands below with no API keys and no live mode.

## Triage the sample inbox (tickets, emails, error logs)

```
$ python -m orchestrator.cli run sample_data
17 items (dry run)

P1  outage          TCK-1001 [needs human]
      P1 score 140 <- category:outage (+70: category base score); urgency:critical (+30: urgency weight); production_impact (+25: names production impact); many_users (+15: affects many users)
      actions: page_oncall=dry_run, open_incident=dry_run, notify_requester=dry_run
P1  security        TCK-1005 [needs human]
      P1 score 105 <- category:security (+70: category base score); urgency:normal (+5: urgency weight); security_words (+30: security language)
      actions: page_oncall=dry_run, open_incident=dry_run, notify_requester=dry_run
P1  outage          TCK-1012 [needs human] [duplicate]
      P1 score 140 <- category:outage (+70: category base score); urgency:critical (+30: urgency weight); production_impact (+25: names production impact); many_users (+15: affects many users)
P1  security        unauthorized-login [needs human]
      P1 score 130 <- category:security (+70: category base score); urgency:critical (+30: urgency weight); security_words (+30: security language)
      actions: page_oncall=dry_run, open_incident=dry_run, notify_requester=dry_run
P1  outage          app-0002 [needs human]
      P1 score 100 <- category:outage (+70: category base score); urgency:normal (+5: urgency weight); production_impact (+25: names production impact)
      actions: page_oncall=dry_run, open_incident=dry_run, notify_requester=dry_run
P1  security        app-0006 [needs human]
      P1 score 105 <- category:security (+70: category base score); urgency:normal (+5: urgency weight); security_words (+30: security language)
      actions: page_oncall=dry_run, open_incident=dry_run, notify_requester=dry_run
P2  how_to          TCK-1008 [needs human]
      P2 score 90 <- category:how_to (+15: category base score); urgency:critical (+30: urgency weight); data_loss (+30: possible data loss); many_users (+15: affects many users)
      actions: open_incident=dry_run, notify_requester=dry_run
P3  billing         TCK-1003
      P3 score 55 <- category:billing (+30: category base score); urgency:normal (+5: urgency weight); money_at_risk (+20: money already moved or is disputed)
      actions: create_ticket=dry_run, notify_requester=dry_run
P3  how_to          TCK-1011 [needs human]
      P3 score 60 <- category:how_to (+15: category base score); urgency:high (+20: urgency weight); vip_requester (+15: requester is an executive account); sla_breach_language (+10: contract or escalation language)
      actions: create_ticket=dry_run, notify_requester=dry_run
P4  access_request  TCK-1002
      P4 score 30 <- category:access_request (+30: category base score); urgency:low (+0: urgency weight)
      actions: create_ticket=dry_run
P4  bug             TCK-1004
      P4 score 30 <- category:bug (+40: category base score); urgency:normal (+5: urgency weight); has_workaround (-15: a workaround exists)
      actions: create_ticket=dry_run
P4  feature_request TCK-1007
      P4 score 40 <- category:feature_request (+10: category base score); urgency:high (+20: urgency weight); sla_breach_language (+10: contract or escalation language)
      actions: create_ticket=dry_run
P4  access_request  TCK-1010
      P4 score 35 <- category:access_request (+30: category base score); urgency:normal (+5: urgency weight)
      actions: create_ticket=dry_run
P4  billing         app-0004
      P4 score 35 <- category:billing (+30: category base score); urgency:normal (+5: urgency weight)
      actions: create_ticket=dry_run
P5  how_to          TCK-1006
      P5 score 20 <- category:how_to (+15: category base score); urgency:normal (+5: urgency weight)
      actions: archive=dry_run
P5  spam            TCK-1009
      P5 score 0 <- category:spam (+0: category base score); urgency:low (+0: urgency weight); low_signal (-20: marketing or bulk mail)
      actions: archive=dry_run
P5  how_to          vpn-question
      P5 score 0 <- category:how_to (+15: category base score); urgency:low (+0: urgency weight); has_workaround (-15: a workaround exists)
      actions: archive=dry_run

{
  "generated_at": "2026-09-16T00:18:00+00:00",
  "mode": "dry_run",
  "items": 17,
  "duplicates": 1,
  "needs_human": 8,
  "classification_failures": 0,
  "by_priority": {
    "1": 6,
    "4": 5,
    "3": 2,
    "5": 3,
    "2": 1
  },
  "by_category": {
    "outage": 3,
    "access_request": 2,
    "billing": 2,
    "bug": 1,
    "security": 3,
    "how_to": 4,
    "feature_request": 1,
    "spam": 1
  },
  "tokens": 9933,
  "cost_usd": 0.0,
  "latency_ms_p50": 0,
  "latency_ms_p95": 1,
  "provider": "offline:rules-v1",
  "prompt_version": "classify.v3",
  "rules_version": "rules-2026-09-16"
}
```

## One item, explained

```
$ python -m orchestrator.cli one "invoice 4471 charged us twice, please refund"
P3  billing         cli-1
      P3 score 55 <- category:billing (+30: category base score); urgency:normal (+5: urgency weight); money_at_risk (+20: money already moved or is disputed)
      actions: create_ticket=dry_run, notify_requester=dry_run
```

## MCP server: tool list and a triage call

```
$ echo {"jsonrpc":"2.0","id":1,"method":"tools/list"} | python -m orchestrator.mcp_server
{"jsonrpc": "2.0", "id": 1, "result": {"tools": [{"name": "triage_item", "description": "Classify one piece of incoming work and return its priority, SLA and the rules that fired. Read-only.", "inputSchema": {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}, "requester": {"type": "string"}, "source": {"type": "string", "enum": ["ticket", "email", "log"]}}, "required": ["body"]}}, {"name": "list_actions", "description": "List the action handlers this deployment can run, and whether live mode is enabled.", "inputSchema": {"type": "object", "properties": {}}}, {"name": "execute_actions", "description": "Run the routed actions for an item. Refuses unless the deployment is in live mode.", "inputSchema": {"type": "object", "properties": {"subject": {"type": "string"}, "body": {"type": "string"}, "requester": {"type": "string"}}, "required": ["body"]}}]}}
{"jsonrpc": "2.0", "id": 2, "result": {"content": [{"type": "text", "text": "{\"category\": \"outage\", \"urgency\": \"critical\", \"confidence\": 0.76, \"priority\": 1, \"score\": 125, \"sla_due\": \"2026-09-16T01:18:01.451949+00:00\", \"needs_human\": true, \"explanation\": \"P1 score 125 <- category:outage (+70: category base score); urgency:critical (+30: urgency weight); production_impact (+25: names production impact)\", \"would_run\": [\"page_oncall\", \"open_incident\", \"notify_requester\"], \"usage\": {\"prompt_tokens\": 522, \"completion_tokens\": 38, \"cost_usd\": 0.0, \"latency_ms\": 137, \"provider\": \"offline\", \"model\": \"rules-v1\"}}"}], "isError": false}}
{"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": "{\"executed\": false, \"reason\": \"this deployment is in dry-run mode; set ORCH_LIVE=1 to allow writes\"}"}], "isError": false}}
```

## Quality gate

```
$ python evals/gate.py
ok  accuracy               1.00 (floor 0.80)
ok  macro_f1               1.00 (floor 0.75)
ok  priority_compliance    1.00 (floor 0.90)

Quality gate passed.
```
