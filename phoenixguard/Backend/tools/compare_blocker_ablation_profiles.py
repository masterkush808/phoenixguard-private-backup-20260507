from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any, Mapping


BASELINE_PROFILE = "BASELINE_FULL_SAFETY"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rate(value: int, total: int) -> float:
    return round(value / total, 6) if total else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare output from run_blocker_ablation_burn.py by profile."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    aggregates: dict[str, dict[str, Any]] = {}
    for line in args.input.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = _mapping(json.loads(line))
        package = _mapping(row.get("shadow_allowance_package") or row)
        profile = str(
            package.get("ablation_profile") or row.get("ablation_profile") or "UNKNOWN"
        ).strip().upper()
        aggregate = aggregates.setdefault(
            profile,
            {
                "records": 0,
                "would_allow": 0,
                "prepare": 0,
                "blocked": 0,
                "no_direction": 0,
                "baseline_blockers": Counter(),
                "ignored_warnings": Counter(),
            },
        )
        aggregate["records"] += 1
        state = str(package.get("shadow_state") or "NO_DIRECTION").strip().upper()
        state_key = {
            "WOULD_ALLOW": "would_allow",
            "PREPARE": "prepare",
            "BLOCKED": "blocked",
        }.get(state, "no_direction")
        aggregate[state_key] += 1
        for blocker in package.get("blocked_by_baseline") or []:
            blocker_row = _mapping(blocker)
            aggregate["baseline_blockers"][str(blocker_row.get("code") or "UNKNOWN")] += 1
        for warning in package.get("warnings_ignored_for_test") or []:
            warning_row = _mapping(warning)
            aggregate["ignored_warnings"][str(warning_row.get("code") or "UNKNOWN")] += 1

    baseline_allow_rate = _rate(
        int(aggregates.get(BASELINE_PROFILE, {}).get("would_allow", 0)),
        int(aggregates.get(BASELINE_PROFILE, {}).get("records", 0)),
    )
    comparison: dict[str, Any] = {}
    for profile, aggregate in sorted(aggregates.items()):
        total = int(aggregate["records"])
        allow_rate = _rate(int(aggregate["would_allow"]), total)
        comparison[profile] = {
            "records": total,
            "would_allow": aggregate["would_allow"],
            "would_allow_rate": allow_rate,
            "allow_rate_delta_vs_baseline": round(allow_rate - baseline_allow_rate, 6),
            "prepare": aggregate["prepare"],
            "blocked": aggregate["blocked"],
            "no_direction": aggregate["no_direction"],
            "top_baseline_blockers": aggregate["baseline_blockers"].most_common(20),
            "top_ignored_warnings": aggregate["ignored_warnings"].most_common(20),
        }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "schema_version": "PG_BLOCKER_ABLATION_COMPARISON_V1",
                "baseline_profile": BASELINE_PROFILE,
                "profiles": comparison,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
