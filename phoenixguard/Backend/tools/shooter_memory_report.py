"""Generate a memory-similarity historical report for the shooter.

Usage: ./.venv/Scripts/python.exe Backend/tools/shooter_memory_report.py --session-history data/session_history.jsonl \
    --out reports/shooter_memory_report.json

The script aggregates `memory_similarity` values, zone match counts, and
recommended confidence thresholds.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from statistics import mean, median
from typing import Any, cast


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--session-history", required=True)
    p.add_argument("--out", default="reports/shooter_memory_report.json")
    p.add_argument("--max-rows", type=int, default=0, help="0 = all rows")
    return p.parse_args()


def load_session_history(path: str | Path, max_rows: int = 0) -> list[dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if max_rows and i >= max_rows:
                break
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if isinstance(payload, Mapping):
                rows.append(dict(cast(Mapping[str, Any], payload)))
    return rows


def percentile(sorted_vals: Sequence[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    d0 = sorted_vals[int(f)] * (c - k)
    d1 = sorted_vals[int(c)] * (k - f)
    return d0 + d1


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sims: list[float] = []
    zone_match_counts: list[int] = []
    preferred_actions: Counter[str] = Counter()
    outcomes: Counter[str] = Counter()

    for r in rows:
        v = r.get("memory_similarity")
        if isinstance(v, (int, float)):
            sims.append(float(v))
        zm = r.get("zone_match_count")
        if isinstance(zm, int):
            zone_match_counts.append(zm)
        pa = r.get("zone_preferred_action")
        if isinstance(pa, str):
            preferred_actions[pa] += 1
        out = r.get("outcome")
        if isinstance(out, str):
            outcomes[out] += 1

    sims_sorted = sorted(sims)
    n = len(sims)

    thresholds = [0.2, 0.25, 0.3, 0.45, 0.6]
    thresh_stats: dict[str, float] = {}
    for t in thresholds:
        thresh_stats[str(t)] = sum(1 for x in sims if x >= t) / n if n else 0.0

    report: dict[str, Any] = {
        "rows": len(rows),
        "memory_similarity": {
            "count": n,
            "mean": mean(sims) if sims else None,
            "median": median(sims) if sims else None,
            "p10": percentile(sims_sorted, 10),
            "p25": percentile(sims_sorted, 25),
            "p75": percentile(sims_sorted, 75),
            "p90": percentile(sims_sorted, 90),
            "percent_above_thresholds": thresh_stats,
        },
        "zone_match_count": {
            "mean": mean(zone_match_counts) if zone_match_counts else None,
            "median": median(zone_match_counts) if zone_match_counts else None,
            "distribution": dict(Counter(zone_match_counts)),
        },
        "zone_preferred_action": dict(preferred_actions),
        "outcomes": dict(outcomes),
    }

    # Recommend thresholds
    recommended: dict[str, float | None] = {}
    if n:
        # Aim for a threshold that retains ~15-30% of past matches as a starting point
        for target_frac in (0.15, 0.25, 0.35):
            # find smallest t where fraction >= target_frac
            found = None
            for t in [i / 100.0 for i in range(0, 101)]:
                frac = sum(1 for x in sims if x >= t) / n
                if frac <= target_frac:
                    found = t
                    break
            recommended[f"retain_{int(target_frac*100)}pct"] = found

    report["recommendation"] = {
        "min_confidence_candidates": [0.20, 0.25, 0.30],
        "rationale": "Historical similarities cluster low; start conservative at 0.20-0.25 and iterate.",
        "auto_candidates": recommended,
    }

    return report


def render_markdown(report: Mapping[str, Any]) -> str:
    lines: list[str] = []
    ms = report["memory_similarity"]
    lines.append("# Shooter Memory Report\n")
    lines.append(f"Rows analyzed: {report['rows']}\n")
    lines.append("## Memory similarity\n")
    lines.append(f"- Count: {ms['count']}\n")
    lines.append(f"- Mean: {ms['mean']}\n")
    lines.append(f"- Median: {ms['median']}\n")
    lines.append(f"- 10th pct: {ms['p10']}\n")
    lines.append(f"- 25th pct: {ms['p25']}\n")
    lines.append(f"- 75th pct: {ms['p75']}\n")
    lines.append(f"- 90th pct: {ms['p90']}\n")
    lines.append("\n## Percent above thresholds\n")
    for k, v in ms['percent_above_thresholds'].items():
        lines.append(f"- >= {k}: {v*100:.2f}%\n")
    lines.append("\n## Zone matches\n")
    z = report['zone_match_count']
    lines.append(f"- mean: {z['mean']}, median: {z['median']}\n")
    lines.append(f"- distribution: {z['distribution']}\n")
    lines.append("\n## Zone preferred actions\n")
    for k, v in report['zone_preferred_action'].items():
        lines.append(f"- {k}: {v}\n")
    lines.append("\n## Recommendation\n")
    rec = report['recommendation']
    lines.append(f"- Candidate min_confidence values: {rec['min_confidence_candidates']}\n")
    lines.append(f"- Rationale: {rec['rationale']}\n")
    lines.append(f"- Auto candidates: {rec['auto_candidates']}\n")
    return "\n".join(lines)


def main():
    args = parse_args()
    rows = load_session_history(args.session_history, args.max_rows)
    report = summarize(rows)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    md = render_markdown(report)
    md_path = out_path.with_suffix(".md")
    with open(md_path, "w", encoding="utf-8") as fh:
        fh.write(md)

    print(f"Wrote report: {out_path} and {md_path}")


if __name__ == "__main__":
    main()
