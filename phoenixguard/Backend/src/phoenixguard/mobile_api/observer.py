from __future__ import annotations

import io
import json
import logging
import math
import os
import re
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence, cast
from uuid import uuid4

from PIL import Image, ImageOps

from phoenixguard.core.config import RUNTIME
from phoenixguard.core.utils import utc_now_iso
from phoenixguard.decision.best_play_engine import analyze_best_play

from .pipeline import DEFAULT_UPLOAD_ORDER, PhoenixGuardPipelineAdapter, PipelineAdapter


LOGGER = logging.getLogger("phoenixguard.mobile_api.observer")

DEFAULT_MAX_UPLOAD_BYTES = 12 * 1024 * 1024
DEFAULT_MAX_IMAGE_DIMENSION = 8192
DEFAULT_MIN_IMAGE_DIMENSION = 64
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
DEFAULT_SIGNAL_POLICY: dict[str, float | int] = {
    "min_actionable_confidence": 0.64,
    "min_directional_confidence": 0.50,
    "min_freshness_score": 0.62,
    "freshness_half_life_sec": 9.0,
    "stale_after_sec": 20.0,
    "signal_cooldown_sec": 6.0,
    "confidence_step_threshold": 0.05,
    "regime_window": 6,
    "history_limit": 48,
    "single_surface_mode": 0,
    "min_thesis_confidence": 0.46,
    "thesis_hysteresis": 0.08,
    "directional_hysteresis": 0.06,
    "directional_timing_floor": 0.34,
}


def _now_iso() -> str:
    return utc_now_iso()


def _now_epoch() -> float:
    return float(time.time())


