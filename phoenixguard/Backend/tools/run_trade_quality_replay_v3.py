from __future__ import annotations

import argparse
import gzip
import json
from collections import Counter
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, cast


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    row = cast(Mapping[object, Any], value)
    return {str(key): item for key, item in row.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", default=".codex_runtime/optimized_masked_future/predictions.jsonl.gz")
    parser.add_argument("--output", default="reports/TRADE_QUALITY_REPLAY_V3_REPORT.md")
    args = parser.parse_args()
    with gzip.open(Path(args.predictions), "rt", encoding="utf-8") as handle:
        rows: list[dict[str, Any]] = [
            _mapping(json.loads(line))
            for line in handle
            if line.strip()
        ]
    selected = [row for row in rows if row["selected_high_confidence"]]
    good_wait = [
        row for row in rows
        if not row["selected_high_confidence"] and not row["target_before_invalidation"]
    ]
    bad_block = [
        row for row in rows
        if not row["selected_high_confidence"] and row["target_before_invalidation"]
    ]
    late_chase = [row for row in rows if str(row["visible_maturity"]) == "LATE_CHASE"]
    outcomes = Counter(str(row["outcome"]) for row in rows)
    selected_precision = (
        sum(bool(row["target_before_invalidation"]) for row in selected)
        / max(1, len(selected))
    )
    report = f"""# Trade-Quality Replay V3 Report

This report scores frozen grouped out-of-sample predictions. It is not a strategy
backtest and it does not infer slippage, spread, payout, or guaranteed profit.

| Measure | Result |
|---|---:|
| Eligible replay windows | {len(rows)} |
| High-confidence selected | {len(selected)} |
| Selection coverage | {100 * len(selected) / max(1, len(rows)):.2f}% |
| Selected target-before-invalidation precision | {100 * selected_precision:.2f}% |
| Mean MFE | {mean(float(row["mfe_ranges"]) for row in rows) if rows else 0:.3f} ranges |
| Mean MAE | {mean(float(row["mae_ranges"]) for row in rows) if rows else 0:.3f} ranges |
| Drawdown-first | {sum(bool(row["drawdown_first"]) for row in rows)} |
| Good waits | {len(good_wait)} |
| Bad blocks / missed valid events | {len(bad_block)} |
| Late-chase observations | {len(late_chase)} |

Outcomes: {json.dumps(dict(sorted(outcomes.items())), sort_keys=True)}.

The replay ledger does not create an allowance package or PG_EXECUTION_PACKET_V3.
"""
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(json.dumps({
        "status": "TRADE_QUALITY_REPLAY_COMPLETE",
        "rows": len(rows),
        "selected": len(selected),
        "precision": round(selected_precision, 6),
        "output": str(output),
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
