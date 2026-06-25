from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import statistics
import time
from typing import Any, Mapping, Sequence, cast


CERT_SCHEMA_VERSION = "PG_V3_CERTIFICATION"


def float_or(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed:
        return float(default)
    return float(parsed)


def int_or(value: Any, default: int = 0) -> int:
    return int(float_or(value, float(default)))


def mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def _empty_bool_dict() -> dict[str, bool]:
    return {}


def _empty_any_dict() -> dict[str, Any]:
    return {}


def _empty_string_list() -> list[str]:
    return []


def percentile(values: Sequence[float], pct: float) -> float:
    rows = sorted(float(value) for value in values)
    if not rows:
        return 0.0
    index = min(len(rows) - 1, max(0, int(round((len(rows) - 1) * pct / 100.0))))
    return round(float(rows[index]), 3)


def average(values: Sequence[float]) -> float:
    return round(float(statistics.mean(values)), 3) if values else 0.0


@dataclass(slots=True)
class CertificationGateResultV3:
    gate: str
    passed: bool
    checks: dict[str, bool] = field(default_factory=_empty_bool_dict)
    metrics: dict[str, Any] = field(default_factory=_empty_any_dict)
    failures: list[str] = field(default_factory=_empty_string_list)
    evidence: dict[str, Any] = field(default_factory=_empty_any_dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CERT_SCHEMA_VERSION,
            "gate": self.gate,
            "verdict": "PASS" if self.passed else "FAIL",
            "checks": dict(self.checks),
            "metrics": dict(self.metrics),
            "failures": list(self.failures),
            "evidence": dict(self.evidence),
            "generated_epoch": time.time(),
        }


def write_report(path: Path | str, payload: Mapping[str, Any]) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, default=str), encoding="utf-8")
    return out


class SessionFreshnessValidatorV3:
    required_frame_keys = (
        "frame_index",
        "display_frame_id",
        "display_capture_epoch",
        "last_capture_epoch",
        "state_version",
    )

    def validate(self, session: Mapping[str, Any], *, now_epoch: float | None = None) -> dict[str, Any]:
        now = time.time() if now_epoch is None else float(now_epoch)
        frame_id = int_or(session.get("frame_index"))
        display_frame_id = int_or(session.get("display_frame_id"))
        display_capture_epoch = float_or(session.get("display_capture_epoch"))
        model_capture_epoch = float_or(
            mapping(session.get("latest_signal")).get("capture_started_epoch")
            or mapping(mapping(session.get("tracking_summary")).get("pipeline_timing")).get("capture_started_epoch")
            or session.get("last_capture_started_epoch")
        )
        last_capture_epoch = float_or(session.get("last_capture_epoch"))
        updated_epoch = float_or(session.get("updated_epoch") or session.get("updated_at_epoch"))
        frame_age_ms = max(0.0, (now - display_capture_epoch) * 1000.0) if display_capture_epoch > 0.0 else 0.0
        model_age_ms = max(0.0, (now - model_capture_epoch) * 1000.0) if model_capture_epoch > 0.0 else 0.0
        touch_only = bool(
            str(session.get("status") or "").lower() == "running"
            and frame_id > 0
            and (last_capture_epoch <= 0.0 or display_capture_epoch <= 0.0 or model_capture_epoch <= 0.0)
        )
        missing = [key for key in self.required_frame_keys if session.get(key) in (None, "", [], {})]
        checks = {
            "required_frame_fields_present": not missing,
            "frame_id_positive": frame_id > 0,
            "display_frame_id_positive": display_frame_id > 0,
            "display_capture_epoch_positive": display_capture_epoch > 0.0,
            "model_capture_epoch_positive": model_capture_epoch > 0.0,
            "last_capture_epoch_positive": last_capture_epoch > 0.0,
            "not_touch_only_stale": not touch_only,
        }
        return {
            "verdict": "PASS" if all(checks.values()) else "FAIL",
            "checks": checks,
            "missing": missing,
            "session_status": "TOUCH_ONLY_STALE" if touch_only else str(session.get("status") or ""),
            "frame_id": frame_id,
            "display_frame_id": display_frame_id,
            "display_capture_epoch": display_capture_epoch,
            "model_capture_epoch": model_capture_epoch,
            "last_capture_epoch": last_capture_epoch,
            "updated_epoch": updated_epoch,
            "frame_age_ms": round(frame_age_ms, 3),
            "model_age_ms": round(model_age_ms, 3),
        }


@dataclass(slots=True)
class CaptureWorkerV3Health:
    session_id: str
    capture_count: int
    frame_index: int
    display_frame_id: int
    display_capture_epoch: float
    model_capture_epoch: float
    last_capture_epoch: float
    study_in_progress: bool = False
    last_error: str = ""

    @classmethod
    def from_session(cls, session: Mapping[str, Any]) -> "CaptureWorkerV3Health":
        signal = mapping(session.get("latest_signal"))
        tracking = mapping(session.get("tracking_summary"))
        pipeline = mapping(tracking.get("pipeline_timing") or signal.get("pipeline_timing"))
        return cls(
            session_id=str(session.get("session_id") or ""),
            capture_count=int_or(session.get("capture_count")),
            frame_index=int_or(session.get("frame_index")),
            display_frame_id=int_or(session.get("display_frame_id")),
            display_capture_epoch=float_or(session.get("display_capture_epoch")),
            model_capture_epoch=float_or(signal.get("capture_started_epoch") or pipeline.get("capture_started_epoch") or session.get("last_capture_started_epoch")),
            last_capture_epoch=float_or(session.get("last_capture_epoch")),
            study_in_progress=bool(session.get("study_in_progress")),
            last_error=str(session.get("last_error") or ""),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "capture_count": self.capture_count,
            "frame_index": self.frame_index,
            "display_frame_id": self.display_frame_id,
            "display_capture_epoch": self.display_capture_epoch,
            "model_capture_epoch": self.model_capture_epoch,
            "last_capture_epoch": self.last_capture_epoch,
            "study_in_progress": self.study_in_progress,
            "last_error": self.last_error,
        }
