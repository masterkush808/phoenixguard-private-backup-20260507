from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

MANIFEST_FILENAME = "user_calibration_manifest.json"
SOURCE_BOXES_FILENAME = "808_shooter_boxes.json"

USER_CALIBRATED = "USER_CALIBRATED"
NOT_USER_CALIBRATED = "NOT_USER_CALIBRATED"

REQUIRED_TARGETS: tuple[str, ...] = (
    "broker_focus_area",
    "expiry_time_field",
    "hourly_plus",
    "hourly_input",
    "hourly_minus",
    "minute_plus",
    "minute_input",
    "minute_minus",
    "second_plus",
    "second_input",
    "second_minus",
    "buy_button",
    "sell_button",
)

OPTIONAL_TARGETS: tuple[str, ...] = (
    "chart_area",
    "amount_field",
    "confirmation_area",
    "confirmation_button",
    "position_area",
    "open_position_area",
)

TARGET_ALIASES: dict[str, tuple[str, ...]] = {
    "buy_button": ("buy_button", "buy_icon"),
    "sell_button": ("sell_button", "sell_icon"),
    "expiry_time_field": ("expiry_time_field", "time_input", "time_button", "time_box"),
    "hourly_plus": ("hourly_plus", "hour_plus", "hours_plus", "expiry_plus", "time_adjustment_plus", "hour_up"),
    "hourly_input": ("hourly_input", "hour_input", "hours_input"),
    "hourly_minus": ("hourly_minus", "hour_minus", "hours_minus", "expiry_minus", "time_adjustment_minus", "hour_down"),
    "minute_plus": ("minute_plus", "minutely_plus", "minutes_plus", "minute_up"),
    "minute_input": ("minute_input", "minutely_input", "minutes_input"),
    "minute_minus": ("minute_minus", "minutely_minus", "minutes_minus", "minute_down"),
    "second_plus": ("second_plus", "seconds_plus", "second_up"),
    "second_input": ("second_input", "seconds_input"),
    "second_minus": ("second_minus", "seconds_minus", "second_down"),
    "expiry_plus": ("expiry_plus", "time_adjustment_plus", "hourly_plus"),
    "expiry_minus": ("expiry_minus", "time_adjustment_minus", "hourly_minus"),
    "amount_field": ("amount_field", "amount_box", "stake_amount", "investment_amount", "amount_input"),
    "broker_focus_area": ("broker_focus_area", "broker_screen", "final_screen"),
    "chart_area": ("chart_area", "chart", "chart_region"),
    "confirmation_area": ("confirmation_area", "confirmation_button", "confirm_button"),
    "confirmation_button": ("confirmation_button", "confirm_button"),
    "position_area": ("position_area", "open_position_area", "positions_area"),
    "open_position_area": ("open_position_area", "position_area", "positions_area"),
}


@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    code: str
    message: str
    missing_targets: tuple[str, ...] = ()
    uncalibrated_targets: tuple[str, ...] = ()
    profile_id: str | None = None
    layout_id: str | None = None
    details: Mapping[str, Any] = field(default_factory=dict)


def load_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path)
    if not manifest_path.exists():
        raise FileNotFoundError(f"Calibration manifest not found: {manifest_path}")
    with manifest_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Calibration manifest must be a JSON object: {manifest_path}")
    return payload


def build_manifest(
    boxes_path: str | Path,
    *,
    profile_id: str = "default",
    layout_id: str | None = None,
    generated_at: str | None = None,
    artifact_paths: Sequence[str | Path] = (),
) -> dict[str, Any]:
    boxes_file = Path(boxes_path)
    boxes_payload = _read_json_object(boxes_file) if boxes_file.exists() else {}
    source_points = _extract_source_points(boxes_payload)
    resolved_layout_id = layout_id or _layout_id_from_payload(boxes_payload) or "default"

    required = {
        target: _target_record(target, source_points, required=True)
        for target in REQUIRED_TARGETS
    }
    optional = {
        target: _target_record(target, source_points, required=False)
        for target in OPTIONAL_TARGETS
        if _find_source_key(target, source_points) is not None
    }
    uncalibrated = [
        target
        for target, record in required.items()
        if record["status"] != USER_CALIBRATED
    ]

    artifacts = [str(Path(item)) for item in artifact_paths]
    if str(boxes_file) not in artifacts:
        artifacts.insert(0, str(boxes_file))

    return {
        "schema_version": 1,
        "manifest_kind": "PHOENIXGUARD_USER_CALIBRATION",
        "authoritative_execution_source": True,
        "generated_at": generated_at or _utc_now(),
        "required_targets": list(REQUIRED_TARGETS),
        "optional_targets": list(OPTIONAL_TARGETS),
        "profiles": {
            profile_id: {
                "profile_id": profile_id,
                "layouts": {
                    resolved_layout_id: {
                        "layout_id": resolved_layout_id,
                        "source_boxes_path": str(boxes_file),
                        "required_targets": required,
                        "optional_targets": optional,
                        "uncalibrated_artifacts": uncalibrated,
                        "runtime_artifacts": artifacts,
                    }
                },
            }
        },
    }


