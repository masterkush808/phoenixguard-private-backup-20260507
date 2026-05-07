from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPLIT_MANIFEST = PROJECT_ROOT / "data_splits" / "split_manifest.csv"
DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "data_splits" / "sequence_teacher_manifest.jsonl"
MANIFEST_METADATA_SUFFIX = ".meta.json"
_DIRECTIONAL_LABEL_TASKS = ("projection_direction", "next_box_direction")
_TRADE_READY_SETUPS = {"consolidation_breakout", "impulse_chain", "reversal_release"}


def _normalize_direction(value: Any) -> str | None:
    text = str(value).strip().upper()
    return text if text else None


def _normalize_lower(value: Any) -> str | None:
    text = str(value).strip().lower()
    return text if text else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clip01(value: Any) -> float:
    return float(max(0.0, min(1.0, _safe_float(value, 0.0))))


def _teacher_label_direction(value: Any) -> str | None:
    normalized = _normalize_direction(value)
    return normalized if normalized in {"BUY", "SELL"} else None


def _build_raw_sequence_targets(
    record: Mapping[str, Any],
    *,
    sequence_targets: Mapping[str, Any],
    adjustments: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    existing_raw_targets_obj = record.get("raw_sequence_targets", {})
    raw_targets = (
        {str(task_name): task_value for task_name, task_value in existing_raw_targets_obj.items()}
        if isinstance(existing_raw_targets_obj, Mapping)
        else {}
    )

    for task_name, task_value in sequence_targets.items():
        raw_targets.setdefault(str(task_name), task_value)

    for task_name, task_payload in adjustments.items():
        if str(task_name) not in _DIRECTIONAL_LABEL_TASKS or not isinstance(task_payload, Mapping):
            continue
        raw_direction = _normalize_direction(task_payload.get("from"))
        if raw_direction in {"BUY", "SELL"}:
            raw_targets[str(task_name)] = raw_direction

    projection = _mapping(record.get("projection", {}))
    next_box = _mapping(record.get("next_box", {}))
    if "projection_direction" not in raw_targets:
        projection_direction = _teacher_label_direction(projection.get("direction"))
        if projection_direction is not None:
            raw_targets["projection_direction"] = projection_direction
    if "next_box_direction" not in raw_targets:
        next_box_direction = _teacher_label_direction(next_box.get("direction"))
        if next_box_direction is not None:
            raw_targets["next_box_direction"] = next_box_direction
    return raw_targets


def _direction_alignment(trade_label: str | None, observed_direction: str | None) -> str:
    if trade_label not in {"BUY", "SELL"} or observed_direction not in {"BUY", "SELL"}:
        return "unknown"
    return "aligned" if trade_label == observed_direction else "opposed"


def _timing_label(
    *,
    action_direction: str | None,
    decision_state: str | None,
    execution_permission: str | None,
) -> str:
    if action_direction in {"BUY", "SELL"} and str(execution_permission).upper() == "EXECUTE":
        return "execute"
    if action_direction in {"BUY", "SELL"} or str(decision_state).upper() == "PROJECTED":
        return "projected"
    return "wait"


def build_teacher_task_labels(record: Mapping[str, Any]) -> dict[str, Any]:
    label_direction = _teacher_label_direction(record.get("label"))
    sequence_targets_obj = record.get("sequence_targets", {})
    sequence_targets = dict(sequence_targets_obj) if isinstance(sequence_targets_obj, Mapping) else {}
    adjustments_obj = record.get("teacher_target_adjustments", {})
    adjustments = (
        {
            str(task_name): dict(task_payload)
            for task_name, task_payload in adjustments_obj.items()
            if isinstance(task_payload, Mapping)
        }
        if isinstance(adjustments_obj, Mapping)
        else {}
    )
    raw_targets = _build_raw_sequence_targets(record, sequence_targets=sequence_targets, adjustments=adjustments)

    projection = _mapping(record.get("projection", {}))
    next_box = _mapping(record.get("next_box", {}))
    chart_state = _mapping(record.get("chart_state", {}))

    raw_projection_direction = _teacher_label_direction(
        raw_targets.get("projection_direction", projection.get("direction"))
    )
    raw_next_box_direction = _teacher_label_direction(
        raw_targets.get("next_box_direction", next_box.get("direction"))
    )
    effective_projection_direction = _teacher_label_direction(sequence_targets.get("projection_direction"))
    effective_next_box_direction = _teacher_label_direction(sequence_targets.get("next_box_direction"))
    action_direction = _teacher_label_direction(record.get("action"))
    decision_state = str(record.get("decision_state", "")).strip().upper()
    execution_permission = str(record.get("execution_permission", "")).strip().upper()
    structure_setup = str(
        chart_state.get(
            "structure_setup",
            record.get("structure_setup", ""),
        )
    ).strip().lower()

    projection_confidence = _clip01(projection.get("confidence", chart_state.get("projection_bias_confidence", 0.0)))
    next_box_confidence = _clip01(next_box.get("confidence", projection_confidence))
    projection_dominance = _clip01(
        projection.get(
            "dominance",
            projection.get("dominance_gap", chart_state.get("projection_dominance", 0.0)),
        )
    )
    action_confidence = _clip01(record.get("confidence", 0.0))

    projection_alignment = _direction_alignment(label_direction, raw_projection_direction)
    next_box_alignment = _direction_alignment(label_direction, raw_next_box_direction)
    action_alignment = _direction_alignment(label_direction, action_direction)

    review_reasons: list[str] = []
    if projection_alignment == "opposed" and projection_confidence >= 0.50:
        review_reasons.append("projection_opposes_trade_label")
    if next_box_alignment == "opposed" and next_box_confidence >= 0.58:
        review_reasons.append("next_box_opposes_trade_label")
    if action_alignment == "opposed" and action_confidence >= 0.34:
        review_reasons.append("action_opposes_trade_label")
    if action_direction is None and label_direction in {"BUY", "SELL"}:
        review_reasons.append("directional_label_resolved_to_hold")
        if structure_setup in _TRADE_READY_SETUPS:
            review_reasons.append("trade_ready_structure_resolved_to_hold")

    timing_label = _timing_label(
        action_direction=action_direction,
        decision_state=decision_state,
        execution_permission=execution_permission,
    )

    if action_alignment == "opposed" and projection_alignment == "opposed":
        review_bucket = "hard_negative"
    elif projection_alignment == "opposed" or next_box_alignment == "opposed":
        review_bucket = "projection_conflict"
    elif action_direction is None and (raw_projection_direction in {"BUY", "SELL"} or raw_next_box_direction in {"BUY", "SELL"}):
        review_bucket = "ambiguous_wait"
    else:
        review_bucket = "clean_alignment"

    if review_bucket == "hard_negative":
        label_quality = "contradictory"
    elif review_bucket == "projection_conflict":
        label_quality = "contradictory" if projection_confidence >= 0.64 or next_box_confidence >= 0.70 else "review_required"
    elif review_bucket == "ambiguous_wait":
        label_quality = "ambiguous"
    else:
        label_quality = "clean"

    review_priority = 0.0
    if projection_alignment == "opposed":
        review_priority += 0.26 + 0.30 * projection_confidence + 0.14 * projection_dominance
    if next_box_alignment == "opposed":
        review_priority += 0.20 + 0.22 * next_box_confidence
    if action_alignment == "opposed":
        review_priority += 0.22 + 0.18 * action_confidence
    if action_direction is None:
        review_priority += 0.10
    if decision_state == "PROJECTED":
        review_priority += 0.06
    if structure_setup in _TRADE_READY_SETUPS:
        review_priority += 0.08
    review_priority = float(min(review_priority, 1.0))
    review_required = bool(review_bucket != "clean_alignment")

    return {
        "trade_direction_label": label_direction or "",
        "execution_direction_label": action_direction or "HOLD",
        "effective_projection_direction_label": effective_projection_direction or "",
        "effective_next_box_direction_label": effective_next_box_direction or "",
        "raw_projection_direction": raw_projection_direction or "",
        "raw_next_box_direction": raw_next_box_direction or "",
        "projection_alignment": projection_alignment,
        "next_box_alignment": next_box_alignment,
        "action_alignment": action_alignment,
        "timing_label": timing_label,
        "decision_state": decision_state.lower(),
        "execution_permission": execution_permission.lower(),
        "structure_setup": structure_setup,
        "label_quality": label_quality,
        "review_bucket": review_bucket,
        "review_required": review_required,
        "review_priority": review_priority,
        "review_reasons": review_reasons,
    }


def extract_sequence_targets(result: Mapping[str, Any]) -> dict[str, str]:
    projection = _mapping(result.get("projection", {}))
    chart_state = _mapping(result.get("chart_state", {}))
    current_box = _mapping(result.get("current_box", {}))
    next_box = _mapping(projection.get("next_box", chart_state.get("projected_next_box", {})))
    swing_state = _mapping(projection.get("swing_state", chart_state.get("swing_state", {})))

    targets: dict[str, str] = {}

    for task_name, raw_value in (
        ("projection_direction", projection.get("direction")),
        ("current_box_direction", current_box.get("direction")),
        ("next_box_direction", next_box.get("direction")),
        ("macro_trend", chart_state.get("macro_trend", swing_state.get("macro_trend"))),
    ):
        value = _normalize_direction(raw_value)
        if value is not None:
            targets[task_name] = value

    for task_name, raw_value in (
        ("current_box_type", current_box.get("box_type")),
        ("next_box_type", next_box.get("box_type")),
        ("trigger", next_box.get("trigger")),
        ("projected_role", swing_state.get("projected_role")),
        ("entry_type", chart_state.get("entry_type")),
        ("local_phase", chart_state.get("local_phase")),
        ("swing_phase", swing_state.get("swing_phase")),
        ("structure_setup", chart_state.get("structure_setup")),
    ):
        value = _normalize_lower(raw_value)
        if value is not None:
            targets[task_name] = value

    return targets


def normalize_teacher_manifest_record(record: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(record)
    if str(normalized.get("error", "")).strip():
        return normalized
    sequence_targets_obj = normalized.get("sequence_targets", {})
    sequence_targets = dict(sequence_targets_obj) if isinstance(sequence_targets_obj, Mapping) else {}
    label_direction = _teacher_label_direction(normalized.get("label"))
    existing_adjustments_obj = normalized.get("teacher_target_adjustments", {})
    adjustments = (
        {
            str(task_name): dict(task_payload)
            for task_name, task_payload in existing_adjustments_obj.items()
            if isinstance(task_payload, Mapping)
        }
        if isinstance(existing_adjustments_obj, Mapping)
        else {}
    )
    raw_sequence_targets = _build_raw_sequence_targets(
        normalized,
        sequence_targets=sequence_targets,
        adjustments=adjustments,
    )

    if label_direction is not None:
        for task_name in _DIRECTIONAL_LABEL_TASKS:
            raw_direction = _normalize_direction(sequence_targets.get(task_name))
            sequence_targets[task_name] = label_direction
            if raw_direction in {"BUY", "SELL"} and raw_direction != label_direction:
                adjustments[task_name] = {
                    "from": raw_direction,
                    "to": label_direction,
                    "reason": "align_with_trade_label",
                }

    normalized["sequence_targets"] = sequence_targets
    normalized["raw_sequence_targets"] = raw_sequence_targets
    if adjustments:
        normalized["teacher_target_adjustments"] = adjustments
    elif "teacher_target_adjustments" in normalized:
        normalized.pop("teacher_target_adjustments", None)
    normalized["teacher_task_labels"] = build_teacher_task_labels(normalized)
    return normalized


def normalize_teacher_manifest_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [normalize_teacher_manifest_record(record) for record in records]


def _write_teacher_manifest_records(manifest_path: Path, records: list[dict[str, Any]]) -> None:
    output_path = manifest_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    temp_output_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(output_path.parent),
            prefix=f"{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output_file:
            temp_output_path = Path(output_file.name)
            for record in records:
                output_file.write(json.dumps(record, ensure_ascii=True) + "\n")
        if temp_output_path is None:
            raise RuntimeError("Internal error: normalized teacher manifest temp file was not created.")
        os.replace(str(temp_output_path), str(output_path))
    finally:
        if temp_output_path is not None and temp_output_path.exists():
            temp_output_path.unlink(missing_ok=True)


def normalize_teacher_manifest_file(manifest_path: Path) -> dict[str, int | bool]:
    manifest = manifest_path.expanduser().resolve()
    if not manifest.exists():
        raise FileNotFoundError(f"Sequence teacher manifest not found: {manifest}")

    records = _read_teacher_manifest_records(manifest)
    normalized_records = normalize_teacher_manifest_records(records)
    adjusted_record_count = 0
    adjusted_target_count = 0
    for record in normalized_records:
        adjustments_obj = record.get("teacher_target_adjustments", {})
        if not isinstance(adjustments_obj, Mapping) or len(adjustments_obj) == 0:
            continue
        adjusted_record_count += 1
        adjusted_target_count += len(adjustments_obj)

    _write_teacher_manifest_records(manifest, normalized_records)
    review_summary = summarize_teacher_task_labels(normalized_records)

    metadata = load_teacher_manifest_metadata(manifest)
    metadata_updated = False
    if metadata is not None:
        metadata_payload = dict(metadata)
        metadata_payload["schema_version"] = max(_metadata_int(metadata_payload, "schema_version", 1), 3)
        metadata_payload["normalized_directional_targets"] = True
        metadata_payload["normalized_directional_tasks"] = list(_DIRECTIONAL_LABEL_TASKS)
        metadata_payload["normalized_record_count"] = int(len(normalized_records))
        metadata_payload["normalized_adjusted_record_count"] = int(adjusted_record_count)
        metadata_payload["normalized_adjusted_target_count"] = int(adjusted_target_count)
        metadata_payload["teacher_task_label_summary"] = review_summary
        metadata_path = teacher_manifest_metadata_path(manifest)
        temp_metadata_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=str(metadata_path.parent),
                prefix=f"{metadata_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as metadata_file:
                temp_metadata_path = Path(metadata_file.name)
                json.dump(metadata_payload, metadata_file, ensure_ascii=True, indent=2, sort_keys=True)
            if temp_metadata_path is None:
                raise RuntimeError("Internal error: normalized teacher metadata temp file was not created.")
            os.replace(str(temp_metadata_path), str(metadata_path))
            metadata_updated = True
        finally:
            if temp_metadata_path is not None and temp_metadata_path.exists():
                temp_metadata_path.unlink(missing_ok=True)

    return {
        "record_count": int(len(normalized_records)),
        "adjusted_record_count": int(adjusted_record_count),
        "adjusted_target_count": int(adjusted_target_count),
        "metadata_updated": bool(metadata_updated),
    }


def teacher_manifest_metadata_path(output_path: Path) -> Path:
    manifest_path = output_path.expanduser()
    return manifest_path.with_name(f"{manifest_path.name}{MANIFEST_METADATA_SUFFIX}")


def _count_split_manifest_rows(split_manifest_path: Path) -> int:
    with split_manifest_path.open("r", encoding="utf-8", newline="") as manifest_file:
        return sum(1 for _ in csv.DictReader(manifest_file))


def _scan_teacher_manifest(manifest_path: Path) -> dict[str, int]:
    record_count = 0
    error_count = 0
    empty_target_count = 0

    with manifest_path.open("r", encoding="utf-8") as manifest_file:
        for raw_line in manifest_file:
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            record_count += 1
            if str(payload.get("error", "")).strip():
                error_count += 1
            sequence_targets = payload.get("sequence_targets", {})
            if not isinstance(sequence_targets, Mapping) or len(sequence_targets) == 0:
                empty_target_count += 1

    return {
        "record_count": record_count,
        "error_count": error_count,
        "empty_target_count": empty_target_count,
    }


def _read_teacher_manifest_records(manifest_path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with manifest_path.open("r", encoding="utf-8") as manifest_file:
        for raw_line in manifest_file:
            line = raw_line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                records.append(payload)
    return records


def summarize_directional_teacher_consistency(
    records: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, float | int]]]:
    summary: dict[str, dict[str, dict[str, float | int]]] = {}
    for task_name in _DIRECTIONAL_LABEL_TASKS:
        task_summary: dict[str, dict[str, float | int]] = {}
        for label in ("BUY", "SELL"):
            task_summary[label] = {
                "rows": 0,
                "contradictions": 0,
                "contradiction_rate": 0.0,
            }
        summary[task_name] = task_summary

    for record in records:
        label = str(record.get("label", "")).strip().upper()
        if label not in {"BUY", "SELL"}:
            continue
        sequence_targets = record.get("sequence_targets", {})
        if not isinstance(sequence_targets, Mapping):
            continue
        for task_name in _DIRECTIONAL_LABEL_TASKS:
            task_value = str(sequence_targets.get(task_name, "")).strip().upper()
            if task_value not in {"BUY", "SELL"}:
                continue
            label_summary = summary[task_name][label]
            label_summary["rows"] = int(label_summary["rows"]) + 1
            if task_value != label:
                label_summary["contradictions"] = int(label_summary["contradictions"]) + 1

    for task_summary in summary.values():
        for label_summary in task_summary.values():
            rows = int(label_summary["rows"])
            contradictions = int(label_summary["contradictions"])
            label_summary["contradiction_rate"] = float(contradictions / rows) if rows > 0 else 0.0

    return summary


def validate_directional_teacher_consistency(
    records: list[dict[str, Any]],
    *,
    contradiction_rate_limit: float = 0.58,
    min_rows_per_label: int = 12,
) -> dict[str, dict[str, dict[str, float | int]]]:
    summary = summarize_directional_teacher_consistency(records)
    issues: list[str] = []
    for task_name, task_summary in summary.items():
        for label, label_summary in task_summary.items():
            rows = int(label_summary["rows"])
            contradiction_rate = float(label_summary["contradiction_rate"])
            contradictions = int(label_summary["contradictions"])
            if rows >= int(min_rows_per_label) and contradiction_rate > float(contradiction_rate_limit):
                issues.append(
                    f"{task_name} contradicts {label} on {contradictions}/{rows} rows "
                    f"({contradiction_rate:.1%})"
                )
    if issues:
        raise RuntimeError(
            "Manifest directional targets contradict the label distribution too often: "
            + "; ".join(issues)
            + ". Rebuild the sequence teacher manifest after fixing projection logic."
        )
    return summary


def summarize_teacher_task_labels(records: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {
        "label_quality": {},
        "review_bucket": {},
        "timing_label": {},
    }
    review_required_count = 0

    for record in records:
        if str(record.get("error", "")).strip():
            continue
        task_labels_obj = record.get("teacher_task_labels", {})
        task_labels = (
            dict(task_labels_obj)
            if isinstance(task_labels_obj, Mapping)
            else build_teacher_task_labels(record)
        )
        for key in ("label_quality", "review_bucket", "timing_label"):
            value = str(task_labels.get(key, "")).strip().lower()
            if value:
                summary[key][value] = int(summary[key].get(value, 0)) + 1
        if bool(task_labels.get("review_required", False)):
            review_required_count += 1

    summary["review_required"] = {"true": int(review_required_count)}
    return summary


def load_teacher_manifest_metadata(manifest_path: Path) -> dict[str, Any] | None:
    metadata_path = teacher_manifest_metadata_path(manifest_path)
    if not metadata_path.exists():
        return None
    with metadata_path.open("r", encoding="utf-8") as metadata_file:
        payload = json.load(metadata_file)
    return payload if isinstance(payload, dict) else None


def _metadata_int(metadata: Mapping[str, Any], key: str, default: int = -1) -> int:
    value = metadata.get(key, default)
    try:
        return int(value)
    except Exception:
        return int(default)


def validate_teacher_manifest(
    *,
    split_manifest_path: Path,
    manifest_path: Path,
) -> dict[str, Any]:
    split_manifest = split_manifest_path.expanduser().resolve()
    manifest = manifest_path.expanduser().resolve()
    if not manifest.exists():
        raise FileNotFoundError(f"Sequence teacher manifest not found: {manifest}")

    split_row_count = _count_split_manifest_rows(split_manifest)
    manifest_stats = _scan_teacher_manifest(manifest)
    metadata = load_teacher_manifest_metadata(manifest)

    if int(manifest_stats["record_count"]) != int(split_row_count):
        raise RuntimeError(
            f"Manifest row count mismatch for {manifest}: "
            f"found {manifest_stats['record_count']} rows, expected {split_row_count} "
            f"from {split_manifest}. Rebuild the teacher manifest."
        )
    if int(manifest_stats["error_count"]) > 0:
        raise RuntimeError(
            f"Manifest {manifest} contains {manifest_stats['error_count']} error rows. "
            "Rebuild the teacher manifest before training."
        )
    if int(manifest_stats["empty_target_count"]) > 0:
        raise RuntimeError(
            f"Manifest {manifest} contains {manifest_stats['empty_target_count']} rows "
            "with empty sequence targets. Rebuild the teacher manifest before training."
        )
    validate_directional_teacher_consistency(_read_teacher_manifest_records(manifest))

    if metadata is None:
        return {
            "record_count": int(manifest_stats["record_count"]),
            "split_row_count": int(split_row_count),
            "metadata_present": False,
        }

    split_stat = split_manifest.stat()
    metadata_split_path = str(metadata.get("split_manifest_path", "")).strip()
    if metadata_split_path and Path(metadata_split_path).expanduser().resolve() != split_manifest:
        raise RuntimeError(
            f"Manifest metadata points to {metadata_split_path}, expected {split_manifest}. "
            "Rebuild the teacher manifest."
        )

    metadata_expected_rows = _metadata_int(metadata, "expected_rows", -1)
    metadata_written_rows = _metadata_int(metadata, "written_rows", -1)
    metadata_error_count = _metadata_int(metadata, "error_count", -1)
    metadata_split_size = _metadata_int(metadata, "split_manifest_size", -1)
    metadata_split_mtime_ns = _metadata_int(metadata, "split_manifest_mtime_ns", -1)

    if metadata_expected_rows != int(split_row_count):
        raise RuntimeError(
            f"Manifest metadata expects {metadata_expected_rows} rows, but {split_manifest} "
            f"currently has {split_row_count}. Rebuild the teacher manifest."
        )
    if metadata_written_rows != int(manifest_stats["record_count"]):
        raise RuntimeError(
            f"Manifest metadata wrote {metadata_written_rows} rows, but {manifest} contains "
            f"{manifest_stats['record_count']}. Rebuild the teacher manifest."
        )
    if metadata_error_count != 0:
        raise RuntimeError(
            f"Manifest metadata reports {metadata_error_count} extraction errors. "
            "Rebuild the teacher manifest before training."
        )
    if metadata_split_size >= 0 and metadata_split_size != int(split_stat.st_size):
        raise RuntimeError(
            f"Split manifest size changed since teacher generation ({metadata_split_size} -> "
            f"{split_stat.st_size}). Rebuild the teacher manifest."
        )
    if metadata_split_mtime_ns >= 0 and metadata_split_mtime_ns != int(split_stat.st_mtime_ns):
        raise RuntimeError(
            "Split manifest timestamp changed since teacher generation. "
            "Rebuild the teacher manifest."
        )

    return {
        "record_count": int(manifest_stats["record_count"]),
        "split_row_count": int(split_row_count),
        "metadata_present": True,
    }


def export_teacher_review_queue(
    *,
    manifest_path: Path,
    output_path: Path,
    min_priority: float = 0.25,
    limit: int | None = None,
) -> dict[str, int | float]:
    manifest = manifest_path.expanduser().resolve()
    if not manifest.exists():
        raise FileNotFoundError(f"Sequence teacher manifest not found: {manifest}")

    records = _read_teacher_manifest_records(manifest)
    rows: list[dict[str, Any]] = []
    for record in records:
        if str(record.get("error", "")).strip():
            continue
        task_labels_obj = record.get("teacher_task_labels", {})
        task_labels = (
            dict(task_labels_obj)
            if isinstance(task_labels_obj, Mapping)
            else build_teacher_task_labels(record)
        )
        if not bool(task_labels.get("review_required", False)):
            continue
        priority = _clip01(task_labels.get("review_priority", 0.0))
        if priority < float(min_priority):
            continue

        projection = _mapping(record.get("projection", {}))
        next_box = _mapping(record.get("next_box", {}))
        row = {
            "image_path": str(record.get("image_path", "")),
            "source_path": str(record.get("source_path", "")),
            "split": str(record.get("split", "")).lower(),
            "trade_label": str(record.get("label", "")).upper(),
            "action": str(record.get("action", "")).upper(),
            "decision_state": str(record.get("decision_state", task_labels.get("decision_state", ""))).upper(),
            "execution_permission": str(record.get("execution_permission", task_labels.get("execution_permission", ""))).upper(),
            "label_quality": str(task_labels.get("label_quality", "")).lower(),
            "review_bucket": str(task_labels.get("review_bucket", "")).lower(),
            "review_priority": f"{priority:.4f}",
            "review_reasons": "|".join(str(reason) for reason in task_labels.get("review_reasons", [])),
            "timing_label": str(task_labels.get("timing_label", "")).lower(),
            "raw_projection_direction": str(task_labels.get("raw_projection_direction", "")).upper(),
            "raw_next_box_direction": str(task_labels.get("raw_next_box_direction", "")).upper(),
            "effective_projection_direction": str(task_labels.get("effective_projection_direction_label", "")).upper(),
            "effective_next_box_direction": str(task_labels.get("effective_next_box_direction_label", "")).upper(),
            "projection_confidence": f"{_clip01(projection.get('confidence', 0.0)):.4f}",
            "projection_dominance": f"{_clip01(projection.get('dominance', 0.0)):.4f}",
            "next_box_confidence": f"{_clip01(next_box.get('confidence', 0.0)):.4f}",
            "structure_setup": str(task_labels.get("structure_setup", "")).lower(),
        }
        rows.append(row)

    rows.sort(
        key=lambda row: (
            -_safe_float(row.get("review_priority", 0.0), 0.0),
            str(row.get("review_bucket", "")),
            str(row.get("image_path", "")),
        )
    )
    if limit is not None:
        rows = rows[: max(int(limit), 0)]

    output = output_path.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "image_path",
        "source_path",
        "split",
        "trade_label",
        "action",
        "decision_state",
        "execution_permission",
        "label_quality",
        "review_bucket",
        "review_priority",
        "review_reasons",
        "timing_label",
        "raw_projection_direction",
        "raw_next_box_direction",
        "effective_projection_direction",
        "effective_next_box_direction",
        "projection_confidence",
        "projection_dominance",
        "next_box_confidence",
        "structure_setup",
    ]

    temp_output_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=str(output.parent),
            prefix=f"{output.name}.",
            suffix=".tmp",
            delete=False,
        ) as output_file:
            temp_output_path = Path(output_file.name)
            writer = csv.DictWriter(output_file, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        if temp_output_path is None:
            raise RuntimeError("Internal error: review queue temp file was not created.")
        os.replace(str(temp_output_path), str(output))
    finally:
        if temp_output_path is not None and temp_output_path.exists():
            temp_output_path.unlink(missing_ok=True)

    return {
        "review_row_count": int(len(rows)),
        "min_priority": float(min_priority),
    }


def build_teacher_manifest(
    *,
    split_manifest_path: Path,
    output_path: Path,
    overlay_mode: str = "history-boxes",
    limit: int | None = None,
    allow_errors: bool = False,
) -> Path:
    split_manifest_path = split_manifest_path.expanduser().resolve()
    output_path = output_path.expanduser().resolve()
    if str(PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(PROJECT_ROOT))

    import main as pg_main_module

    with split_manifest_path.open("r", encoding="utf-8", newline="") as manifest_file:
        all_rows = list(csv.DictReader(manifest_file))

    source_total_rows = len(all_rows)
    rows = list(all_rows)

    if limit is not None:
        rows = rows[: max(int(limit), 0)]

    output_path.parent.mkdir(parents=True, exist_ok=True)

    total_rows = len(rows)
    error_count = 0
    adjusted_record_count = 0
    adjusted_target_count = 0
    split_stat = split_manifest_path.stat()
    temp_output_path: Path | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(output_path.parent),
            prefix=f"{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as output_file:
            temp_output_path = Path(output_file.name)
            for index, row in enumerate(rows, start=1):
                source_path = str(row.get("source_path", "")).strip()
                image_path = str(row.get("destination_path", "")).strip()
                label = str(row.get("label", "")).strip().upper()

                record: dict[str, Any] = {
                    "image_path": image_path,
                    "source_path": source_path,
                    "label": label,
                    "split": str(row.get("split", "")).strip().lower(),
                    "sequence_targets": {},
                }

                try:
                    result, _overlay, _gauge, _skill_fig = pg_main_module.run_inference(
                        source_path,
                        overlay_mode=overlay_mode,
                        side_effect_free=True,
                    )
                    projection = _mapping(result.get("projection", {}))
                    chart_state = _mapping(result.get("chart_state", {}))
                    next_box = _mapping(projection.get("next_box", chart_state.get("projected_next_box", {})))
                    swing_state = _mapping(projection.get("swing_state", chart_state.get("swing_state", {})))
                    raw_sequence_targets = extract_sequence_targets(result)

                    record["sequence_targets"] = dict(raw_sequence_targets)
                    record["raw_sequence_targets"] = dict(raw_sequence_targets)
                    record["projection"] = {
                        "direction": str(projection.get("direction", "")).upper(),
                        "box_type": str(projection.get("box_type", "")).lower(),
                        "confidence": float(projection.get("confidence", 0.0) or 0.0),
                        "dominance": float(projection.get("dominance", 0.0) or 0.0),
                        "explanation": str(projection.get("explanation", "")).strip(),
                    }
                    record["chart_state"] = {
                        "entry_type": str(chart_state.get("entry_type", "")).lower(),
                        "macro_trend": str(chart_state.get("macro_trend", "")).upper(),
                        "local_phase": str(chart_state.get("local_phase", "")).lower(),
                        "structure_setup": str(chart_state.get("structure_setup", "")).lower(),
                        "structure_setup_source": str(chart_state.get("structure_setup_source", "")).lower(),
                        "structure_trade_ready": bool(chart_state.get("structure_trade_ready", False)),
                        "path_clarity": float(chart_state.get("path_clarity", 0.0) or 0.0),
                        "projection_bias_direction": str(chart_state.get("projection_bias_direction", "")).upper(),
                        "projection_bias_confidence": float(chart_state.get("projection_bias_confidence", 0.0) or 0.0),
                        "projection_dominance": float(chart_state.get("projection_dominance", 0.0) or 0.0),
                    }
                    record["next_box"] = {
                        "box_type": str(next_box.get("box_type", "")).lower(),
                        "direction": str(next_box.get("direction", "")).upper(),
                        "trigger": str(next_box.get("trigger", "")).lower(),
                        "confidence": float(next_box.get("confidence", 0.0) or 0.0),
                        "path_clarity": float(next_box.get("path_clarity", 0.0) or 0.0),
                    }
                    record["swing_state"] = {
                        "macro_trend": str(swing_state.get("macro_trend", "")).upper(),
                        "swing_phase": str(swing_state.get("swing_phase", "")).lower(),
                        "projected_role": str(swing_state.get("projected_role", "")).lower(),
                        "summary": str(swing_state.get("summary", "")).strip(),
                    }
                    record["action"] = str(result.get("action", "")).upper()
                    record["decision_state"] = str(result.get("decision_state", "")).upper()
                    record["execution_permission"] = str(result.get("execution_permission", "")).upper()
                    record["confidence"] = float(result.get("confidence", 0.0) or 0.0)
                    record = normalize_teacher_manifest_record(record)
                    adjustments_obj = record.get("teacher_target_adjustments", {})
                    if isinstance(adjustments_obj, Mapping) and len(adjustments_obj) > 0:
                        adjusted_record_count += 1
                        adjusted_target_count += len(adjustments_obj)
                except Exception as exc:
                    record["error"] = str(exc)
                    error_count += 1

                output_file.write(json.dumps(record, ensure_ascii=True) + "\n")

                if index == total_rows or index % 10 == 0:
                    print(f"[SEQUENCE MANIFEST] processed {index}/{total_rows}")

        if error_count > 0 and not allow_errors:
            raise RuntimeError(
                f"Sequence teacher manifest build failed for {error_count}/{total_rows} images. "
                "Re-run after fixing the CV runtime/caches, or pass --allow-errors to keep partial rows."
            )

        if temp_output_path is None:
            raise RuntimeError("Internal error: teacher manifest temp file was not created.")
        os.replace(str(temp_output_path), str(output_path))
        normalized_records = _read_teacher_manifest_records(output_path)
        review_summary = summarize_teacher_task_labels(normalized_records)

        metadata = {
            "schema_version": 3,
            "split_manifest_path": str(split_manifest_path),
            "split_manifest_size": int(split_stat.st_size),
            "split_manifest_mtime_ns": int(split_stat.st_mtime_ns),
            "source_total_rows": int(source_total_rows),
            "expected_rows": int(total_rows),
            "written_rows": int(total_rows),
            "error_count": int(error_count),
            "allow_errors": bool(allow_errors),
            "overlay_mode": str(overlay_mode),
            "limit": int(limit) if limit is not None else None,
            "side_effect_free": True,
            "normalized_directional_targets": True,
            "normalized_directional_tasks": list(_DIRECTIONAL_LABEL_TASKS),
            "normalized_adjusted_record_count": int(adjusted_record_count),
            "normalized_adjusted_target_count": int(adjusted_target_count),
            "teacher_task_label_summary": review_summary,
        }
        metadata_path = teacher_manifest_metadata_path(output_path)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(metadata_path.parent),
            prefix=f"{metadata_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as metadata_file:
            temp_metadata_path = Path(metadata_file.name)
            json.dump(metadata, metadata_file, ensure_ascii=True, indent=2, sort_keys=True)
        os.replace(str(temp_metadata_path), str(metadata_path))
    finally:
        if temp_output_path is not None and temp_output_path.exists():
            temp_output_path.unlink(missing_ok=True)

    print(f"[SEQUENCE MANIFEST] wrote {output_path}")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a sequence-teacher manifest from the live PhoenixGuard CV inference pipeline.",
    )
    parser.add_argument(
        "--split-manifest",
        type=Path,
        default=DEFAULT_SPLIT_MANIFEST,
        help="CSV manifest produced by build_clean_split.py",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help="Output JSONL teacher manifest path",
    )
    parser.add_argument(
        "--overlay-mode",
        type=str,
        default="history-boxes",
        help="Overlay mode forwarded to main.run_inference()",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Optional row limit for smoke runs",
    )
    parser.add_argument(
        "--allow-errors",
        action="store_true",
        help="Keep partial rows even if some images fail teacher-label extraction.",
    )
    parser.add_argument(
        "--normalize-existing",
        action="store_true",
        help="Rewrite the output manifest in place to align directional targets and refresh teacher task-label metadata.",
    )
    parser.add_argument(
        "--export-review-queue",
        action="store_true",
        help="Write a prioritized CSV queue of contradictory or ambiguous teacher-label rows for manual review.",
    )
    parser.add_argument(
        "--review-output",
        type=Path,
        default=None,
        help="Optional CSV output path for --export-review-queue. Defaults to <output>.review.csv",
    )
    parser.add_argument(
        "--review-priority-min",
        type=float,
        default=0.25,
        help="Minimum review priority score to include when exporting the review queue.",
    )
    parser.add_argument(
        "--review-limit",
        type=int,
        default=None,
        help="Optional limit when exporting the review queue.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if bool(args.export_review_queue):
        review_output = (
            args.review_output
            if args.review_output is not None
            else args.output.with_name(f"{args.output.name}.review.csv")
        )
        stats = export_teacher_review_queue(
            manifest_path=args.output,
            output_path=review_output,
            min_priority=float(args.review_priority_min),
            limit=args.review_limit,
        )
        print(
            "[SEQUENCE MANIFEST] exported review queue "
            f"{stats['review_row_count']} rows to {review_output} "
            f"(min_priority={stats['min_priority']:.2f})"
        )
        return
    if bool(args.normalize_existing):
        stats = normalize_teacher_manifest_file(args.output)
        print(
            "[SEQUENCE MANIFEST] normalized "
            f"{stats['record_count']} rows; adjusted {stats['adjusted_target_count']} targets "
            f"across {stats['adjusted_record_count']} records"
        )
        return

    build_teacher_manifest(
        split_manifest_path=args.split_manifest,
        output_path=args.output,
        overlay_mode=str(args.overlay_mode),
        limit=args.limit,
        allow_errors=bool(args.allow_errors),
    )


if __name__ == "__main__":
    main()
