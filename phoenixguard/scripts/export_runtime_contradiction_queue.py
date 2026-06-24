from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, cast


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


DEFAULT_SPLIT_ROOT = PROJECT_ROOT / "data" / "clean_split" / "test"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data_splits" / "runtime_contradiction_review.csv"
_READY_SETUPS = {"consolidation_breakout", "impulse_chain", "reversal_release"}
_VALID_ACTIONS = {"BUY", "SELL", "HOLD"}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clip01(value: Any, default: float = 0.0) -> float:
    return float(max(0.0, min(1.0, _safe_float(value, default))))


def _normalize_direction(value: Any) -> str:
    normalized = str(value).strip().upper()
    return normalized if normalized in {"BUY", "SELL"} else ""


def _normalize_action(value: Any) -> str:
    normalized = str(value).strip().upper()
    return normalized if normalized in _VALID_ACTIONS else "HOLD"


def _as_mapping(value: object) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else {}


def _canonical_label_dir_name(dir_name: str) -> str:
    normalized = str(dir_name).strip().upper()
    if normalized in {"BUY", "BUYS"}:
        return "BUY"
    if normalized in {"SELL", "SELLS"}:
        return "SELL"
    return normalized


def build_runtime_review_row(
    *,
    image_path: str,
    folder_label: str,
    result: Mapping[str, Any],
) -> dict[str, str]:
    chart_state = _as_mapping(result.get("chart_state", {}))
    projection = _as_mapping(result.get("projection", {}))

    action = _normalize_action(result.get("action"))
    decision_state = str(result.get("decision_state", "UNCERTAIN") or "UNCERTAIN").strip().upper()
    trade_bias = _normalize_action(result.get("trade_bias", action))
    execution_permission = str(
        result.get("execution_permission", "WAIT_FOR_CONFIRMATION") or "WAIT_FOR_CONFIRMATION"
    ).strip().upper()
    confidence = _clip01(result.get("confidence", 0.0))

    chart_direction = _normalize_direction(chart_state.get("direction"))
    direction_probability = _clip01(chart_state.get("direction_probability", 0.0))
    structure_setup = str(chart_state.get("structure_setup", "none") or "none").strip().lower()
    structure_setup_source = str(chart_state.get("structure_setup_source", "") or "").strip().lower()
    structure_ready = structure_setup in _READY_SETUPS

    projection_direction = _normalize_direction(
        projection.get("direction", chart_state.get("projection_bias_direction", ""))
    )
    projection_confidence = _clip01(
        projection.get("confidence", chart_state.get("projection_bias_confidence", 0.0))
    )
    projection_dominance = _clip01(
        projection.get(
            "dominance",
            projection.get("dominance_gap", chart_state.get("projection_dominance", 0.0)),
        )
    )
    projection_box_type = str(
        projection.get(
            "box_type",
            chart_state.get("structure_setup", "balance"),
        )
        or "balance"
    ).strip().lower()
    projection_support = bool(result.get("projection_support", False))
    projection_watch_ready = bool(result.get("projection_watch_ready", False))

    review_reasons: list[str] = []
    if action in {"BUY", "SELL"} and action != folder_label and confidence >= 0.34:
        review_reasons.append("action_opposes_label")
    if projection_direction in {"BUY", "SELL"} and projection_direction != folder_label and projection_confidence >= 0.58:
        review_reasons.append("projection_opposes_label")
    if (
        chart_direction in {"BUY", "SELL"}
        and chart_direction != folder_label
        and direction_probability >= 0.52
        and structure_ready
    ):
        review_reasons.append("structure_opposes_label")
    if action == "HOLD" and folder_label in {"BUY", "SELL"}:
        if projection_direction == folder_label and (projection_watch_ready or projection_support):
            review_reasons.append("aligned_projection_held")
        if chart_direction == folder_label and structure_ready:
            review_reasons.append("trade_ready_structure_held")

    opposing_reasons = {
        "action_opposes_label",
        "projection_opposes_label",
        "structure_opposes_label",
    }
    opposing_count = sum(1 for reason in review_reasons if reason in opposing_reasons)

    if opposing_count >= 2:
        review_bucket = "hard_negative"
    elif opposing_count == 1:
        review_bucket = "projection_conflict"
    elif review_reasons:
        review_bucket = "ambiguous_wait"
    else:
        review_bucket = "clean_alignment"

    review_priority = 0.0
    if "action_opposes_label" in review_reasons:
        review_priority += 0.28 + 0.20 * confidence
    if "projection_opposes_label" in review_reasons:
        review_priority += 0.24 + 0.26 * projection_confidence + 0.12 * projection_dominance
    if "structure_opposes_label" in review_reasons:
        review_priority += 0.16 + 0.18 * direction_probability
    if "aligned_projection_held" in review_reasons:
        review_priority += 0.16 + 0.14 * projection_confidence
    if "trade_ready_structure_held" in review_reasons:
        review_priority += 0.12 + 0.10 * direction_probability
    if decision_state == "PROJECTED":
        review_priority += 0.05
    if projection_support or projection_watch_ready:
        review_priority += 0.05
    review_priority = float(min(review_priority, 1.0))

    return {
        "image_path": str(image_path),
        "split": _canonical_label_dir_name(Path(image_path).parent.name),
        "trade_label": folder_label,
        "action": action,
        "trade_bias": trade_bias,
        "decision_state": decision_state,
        "execution_permission": execution_permission,
        "confidence": f"{confidence:.4f}",
        "chart_direction": chart_direction or "HOLD",
        "direction_probability": f"{direction_probability:.4f}",
        "structure_setup": structure_setup,
        "structure_setup_source": structure_setup_source,
        "projection_direction": projection_direction or "HOLD",
        "projection_box_type": projection_box_type,
        "projection_confidence": f"{projection_confidence:.4f}",
        "projection_dominance": f"{projection_dominance:.4f}",
        "projection_support": "true" if projection_support else "false",
        "projection_watch_ready": "true" if projection_watch_ready else "false",
        "review_bucket": review_bucket,
        "review_priority": f"{review_priority:.4f}",
        "review_reasons": "|".join(review_reasons),
    }