def validate_profile_layout(
    manifest_or_path: Mapping[str, Any] | str | Path,
    profile_id: str,
    layout_id: str,
) -> ValidationResult:
    try:
        manifest = (
            load_manifest(manifest_or_path)
            if isinstance(manifest_or_path, (str, Path))
            else dict(manifest_or_path)
        )
    except FileNotFoundError as exc:
        return ValidationResult(False, "MISSING_MANIFEST", str(exc), profile_id=profile_id, layout_id=layout_id)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return ValidationResult(False, "INVALID_MANIFEST", str(exc), profile_id=profile_id, layout_id=layout_id)

    profiles = manifest.get("profiles")
    if not isinstance(profiles, Mapping):
        return ValidationResult(False, "INVALID_MANIFEST", "Manifest has no profiles object.", profile_id=profile_id, layout_id=layout_id)

    profile = profiles.get(profile_id)
    if not isinstance(profile, Mapping):
        return ValidationResult(False, "UNKNOWN_PROFILE", f"Unknown calibration profile: {profile_id}", profile_id=profile_id, layout_id=layout_id)

    layouts = profile.get("layouts")
    if not isinstance(layouts, Mapping):
        return ValidationResult(False, "INVALID_PROFILE", f"Profile has no layouts object: {profile_id}", profile_id=profile_id, layout_id=layout_id)

    layout = layouts.get(layout_id)
    if not isinstance(layout, Mapping):
        return ValidationResult(False, "LAYOUT_MISMATCH", f"Calibration layout mismatch: {layout_id}", profile_id=profile_id, layout_id=layout_id)

    required_records = layout.get("required_targets")
    if not isinstance(required_records, Mapping):
        return ValidationResult(False, "INVALID_LAYOUT", f"Layout has no required_targets object: {layout_id}", profile_id=profile_id, layout_id=layout_id)

    missing: list[str] = []
    uncalibrated: list[str] = []
    invalid_points: list[str] = []
    for target in REQUIRED_TARGETS:
        record = required_records.get(target)
        if not isinstance(record, Mapping):
            missing.append(target)
            uncalibrated.append(target)
            continue
        if record.get("status") != USER_CALIBRATED or record.get("marked") is not True:
            uncalibrated.append(target)
            continue
        point = record.get("point")
        if not isinstance(point, Mapping):
            invalid_points.append(target)
            continue
        try:
            x = float(point.get("x"))
            y = float(point.get("y"))
        except (TypeError, ValueError):
            invalid_points.append(target)
            continue
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            invalid_points.append(target)

    if uncalibrated:
        return ValidationResult(
            False,
            "NOT_USER_CALIBRATED",
            "Required execution targets are missing or not user calibrated.",
            missing_targets=tuple(missing),
            uncalibrated_targets=tuple(dict.fromkeys(uncalibrated)),
            profile_id=profile_id,
            layout_id=layout_id,
        )

    if invalid_points:
        return ValidationResult(
            False,
            "INVALID_CALIBRATION_POINT",
            "Required execution targets have invalid calibrated points.",
            uncalibrated_targets=tuple(dict.fromkeys(invalid_points)),
            profile_id=profile_id,
            layout_id=layout_id,
        )

    return ValidationResult(True, "USER_CALIBRATED", "Profile and layout are user calibrated.", profile_id=profile_id, layout_id=layout_id)


