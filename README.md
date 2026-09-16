# AI Workflow Orchestrator

Ingests tickets, emails and log lines; classifies and prioritises them; applies business
rules that live in a YAML file rather than in a prompt; triggers the routed actions; and
writes an audit record for every decision. Exposed as a CLI and as a Model Context
Protocol server, so an agent can call it as a tool.

Built by **Jason P. Woods** — [jasonpwoods.com](https://jasonpwoods.com)

```bash
pip install -r requirements.txt && pip install -e .    # or: export PYTHONPATH=src
python -m orchestrator.cli run sample_data
```

```
17 items (dry run)

P1  outage          TCK-1001
      P1 score 145 <- category:outage (+70: category base score); urgency:critical (+30: urgency weight);
      production_impact (+25: names production impact); many_users (+15: affects many users) [needs human]
      actions: page_oncall=dry_run, open_incident=dry_run, notify_requester=dry_run
P2  security        unauthorized-login [needs human]
...
P5  spam            TCK-1009
      actions: archive=dry_run
```

## What it shows

| Concern | How it is handled |
| --- | --- |
| Classification | Versioned prompt with few-shot examples and a reasoning field; JSON validated into a pydantic contract; an invented label is an error, not a pass-through |
| Prioritisation | Explainable rules engine in `rules.yaml`: base points, urgency weight, signal rules, priority bands, SLA hours, routing |
| Explainability | Every decision carries the rules that fired with their points and reasons |
| Actions | Registry of named handlers; **dry run by default**; idempotency keys stop a replay double-paging on-call; one failed action never stops the rest |
| Human in the loop | Low confidence, any security item and every P1 are flagged for review |
| Idempotency | Items are fingerprinted; a repeat is recorded and takes no action |
| Resilience | A provider failure degrades to a recorded fallback classification instead of killing the batch |
| Audit | One JSON line per item: decision, rules, actions, tokens, cost, latency |
| Observability | Counts by priority and category, duplicates, p50/p95 latency, spend, prompt and rules versions |
| Agent access | MCP stdio server with declared tool schemas; the write tool refuses outside live mode |

## Runs with no API key

The default classifier is deterministic weighted-evidence scoring over the label
vocabulary — no key, no network, same answer every run, which is what makes the CI
quality gate meaningful. Add a key and the same pipeline uses a hosted model:

```bash
export OPENAI_API_KEY=...      # or ANTHROPIC_API_KEY
export ORCH_PROVIDER=openai    # optional, inferred from the key
python -m orchestrator.cli one "checkout is returning 503 for every customer"
```

| Setting | Default | Meaning |
| --- | --- | --- |
| `ORCH_PROVIDER` | inferred | `offline`, `openai`, `anthropic` |
| `ORCH_MODEL` | per provider | chat model id |
| `ORCH_LIVE` | unset | `1` allows real side effects; anything else is a dry run |
| `ORCH_PAGER_WEBHOOK` | unset | paging endpoint used in live mode |
| `ORCH_OUTBOX` | `.cache/outbox.jsonl` | where dry-run payloads are written for review |

## Business rules are data

```yaml
signals:
  - id: data_loss
    points: 30
    any_of: ["data loss", "deleted records", "corrupted", "cannot recover"]
    why: possible data loss

priority_bands:
  - { min_score: 100, priority: 1 }
routing:
  1: ["page_oncall", "open_incident", "notify_requester"]
human_review:
  min_confidence: 0.55
  always_for_categories: ["security"]
```

Changing how work is prioritised is a pull request against `rules.yaml`, reviewable by the
operations lead who owns the policy — not a prompt edit and not a deploy.

## MCP server

```bash
python -m orchestrator.mcp_server      # JSON-RPC 2.0 over stdin/stdout
```

Declared tools: `triage_item` (read-only), `list_actions`, `execute_actions` (refuses
unless `ORCH_LIVE=1`). That refusal is the point: a model gets a safe, declared surface
instead of direct access to the paging system.

## Evaluation

```bash
python evals/run_eval.py     # writes evals/REPORT.md
python evals/gate.py         # CI gate: accuracy, macro-F1, priority compliance
```

30 labelled items across all eight categories. Macro-F1 is reported alongside accuracy
because the classes are unbalanced, plus a confusion matrix, priority compliance, latency
percentiles and cost per 1,000 items. Results: [`evals/REPORT.md`](evals/REPORT.md).

## Architecture

```
tickets.json  emails/*.eml  app.log
        │ ingest.py (total parsers: a bad record is skipped, not fatal)
        ▼
      Item ──classify.py──► Classification        prompts/classify.v3.yaml
        │                    (validated labels)   providers.py: offline | openai | anthropic
        ▼
   prioritize.py + rules.yaml ──► Decision (priority, SLA, rule hits, routing, needs_human)
        │
        ▼
    actions.py (registry, dry run, idempotency) ──► ActionResult[]
        │
        ▼
   audit.jsonl + metrics            CLI (cli.py) · MCP server (mcp_server.py)
```

## Tests

```bash
python -m pytest
```

Covers ingestion of all three sources, classification and label validation, rule scoring
and explanations, dry-run safety, idempotency and duplicate suppression, provider failure
handling, the audit log, metrics, and the full MCP protocol surface.

## Known limits

- The offline classifier is keyword evidence, not a language model. It is deliberately the
  CI baseline; a hosted model handles paraphrase and tone better and the evaluation is how
  you see the difference.
- Action handlers beyond paging write to an outbox file rather than integrating with a
  real ticketing system. Adding one is a handler in `actions.py` and a name in `rules.yaml`.
- State is per-process. A durable queue and a shared idempotency store are the next step
  for a real deployment.

## Licence

MIT.
