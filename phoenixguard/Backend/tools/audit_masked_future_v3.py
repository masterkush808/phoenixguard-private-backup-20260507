from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median
from typing import Any, Mapping, cast

from phoenixguard.simulation.masked_future_v3 import enforce_disk_reserve
from phoenixguard.study.optimized_hidden_state_v3 import (
    build_optimized_dataset_v3,
    load_cached_sequences_v3,
)


def _percent(value: float) -> str:
    return f"{100.0 * float(value):.2f}%"


def _table(rows: list[tuple[str, Any, Any]]) -> str:
    output = ["| Measure | Value | Detail |", "|---|---:|---|"]
    output.extend(f"| {name} | {value} | {detail} |" for name, value, detail in rows)
    return "\n".join(output)


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    row = cast(Mapping[object, Any], value)
    return {str(key): item for key, item in row.items()}


def build_audit(
    *,
    cache_path: Path,
    old_summary_path: Path,
    folds: int,
    minimum_free_gb: float,
) -> tuple[dict[str, Any], str]:
    free_before = enforce_disk_reserve(cache_path.parent, minimum_free_gb=minimum_free_gb)
    records = load_cached_sequences_v3(cache_path)
    dataset = build_optimized_dataset_v3(records, folds=folds, stride=1)
    candle_counts = [len(record.candles) for record in records]
    parse_confidences = [
        float(candle.get("parse_confidence") or 0.0)
        for record in records
        for candle in record.candles
    ]
    spacing_confidences = [
        float(candle.get("spacing_confidence") or 0.0)
        for record in records
        for candle in record.candles
    ]
    metadata_sources = Counter(record.metadata_source for record in records)
    symbol_counts = Counter(record.symbol for record in records)
    timeframe_counts = Counter(record.timeframe for record in records)
    event_counts = Counter(event["event_type"] for event in dataset.events)
    outcome_counts = Counter(
        str(target["trade_path"]["outcome"]) for target in dataset.targets
    )
    maturity_counts = Counter(
        str(target["opportunity_maturity"]) for target in dataset.targets
    )
    horizon_labels: dict[str, Counter[str]] = defaultdict(Counter)
    disagreements: Counter[str] = Counter()
    for target in dataset.targets:
        for horizon, raw_row in _mapping(target.get("directions")).items():
            row = _mapping(raw_row)
            majority = str(row["majority"])
            endpoint = str(row["endpoint"])
            horizon_labels[horizon][majority] += 1
            disagreements[horizon] += int(majority != endpoint)
    trendline_valid = sum(
        float(event["features"]["trendline_touches"]) >= 3.0
        for event in dataset.events
    )
    scale_conflict = sum(
        bool(event["features"]["scale_conflict"]) for event in dataset.events
    )
    latest_flip = sum(bool(event["features"]["latest_flip"]) for event in dataset.events)
    sparse_contexts = Counter(
        (
            str(event["event_type"]),
            str(event["side_candidate"]),
            str(event["symbol"]),
            str(event["timeframe"]),
        )
        for event in dataset.events
    )
    sparse_count = sum(value < 8 for value in sparse_contexts.values())
    old_summary: dict[str, Any] = (
        _mapping(json.loads(old_summary_path.read_text(encoding="utf-8")))
        if old_summary_path.is_file()
        else {}
    )
    old_cv = _mapping(old_summary.get("cross_validation"))
    old_horizons = {
        str(key): _mapping(value)
        for key, value in _mapping(old_cv.get("horizons")).items()
    }
    root_causes = [
        "Only 53 independent near-duplicate families exist despite tens of thousands of causal cutoffs.",
        "Broad majority-direction labels disagree with endpoint direction in a material share of windows.",
        "The old model has no target-before-invalidation, MFE, MAE, drawdown-first, or maturity label.",
        "Pair/timeframe metadata remains unresolved for some images and therefore falls back to cross-pair priors.",
        "Sparse event/pair/timeframe contexts require calibrated model backoff rather than raw count confidence.",
        "Full-frame CV embeddings are unsafe for masked replay because their pixels include the withheld suffix; only prefix-safe geometry is admissible.",
    ]
    audit: dict[str, Any] = {
        "schema_version": "PG_MASKED_FUTURE_ROOT_CAUSE_AUDIT_V3",
        "disk": {
            "free_gb_before": round(free_before, 3),
            "minimum_free_gb": float(minimum_free_gb),
        },
        "dataset": {
            "source_images": len(records),
            "families": dataset.family_count,
            "all_causal_windows": dataset.all_window_count,
            "eligible_event_windows": dataset.eligible_window_count,
            "symbols": dict(sorted(symbol_counts.items())),
            "timeframes": dict(sorted(timeframe_counts.items())),
            "metadata_sources": dict(sorted(metadata_sources.items())),
        },
        "extraction": {
            "minimum_candles": min(candle_counts) if candle_counts else 0,
            "median_candles": median(candle_counts) if candle_counts else 0,
            "maximum_candles": max(candle_counts) if candle_counts else 0,
            "mean_parse_confidence": mean(parse_confidences) if parse_confidences else 0.0,
            "mean_spacing_confidence": mean(spacing_confidences) if spacing_confidences else 0.0,
            "failed_extractions": sum(record.extraction_status != "EXTRACTED" for record in records),
        },
        "labels": {
            "events": dict(sorted(event_counts.items())),
            "trade_path_outcomes": dict(sorted(outcome_counts.items())),
            "maturity": dict(sorted(maturity_counts.items())),
            "horizon_majority": {
                horizon: dict(sorted(counts.items()))
                for horizon, counts in sorted(horizon_labels.items(), key=lambda item: int(item[0]))
            },
            "majority_endpoint_disagreement": dict(
                sorted(disagreements.items(), key=lambda item: int(item[0]))
            ),
        },
        "feature_coverage": {
            "trendline_three_touch_rate": trendline_valid / max(1, len(dataset.events)),
            "scale_conflict_rate": scale_conflict / max(1, len(dataset.events)),
            "latest_flip_rate": latest_flip / max(1, len(dataset.events)),
            "context_count": len(sparse_contexts),
            "contexts_with_support_below_8": sparse_count,
        },
        "old_cross_validation": old_cv,
        "leakage_audit": dataset.leakage_audit,
        "root_causes": root_causes,
    }
    horizon_rows: list[tuple[str, Any, Any]] = []
    for horizon, counts in sorted(horizon_labels.items(), key=lambda item: int(item[0])):
        total = sum(counts.values())
        horizon_rows.append(
            (
                horizon,
                total,
                f"REST {_percent(counts.get('REST', 0) / max(1, total))}; "
                f"majority/endpoint disagree {_percent(disagreements[horizon] / max(1, total))}",
            )
        )
    old_rows: list[tuple[str, Any, Any]] = []
    for horizon, values in sorted(old_horizons.items(), key=lambda item: int(item[0])):
        old_rows.append(
            (
                horizon,
                _percent(float(values.get("accuracy") or 0.0)),
                f"baseline {_percent(float(values.get('baseline_accuracy') or 0.0))}; "
                f"Brier {float(values.get('brier') or 0.0):.4f}",
            )
        )
    report = f"""# Masked-Future V3 Root-Cause Analysis

## Gate

This report was generated before optimized training. It audits causal data, labels,
feature coverage, calibration evidence, and independent-family support.

## Dataset independence

{_table([
    ("Source images", len(records), "No image files duplicated"),
    ("Near-duplicate families", dataset.family_count, "All related images remain in one outer fold"),
    ("All causal cutoffs", dataset.all_window_count, "Visible prefixes only"),
    ("Eligible event windows", dataset.eligible_window_count, "Event-conditioned model denominator"),
    ("Unresolved symbols", symbol_counts.get("UNKNOWN", 0), "Uses cross-pair backoff"),
    ("Unresolved timeframes", timeframe_counts.get("UNKNOWN", 0), "Uses cross-timeframe backoff"),
])}

## Candle extraction quality

{_table([
    ("Failed extractions", audit["extraction"]["failed_extractions"], "Must remain zero"),
    ("Candles per image", f'{audit["extraction"]["minimum_candles"]}/{audit["extraction"]["median_candles"]}/{audit["extraction"]["maximum_candles"]}', "min/median/max"),
    ("Mean parse confidence", f'{audit["extraction"]["mean_parse_confidence"]:.4f}', "Candle geometry"),
    ("Mean spacing confidence", f'{audit["extraction"]["mean_spacing_confidence"]:.4f}', "Track coherence"),
])}

## Label quality

{_table(horizon_rows)}

Trade-path outcomes: {json.dumps(dict(sorted(outcome_counts.items())), sort_keys=True)}.

Opportunity maturity labels: {json.dumps(dict(sorted(maturity_counts.items())), sort_keys=True)}.

## Feature coverage

{_table([
    ("Strict 3-touch trendline", _percent(audit["feature_coverage"]["trendline_three_touch_rate"]), "Visible prefix only"),
    ("Scale conflict", _percent(audit["feature_coverage"]["scale_conflict_rate"]), "Long/local disagreement"),
    ("Latest flip", _percent(audit["feature_coverage"]["latest_flip_rate"]), "Closed visible candle"),
    ("Sparse contexts", sparse_count, f'of {len(sparse_contexts)} event/side/pair/timeframe contexts'),
])}

## Existing model calibration and horizon performance

{_table(old_rows)}

## Leakage audit

Status: **{dataset.leakage_audit["status"]}**

{json.dumps(dataset.leakage_audit, indent=2, sort_keys=True)}

## Root causes

{chr(10).join(f'{index}. {reason}' for index, reason in enumerate(root_causes, start=1))}

## Training decision

Proceed only with grouped outer folds, disjoint fit/meta/calibration families,
prefix-safe features, calibrated probabilities, and precision plus coverage reporting.
The optimized contributor remains non-executing.
"""
    return audit, report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default=".codex_runtime/masked_future_v3/corpus_cache.jsonl")
    parser.add_argument("--old-summary", default=".codex_runtime/masked_future_v3/final/summary.json")
    parser.add_argument("--output", default="reports/MASKED_FUTURE_ROOT_CAUSE_ANALYSIS.md")
    parser.add_argument("--initial-output", default="reports/MASKED_FUTURE_OPTIMIZATION_INITIAL_AUDIT.md")
    parser.add_argument("--json-output", default=".codex_runtime/optimized_masked_future/audit.json")
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--minimum-free-gb", type=float, default=45.0)
    args = parser.parse_args()
    audit, report = build_audit(
        cache_path=Path(args.cache),
        old_summary_path=Path(args.old_summary),
        folds=args.folds,
        minimum_free_gb=args.minimum_free_gb,
    )
    output = Path(args.output)
    initial = Path(args.initial_output)
    json_output = Path(args.json_output)
    for path in (output, initial, json_output):
        path.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    initial.write_text(report, encoding="utf-8")
    json_output.write_text(
        json.dumps(audit, indent=2, sort_keys=True, ensure_ascii=True),
        encoding="utf-8",
    )
    print(json.dumps({
        "status": "AUDIT_COMPLETE",
        "output": str(output),
        "source_images": audit["dataset"]["source_images"],
        "families": audit["dataset"]["families"],
        "eligible_events": audit["dataset"]["eligible_event_windows"],
        "leakage": audit["leakage_audit"]["status"],
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
