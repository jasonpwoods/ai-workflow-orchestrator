"""Quality gate for CI: the floor the triage pipeline must hold.

    python evals/gate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from run_eval import run, write_report  # noqa: E402

THRESHOLDS = {
    "accuracy": 0.80,
    "macro_f1": 0.75,
    "priority_compliance": 0.90,
}


def main() -> int:
    result = run()
    write_report(result)
    failed = []
    for metric, floor in THRESHOLDS.items():
        ok = result[metric] >= floor
        print(f"{'ok ' if ok else 'FAIL'} {metric:22} {result[metric]:.2f} (floor {floor:.2f})")
        if not ok:
            failed.append(metric)
    if failed:
        print("\nQuality gate failed: " + ", ".join(failed))
        return 1
    print("\nQuality gate passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
