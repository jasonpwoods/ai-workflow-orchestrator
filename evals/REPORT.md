# Evaluation report

`python evals/run_eval.py` over 30 labelled items.

- classifier: `offline:rules-v1`  prompt: `classify.v3`  rules: `rules-2026-09-16`

| Metric | Result |
| --- | --- |
| Accuracy | 100% |
| Macro-F1 | 1.00 |
| Priority compliance | 100% |
| Latency p50 | 0 ms |
| Latency p95 | 0 ms |
| Cost per 1,000 items | $0.0000 |

## Per label

| Label | Precision | Recall | F1 | Support |
| --- | --- | --- | --- | --- |
| outage | 1.00 | 1.00 | 1.00 | 4 |
| bug | 1.00 | 1.00 | 1.00 | 4 |
| access_request | 1.00 | 1.00 | 1.00 | 4 |
| billing | 1.00 | 1.00 | 1.00 | 4 |
| how_to | 1.00 | 1.00 | 1.00 | 4 |
| feature_request | 1.00 | 1.00 | 1.00 | 3 |
| security | 1.00 | 1.00 | 1.00 | 4 |
| spam | 1.00 | 1.00 | 1.00 | 3 |
