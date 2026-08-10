from __future__ import annotations

import argparse
import gzip
import json
import shutil
from pathlib import Path
from typing import Any, Mapping, cast

from phoenixguard.simulation.masked_future_v3 import enforce_disk_reserve
from phoenixguard.study.optimized_hidden_state_v3 import (
    DEFAULT_OPTIMIZED_MODEL_NAME,
    build_optimized_dataset_v3,
    cross_validate_optimized_hidden_state_v3,
    load_cached_sequences_v3,
    save_optimized_model_v3,
    train_production_bundle_v3,
)


def _percent(value: Any) -> str:
    return f"{100.0 * float(value or 0.0):.2f}%"


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    row = cast(Mapping[object, Any], value)
    return {str(key): item for key, item in row.items()}


def _string_list(value: object) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    items = cast(list[object] | tuple[object, ...], value)
    return [str(item) for item in items]


def render_final_report(
    summary: Mapping[str, Any],
    *,
    artifact_path: Path,
    packaged_path: Path | None,
    free_before: float,
    free_after: float,
) -> str:
    promotion = _mapping(summary.get("promotion"))
    gates = _mapping(promotion.get("gates"))
    calibration = _mapping(summary.get("calibration"))
    event_rows: list[str] = []
    for name, values in _mapping(summary.get("by_event")).items():
        row = _mapping(values)
        event_rows.append(
            f"| {name} | {row.get('rows', 0)} | {_percent(row.get('direction_accuracy'))} | "
            f"{row.get('selected', 0)} | {_percent(row.get('selected_precision'))} |"
        )
    gate_rows: list[str] = [
        f"| {name} | {'PASS' if passed else 'FAIL'} |"
        for name, passed in gates.items()
    ]
    return f"""# Final Optimized Masked-Future V3 Retrain Report

## Final recommendation

**{promotion.get("reason", "REJECT_MODEL")}**

Promotion eligible: **{bool(promotion.get("eligible", False))}**

This contributor remains hidden-state evidence. It cannot grant entry permission,
bypass source lock, construct PG_EXECUTION_PACKET_V3, or call the shooter.

## Dataset and leakage

- Independent near-duplicate families: {summary.get("independent_family_count", 0)}
- All causal windows: {summary.get("all_window_count", 0)}
- Eligible event windows: {summary.get("eligible_event_window_count", 0)}
- Outer-fold prediction rows: {summary.get("rows", 0)}
- Leakage audit: {_mapping(summary.get("leakage_audit")).get("status", "UNKNOWN")}

## Model suite

{chr(10).join(f'- {name}' for name in _string_list(summary.get("model_suite")))}

The vision-fusion member uses prefix-only candle geometry. Full screenshot
embeddings were excluded because unmasked screenshots contain future pixels.

## Out-of-sample results

| Metric | Result |
|---|---:|
| Event-conditioned direction accuracy | {_percent(summary.get("event_conditioned_direction_accuracy"))} |
| Target-before-invalidation precision | {_percent(summary.get("target_before_invalidation_precision"))} |
| High-confidence selective precision | {_percent(summary.get("high_confidence_selective_precision"))} |
| High-confidence coverage | {_percent(summary.get("high_confidence_coverage"))} |
| Visible pullback resolution | {_percent(summary.get("visible_pullback_accuracy"))} |
| Counter-move classification | {_percent(summary.get("future_counter_move_accuracy"))} |
| Brier score | {float(calibration.get("brier") or 0.0):.6f} |
| Log loss | {float(calibration.get("log_loss") or 0.0):.6f} |
| Expected calibration error | {float(calibration.get("ece") or 0.0):.6f} |

## Event breakdown

| Event | Rows | Direction accuracy | Selected | Selected precision |
|---|---:|---:|---:|---:|
{chr(10).join(event_rows)}

## Acceptance gates

| Gate | Result |
|---|---|
{chr(10).join(gate_rows)}

## Disk contract

- Free before: {free_before:.3f} GB
- Free after: {free_after:.3f} GB
- Required reserve: 45.000 GB
- Images duplicated: no
- Runtime artifact: {artifact_path}
- Packaged artifact: {packaged_path if packaged_path else "not packaged because promotion gates failed"}

## Integration rule

If promoted, V3 may consume this as masked_future_optimized_v3,
opportunity_maturity, target_before_invalidation, and pullback-resolution evidence.
It remains incapable of direct execution authorization.
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", default=".codex_runtime/masked_future_v3/corpus_cache.jsonl")
    parser.add_argument("--old-summary", default=".codex_runtime/masked_future_v3/final/summary.json")
    parser.add_argument("--audit-report", default="reports/MASKED_FUTURE_ROOT_CAUSE_ANALYSIS.md")
    parser.add_argument("--output-dir", default=".codex_runtime/optimized_masked_future")
    parser.add_argument("--final-report", default="reports/FINAL_OPTIMIZED_MASKED_FUTURE_RETRAIN_REPORT.md")
    parser.add_argument("--minimum-free-gb", type=float, default=45.0)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--horizons", default="3,5,8,13,21,34")
    parser.add_argument("--minimum-prefix", type=int, default=24)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--neural-epochs", type=int, default=1)
    for flag in (
        "event-windows",
        "train-empirical",
        "train-boosted",
        "train-sequence",
        "train-fusion",
        "train-metalabeler",
        "calibrate",
        "leakage-audit",
    ):
        parser.add_argument(f"--{flag}", action="store_true")
    args = parser.parse_args()
    audit_report = Path(args.audit_report)
    if not audit_report.is_file():
        raise SystemExit(
            "PG_OPTIMIZED_TRAINING_BLOCKED: root-cause audit must exist before training"
        )
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    free_before = enforce_disk_reserve(output_dir, minimum_free_gb=args.minimum_free_gb)
    records = load_cached_sequences_v3(args.cache)
    horizons = tuple(int(value.strip()) for value in args.horizons.split(",") if value.strip())
    dataset = build_optimized_dataset_v3(
        records,
        folds=args.folds,
        horizons=horizons,
        minimum_prefix=args.minimum_prefix,
        stride=args.stride,
    )
    old_summary = _mapping(json.loads(Path(args.old_summary).read_text(encoding="utf-8")))
    old_cv = _mapping(old_summary.get("cross_validation"))
    summary, predictions = cross_validate_optimized_hidden_state_v3(
        dataset,
        folds=args.folds,
        neural_epochs=args.neural_epochs,
        minimum_free_gb=args.minimum_free_gb,
        reserve_path=output_dir,
        old_cross_validation=old_cv,
    )
    predictions_path = output_dir / "predictions.jsonl.gz"
    with gzip.open(predictions_path, "wt", encoding="utf-8", compresslevel=6) as handle:
        for row in predictions:
            handle.write(json.dumps(row, separators=(",", ":"), ensure_ascii=True))
            handle.write("\n")
    bundle, production_training = train_production_bundle_v3(
        dataset,
        neural_epochs=args.neural_epochs,
    )
    artifact_path = save_optimized_model_v3(
        bundle,
        summary=summary,
        training_report=production_training,
        path=output_dir / DEFAULT_OPTIMIZED_MODEL_NAME,
    )
    packaged_path: Path | None = None
    if bool(_mapping(summary.get("promotion")).get("eligible", False)):
        packaged_path = Path("Backend") / "src" / "phoenixguard" / DEFAULT_OPTIMIZED_MODEL_NAME
        packaged_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(artifact_path, packaged_path)
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=True),
        encoding="utf-8",
    )
    free_after = enforce_disk_reserve(output_dir, minimum_free_gb=args.minimum_free_gb)
    report = render_final_report(
        summary,
        artifact_path=artifact_path,
        packaged_path=packaged_path,
        free_before=free_before,
        free_after=free_after,
    )
    final_report = Path(args.final_report)
    final_report.parent.mkdir(parents=True, exist_ok=True)
    final_report.write_text(report, encoding="utf-8")
    print(json.dumps({
        "status": "PROMOTED" if packaged_path else "DIAGNOSTIC_ONLY",
        "reason": _mapping(summary.get("promotion")).get("reason"),
        "precision": summary.get("high_confidence_selective_precision"),
        "coverage": summary.get("high_confidence_coverage"),
        "pullback": summary.get("visible_pullback_accuracy"),
        "artifact": str(artifact_path),
        "packaged": str(packaged_path) if packaged_path else "",
        "free_gb": round(free_after, 3),
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
