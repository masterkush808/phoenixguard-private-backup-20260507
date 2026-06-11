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


def _normalize_direction(value: Any) -> str | None:
    text = str(value).strip().upper()
    return text if text else None


def _normalize_lower(value: Any) -> str | None:
    text = str(value).strip().lower()
    return text if text else None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


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


def build_teacher_manifest(
    *,
    split_manifest_path: Path,
    output_path: Path,
    overlay_mode: str = "history-plus-projection",
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

                    record["sequence_targets"] = extract_sequence_targets(result)
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
                    record["confidence"] = float(result.get("confidence", 0.0) or 0.0)
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

        metadata = {
            "schema_version": 1,
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
        default="history-plus-projection",
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_teacher_manifest(
        split_manifest_path=args.split_manifest,
        output_path=args.output,
        overlay_mode=str(args.overlay_mode),
        limit=args.limit,
        allow_errors=bool(args.allow_errors),
    )


if __name__ == "__main__":
    main()