def build_runtime_error_row(
    *,
    image_path: str,
    folder_label: str,
    error: BaseException,
) -> dict[str, str]:
    return {
        "image_path": str(image_path),
        "split": _canonical_label_dir_name(Path(image_path).parent.name),
        "trade_label": folder_label,
        "action": "HOLD",
        "trade_bias": "HOLD",
        "decision_state": "ERROR",
        "execution_permission": "WAIT_FOR_CONFIRMATION",
        "confidence": "0.0000",
        "chart_direction": "HOLD",
        "direction_probability": "0.0000",
        "structure_setup": "none",
        "structure_setup_source": "",
        "projection_direction": "HOLD",
        "projection_box_type": "balance",
        "projection_confidence": "0.0000",
        "projection_dominance": "0.0000",
        "projection_support": "false",
        "projection_watch_ready": "false",
        "review_bucket": "runtime_error",
        "review_priority": "1.0000",
        "review_reasons": f"runtime_error:{type(error).__name__}",
    }


def export_runtime_contradiction_queue(
    *,
    split_root: Path,
    output_path: Path,
    min_priority: float = 0.0,
    limit: int | None = None,
    include_clean: bool = False,
    side_effect_free: bool = True,
    infer_fn: Callable[[Path], Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    resolved_split_root = split_root.expanduser().resolve()
    if not resolved_split_root.exists():
        raise FileNotFoundError(f"Split root not found: {resolved_split_root}")

    if infer_fn is None:
        import main

        def _default_infer(path: Path) -> Mapping[str, Any]:
            result, *_ = main.run_inference(str(path), side_effect_free=side_effect_free)
            return result

        infer_fn = _default_infer

    rows: list[dict[str, str]] = []
    bucket_counts: Counter[str] = Counter()
    evaluated_count = 0
    error_count = 0

    direct_label = _canonical_label_dir_name(resolved_split_root.name)
    if direct_label in {"BUY", "SELL"} and any(path.is_file() for path in resolved_split_root.iterdir()):
        label_dirs = [resolved_split_root]
    else:
        label_dirs = [path for path in sorted(resolved_split_root.iterdir()) if path.is_dir()]

    for label_dir in label_dirs:
        if not label_dir.is_dir():
            continue
        folder_label = _canonical_label_dir_name(label_dir.name)
        if folder_label not in {"BUY", "SELL"}:
            continue
        for image_path in sorted(label_dir.glob("*")):
            if not image_path.is_file():
                continue
            evaluated_count += 1
            try:
                result = infer_fn(image_path)
                row = build_runtime_review_row(
                    image_path=str(image_path),
                    folder_label=folder_label,
                    result=result,
                )
            except Exception as exc:
                error_count += 1
                row = build_runtime_error_row(
                    image_path=str(image_path),
                    folder_label=folder_label,
                    error=exc,
                )
            priority = _safe_float(row.get("review_priority", 0.0), 0.0)
            bucket = str(row.get("review_bucket", "clean_alignment"))
            if bucket == "clean_alignment" and not include_clean:
                continue
            if priority < float(min_priority):
                continue
            rows.append(row)
            bucket_counts[bucket] += 1

    rows.sort(
        key=lambda row: (
            -_safe_float(row.get("review_priority", 0.0), 0.0),
            str(row.get("review_bucket", "")),
            str(row.get("image_path", "")),
        )
    )
    if limit is not None and limit >= 0:
        rows = rows[: int(limit)]

    resolved_output = output_path.expanduser().resolve()
    resolved_output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "image_path",
        "split",
        "trade_label",
        "action",
        "trade_bias",
        "decision_state",
        "execution_permission",
        "confidence",
        "chart_direction",
        "direction_probability",
        "structure_setup",
        "structure_setup_source",
        "projection_direction",
        "projection_box_type",
        "projection_confidence",
        "projection_dominance",
        "projection_support",
        "projection_watch_ready",
        "review_bucket",
        "review_priority",
        "review_reasons",
    ]
    with resolved_output.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return {
        "split_root": str(resolved_split_root),
        "output_path": str(resolved_output),
        "evaluated_count": int(evaluated_count),
        "review_row_count": int(len(rows)),
        "bucket_counts": dict(bucket_counts),
        "error_count": int(error_count),
        "min_priority": float(min_priority),
        "limit": None if limit is None else int(limit),
        "include_clean": bool(include_clean),
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Replay PhoenixGuard inference over a split and export contradictory or ambiguous signal rows for review.",
    )
    parser.add_argument(
        "--split-root",
        type=Path,
        default=DEFAULT_SPLIT_ROOT,
        help="Root directory containing BUY/SELL split folders to replay.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="CSV output path for the contradiction review queue.",
    )
    parser.add_argument(
        "--min-priority",
        type=float,
        default=0.0,
        help="Minimum review priority required to keep a row.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Optional maximum number of rows to export after sorting by priority.",
    )
    parser.add_argument(
        "--include-clean",
        action="store_true",
        help="Include clean-alignment rows in the CSV instead of exporting only review rows.",
    )
    parser.add_argument(
        "--no-side-effect-free",
        action="store_true",
        help="Run inference with side_effect_free=False.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    stats = export_runtime_contradiction_queue(
        split_root=args.split_root,
        output_path=args.output,
        min_priority=float(args.min_priority),
        limit=args.limit,
        include_clean=bool(args.include_clean),
        side_effect_free=not bool(args.no_side_effect_free),
    )
    print(
        "[RUNTIME REVIEW] exported "
        f"{stats['review_row_count']} rows from {stats['evaluated_count']} images "
        f"to {stats['output_path']} "
        f"(buckets={stats['bucket_counts']})"
    )


if __name__ == "__main__":
    main()