def list_uncalibrated_artifacts(
    manifest_or_path: Mapping[str, Any] | str | Path,
    *,
    profile_id: str | None = None,
    layout_id: str | None = None,
) -> list[dict[str, Any]]:
    manifest = load_manifest(manifest_or_path) if isinstance(manifest_or_path, (str, Path)) else dict(manifest_or_path)
    artifacts: list[dict[str, Any]] = []
    profiles = manifest.get("profiles", {})
    if not isinstance(profiles, Mapping):
        return artifacts

    for current_profile_id, profile in profiles.items():
        if profile_id is not None and current_profile_id != profile_id:
            continue
        if not isinstance(profile, Mapping):
            continue
        layouts = profile.get("layouts", {})
        if not isinstance(layouts, Mapping):
            continue
        for current_layout_id, layout in layouts.items():
            if layout_id is not None and current_layout_id != layout_id:
                continue
            if not isinstance(layout, Mapping):
                continue
            for target in _uncalibrated_targets(layout):
                artifacts.append(
                    {
                        "profile_id": current_profile_id,
                        "layout_id": current_layout_id,
                        "artifact_type": "required_target",
                        "artifact_id": target,
                        "status": NOT_USER_CALIBRATED,
                        "would_delete": False,
                    }
                )
    return artifacts


def create_deletion_report(
    artifacts: Iterable[Mapping[str, Any] | str | Path],
    *,
    reason: str = "NOT_USER_CALIBRATED",
    delete: bool = False,
) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    for artifact in artifacts:
        if isinstance(artifact, Mapping):
            entry = dict(artifact)
        else:
            entry = {"artifact_id": str(Path(artifact)), "artifact_type": "path"}
        entry["reason"] = reason
        entry["deleted"] = False
        entry["would_delete"] = bool(delete)
        entries.append(entry)
    return {
        "schema_version": 1,
        "generated_at": _utc_now(),
        "dry_run": not delete,
        "deleted_count": 0,
        "artifacts": entries,
    }


def write_manifest(manifest: Mapping[str, Any], path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return output_path


def _read_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Calibration JSON must be an object: {path}")
    return payload


def _extract_source_points(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = payload.get("calibration_points")
    if isinstance(nested, Mapping):
        return nested
    return payload


def _target_record(target: str, source_points: Mapping[str, Any], *, required: bool) -> dict[str, Any]:
    source_key = _find_source_key(target, source_points)
    if source_key is None:
        return {
            "status": NOT_USER_CALIBRATED,
            "marked": False,
            "required": required,
            "source_key": None,
            "point": None,
        }

    point = source_points.get(source_key)
    if not _is_marked_point(point):
        return {
            "status": NOT_USER_CALIBRATED,
            "marked": False,
            "required": required,
            "source_key": source_key,
            "point": point if isinstance(point, Mapping) else None,
        }

    return {
        "status": USER_CALIBRATED,
        "marked": True,
        "required": required,
        "source_key": source_key,
        "point": {"x": float(point["x"]), "y": float(point["y"])},
    }


def _find_source_key(target: str, source_points: Mapping[str, Any]) -> str | None:
    for alias in TARGET_ALIASES.get(target, (target,)):
        if alias in source_points:
            return alias
    return None


def _is_marked_point(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        x = float(value["x"])
        y = float(value["y"])
    except (KeyError, TypeError, ValueError):
        return False
    return 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0


def _uncalibrated_targets(layout: Mapping[str, Any]) -> list[str]:
    records = layout.get("required_targets", {})
    if not isinstance(records, Mapping):
        return list(REQUIRED_TARGETS)
    uncalibrated: list[str] = []
    for target in REQUIRED_TARGETS:
        record = records.get(target)
        if not isinstance(record, Mapping) or record.get("status") != USER_CALIBRATED or record.get("marked") is not True:
            uncalibrated.append(target)
    return uncalibrated


def _layout_id_from_payload(payload: Mapping[str, Any]) -> str | None:
    layout_id = payload.get("layout_id")
    if isinstance(layout_id, str) and layout_id.strip():
        return layout_id.strip()
    window_rect = payload.get("window_rect")
    if isinstance(window_rect, Mapping):
        try:
            width = int(window_rect["right"]) - int(window_rect["left"])
            height = int(window_rect["bottom"]) - int(window_rect["top"])
        except (KeyError, TypeError, ValueError):
            return None
        if width > 0 and height > 0:
            return f"{width}x{height}"
    return None


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