def _clip01(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    return float(max(0.0, min(1.0, number)))


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _direction(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    if normalized.startswith("BUY"):
        return "BUY"
    if normalized.startswith("SELL"):
        return "SELL"
    return "HOLD"


def _slugify(value: str, fallback: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("._").lower()
    return slug or fallback


def _filesystem_path(path: Path) -> Path:
    """Return a Windows extended-length path for deep observer artifacts."""

    if os.name != "nt":
        return path
    raw = str(path.absolute())
    if raw.startswith("\\\\?\\"):
        return Path(raw)
    if raw.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + raw[2:])
    return Path("\\\\?\\" + raw)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    io_path = _filesystem_path(path)
    io_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = io_path.with_suffix(io_path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(dict(payload), ensure_ascii=True, indent=2, default=str),
        encoding="utf-8",
    )
    for attempt in range(6):
        try:
            tmp_path.replace(io_path)
            return
        except PermissionError:
            if attempt >= 5:
                raise
            time.sleep(0.05 * float(attempt + 1))


def _read_json(path: Path, default: Any) -> Any:
    io_path = _filesystem_path(path)
    if not io_path.exists():
        return default
    try:
        return json.loads(io_path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _payload_dict(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(cast(Mapping[str, Any], value))
    return {}


def _payload_items(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    items = cast(Sequence[object], value)
    return [cast(Mapping[str, Any], item) for item in items if isinstance(item, Mapping)]


def _normalize_policy(policy: Mapping[str, Any] | None) -> dict[str, Any]:
    raw = dict(DEFAULT_SIGNAL_POLICY)
    raw.update(dict(policy or {}))
    return {
        "min_actionable_confidence": _clip01(raw.get("min_actionable_confidence", 0.64)),
        "min_directional_confidence": _clip01(raw.get("min_directional_confidence", 0.50)),
        "min_freshness_score": _clip01(raw.get("min_freshness_score", 0.62)),
        "freshness_half_life_sec": max(1.0, _safe_float(raw.get("freshness_half_life_sec", 9.0), 9.0)),
        "stale_after_sec": max(1.0, _safe_float(raw.get("stale_after_sec", 20.0), 20.0)),
        "signal_cooldown_sec": max(0.0, _safe_float(raw.get("signal_cooldown_sec", 6.0), 6.0)),
        "confidence_step_threshold": _clip01(raw.get("confidence_step_threshold", 0.05)),
        "regime_window": max(2, _safe_int(raw.get("regime_window", 6), 6)),
        "history_limit": max(8, _safe_int(raw.get("history_limit", 48), 48)),
        "single_surface_mode": bool(raw.get("single_surface_mode", False)),
        "min_thesis_confidence": _clip01(raw.get("min_thesis_confidence", 0.46)),
        "thesis_hysteresis": _clip01(raw.get("thesis_hysteresis", 0.08)),
        "directional_hysteresis": _clip01(raw.get("directional_hysteresis", 0.06)),
        "directional_timing_floor": _clip01(raw.get("directional_timing_floor", 0.34)),
    }


def _freshness_score(age_sec: float, policy: Mapping[str, Any]) -> float:
    age = max(0.0, float(age_sec))
    half_life = max(1.0, _safe_float(policy.get("freshness_half_life_sec", 9.0), 9.0))
    decay = math.exp(-math.log(2.0) * age / half_life)
    return _clip01(decay)


def _signal_age_sec(signal: Mapping[str, Any]) -> float:
    for epoch_key in (
        "completed_epoch",
        "published_epoch",
        "signal_created_epoch",
        "created_epoch",
        "timestamp_epoch",
    ):
        epoch = _safe_float(signal.get(epoch_key, 0.0), 0.0)
        if epoch > 0.0:
            return max(0.0, _now_epoch() - epoch)

    for time_key in ("timestamp", "published_at", "completed_at", "created_at", "signal_created_at"):
        raw_value = str(signal.get(time_key, "") or "").strip()
        if not raw_value:
            continue
        try:
            parsed = datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, _now_epoch() - parsed.timestamp())
    return 0.0


def _compute_regime_flip_rate(
    signal_history: Sequence[Mapping[str, Any]],
    *,
    window: int,
) -> float:
    directional = [
        _direction(item.get("candidate_action", item.get("base_action", "HOLD")))
        for item in signal_history
    ]
    filtered = [item for item in directional if item in {"BUY", "SELL"}]
    if len(filtered) < 2:
        return 0.0
    recent = filtered[-max(2, int(window)) :]
    flips = sum(1 for left, right in zip(recent, recent[1:]) if left != right)
    return _clip01(flips / max(len(recent) - 1, 1))


def _format_phase_name(value: Any) -> str:
    return str(value or "transition").strip().replace("_", " ").title()


def _infer_market_phase(
    result: Mapping[str, Any],
    best_play: Mapping[str, Any],
) -> dict[str, Any]:
    chart_state = cast(Mapping[str, Any], result.get("chart_state", {}))
    sequence_state = cast(Mapping[str, Any], result.get("sequence_state", {}))
    multi_timeframe = cast(Mapping[str, Any], result.get("multi_timeframe", {}))
    timing_signal = cast(Mapping[str, Any], result.get("timing_signal", {}))
    current_box = cast(Mapping[str, Any], sequence_state.get("current_box", {}))
    structure_setup = str(chart_state.get("structure_setup", "none") or "none").strip().lower()
    current_box_type = str(current_box.get("box_type", "balance") or "balance").strip().lower()
    continuation_probability = max(
        _clip01(chart_state.get("continuation_probability", 0.0)),
        _clip01(sequence_state.get("continuation_probability", 0.0)),
    )
    pullback_probability = _clip01(sequence_state.get("pullback_probability", 0.0))
    reversal_probability = max(
        _clip01(chart_state.get("reversal_probability", 0.0)),
        _clip01(sequence_state.get("reversal_probability", 0.0)),
    )
    fakeout_probability = max(
        _clip01(chart_state.get("fakeout_probability", 0.0)),
        _clip01(sequence_state.get("fakeout_probability", 0.0)),
    )
    consolidation_active = bool(
        chart_state.get("has_active_consolidation", False)
        or sequence_state.get("has_active_consolidation", False)
        or "consolidation" in structure_setup
        or current_box_type == "balance"
    )
    phase_name = "transition"
    phase_confidence = max(
        continuation_probability,
        pullback_probability,
        reversal_probability,
        fakeout_probability,
    )
    if consolidation_active and phase_confidence < 0.62:
        phase_name = "consolidation"
        phase_confidence = max(phase_confidence, 0.44)
    elif "reversal" in structure_setup or reversal_probability >= max(continuation_probability + 0.05, pullback_probability + 0.03, 0.40):
        phase_name = "reversal"
        phase_confidence = max(reversal_probability, 0.50 if "reversal" in structure_setup else 0.0)
    elif current_box_type == "pullback" or pullback_probability >= max(continuation_probability, reversal_probability, 0.38):
        phase_name = "pullback"
        phase_confidence = max(pullback_probability, 0.42 if current_box_type == "pullback" else 0.0)
    elif continuation_probability >= max(pullback_probability, reversal_probability, 0.40):
        phase_name = "continuation"
        phase_confidence = continuation_probability
    elif fakeout_probability >= max(continuation_probability, pullback_probability, reversal_probability, 0.42):
        phase_name = "fakeout"
        phase_confidence = fakeout_probability

    focus_timeframe = str(timing_signal.get("timeframe", "") or "").strip().upper()
    if not focus_timeframe:
        entries = cast(Sequence[Mapping[str, Any]], multi_timeframe.get("entries", []))
        if entries:
            focus_timeframe = str(entries[-1].get("timeframe", entries[0].get("timeframe", "")) or "").strip().upper()
    if not focus_timeframe:
        focus_timeframe = "M5"

    phase_bias = _direction(
        best_play.get(
            "recommended_direction",
            result.get("headline_action", result.get("execution_action", result.get("action", "HOLD"))),
        )
    )
    return {
        "name": phase_name,
        "label": _format_phase_name(phase_name),
        "confidence": _clip01(phase_confidence),
        "bias": phase_bias,
        "structure_setup": structure_setup,
        "consolidation_active": consolidation_active,
        "continuation_probability": continuation_probability,
        "pullback_probability": pullback_probability,
        "reversal_probability": reversal_probability,
        "fakeout_probability": fakeout_probability,
        "focus_timeframe": focus_timeframe,
    }


def _compute_thesis_state(
    signal_history: Sequence[Mapping[str, Any]],
    *,
    current_candidate_action: str,
    current_candidate_confidence: float,
    current_memory_similarity: float,
    phase: Mapping[str, Any],
    previous_signal: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    supports = {"BUY": 0.0, "SELL": 0.0}
    recent_rows = [_payload_dict(item) for item in signal_history[-5:]]
    for index, entry in enumerate(reversed(recent_rows), start=1):
        direction = _direction(
            entry.get(
                "thesis_action",
                entry.get("candidate_action", entry.get("best_play_action", entry.get("base_action", "HOLD"))),
            )
        )
        if direction not in supports:
            continue
        base_confidence = max(
            _clip01(entry.get("thesis_confidence", 0.0)),
            _clip01(entry.get("candidate_confidence", 0.0)),
            _clip01(entry.get("best_play_confidence", 0.0)),
            _clip01(entry.get("effective_confidence", 0.0)),
        )
        recency_weight = max(0.42, 1.12 - 0.14 * (index - 1))
        supports[direction] += base_confidence * recency_weight

    phase_bias = _direction(phase.get("bias", "HOLD"))
    phase_confidence = _clip01(phase.get("confidence", 0.0))
    if current_candidate_action in supports:
        current_weight = current_candidate_confidence * (1.10 if phase_bias == current_candidate_action else 0.98)
        current_weight += 0.10 * current_memory_similarity
        supports[current_candidate_action] += current_weight
    if phase_bias in supports:
        supports[phase_bias] += 0.08 * phase_confidence

    buy_support = _clip01(supports["BUY"] / 1.85)
    sell_support = _clip01(supports["SELL"] / 1.85)
    sorted_support = sorted(supports.items(), key=lambda item: item[1], reverse=True)
    dominant_action, dominant_raw = sorted_support[0]
    opposing_raw = float(sorted_support[1][1])
    support_gap = max(0.0, dominant_raw - opposing_raw)
    total_support = max(1e-6, dominant_raw + opposing_raw)
    conviction = _clip01(support_gap / total_support)
    dominant_confidence = _clip01(
        0.58 * current_candidate_confidence
        + 0.24 * conviction
        + 0.10 * _clip01(dominant_raw / 1.45)
        + 0.08 * current_memory_similarity
    )
    previous_thesis = _direction(
        previous_signal.get(
            "thesis_action",
            previous_signal.get("candidate_action", previous_signal.get("base_action", "HOLD")),
        )
    )
    thesis_floor = _clip01(policy.get("min_thesis_confidence", 0.46))
    hysteresis = _clip01(policy.get("thesis_hysteresis", 0.08))
    thesis_action = "HOLD"
    if dominant_confidence >= thesis_floor and support_gap >= max(0.04, hysteresis * 0.8):
        thesis_action = dominant_action
    elif previous_thesis in supports and previous_thesis != "HOLD":
        prior_support = float(supports[previous_thesis])
        if prior_support >= dominant_raw - max(0.06, hysteresis) and dominant_confidence >= max(0.32, thesis_floor * 0.82):
            thesis_action = previous_thesis

    thesis_age = 0
    if thesis_action in supports:
        thesis_age = 1
        for entry in reversed(recent_rows):
            historical_action = _direction(
                entry.get(
                    "thesis_action",
                    entry.get("candidate_action", entry.get("base_action", "HOLD")),
                )
            )
            if historical_action == thesis_action:
                thesis_age += 1
            else:
                break

    thesis_state = "mixed"
    if thesis_action in supports:
        if thesis_age >= 4 and dominant_confidence >= max(thesis_floor, 0.58):
            thesis_state = "locked"
        elif dominant_confidence >= thesis_floor:
            thesis_state = "building"
    return {
        "action": thesis_action,
        "confidence": dominant_confidence if thesis_action in supports else _clip01(dominant_confidence * 0.74),
        "state": thesis_state,
        "age": thesis_age,
        "buy_support": buy_support,
        "sell_support": sell_support,
        "conviction": conviction,
    }


def _compute_directional_watch_state(
    *,
    candidate_action: str,
    adaptive_confidence: float,
    gate_state: str,
    gate_strength: float,
    timing_state: str,
    timing_score: float,
    thesis: Mapping[str, Any],
    phase: Mapping[str, Any],
    previous_signal: Mapping[str, Any],
    policy: Mapping[str, Any],
    single_surface_mode: bool,
) -> dict[str, Any]:
    if candidate_action not in {"BUY", "SELL"}:
        threshold = max(
            0.34,
            _clip01(
                policy.get(
                    "min_directional_confidence",
                    max(_safe_float(policy.get("min_actionable_confidence", 0.64), 0.64) - 0.14, 0.0),
                )
            ),
        )
        return {
            "ready": False,
            "threshold": threshold,
            "gate_ready": False,
            "timing_ready": False,
            "support_ready": False,
        }

    thesis_action = _direction(thesis.get("action", "HOLD"))
    thesis_confidence = _clip01(thesis.get("confidence", 0.0))
    phase_bias = _direction(phase.get("bias", "HOLD"))
    previous_action = _direction(previous_signal.get("base_action", previous_signal.get("action", "HOLD")))
    directional_floor = _clip01(
        policy.get(
            "min_directional_confidence",
            max(_safe_float(policy.get("min_actionable_confidence", 0.64), 0.64) - 0.14, 0.0),
        )
    )
    hysteresis = _clip01(policy.get("directional_hysteresis", 0.06))
    threshold_bonus = 0.0
    if thesis_action == candidate_action:
        threshold_bonus += 0.04 + 0.03 * thesis_confidence
    if phase_bias == candidate_action:
        threshold_bonus += 0.02
    if previous_action == candidate_action:
        threshold_bonus += hysteresis
    directional_threshold = max(0.34, directional_floor - threshold_bonus)
    watch_gate_floor = 0.24 if single_surface_mode else 0.32
    gate_ready = gate_state == "confirmed" or (gate_state == "watch" and gate_strength >= watch_gate_floor)
    timing_ready = timing_state == "READY" or timing_score >= _clip01(policy.get("directional_timing_floor", 0.34))
    support_ready = bool(
        thesis_action == candidate_action
        or phase_bias == candidate_action
        or thesis_confidence >= max(0.38, directional_threshold - 0.06)
    )
    ready = bool(
        gate_ready
        and timing_ready
        and support_ready
        and adaptive_confidence >= directional_threshold
    )
    return {
        "ready": ready,
        "threshold": directional_threshold,
        "gate_ready": gate_ready,
        "timing_ready": timing_ready,
        "support_ready": support_ready,
    }


def _compute_signal_arm_state(
    *,
    candidate_action: str,
    adaptive_confidence: float,
    gate_state: str,
    gate_strength: float,
    timing_state: str,
    timing_score: float,
    execution_permission: str,
    thesis: Mapping[str, Any],
    phase: Mapping[str, Any],
    previous_signal: Mapping[str, Any],
    current_memory_similarity: float,
    regime_flip_rate: float,
    policy: Mapping[str, Any],
    single_surface_mode: bool,
) -> dict[str, Any]:
    directional_floor = _clip01(
        policy.get(
            "min_directional_confidence",
            max(_safe_float(policy.get("min_actionable_confidence", 0.64), 0.64) - 0.14, 0.0),
        )
    )
    if candidate_action not in {"BUY", "SELL"}:
        threshold = max(0.34, directional_floor)
        return {
            "ready": False,
            "state": "standby",
            "score": 0.0,
            "threshold": threshold,
            "action": "HOLD",
            "timing_ready": False,
            "structure_support": False,
            "gate_support": False,
            "reverse_guard": True,
        }

    thesis_action = _direction(thesis.get("action", "HOLD"))
    thesis_confidence = _clip01(thesis.get("confidence", 0.0))
    phase_bias = _direction(phase.get("bias", "HOLD"))
    phase_confidence = _clip01(phase.get("confidence", 0.0))
    reversal_probability = _clip01(phase.get("reversal_probability", 0.0))
    previous_action = _direction(previous_signal.get("base_action", previous_signal.get("action", "HOLD")))
    actionable_floor = _clip01(policy.get("min_actionable_confidence", 0.64))
    base_threshold = max(
        0.40,
        min(
            actionable_floor - (0.10 if single_surface_mode else 0.06),
            directional_floor - (0.03 if single_surface_mode else 0.01),
        ),
    )
    threshold = base_threshold
    if previous_action in {"BUY", "SELL"} and previous_action != candidate_action:
        threshold += max(0.0, 0.10 - 0.12 * reversal_probability)
    elif previous_action == candidate_action:
        threshold -= 0.03
    threshold = _clip01(max(0.38, threshold + 0.04 * regime_flip_rate))

    timing_floor = max(0.40, _clip01(policy.get("directional_timing_floor", 0.34)))
    timing_ready = timing_state == "READY" or timing_score >= timing_floor
    structure_support = bool(
        thesis_action == candidate_action
        or phase_bias == candidate_action
        or thesis_confidence >= 0.44
    )
    gate_support = bool(
        gate_state == "confirmed"
        or (gate_state == "watch" and gate_strength >= (0.18 if single_surface_mode else 0.26))
        or thesis_confidence >= 0.64
    )
    reverse_guard = bool(
        previous_action not in {"BUY", "SELL"}
        or previous_action == candidate_action
        or reversal_probability >= 0.46
        or (thesis_action == candidate_action and thesis_confidence >= 0.62)
        or (phase_bias == candidate_action and phase_confidence >= 0.60)
    )

    support_score = (
        0.54 * adaptive_confidence
        + 0.15 * thesis_confidence
        + 0.10 * phase_confidence
        + 0.08 * timing_score
        + 0.05 * current_memory_similarity
        + (0.07 if gate_state == "confirmed" else (0.02 + 0.05 * gate_strength if gate_state == "watch" else 0.0))
        + (0.05 if execution_permission == "EXECUTE" else (0.02 if execution_permission else 0.0))
        + (0.03 if thesis_action == candidate_action else 0.0)
        + (0.02 if phase_bias == candidate_action else 0.0)
        + (0.03 if previous_action == candidate_action else 0.0)
        - 0.08 * regime_flip_rate
        - (
            max(0.0, 0.08 - 0.10 * reversal_probability)
            if previous_action in {"BUY", "SELL"} and previous_action != candidate_action
            else 0.0
        )
        - (0.04 if phase_bias not in {candidate_action, "HOLD"} and reversal_probability < 0.42 else 0.0)
    )
    score = _clip01(support_score)
    ready = bool(
        timing_ready
        and structure_support
        and gate_support
        and reverse_guard
        and score >= threshold
    )
    state = "standby"
    if ready:
        state = "armed"
    elif timing_ready and structure_support and score >= max(0.34, threshold - 0.08):
        state = "building"
    return {
        "ready": ready,
        "state": state,
        "score": score,
        "threshold": threshold,
        "action": candidate_action,
        "timing_ready": timing_ready,
        "structure_support": structure_support,
        "gate_support": gate_support,
        "reverse_guard": reverse_guard,
    }


def _public_artifact_payload(
    session_id: str,
    bundle_id: str,
    artifact: Mapping[str, Any],
) -> dict[str, Any]:
    name = Path(str(artifact.get("name", ""))).name
    return {
        "name": name,
        "kind": str(artifact.get("kind", "")),
        "label": str(artifact.get("label", "")),
        "slot_index": int(artifact.get("slot_index", 0) or 0),
        "slot_key": str(artifact.get("slot_key", "")),
        "slot_label": str(artifact.get("slot_label", "")),
        "url": f"/v1/mobile/observer/sessions/{session_id}/bundles/{bundle_id}/artifacts/{name}",
        "path": str(artifact.get("path", "")),
    }


def _artifact_response_payload(artifact: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "name": Path(str(artifact.get("name", ""))).name,
        "kind": str(artifact.get("kind", "")),
        "label": str(artifact.get("label", "")),
        "slot_index": int(artifact.get("slot_index", 0) or 0),
        "slot_key": str(artifact.get("slot_key", "")),
        "slot_label": str(artifact.get("slot_label", "")),
        "url": str(artifact.get("url", "")),
    }


class SignalObserverService:
    def __init__(
        self,
        *,
        root_dir: Path | None = None,
        pipeline_adapter: PipelineAdapter | None = None,
        max_workers: int = 1,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
        max_image_dimension: int = DEFAULT_MAX_IMAGE_DIMENSION,
        min_image_dimension: int = DEFAULT_MIN_IMAGE_DIMENSION,
    ) -> None:
        self.root_dir = Path(root_dir or (RUNTIME.data_dir / "mobile_observer"))
        self.sessions_dir = self.root_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.pipeline_adapter: PipelineAdapter = pipeline_adapter or PhoenixGuardPipelineAdapter()
        self.max_upload_bytes = int(max_upload_bytes)
        self.max_image_dimension = int(max_image_dimension)
        self.min_image_dimension = int(min_image_dimension)
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="phoenixguard-observer",
        )
        self._lock = threading.RLock()
        self._futures: dict[str, Future[Any]] = {}

    def describe(self) -> dict[str, Any]:
        return {
            "product": {
                "name": "PhoenixGuard Continuous Observer",
                "subtitle": "Freshness-Aware Market Watch",
            },
            "pipeline": dict(self.pipeline_adapter.describe()),
            "policy_defaults": dict(_normalize_policy(None)),
            "limits": {
                "max_upload_bytes": self.max_upload_bytes,
                "min_dimension": self.min_image_dimension,
                "max_dimension": self.max_image_dimension,
            },
        }

    def create_session(
        self,
        *,
        session_id: str | None = None,
        name: str = "",
        market: str = "",
        settings: Mapping[str, Any] | None = None,
        policy: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        normalized_settings = self.pipeline_adapter.normalize_render_config(settings or {})
        normalized_policy = _normalize_policy(policy)
        resolved_session_id = _slugify(session_id or name or market, uuid4().hex)
        session_dir = self.sessions_dir / resolved_session_id
        with self._lock:
            if session_dir.exists():
                raise ValueError(f"Observer session already exists: {resolved_session_id}")
            payload: dict[str, Any] = {
                "session_id": resolved_session_id,
                "name": str(name or "").strip(),
                "market": str(market or "").strip(),
                "status": "idle",
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "last_error": "",
                "settings": normalized_settings,
                "policy": normalized_policy,
                "latest_bundle_id": "",
                "last_alert_at": "",
                "last_alert_epoch": 0.0,
                "latest_signal": {},
                "signal_history": [],
                "bundle_summaries": [],
            }
            self._write_session(resolved_session_id, payload)
        return self.get_session(resolved_session_id)

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        sessions: list[dict[str, Any]] = []
        for path in sorted(self.sessions_dir.glob("*/session.json"), reverse=True):
            payload = _payload_dict(_read_json(path, {}))
            if payload:
                sessions.append(self._public_session_payload(payload, include_history=False))
        sessions.sort(key=lambda item: str(item.get("updated_at", "")), reverse=True)
        return sessions[: max(1, int(limit))]

    def get_session(self, session_id: str) -> dict[str, Any]:
        payload = self._read_session(session_id)
        if not payload:
            raise KeyError(session_id)
        return self._public_session_payload(payload, include_history=True)

    def submit_bundle(
        self,
        session_id: str,
        uploads: Sequence[tuple[str, bytes]],
        *,
        settings: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if len(uploads) != len(DEFAULT_UPLOAD_ORDER):
            raise ValueError(
                "Upload exactly four chart images: two higher timeframe views first, then two lower timeframe views."
            )
        with self._lock:
            session = self._read_session(session_id)
            if not session:
                raise KeyError(session_id)
            merged_settings = _payload_dict(session.get("settings", {}))
            merged_settings.update(dict(settings or {}))
            normalized_settings = self.pipeline_adapter.normalize_render_config(merged_settings)
            bundle_id = uuid4().hex
            bundle_dir = self._bundle_dir(session_id, bundle_id)
            upload_records = self._stage_uploads(bundle_dir / "uploads", uploads)
            bundle_payload: dict[str, Any] = {
                "session_id": session_id,
                "bundle_id": bundle_id,
                "status": "queued",
                "created_at": _now_iso(),
                "updated_at": _now_iso(),
                "started_at": "",
                "completed_at": "",
                "completed_epoch": 0.0,
                "last_error": "",
                "settings": normalized_settings,
                "uploads": upload_records,
                "result_path": "",
                "artifacts": [],
                "signal": {},
            }
            self._write_bundle(session_id, bundle_id, bundle_payload)
            session["status"] = "running"
            session["updated_at"] = _now_iso()
            session["latest_bundle_id"] = bundle_id
            session["last_error"] = ""
            self._write_session(session_id, session)
            future = self._executor.submit(self._run_bundle, session_id, bundle_id)
            self._futures[f"{session_id}:{bundle_id}"] = future
        return self.get_bundle(session_id, bundle_id)

    def get_bundle(self, session_id: str, bundle_id: str) -> dict[str, Any]:
        payload = self._read_bundle(session_id, bundle_id)
        if not payload:
            raise KeyError(bundle_id)
        public_payload = self._public_bundle_payload(payload)
        result_path = str(payload.get("result_path", "")).strip()
        if result_path and _filesystem_path(Path(result_path)).exists():
            public_payload["result"] = _read_json(Path(result_path), {})
        return public_payload

    def wait_for_bundle(
        self,
        session_id: str,
        bundle_id: str,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        key = f"{session_id}:{bundle_id}"
        with self._lock:
            future = self._futures.get(key)
        if future is not None:
            future.result(timeout=timeout)
        return self.get_bundle(session_id, bundle_id)

    def latest_signal(self, session_id: str) -> dict[str, Any]:
        payload = self._read_session(session_id)
        if not payload:
            raise KeyError(session_id)
        return self._render_signal(
            _payload_dict(payload.get("latest_signal", {})),
            _payload_dict(payload.get("policy", {})),
        )

    def update_session_market(self, session_id: str, market: str) -> dict[str, Any]:
        normalized_market = str(market or "").strip()
        with self._lock:
            payload = self._read_session(session_id)
            if not payload:
                raise KeyError(session_id)
            if not normalized_market or normalized_market == str(payload.get("market", "")).strip():
                return self._public_session_payload(payload, include_history=True)
            payload["market"] = normalized_market
            latest_signal = _payload_dict(payload.get("latest_signal", {}))
            if latest_signal:
                latest_signal["market"] = normalized_market
                payload["latest_signal"] = latest_signal
            signal_history = [_payload_dict(item) for item in _payload_items(payload.get("signal_history", []))]
            for item in signal_history:
                item["market"] = normalized_market
            if signal_history:
                payload["signal_history"] = signal_history
            bundle_summaries = [_payload_dict(item) for item in _payload_items(payload.get("bundle_summaries", []))]
            for item in bundle_summaries:
                signal = _payload_dict(item.get("signal", {}))
                if signal:
                    signal["market"] = normalized_market
                    item["signal"] = signal
            if bundle_summaries:
                payload["bundle_summaries"] = bundle_summaries
            payload["updated_at"] = _now_iso()
            self._write_session(session_id, payload)
        return self.get_session(session_id)

    def artifact_path(self, session_id: str, bundle_id: str, artifact_name: str) -> Path:
        payload = self._read_bundle(session_id, bundle_id)
        if not payload:
            raise KeyError(bundle_id)
        safe_name = Path(str(artifact_name)).name
        for artifact in payload.get("artifacts", []):
            if str(artifact.get("name", "")) != safe_name:
                continue
            path = Path(str(artifact.get("path", "")))
            io_path = _filesystem_path(path)
            if io_path.exists() and io_path.is_file():
                return io_path
            break
        raise FileNotFoundError(safe_name)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _run_bundle(self, session_id: str, bundle_id: str) -> None:
        payload = self._read_bundle(session_id, bundle_id)
        if not payload:
            return
        self._update_bundle(
            session_id,
            bundle_id,
            status="running",
            started_at=_now_iso(),
            updated_at=_now_iso(),
            last_error="",
        )
        upload_paths = [
            str(_filesystem_path(Path(str(item.get("path", "")))))
            for item in payload.get("uploads", [])
            if str(item.get("path", "")).strip()
        ]
        render_config = dict(payload.get("settings", {}))
        try:
            result, source_image_state, final_source_path = self.pipeline_adapter.analyze_bundle(
                upload_paths,
                render_config,
            )
            normalized_result = self.pipeline_adapter.normalize_result(result)
            artifacts = self._export_bundle_artifacts(
                session_id,
                bundle_id,
                normalized_result,
                source_image_state,
            )
            best_play = self._build_best_play_analysis(
                normalized_result,
                render_config=render_config,
                file_path=str(final_source_path),
            )
            session_payload = self._read_session(session_id)
            signal = self._build_signal_payload(
                session_payload,
                normalized_result,
                best_play,
                bundle_id=bundle_id,
                file_path=str(final_source_path),
            )
            result_payload = self._build_result_payload(
                session_id=session_id,
                bundle_id=bundle_id,
                result=normalized_result,
                render_config=render_config,
                final_source_path=str(final_source_path),
                artifacts=artifacts,
                best_play=best_play,
                signal=signal,
            )
            result_path = self._bundle_dir(session_id, bundle_id) / "result.json"
            _write_json_atomic(result_path, result_payload)
            public_artifacts = [
                _public_artifact_payload(session_id, bundle_id, artifact)
                for artifact in artifacts
            ]
            self._update_bundle(
                session_id,
                bundle_id,
                status="completed",
                completed_at=str(signal.get("completed_at", _now_iso())),
                completed_epoch=float(signal.get("completed_epoch", _now_epoch()) or _now_epoch()),
                updated_at=_now_iso(),
                result_path=str(result_path),
                artifacts=public_artifacts,
                signal=signal,
                last_error="",
            )
            self._apply_completed_bundle_to_session(
                session_id,
                bundle_id,
                signal=signal,
                last_error="",
            )
        except Exception as exc:
            LOGGER.exception("Observer bundle %s/%s failed.", session_id, bundle_id)
            self._update_bundle(
                session_id,
                bundle_id,
                status="failed",
                completed_at=_now_iso(),
                completed_epoch=_now_epoch(),
                updated_at=_now_iso(),
                last_error=str(exc),
            )
            self._apply_completed_bundle_to_session(
                session_id,
                bundle_id,
                signal={},
                last_error=str(exc),
            )

    def _apply_completed_bundle_to_session(
        self,
        session_id: str,
        bundle_id: str,
        *,
        signal: Mapping[str, Any],
        last_error: str,
    ) -> None:
        with self._lock:
            payload = self._read_session(session_id)
            if not payload:
                return
            summaries = [_payload_dict(item) for item in _payload_items(payload.get("bundle_summaries", []))]
            bundle_payload = self._read_bundle(session_id, bundle_id)
            if bundle_payload:
                summaries.append(
                    {
                        "bundle_id": str(bundle_payload.get("bundle_id", bundle_id)),
                        "status": str(bundle_payload.get("status", "")),
                        "created_at": str(bundle_payload.get("created_at", "")),
                        "completed_at": str(bundle_payload.get("completed_at", "")),
                        "signal": _payload_dict(bundle_payload.get("signal", {})),
                    }
                )
            history_limit = int(
                _payload_dict(payload.get("policy", {})).get("history_limit", DEFAULT_SIGNAL_POLICY["history_limit"]) or 48
            )
            payload["bundle_summaries"] = summaries[-max(8, history_limit) :]
            payload["latest_bundle_id"] = str(bundle_id)
            payload["updated_at"] = _now_iso()
            payload["last_error"] = str(last_error or "")
            payload["status"] = "error" if last_error else "idle"
            if signal:
                signal_history = [_payload_dict(item) for item in _payload_items(payload.get("signal_history", []))]
                signal_history.append(dict(signal))
                payload["signal_history"] = signal_history[-max(8, history_limit) :]
                payload["latest_signal"] = dict(signal)
                if bool(signal.get("alert", False)):
                    payload["last_alert_at"] = str(signal.get("completed_at", _now_iso()))
                    payload["last_alert_epoch"] = float(signal.get("completed_epoch", _now_epoch()) or _now_epoch())
            self._write_session(session_id, payload)

    def _build_result_payload(
        self,
        *,
        session_id: str,
        bundle_id: str,
        result: Mapping[str, Any],
        render_config: Mapping[str, Any],
        final_source_path: str,
        artifacts: Sequence[Mapping[str, Any]],
        best_play: Mapping[str, Any],
        signal: Mapping[str, Any],
    ) -> dict[str, Any]:
        return {
            "session_id": session_id,
            "bundle_id": bundle_id,
            "completed_at": _now_iso(),
            "final_source_path": str(final_source_path),
            "action": str(result.get("action", "HOLD")).upper(),
            "headline_action": str(result.get("headline_action", result.get("action", "HOLD"))).upper(),
            "execution_action": str(result.get("execution_action", result.get("action", "HOLD"))).upper(),
            "active_trade_state": str(result.get("active_trade_state", "HOLD_TRUE")).upper(),
            "directional_intent": str(result.get("directional_intent", "HOLD")).upper(),
            "confidence": float(result.get("confidence", 0.0) or 0.0),
            "decision_state": str(result.get("decision_state", "")),
            "execution_permission": str(result.get("execution_permission", "")),
            "memory_similarity": float(result.get("memory_similarity", 0.0) or 0.0),
            "trade_bias": str(result.get("trade_bias", result.get("action", "HOLD"))).upper(),
            "probabilities": dict(result.get("probabilities", {})),
            "projection": dict(result.get("projection", {})),
            "timestamp": str(result.get("timestamp", "")),
            "timing_signal": dict(result.get("timing_signal", {})),
            "render_config": dict(render_config),
            "multi_timeframe": dict(result.get("multi_timeframe", {})),
            "chart_state": dict(result.get("chart_state", {})),
            "sequence_state": dict(result.get("sequence_state", {})),
            "module_reliability": dict(result.get("module_reliability", {})),
            "transition_summary": dict(result.get("transition_summary", {})),
            "rl_policy": dict(result.get("rl_policy", {})),
            "meta": dict(result.get("meta", {})),
            "inference_id": str(result.get("inference_id", "")),
            "best_play": dict(best_play),
            "signal": dict(signal),
            "artifacts": [
                _artifact_response_payload(
                    _public_artifact_payload(session_id, bundle_id, artifact)
                )
                for artifact in artifacts
            ],
        }

    def _build_best_play_analysis(
        self,
        result: Mapping[str, Any],
        *,
        render_config: Mapping[str, Any],
        file_path: str,
    ) -> dict[str, Any]:
        module = getattr(self.pipeline_adapter, "module", None)
        snapshot: Mapping[str, Any] | None = None
        if module is not None:
            builder = getattr(module, "_build_best_play_input_snapshot", None)
            if callable(builder):
                try:
                    snapshot = cast(
                        Mapping[str, Any],
                        builder(result, render_config=render_config, file_path=file_path),
                    )
                except Exception:
                    LOGGER.exception("Observer best-play snapshot build failed.")
        if snapshot is not None:
            try:
                analysis = analyze_best_play(snapshot)
                return analysis
            except Exception:
                LOGGER.exception("Observer best-play analysis failed.")

        return {
            "status": "unavailable",
            "recommended_direction": "HOLD",
            "recommended_play": "Stand Aside",
            "recommended_confidence": 0.0,
            "recommended_risk": 1.0,
            "likelihoods": {"BUY": 0.0, "SELL": 0.0, "HOLD": 1.0},
            "recommended_reasons": ["best-play model snapshot is required before execution"],
            "frame_count": 0,
            "source": "model_required",
        }

    def _build_signal_payload(
        self,
        session_payload: Mapping[str, Any],
        result: Mapping[str, Any],
        best_play: Mapping[str, Any],
        *,
        bundle_id: str,
        file_path: str,
    ) -> dict[str, Any]:
        policy = _normalize_policy(cast(Mapping[str, Any] | None, session_payload.get("policy", {})))
        multi_timeframe = cast(Mapping[str, Any], result.get("multi_timeframe", {}))
        timing_signal = cast(Mapping[str, Any], result.get("timing_signal", {}))
        phase = _infer_market_phase(result, best_play)
        execution_permission = str(result.get("execution_permission", "") or "").strip().upper()
        gate_state = str(multi_timeframe.get("gate_state", "watch") or "watch").strip().lower()
        gate_strength = _clip01(multi_timeframe.get("gate_strength", 0.0))
        timing_state = str(timing_signal.get("entry_state", "WATCH") or "WATCH").strip().upper()
        timing_score = _clip01(timing_signal.get("timing_score", 0.0))
        model_action = _direction(result.get("headline_action", result.get("action", "HOLD")))
        execution_action = _direction(result.get("execution_action", model_action))
        best_play_status = str(
            best_play.get(
                "status",
                "ready" if best_play.get("recommended_direction") or best_play.get("recommended_confidence") else "unavailable",
            )
            or "unavailable"
        ).strip().lower()
        best_play_ready = best_play_status == "ready"
        best_play_action = _direction(best_play.get("recommended_direction", execution_action))
        candidate_action = best_play_action if best_play_ready and best_play_action in {"BUY", "SELL"} else "HOLD"
        if best_play_ready and candidate_action == "HOLD" and execution_action in {"BUY", "SELL"}:
            candidate_action = execution_action

        raw_confidence = max(
            _clip01(result.get("execution_confidence", 0.0)),
            _clip01(result.get("confidence", 0.0)),
        )
        best_play_confidence = _clip01(best_play.get("recommended_confidence", 0.0))
        agreement_factor = 1.0 if execution_action in {"HOLD", candidate_action} else 0.80
        history = cast(Sequence[Mapping[str, Any]], session_payload.get("signal_history", []))
        regime_flip_rate = _compute_regime_flip_rate(
            history,
            window=int(policy.get("regime_window", 6) or 6),
        )
        regime_stability = _clip01(1.0 - regime_flip_rate)
        single_surface_mode = bool(policy.get("single_surface_mode", False))
        structural_confidence = _clip01(
            (
                0.44 * raw_confidence
                + 0.24 * best_play_confidence
                + 0.18 * gate_strength
                + 0.14 * timing_score
            )
            * agreement_factor
        )
        adaptive_confidence = _clip01(structural_confidence * (0.72 + 0.28 * regime_stability))
        base_threshold = float(policy.get("min_actionable_confidence", 0.64) or 0.64)
        adaptive_threshold = _clip01(
            base_threshold
            + (0.10 if single_surface_mode else 0.16) * regime_flip_rate
            + (
                0.00
                if gate_state == "confirmed"
                else ((0.03 if single_surface_mode else 0.05) if gate_state == "watch" else 0.12)
            )
        )
        gating_ready = gate_state == "confirmed" if not single_surface_mode else gate_state in {"confirmed", "watch"} and gate_strength >= 0.28
        timing_ready = timing_state == "READY"
        permission_ready = execution_permission == "EXECUTE"
        previous_signal = cast(Mapping[str, Any], session_payload.get("latest_signal", {}))
        current_memory_similarity = _clip01(result.get("memory_similarity", 0.0))
        thesis = _compute_thesis_state(
            history,
            current_candidate_action=candidate_action,
            current_candidate_confidence=adaptive_confidence,
            current_memory_similarity=current_memory_similarity,
            phase=phase,
            previous_signal=previous_signal,
            policy=policy,
        )
        directional_watch = _compute_directional_watch_state(
            candidate_action=candidate_action,
            adaptive_confidence=adaptive_confidence,
            gate_state=gate_state,
            gate_strength=gate_strength,
            timing_state=timing_state,
            timing_score=timing_score,
            thesis=thesis,
            phase=phase,
            previous_signal=previous_signal,
            policy=policy,
            single_surface_mode=single_surface_mode,
        )
        arming_state = _compute_signal_arm_state(
            candidate_action=candidate_action,
            adaptive_confidence=adaptive_confidence,
            gate_state=gate_state,
            gate_strength=gate_strength,
            timing_state=timing_state,
            timing_score=timing_score,
            execution_permission=execution_permission,
            thesis=thesis,
            phase=phase,
            previous_signal=previous_signal,
            current_memory_similarity=current_memory_similarity,
            regime_flip_rate=regime_flip_rate,
            policy=policy,
            single_surface_mode=single_surface_mode,
        )
        signal_armed = bool(single_surface_mode and arming_state.get("ready", False))
        actionable = bool(
            best_play_ready
            and candidate_action in {"BUY", "SELL"}
            and timing_ready
            and adaptive_confidence >= adaptive_threshold
            and (
                (gating_ready and permission_ready)
                or (single_surface_mode and signal_armed)
            )
        )
        previous_action = _direction(previous_signal.get("base_action", previous_signal.get("action", "HOLD")))
        directional_watch_ready = bool(directional_watch.get("ready", False))
        if not best_play_ready:
            directional_watch_ready = False
            signal_armed = False
            actionable = False
        if (
            single_surface_mode
            and previous_action in {"BUY", "SELL"}
            and previous_action != candidate_action
            and not signal_armed
            and not bool(arming_state.get("reverse_guard", True))
        ):
            directional_watch_ready = False
        base_action = candidate_action if actionable or signal_armed or directional_watch_ready else "HOLD"
        if previous_action == "HOLD" and base_action in {"BUY", "SELL"}:
            transition = "enter"
        elif previous_action in {"BUY", "SELL"} and base_action == "HOLD":
            transition = "exit"
        elif previous_action in {"BUY", "SELL"} and base_action in {"BUY", "SELL"} and previous_action != base_action:
            transition = "reverse"
        elif previous_action == base_action and base_action in {"BUY", "SELL"}:
            transition = "reaffirm"
        else:
            transition = "standby"

        confidence_step = abs(
            adaptive_confidence - _clip01(previous_signal.get("candidate_confidence", 0.0))
        )
        completed_epoch = _now_epoch()
        last_alert_epoch = _safe_float(session_payload.get("last_alert_epoch", 0.0), 0.0)
        cooldown_sec = max(0.0, _safe_float(policy.get("signal_cooldown_sec", 6.0), 6.0))
        cooldown_ready = (completed_epoch - last_alert_epoch) >= cooldown_sec
        alert = bool(
            base_action in {"BUY", "SELL"}
            and cooldown_ready
            and (signal_armed or actionable)
            and (
                transition in {"enter", "reverse"}
                or confidence_step >= float(policy.get("confidence_step_threshold", 0.05) or 0.05)
            )
        )

        reasons: list[str] = []
        if str(best_play.get("recommended_play", "")).strip():
            reasons.append(str(best_play.get("recommended_play", "")).strip())
        phase_reason = f"{phase['label']} phase" if str(phase.get("label", "")).strip() else ""
        if phase_reason:
            reasons.append(phase_reason)
        if thesis.get("action", "HOLD") in {"BUY", "SELL"}:
            reasons.append(
                f"thesis {str(thesis.get('action', 'HOLD'))} {float(thesis.get('confidence', 0.0) or 0.0):.2f}"
            )
        if signal_armed and not actionable:
            reasons.append(
                f"signal armed {float(arming_state.get('score', 0.0) or 0.0):.2f} while execute confirmation catches up"
            )
        if directional_watch_ready and not actionable:
            reasons.append("directional bias active while execute gate waits")
        if actionability_reason := (
            ""
            if actionable
            else (
                "signal is armed and waiting for final execute promotion"
                if signal_armed
                else (
                    "multi-timeframe gate not confirmed"
                    if not gating_ready
                    else (
                        "timing not ready"
                        if not timing_ready
                        else (
                            "execution permission not granted"
                            if not permission_ready
                            else "adaptive confidence below threshold"
                        )
                    )
                )
            )
        ):
            reasons.append(actionability_reason)
        if not best_play_ready:
            reasons.append("best-play model snapshot is required before execution")
        for item in cast(Sequence[Any], best_play.get("recommended_reasons", [])):
            text = str(item or "").strip()
            if text and text not in reasons:
                reasons.append(text)
            if len(reasons) >= 5:
                break
        if gate_state:
            reasons.append(f"gate={gate_state}")
        if timing_state:
            reasons.append(f"timing={timing_state.lower()}")

        status = (
            "ready"
            if actionable and base_action in {"BUY", "SELL"}
            else (
                "armed"
                if signal_armed and base_action in {"BUY", "SELL"}
                else ("watch" if str(thesis.get("action", "HOLD")) in {"BUY", "SELL"} else "hold")
            )
        )
        if status == "hold" and base_action in {"BUY", "SELL"}:
            status = "watch"
        summary = (
            f"desk={base_action} | thesis={str(thesis.get('action', 'HOLD'))} {float(thesis.get('confidence', 0.0) or 0.0):.2f} "
            f"| phase={str(phase.get('name', 'transition'))} {float(phase.get('confidence', 0.0) or 0.0):.2f} "
            f"| gate={gate_state} | timing={timing_state} | armed={signal_armed}"
        )
        return {
            "signal_id": uuid4().hex,
            "bundle_id": bundle_id,
            "completed_at": _now_iso(),
            "completed_epoch": completed_epoch,
            "file_path": str(file_path),
            "market": str(session_payload.get("market", "")),
            "status": status,
            "action": base_action,
            "base_action": base_action,
            "candidate_action": candidate_action,
            "model_action": model_action,
            "execution_action": execution_action,
            "best_play_action": best_play_action,
            "best_play_status": best_play_status,
            "best_play_ready": best_play_ready,
            "raw_confidence": raw_confidence,
            "best_play_confidence": best_play_confidence,
            "memory_similarity": _clip01(result.get("memory_similarity", 0.0)),
            "recommended_play": str(best_play.get("recommended_play", "")).strip(),
            "recommended_risk": _clip01(best_play.get("recommended_risk", 0.0)),
            "likelihoods": dict(best_play.get("likelihoods", {})),
            "candidate_confidence": adaptive_confidence,
            "effective_confidence": adaptive_confidence,
            "adaptive_threshold": adaptive_threshold,
            "signal_armed": signal_armed,
            "signal_armed_action": str(arming_state.get("action", "HOLD")),
            "signal_armed_score": _clip01(arming_state.get("score", 0.0)),
            "signal_armed_threshold": _clip01(arming_state.get("threshold", 0.0)),
            "signal_armed_state": str(arming_state.get("state", "standby")),
            "signal_armed_reverse_guard": bool(arming_state.get("reverse_guard", True)),
            "directional_watch_ready": directional_watch_ready,
            "directional_threshold": float(directional_watch.get("threshold", 0.0) or 0.0),
            "single_surface_mode": single_surface_mode,
            "gate_state": gate_state,
            "gate_strength": gate_strength,
            "timing_state": timing_state,
            "timing_score": timing_score,
            "execution_permission": execution_permission,
            "regime_flip_rate": regime_flip_rate,
            "regime_stability": regime_stability,
            "thesis_action": str(thesis.get("action", "HOLD")),
            "thesis_confidence": _clip01(thesis.get("confidence", 0.0)),
            "thesis_state": str(thesis.get("state", "mixed")),
            "thesis_age": int(thesis.get("age", 0) or 0),
            "thesis_buy_support": _clip01(thesis.get("buy_support", 0.0)),
            "thesis_sell_support": _clip01(thesis.get("sell_support", 0.0)),
            "thesis_conviction": _clip01(thesis.get("conviction", 0.0)),
            "market_phase": str(phase.get("name", "transition")),
            "market_phase_label": str(phase.get("label", "Transition")),
            "phase_confidence": _clip01(phase.get("confidence", 0.0)),
            "phase_bias": str(phase.get("bias", "HOLD")),
            "structure_setup": str(phase.get("structure_setup", "none")),
            "consolidation_active": bool(phase.get("consolidation_active", False)),
            "continuation_probability": _clip01(phase.get("continuation_probability", 0.0)),
            "pullback_probability": _clip01(phase.get("pullback_probability", 0.0)),
            "reversal_probability": _clip01(phase.get("reversal_probability", 0.0)),
            "fakeout_probability": _clip01(phase.get("fakeout_probability", 0.0)),
            "focus_timeframe": str(phase.get("focus_timeframe", "M5")),
            "transition": transition,
            "alert": alert,
            "actionable": actionable,
            "freshness_score": 1.0,
            "age_sec": 0.0,
            "stale": False,
            "summary": summary,
            "reasons": reasons[:5],
        }

    def _render_signal(
        self,
        signal: Mapping[str, Any],
        policy: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not signal:
            return {
                "status": "empty",
                "action": "HOLD",
                "stale": False,
                "age_sec": 0.0,
                "freshness_score": 0.0,
                "effective_confidence": 0.0,
                "reasons": [],
            }
        rendered = dict(signal)
        age_sec = _signal_age_sec(signal)
        freshness = _freshness_score(age_sec, policy)
        stale_after_sec = max(1.0, _safe_float(policy.get("stale_after_sec", 20.0), 20.0))
        min_freshness = _clip01(policy.get("min_freshness_score", 0.62))
        effective_confidence = _clip01(signal.get("candidate_confidence", 0.0)) * freshness
        signal_id = str(signal.get("signal_id", "") or "").strip().lower()
        manual_test_signal = bool(signal.get("test_mode", False)) or signal_id.startswith("manual_test")
        stale = bool(manual_test_signal or age_sec >= stale_after_sec or freshness < min_freshness)
        action = _direction(signal.get("base_action", signal.get("action", "HOLD")))
        threshold = _clip01(signal.get("adaptive_threshold", policy.get("min_actionable_confidence", 0.64)))
        armed_threshold = _clip01(
            signal.get(
                "signal_armed_threshold",
                max(
                    0.0,
                    _safe_float(policy.get("min_actionable_confidence", 0.64), 0.64) - 0.10,
                ),
            )
        )
        directional_threshold = _clip01(
            signal.get(
                "directional_threshold",
                policy.get(
                    "min_directional_confidence",
                    max(_safe_float(policy.get("min_actionable_confidence", 0.64), 0.64) - 0.14, 0.0),
                ),
            )
        )
        required_threshold = (
            threshold
            if bool(signal.get("actionable", False))
            else (armed_threshold if bool(signal.get("signal_armed", False)) else directional_threshold)
        )
        blocked_reason = ""
        if manual_test_signal:
            blocked_reason = "Manual/test observer signal is blocked from live execution."
        elif stale:
            blocked_reason = "Observer signal is stale."
        if stale or action not in {"BUY", "SELL"} or effective_confidence < required_threshold:
            action = "HOLD"
            rendered["actionable"] = False
            rendered["signal_armed"] = False
            rendered["execution_action"] = "HOLD"
            rendered["base_action"] = "HOLD"
            rendered["candidate_action"] = "HOLD"
            if blocked_reason:
                rendered["no_trade_reason"] = blocked_reason
                rendered["execution_block_reason"] = blocked_reason
        rendered["age_sec"] = age_sec
        rendered["freshness_score"] = freshness
        rendered["stale"] = stale
        rendered["action"] = action
        rendered["effective_confidence"] = effective_confidence
        rendered["status"] = (
            "blocked"
            if manual_test_signal
            else "stale"
            if stale
            else (
                "ready"
                if bool(signal.get("actionable", False)) and action in {"BUY", "SELL"}
                else (
                    "armed"
                    if bool(signal.get("signal_armed", False)) and action in {"BUY", "SELL"}
                    else ("watch" if _direction(signal.get("thesis_action", "HOLD")) in {"BUY", "SELL"} else "hold")
                )
            )
        )
        if rendered["status"] == "hold" and action in {"BUY", "SELL"}:
            rendered["status"] = "watch"
        rendered["summary"] = (
            f"desk={action} | thesis={_direction(signal.get('thesis_action', 'HOLD'))} "
            f"| freshness={freshness:.2f} | confidence={effective_confidence:.2f} "
            f"| phase={str(signal.get('market_phase', 'transition'))} "
            f"| gate={str(signal.get('gate_state', 'watch'))} | timing={str(signal.get('timing_state', 'WATCH'))}"
        )
        return rendered

    def _public_session_payload(
        self,
        payload: Mapping[str, Any],
        *,
        include_history: bool,
    ) -> dict[str, Any]:
        policy = _normalize_policy(cast(Mapping[str, Any] | None, payload.get("policy", {})))
        latest_signal = self._render_signal(
            _payload_dict(payload.get("latest_signal", {})),
            policy,
        )
        signal_history = [
            self._render_signal(item, policy)
            for item in _payload_items(payload.get("signal_history", []))
        ]
        bundle_summaries: list[dict[str, Any]] = [
            {
                "bundle_id": str(item.get("bundle_id", "")),
                "status": str(item.get("status", "")),
                "created_at": str(item.get("created_at", "")),
                "completed_at": str(item.get("completed_at", "")),
                "signal": self._render_signal(_payload_dict(item.get("signal", {})), policy),
            }
            for item in _payload_items(payload.get("bundle_summaries", []))
        ]
        public_payload: dict[str, Any] = {
            "session_id": str(payload.get("session_id", "")),
            "name": str(payload.get("name", "")),
            "market": str(payload.get("market", "")),
            "status": str(payload.get("status", "")),
            "created_at": str(payload.get("created_at", "")),
            "updated_at": str(payload.get("updated_at", "")),
            "last_error": str(payload.get("last_error", "")),
            "settings": dict(payload.get("settings", {})),
            "policy": policy,
            "latest_bundle_id": str(payload.get("latest_bundle_id", "")),
            "last_alert_at": str(payload.get("last_alert_at", "")),
            "latest_signal": latest_signal,
            "recent_bundles": bundle_summaries[-12:],
        }
        if include_history:
            public_payload["signal_history"] = signal_history[-12:]
        return public_payload

    def _public_bundle_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("session_id", ""))
        policy: Mapping[str, Any] = {}
        session = self._read_session(session_id)
        if session:
            policy = _payload_dict(session.get("policy", {}))
        return {
            "session_id": session_id,
            "bundle_id": str(payload.get("bundle_id", "")),
            "status": str(payload.get("status", "")),
            "created_at": str(payload.get("created_at", "")),
            "updated_at": str(payload.get("updated_at", "")),
            "started_at": str(payload.get("started_at", "")),
            "completed_at": str(payload.get("completed_at", "")),
            "last_error": str(payload.get("last_error", "")),
            "settings": dict(payload.get("settings", {})),
            "uploads": [
                {
                    "slot_index": int(item.get("slot_index", 0) or 0),
                    "slot_key": str(item.get("slot_key", "")),
                    "slot_label": str(item.get("slot_label", "")),
                    "original_name": str(item.get("original_name", "")),
                    "width": int(item.get("width", 0) or 0),
                    "height": int(item.get("height", 0) or 0),
                }
                for item in _payload_items(payload.get("uploads", []))
            ],
            "signal": self._render_signal(
                _payload_dict(payload.get("signal", {})),
                policy,
            ),
            "artifacts": [
                _artifact_response_payload(item)
                for item in _payload_items(payload.get("artifacts", []))
            ],
        }

    def _stage_uploads(
        self,
        upload_dir: Path,
        uploads: Sequence[tuple[str, bytes]],
    ) -> list[dict[str, Any]]:
        _filesystem_path(upload_dir).mkdir(parents=True, exist_ok=True)
        records: list[dict[str, Any]] = []
        for index, (filename, payload) in enumerate(uploads, start=1):
            slot = DEFAULT_UPLOAD_ORDER[index - 1]
            original_name = str(filename or f"frame_{index}.png").strip() or f"frame_{index}.png"
            suffix = Path(original_name).suffix.lower()
            if suffix not in ALLOWED_IMAGE_SUFFIXES:
                raise ValueError(f"Unsupported image type for slot {index}: {original_name}")
            size_bytes = len(payload)
            if size_bytes <= 0:
                raise ValueError(f"Slot {index} is empty.")
            if size_bytes > self.max_upload_bytes:
                raise ValueError(
                    f"Slot {index} exceeds the {self.max_upload_bytes} byte upload limit."
                )
            width, height = self._validate_image_bytes(payload)
            target_path = upload_dir / f"{index:02d}_{slot['key']}{suffix}"
            _filesystem_path(target_path).write_bytes(payload)
            records.append(
                {
                    "slot_index": index,
                    "slot_key": slot["key"],
                    "slot_label": slot["label"],
                    "original_name": original_name,
                    "width": width,
                    "height": height,
                    "path": str(target_path),
                }
            )
        return records

    def _validate_image_bytes(self, payload: bytes) -> tuple[int, int]:
        try:
            with Image.open(io.BytesIO(payload)) as image:
                prepared = ImageOps.exif_transpose(image)
                prepared.load()
                width, height = prepared.size
        except Exception as exc:
            raise ValueError("One of the uploaded files is not a valid image.") from exc
        if width < self.min_image_dimension or height < self.min_image_dimension:
            raise ValueError("One of the uploaded images is too small for reliable analysis.")
        if width > self.max_image_dimension or height > self.max_image_dimension:
            raise ValueError(
                "One of the uploaded images is outside the allowed size limits for this observer API."
            )
        return width, height

    def _export_bundle_artifacts(
        self,
        session_id: str,
        bundle_id: str,
        result: Mapping[str, Any],
        source_image_state: Any,
    ) -> list[dict[str, Any]]:
        artifact_dir = self._bundle_dir(session_id, bundle_id) / "artifacts"
        try:
            return self.pipeline_adapter.export_artifacts(
                result,
                source_image_state,
                _filesystem_path(artifact_dir),
                bundle_id,
            )
        except Exception:
            LOGGER.exception("Observer artifact export failed for %s/%s.", session_id, bundle_id)
            return []

    def _session_dir(self, session_id: str) -> Path:
        return self.sessions_dir / str(session_id)

    def _bundle_dir(self, session_id: str, bundle_id: str) -> Path:
        return self._session_dir(session_id) / "bundles" / str(bundle_id)

    def _write_session(self, session_id: str, payload: Mapping[str, Any]) -> None:
        _write_json_atomic(self._session_dir(session_id) / "session.json", payload)

    def _read_session(self, session_id: str) -> dict[str, Any]:
        return _payload_dict(_read_json(self._session_dir(session_id) / "session.json", {}))

    def _write_bundle(
        self,
        session_id: str,
        bundle_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        _write_json_atomic(self._bundle_dir(session_id, bundle_id) / "bundle.json", payload)

    def _read_bundle(self, session_id: str, bundle_id: str) -> dict[str, Any]:
        return _payload_dict(_read_json(self._bundle_dir(session_id, bundle_id) / "bundle.json", {}))

    def _update_bundle(
        self,
        session_id: str,
        bundle_id: str,
        **updates: Any,
    ) -> dict[str, Any]:
        with self._lock:
            payload = self._read_bundle(session_id, bundle_id)
            if not payload:
                return {}
            payload.update(dict(updates))
            payload["session_id"] = session_id
            payload["bundle_id"] = bundle_id
            payload["updated_at"] = _now_iso()
            self._write_bundle(session_id, bundle_id, payload)
            return payload
