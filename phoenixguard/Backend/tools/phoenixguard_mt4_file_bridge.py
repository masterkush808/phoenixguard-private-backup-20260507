from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping, Sequence, cast

_PROJECT_ROOT_BOOTSTRAP = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_BOOTSTRAP))

from _pg_bootstrap import ensure_project_paths

PROJECT_ROOT = ensure_project_paths()

from phoenixguard.runtime.python_environment_v3 import assert_repo_venv_runtime
from phoenixguard.decision.playbook_ai_intelligence_v3 import compact_playbook_ai_intelligence_v3
from phoenixguard.execution.packet_v3 import validate_execution_packet_v3

_PYTHON_ENVIRONMENT_STATUS = assert_repo_venv_runtime("mt4_bridge", PROJECT_ROOT)


def _default_common_files_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set; cannot resolve MetaQuotes common Files directory.")
    return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"


SLOT_BYTES = 65536


def _json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in cast(Mapping[object, object], value).items()}
    if isinstance(value, list):
        return [_json_safe(child) for child in cast(Sequence[object], value)]
    if isinstance(value, tuple):
        return [_json_safe(child) for child in cast(Sequence[object], value)]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _json_dumps(value: object, *, sort_keys: bool = False) -> str:
    return json.dumps(
        _json_safe(value),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=sort_keys,
    )


def _write_text_atomic(path: Path, text: str) -> float:
    """Publish a small MT4 command file with temp-file + atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (text.rstrip("\r\n") + "\n").encode("utf-8")
    if len(payload) > SLOT_BYTES:
        raise ValueError(f"MT4 bridge payload too large: {len(payload)} bytes")
    started = time.perf_counter()
    last_error: Exception | None = None
    fsync_enabled = str(os.getenv("PHOENIXGUARD_MT4_BRIDGE_FSYNC", "0") or "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    temp_name = ""
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            if fsync_enabled:
                os.fsync(handle.fileno())
        for attempt in range(40):
            try:
                os.replace(temp_name, path)
                return (time.perf_counter() - started) * 1000.0
            except (PermissionError, OSError) as exc:
                last_error = exc
                time.sleep(min(0.12, 0.015 * float(attempt + 1)))
    finally:
        if temp_name:
            try:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            except OSError:
                pass
    raise last_error or PermissionError(path)


def _write_text_shared(path: Path, text: str) -> float:
    """Backward-compatible wrapper; now uses atomic publish semantics."""
    return _write_text_atomic(path, text)


def _append_jsonl(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = _json_dumps(record, sort_keys=True) + "\n"
    last_error: Exception | None = None
    for _ in range(5):
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.025)
    if last_error:
        raise last_error


def _get_json(url: str, timeout: float) -> tuple[int, dict[str, object]]:
    req = urllib.request.Request(url=url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            payload: object = json.loads(raw) if raw.strip() else {}
            return int(resp.status), cast(dict[str, object], payload) if isinstance(payload, dict) else {"raw": payload}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload: object = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            payload = {"detail": raw}
        return int(exc.code), cast(dict[str, object], payload) if isinstance(payload, dict) else {"raw": payload}


def _status(
    status: str,
    *,
    detail: str = "",
    http_status: int = 0,
    bridge_sequence: int = 0,
    error: str = "",
) -> dict[str, object]:
    written_epoch = time.time()
    return {
        "schema_version": "PG_MT4_BRIDGE_STATUS_V1",
        "bridge_status": status,
        "detail": detail,
        "http_status": http_status,
        "bridge_sequence": bridge_sequence,
        "written_epoch": written_epoch,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(written_epoch)),
        "heartbeat": {
            "alive": status != "BRIDGE_ERROR",
            "bridge_sequence": bridge_sequence,
            "written_epoch": written_epoch,
        },
        "error": error,
    }


def _nested(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    return dict(cast(Mapping[str, object], value)) if isinstance(value, dict) else {}


def _first_nested(key: str, *sources: dict[str, object]) -> dict[str, object]:
    for source in sources:
        value = _nested(source, key)
        if value:
            return value
    return {}


def _int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return int(default)


def _float(value: object, default: float = 0.0) -> float:
    try:
        result = float(str(value))
    except (TypeError, ValueError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _epoch_seconds(value: object) -> float:
    epoch = _float(value, 0.0)
    if epoch > 100_000_000_000.0:
        return epoch / 1000.0
    return epoch


def _epoch_candidates_from(source_name: str, payload: Mapping[str, object]) -> list[tuple[str, float]]:
    candidates: list[tuple[str, float]] = []
    for key in (
        "last_capture_epoch",
        "capture_epoch",
        "display_capture_epoch",
        "last_display_published_epoch",
        "display_published_epoch",
        "generated_epoch",
        "snapshot_epoch",
        "timestamp_epoch",
        "updated_epoch",
        "last_update_epoch",
    ):
        epoch = _epoch_seconds(payload.get(key))
        if epoch > 0.0:
            candidates.append((f"{source_name}.{key}", epoch))
    for key in (
        "last_capture_epoch_ms",
        "capture_epoch_ms",
        "display_capture_epoch_ms",
        "last_display_published_epoch_ms",
        "display_published_epoch_ms",
        "generated_epoch_ms",
        "snapshot_epoch_ms",
        "timestamp_epoch_ms",
        "updated_epoch_ms",
        "last_update_epoch_ms",
    ):
        epoch = _epoch_seconds(payload.get(key))
        if epoch > 0.0:
            candidates.append((f"{source_name}.{key}", epoch))
    return candidates


def _latest_monitor_capture_epoch(
    live: Mapping[str, object],
    performance: Mapping[str, object] | None,
    *,
    now_epoch: float,
) -> tuple[float, str]:
    candidates = _epoch_candidates_from("live", live)
    provider_status = live.get("provider_status")
    if isinstance(provider_status, dict):
        candidates.extend(_epoch_candidates_from("live.provider_status", cast(Mapping[str, object], provider_status)))
    timing_trace: Mapping[str, object] = {}
    if performance is not None:
        candidates.extend(_epoch_candidates_from("performance", performance))
        timing_raw = performance.get("timing_trace")
        if isinstance(timing_raw, dict):
            timing_trace = cast(Mapping[str, object], timing_raw)
            candidates.extend(_epoch_candidates_from("performance.timing_trace", timing_trace))
        frame_age_ms = _float(performance.get("frame_age_ms"), -1.0)
        if frame_age_ms < 0.0:
            frame_age_ms = _float(timing_trace.get("frame_age_ms"), -1.0)
        if 0.0 <= frame_age_ms <= 86_400_000.0:
            candidates.append(("performance.frame_age_ms", now_epoch - (frame_age_ms / 1000.0)))

    bounded = [(label, epoch) for label, epoch in candidates if epoch > 0.0 and epoch <= now_epoch + 5.0]
    if not bounded:
        return 0.0, ""
    label, epoch = max(bounded, key=lambda item: item[1])
    return epoch, label


def _monitor_frame_id(live: Mapping[str, object], performance: Mapping[str, object] | None) -> int:
    frame = max(
        _int(live.get("frame_id"), 0),
        _int(live.get("display_frame_id"), 0),
        _int(live.get("frame_index"), 0),
    )
    if performance is None:
        return frame
    timing_raw = performance.get("timing_trace")
    timing_trace: Mapping[str, object] = {}
    if isinstance(timing_raw, dict):
        timing_trace = cast(Mapping[str, object], timing_raw)
    return max(
        frame,
        _int(performance.get("frame_id"), 0),
        _int(performance.get("display_frame_id"), 0),
        _int(performance.get("frame_index"), 0),
        _int(timing_trace.get("frame_id"), 0),
        _int(timing_trace.get("display_frame_id"), 0),
        _int(timing_trace.get("display_frame_index"), 0),
    )


def _monitor_capture_count(live: Mapping[str, object], performance: Mapping[str, object] | None, fallback: int) -> int:
    capture = max(_int(live.get("capture_count"), 0), _int(live.get("display_capture_count"), 0))
    if performance is not None:
        timing_raw = performance.get("timing_trace")
        timing_trace: Mapping[str, object] = {}
        if isinstance(timing_raw, dict):
            timing_trace = cast(Mapping[str, object], timing_raw)
        capture = max(
            capture,
            _int(performance.get("capture_count"), 0),
            _int(performance.get("display_capture_count"), 0),
            _int(timing_trace.get("capture_count"), 0),
            _int(timing_trace.get("display_capture_count"), 0),
        )
    return capture if capture > 0 else fallback


def _collect_reason_codes(*sources: dict[str, object]) -> list[str]:
    reasons: list[str] = []
    for source in sources:
        for key in ("reason_codes", "reasons", "blockers", "deny_reasons"):
            raw = source.get(key)
            if isinstance(raw, list):
                reasons.extend(str(item) for item in cast(Sequence[object], raw) if str(item))
            elif isinstance(raw, str) and raw:
                reasons.append(raw)
    return list(dict.fromkeys(reasons))


def _packet_current_in_live_monitor(
    packet: Mapping[str, object],
    live: Mapping[str, object],
    *,
    performance: Mapping[str, object] | None = None,
    now_epoch: float,
    max_live_age_sec: float,
    max_packet_frame_lag: int,
) -> tuple[bool, str]:
    if live.get("tracking_enabled") is not True:
        return False, "live monitor tracking_enabled is not true"
    if str(live.get("status") or "").strip().upper() not in {"RUNNING", "ACTIVE", "LIVE"}:
        return False, "live monitor status is not running"
    packet_id = str(packet.get("packet_id") or "").strip()
    if not packet_id:
        return False, "packet id missing"
    created_epoch = _float(packet.get("created_epoch_sec") or packet.get("created_epoch"), 0.0)
    if created_epoch <= 0.0:
        return False, "packet created_epoch missing"
    valid_until = _float(packet.get("valid_until_epoch_sec") or packet.get("valid_until_epoch"), 0.0)
    if valid_until <= 0.0:
        return False, "packet valid_until missing"
    if valid_until <= created_epoch:
        return False, "packet valid_until is not after created_epoch"
    if now_epoch >= valid_until:
        return False, f"packet expired {now_epoch - valid_until:.1f}s ago"
    live_packet = _nested(dict(live), "latest_execution_packet")
    live_packet_id = str(live_packet.get("packet_id") or "").strip()
    if not live_packet_id:
        return False, "live monitor latest execution packet id missing"
    if packet_id != live_packet_id:
        return False, f"live monitor packet id changed: {live_packet_id}"
    packet_frame = _int(packet.get("frame_id") or packet.get("capture_count"), 0)
    live_frame = _monitor_frame_id(live, performance)
    if packet_frame <= 0 or live_frame <= 0:
        return False, "packet/live frame id missing"
    if live_frame < packet_frame:
        return False, f"live frame {live_frame} is behind packet frame {packet_frame}"
    frame_lag = live_frame - packet_frame
    if frame_lag > max(0, int(max_packet_frame_lag)):
        return False, f"packet frame lag {frame_lag} exceeds {max_packet_frame_lag}"
    packet_capture = _int(packet.get("capture_count"), packet_frame)
    live_capture = _monitor_capture_count(live, performance, live_frame)
    if live_capture < packet_capture:
        return False, f"live capture {live_capture} is behind packet capture {packet_capture}"
    capture_epoch, capture_epoch_source = _latest_monitor_capture_epoch(live, performance, now_epoch=now_epoch)
    if capture_epoch <= 0.0:
        return False, "live monitor capture epoch missing"
    capture_age = max(0.0, now_epoch - capture_epoch)
    if capture_age > max_live_age_sec:
        return False, (
            f"live monitor capture age {capture_age:.1f}s exceeds {max_live_age_sec:.1f}s"
            + (f" from {capture_epoch_source}" if capture_epoch_source else "")
        )
    execution = _nested(dict(packet), "execution")
    side = str(packet.get("side") or execution.get("side") or "").strip().upper()
    live_execution = _nested(live_packet, "execution")
    live_side = str(live_packet.get("side") or live_execution.get("side") or "").strip().upper()
    if side in {"BUY", "SELL"} and live_side in {"BUY", "SELL"} and side != live_side:
        return False, f"live monitor packet side changed: {live_side}"
    detail = "live monitor freshness passed"
    if capture_epoch_source:
        detail += f" using {capture_epoch_source}"
    return True, detail


def _allowance_source(payload: dict[str, object], execution: dict[str, object], council: dict[str, object]) -> dict[str, object]:
    for source in (
        _nested(payload, "allowance_package"),
        _nested(council, "allowance_package"),
    ):
        if source:
            return source
    return {}


def _compact_list(value: object, *, limit: int = 12) -> list[str]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [str(item) for item in cast(Sequence[object], value)[:limit]]
    return []


def _compact_side_score(value: object) -> dict[str, object]:
    score = dict(cast(Mapping[str, object], value)) if isinstance(value, dict) else {}
    components = _nested(score, "components")
    return {
        "side": str(score.get("side") or ""),
        "score": _float(score.get("score"), 0.0),
        "overlay_score": _float(_nested(components, "overlay").get("score"), 0.0),
        "professional_score": _float(_nested(components, "professional").get("score"), 0.0),
        "candle_score": _float(_nested(components, "candle_movement").get("score"), 0.0),
        "market_score": _float(_nested(components, "market").get("score"), 0.0),
    }


def _sanitize_playbook_ai_summary(summary: dict[str, object]) -> dict[str, object]:
    coverage = _nested(summary, "coverage")
    router = _nested(summary, "regime_router")
    arbitration = _nested(summary, "thesis_arbitration")
    scores = _nested(arbitration, "scores")
    meta = _nested(summary, "meta_label")
    horizon = _nested(summary, "horizon")
    return {
        "schema_version": "PG_PLAYBOOK_AI_SUMMARY_V3",
        "source_schema_version": str(summary.get("source_schema_version") or summary.get("schema_version") or ""),
        "semantic_interpretation": str(summary.get("semantic_interpretation") or summary.get("interpretation") or ""),
        "full_suite_ready": bool(summary.get("full_suite_ready", coverage.get("full_suite_ready", False))),
        "coverage": {
            "rows_total": _int(coverage.get("rows_total"), 0),
            "actionable_count": _int(coverage.get("actionable_count"), 0),
            "same_side_actionable_count": _int(coverage.get("same_side_actionable_count"), 0),
            "entry_window_count": _int(coverage.get("entry_window_count"), 0),
            "same_side_entry_window_count": _int(coverage.get("same_side_entry_window_count"), 0),
            "target_window_count": _int(coverage.get("target_window_count"), 0),
            "opposing_force_count": _int(coverage.get("opposing_force_count"), 0),
            "invalidation_count": _int(coverage.get("invalidation_count"), 0),
            "prediction_path_count": _int(coverage.get("prediction_path_count"), 0),
            "structure_box_count": _int(coverage.get("structure_box_count"), 0),
            "trendline_count": _int(coverage.get("trendline_count"), 0),
            "overlay_arsenal_score": _float(coverage.get("overlay_arsenal_score"), 0.0),
            "expected_move_candles": _int(coverage.get("expected_move_candles"), 0),
        },
        "missing_first_class_feeds": _compact_list(summary.get("missing_first_class_feeds"), limit=12),
        "regime_router": {
            "regime": str(router.get("regime") or ""),
            "route": str(router.get("route") or ""),
            "route_side": str(router.get("route_side") or ""),
            "confidence": _float(router.get("confidence"), 0.0),
            "current_leg_side": str(router.get("current_leg_side") or ""),
            "current_leg_stage": str(router.get("current_leg_stage") or ""),
        },
        "thesis_arbitration": {
            "candidate_side": str(arbitration.get("candidate_side") or ""),
            "winner": str(arbitration.get("winner") or ""),
            "winning_score": _float(arbitration.get("winning_score"), 0.0),
            "margin": _float(arbitration.get("margin"), 0.0),
            "candidate_score": _float(arbitration.get("candidate_score"), 0.0),
            "candidate_supported": bool(arbitration.get("candidate_supported", False)),
            "conflict": bool(arbitration.get("conflict", False)),
            "state": str(arbitration.get("state") or ""),
            "scores": {
                "BUY": _compact_side_score(scores.get("BUY")),
                "SELL": _compact_side_score(scores.get("SELL")),
            },
        },
        "meta_label": {
            "selected_side": str(meta.get("selected_side") or ""),
            "candidate_tradeable": bool(meta.get("candidate_tradeable", False)),
            "target_before_invalidation_probability": _float(meta.get("target_before_invalidation_probability"), 0.0),
            "invalidation_first_risk": _float(meta.get("invalidation_first_risk"), 0.0),
            "label": str(meta.get("label") or ""),
        },
        "horizon": {
            "selected_side": str(horizon.get("selected_side") or ""),
            "optimized_candle_count": _int(horizon.get("optimized_candle_count"), 0),
            "optimized_duration_sec": _int(horizon.get("optimized_duration_sec"), 0),
            "optimized_duration_text": str(horizon.get("optimized_duration_text") or ""),
            "horizon_class": str(horizon.get("horizon_class") or ""),
            "basis": str(horizon.get("basis") or ""),
            "target_before_invalidation_probability": _float(
                horizon.get("target_before_invalidation_probability"), 0.0
            ),
        },
        "rules_applied": _compact_list(summary.get("rules_applied"), limit=12),
    }


def _compact_playbook_ai_summary(*sources: dict[str, object]) -> dict[str, object]:
    summary = _first_nested("playbook_ai_summary_v3", *sources)
    if summary:
        if "semantic_graph" in summary:
            return cast(dict[str, object], compact_playbook_ai_intelligence_v3(cast(Mapping[str, Any], summary)))
        return _sanitize_playbook_ai_summary(summary)
    intelligence = _first_nested("playbook_ai_intelligence_v3", *sources)
    if not intelligence:
        return {}
    return cast(dict[str, object], compact_playbook_ai_intelligence_v3(cast(Mapping[str, Any], intelligence)))


def _compact_thesis_resolution(resolution: dict[str, object]) -> dict[str, object]:
    if not resolution:
        return {}
    return {
        "schema_version": str(resolution.get("schema_version") or ""),
        "authority_side": str(resolution.get("authority_side") or ""),
        "raw_candidate_side": str(resolution.get("raw_candidate_side") or ""),
        "thesis_state": str(resolution.get("thesis_state") or ""),
        "reason": str(resolution.get("reason") or "")[:280],
        "global_side": str(resolution.get("global_side") or ""),
        "local_side": str(resolution.get("local_side") or ""),
        "dominant_side": str(resolution.get("dominant_side") or ""),
        "primary_bias_side": str(resolution.get("primary_bias_side") or ""),
        "current_leg_side": str(resolution.get("current_leg_side") or ""),
        "current_leg_candle_count": _int(resolution.get("current_leg_candle_count"), 0),
        "current_leg_stage": str(resolution.get("current_leg_stage") or ""),
        "directional_target_room_candles": _int(resolution.get("directional_target_room_candles"), 0),
        "room_ok": bool(resolution.get("room_ok", False)),
        "side_reframed": bool(resolution.get("side_reframed", False)),
        "opposing_force_reaction_ready": bool(resolution.get("opposing_force_reaction_ready", False)),
        "opposing_force_rejection_confirmed": bool(resolution.get("opposing_force_rejection_confirmed", False)),
    }


def _compact_professional_trade_plan(source: dict[str, object], *fallback_sources: dict[str, object]) -> dict[str, object]:
    sources = (source, *fallback_sources)
    plan = _first_nested("professional_trade_plan", *sources)
    resolution = _first_nested("professional_thesis_resolution", *sources, plan)
    horizon = _first_nested("thesis_horizon", *sources, plan)
    entry_window = _first_nested("entry_window", *sources, plan)
    expected = _first_nested("expected_move_time", *sources, plan)
    movement = _first_nested("candle_movement", *sources, plan)
    playbook_ai_summary = _compact_playbook_ai_summary(*sources, plan, resolution, horizon, entry_window, expected, movement)
    return {
        "schema_version": str(plan.get("schema_version") or "PG_PROFESSIONAL_TRADE_PLAN_V3"),
        "professional_grade": bool(plan.get("professional_grade", source.get("professional_grade", False))),
        "side": str(plan.get("side") or source.get("side") or ""),
        "authority_side": str(plan.get("authority_side") or source.get("professional_authority_side") or source.get("side") or ""),
        "thesis_class": str(plan.get("thesis_class") or ""),
        "professional_thesis_state": str(
            plan.get("professional_thesis_state")
            or source.get("professional_thesis_state")
            or resolution.get("thesis_state")
            or ""
        ),
        "blocker": str(plan.get("blocker") or ""),
        "next_required": str(plan.get("next_required") or source.get("next_required") or ""),
        "expected_duration_sec": _int(
            horizon.get("expected_duration_sec")
            or expected.get("expected_duration_sec")
            or 0
        ),
        "expected_candle_count": _int(
            horizon.get("expected_candle_count")
            or expected.get("expected_candle_count")
            or 0
        ),
        "minimum_professional_candles": _int(horizon.get("minimum_professional_candles"), 0),
        "current_leg_candle_count": _int(
            horizon.get("current_leg_candle_count")
            or movement.get("current_leg_candle_count")
            or 0
        ),
        "current_leg_side": str(horizon.get("current_leg_side") or movement.get("current_leg_side") or ""),
        "current_leg_stage": str(horizon.get("current_leg_stage") or movement.get("current_leg_stage") or ""),
        "estimated_candles_to_force": _int(horizon.get("estimated_candles_to_force"), 0),
        "entry_window_duration_sec": _int(entry_window.get("duration_sec"), 0),
        "entry_window_candle_count": _int(entry_window.get("candle_count"), 0),
        "thesis_resolution": _compact_thesis_resolution(resolution),
        "playbook_ai_summary_v3": playbook_ai_summary,
    }


def _sanitize_expected_move_time(expected: dict[str, object], horizon: dict[str, object]) -> dict[str, object]:
    entry_window = _nested(expected, "entry_window")
    thesis_horizon = _nested(expected, "thesis_horizon") or horizon
    professional_plan = _nested(expected, "professional_trade_plan")
    return {
        "expected_duration_sec": _int(
            expected.get("expected_duration_sec")
            or thesis_horizon.get("expected_duration_sec")
            or professional_plan.get("expected_duration_sec"),
            0,
        ),
        "expected_duration_text": str(expected.get("expected_duration_text") or thesis_horizon.get("expected_duration_text") or ""),
        "expected_candle_count": _int(
            expected.get("expected_candle_count")
            or thesis_horizon.get("expected_candle_count")
            or professional_plan.get("expected_candle_count"),
            0,
        ),
        "timeframe": str(expected.get("timeframe") or thesis_horizon.get("timeframe") or ""),
        "timeframe_seconds": _int(expected.get("timeframe_seconds") or thesis_horizon.get("timeframe_seconds"), 0),
        "current_leg_candle_count": _int(
            expected.get("current_leg_candle_count")
            or thesis_horizon.get("current_leg_candle_count")
            or professional_plan.get("current_leg_candle_count"),
            0,
        ),
        "current_leg_side": str(
            expected.get("current_leg_side")
            or thesis_horizon.get("current_leg_side")
            or professional_plan.get("current_leg_side")
            or ""
        ),
        "current_leg_stage": str(
            expected.get("current_leg_stage")
            or thesis_horizon.get("current_leg_stage")
            or professional_plan.get("current_leg_stage")
            or ""
        ),
        "projected_total_current_leg_candles": _int(
            expected.get("projected_total_current_leg_candles")
            or thesis_horizon.get("projected_total_current_leg_candles"),
            0,
        ),
        "entry_window": {
            "duration_sec": _int(entry_window.get("duration_sec"), 0),
            "duration_text": str(entry_window.get("duration_text") or ""),
            "candle_count": _int(entry_window.get("candle_count"), 0),
            "purpose": str(entry_window.get("purpose") or "immediate entry validity window only"),
        },
        "basis": str(expected.get("basis") or thesis_horizon.get("basis") or ""),
    }


def _compact_expected_move_time(
    source: dict[str, object],
    professional_plan: dict[str, object],
    *fallback_sources: dict[str, object],
) -> dict[str, object]:
    raw_professional_plan = _first_nested("professional_trade_plan", source, *fallback_sources)
    sources = (source, raw_professional_plan, *fallback_sources, professional_plan)
    expected = _first_nested("expected_move_time", *sources)
    horizon = _first_nested("thesis_horizon", *sources)
    if expected:
        return _sanitize_expected_move_time(expected, horizon)
    if not horizon:
        return {}
    compact: dict[str, object] = {}
    for key in (
        "expected_duration_sec",
        "expected_duration_text",
        "expected_candle_count",
        "minimum_professional_candles",
        "current_leg_candle_count",
        "current_leg_side",
        "current_leg_stage",
        "estimated_candles_to_force",
        "projected_total_current_leg_candles",
    ):
        if key in horizon:
            compact[key] = horizon[key]
    if compact and "basis" not in compact:
        compact["basis"] = "thesis_horizon"
    return compact


def _compact_allowance_package(
    payload: dict[str, object],
    *,
    execution: dict[str, object],
    council: dict[str, object],
    eligible: bool,
    side: str,
) -> dict[str, object]:
    source = _allowance_source(payload, execution, council)
    source_present = bool(source)
    timing_decision = _nested(payload, "timing_decision") or _nested(council, "timing_decision")
    if source_present:
        timing_mode = str(source.get("timing_mode") or "").upper()
        entry_now_allowed = source.get("entry_now_allowed") is True
    else:
        timing_mode = str(
            timing_decision.get("timing_mode")
            or execution.get("timing_mode")
            or ""
        ).upper()
        entry_now_allowed = timing_decision.get("entry_now_allowed") is True
    package_type = str(source.get("package_type") or "").upper()
    if not package_type and not source_present:
        package_type = "INTRADAY_ENTER_NOW" if entry_now_allowed and timing_mode == "ENTER_NOW" else "SWING"
    allowance_family = str(source.get("allowance_family") or "").upper()
    if not allowance_family and not source_present:
        allowance_family = "INTRADAY" if package_type == "INTRADAY_ENTER_NOW" else "SWING"
    professional_plan = _compact_professional_trade_plan(source, payload, execution, council, timing_decision)
    expected_move_time = _compact_expected_move_time(source, professional_plan, payload, execution, council, timing_decision)
    playbook_ai_summary = _compact_playbook_ai_summary(source, professional_plan, payload, execution, council, timing_decision)
    return {
        "schema_version": str(
            source.get("schema_version")
            or ("" if source_present else "PG_ALLOWANCE_PACKAGE_V1")
        ),
        "package_type": package_type,
        "allowance_family": allowance_family,
        "execution_authority": str(
            source.get("execution_authority")
            or ("" if source_present else "PLAYBOOK_FINAL_DECIDER_V3")
        ),
        "packet_authority": str(
            source.get("packet_authority")
            or ("" if source_present else "PG_EXECUTION_PACKET_V3")
        ),
        "source_present": source_present,
        "inferred": not source_present,
        "side": str(source.get("side") or ("" if source_present else side)),
        "accepted": source.get("accepted") is True if source_present else bool(eligible),
        "decision_accepted": source.get("decision_accepted") is True if source_present else bool(eligible),
        "execution_ready": source.get("execution_ready") is True if source_present else bool(eligible),
        "executable": source.get("executable") is True if source_present else bool(eligible),
        "live_executable": source.get("live_executable"),
        "ablation_profile": str(source.get("ablation_profile") or ""),
        "tracking_active": bool(source.get("tracking_active", False)) and not eligible,
        "intraday_capture_active": bool(source.get("intraday_capture_active", package_type == "INTRADAY_ENTER_NOW" and eligible)),
        "entry_now_allowed": bool(entry_now_allowed),
        "timing_mode": timing_mode,
        "path_class": str(source.get("path_class") or timing_decision.get("path_class") or ""),
        "selected_lane": str(source.get("selected_lane") or council.get("selected_execution_lane") or council.get("selected_lane") or payload.get("selected_execution_lane") or ""),
        "score": source.get("score", council.get("final_execution_score", payload.get("final_score", 0.0))),
        "threshold": source.get("threshold", council.get("threshold", payload.get("threshold", 0.0))),
        "true_blocker": source.get("true_blocker", ""),
        "next_required": source.get("next_required", ""),
        "professional_trade_plan": professional_plan,
        "playbook_ai_summary_v3": playbook_ai_summary,
        "professional_grade": professional_plan["professional_grade"],
        "professional_thesis_state": professional_plan["professional_thesis_state"],
        "professional_authority_side": professional_plan["authority_side"],
        "expected_move_time": expected_move_time,
    }


def _clean_override(value: object) -> str:
    return str(value or "").strip()


def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        return float(default)


def _compact_command(
    payload: dict[str, object],
    *,
    bridge_sequence: int = 0,
    symbol_override: object = "",
    timeframe_override: object = "",
    validation_now_epoch: float | None = None,
) -> dict[str, object]:
    validation = validate_execution_packet_v3(
        payload,
        now_epoch=validation_now_epoch,
        require_executable=True,
        require_overlay_truth=True,
        require_live_handoff_truth=True,
    )
    if validation.rejected:
        raise ValueError(
            "MT4 source packet failed shared live validation: "
            + ",".join(validation.reason_codes)
        )
    execution = _nested(payload, "execution")
    council = _nested(payload, "model_council")
    live = _nested(payload, "live_integrity")
    health = _nested(payload, "runtime_model_health")
    sequence = _nested(council, "sequence_context")
    permission = _nested(payload, "trade_permission") or _nested(council, "trade_permission")
    time_sequence = _nested(execution, "time_sequence")
    created_epoch = payload.get("created_epoch_sec") or payload.get("created_epoch", 0)
    valid_until = payload.get("valid_until_epoch_sec") or payload.get("valid_until_epoch", 0)
    execution_state = str(execution.get("state", ""))
    side = str(execution.get("side") or council.get("final_side") or "")
    executable_allowed = permission.get("executable_allowed") is True if permission else False
    eligible = bool(execution.get("enabled", False)) and execution_state == "EXECUTABLE" and bool(executable_allowed)
    allowance_package = _compact_allowance_package(
        payload,
        execution=execution,
        council=council,
        eligible=eligible,
        side=side,
    )
    bridge_written_epoch = time.time()
    symbol = _clean_override(symbol_override) or str(payload.get("symbol", ""))
    timeframe = _clean_override(timeframe_override) or str(payload.get("timeframe", ""))
    return {
        "schema_version": "PG_MT4_EXECUTION_COMMAND_V1",
        "source_schema_version": payload.get("schema_version", ""),
        "bridge_sequence": bridge_sequence,
        "packet_id": payload.get("packet_id", ""),
        "session_id": payload.get("session_id", ""),
        "symbol": symbol,
        "timeframe": timeframe,
        "frame_id": payload.get("frame_id", 0),
        "capture_count": payload.get("capture_count", 0),
        "state_version": payload.get("state_version", 0),
        "created_epoch_sec": bridge_written_epoch,
        "source_created_epoch_sec": created_epoch,
        "valid_until_epoch_sec": valid_until,
        "signal_state": {
            "state": execution_state or str(council.get("final_state", "")),
            "side": side,
            "source": "model_council_execution_latest",
            "allowance_package_type": allowance_package["package_type"],
        },
        "permission_state": {
            "execution_enabled": bool(execution.get("enabled", False)),
            "execution_state": execution_state,
            "trade_executable_allowed": bool(executable_allowed),
            "entry_eligible": eligible,
        },
        "entry_eligibility": {
            "eligible": eligible,
            "side": side,
            "state": execution_state,
            "valid_until_epoch_sec": valid_until,
            "allowance_package_type": allowance_package["package_type"],
            "allowance_family": allowance_package["allowance_family"],
        },
        "allowance_package": allowance_package,
        "playbook_ai_summary_v3": allowance_package["playbook_ai_summary_v3"],
        "expected_move_time": allowance_package["expected_move_time"],
        "professional_trade_plan": allowance_package["professional_trade_plan"],
        "reason_codes": _collect_reason_codes(payload, execution, council, permission),
        "confidence_score": council.get("confidence")
        or council.get("final_confidence")
        or council.get("dominance_margin")
        or payload.get("confidence")
        or 0.0,
        "execution": {
            "enabled": execution.get("enabled", False),
            "state": execution_state,
            "side": side,
            "expiry_seconds": execution.get("expiry_seconds", 0),
            "amount_action": execution.get("amount_action", ""),
            "allowance_package_type": allowance_package["package_type"],
            "time_sequence": {
                "target_seconds": time_sequence.get("target_seconds") or execution.get("expiry_seconds", 0),
                "target_text": time_sequence.get("target_text", ""),
            },
        },
        "model_council": {
            "final_state": council.get("final_state", ""),
            "final_side": council.get("final_side", ""),
            "dominance_margin": council.get("dominance_margin", 0.0),
            "allowance_package_type": allowance_package["package_type"],
            "sequence_context": {
                "sequence_status": sequence.get("sequence_status") or sequence.get("status", ""),
                "status": sequence.get("status") or sequence.get("sequence_status", ""),
                "sequence_length": sequence.get("sequence_length", 0),
                "sequence_confidence": sequence.get("sequence_confidence", 1.0),
            },
        },
        "live_integrity": {
            "is_live": live.get("is_live") is True,
            "frame_advancing": live.get("frame_advancing") is True,
            "capture_advancing": live.get("capture_advancing") is True,
            "state_advancing": live.get("state_advancing") is True,
            "source": live.get("source", ""),
            "cache_status": live.get("cache_status", ""),
            "input_frame_hash": live.get("input_frame_hash", ""),
        },
        "runtime_model_health": {
            "all_required_models_awake": health.get("all_required_models_awake") is True,
        },
        "trade_permission": {
            "executable_allowed": executable_allowed,
        },
        "heartbeat": {
            "alive": True,
            "bridge_sequence": bridge_sequence,
            "source_created_epoch_sec": created_epoch,
            "valid_until_epoch_sec": valid_until,
            "bridge_written_epoch": bridge_written_epoch,
        },
        "error": "",
        "bridge_compacted": True,
        "bridge_written_epoch": bridge_written_epoch,
    }


def _validate_command(command: dict[str, object]) -> None:
    required_top = (
        "schema_version",
        "packet_id",
        "symbol",
        "created_epoch_sec",
        "valid_until_epoch_sec",
        "execution",
        "allowance_package",
        "model_council",
        "live_integrity",
        "trade_permission",
        "bridge_sequence",
        "heartbeat",
    )
    missing = [key for key in required_top if key not in command]
    if missing:
        raise ValueError(f"MT4 command missing required fields: {missing}")
    if command.get("schema_version") != "PG_MT4_EXECUTION_COMMAND_V1":
        raise ValueError("MT4 command schema_version mismatch")
    if not str(command.get("packet_id") or ""):
        raise ValueError("MT4 command packet_id is empty")
    if not str(command.get("symbol") or ""):
        raise ValueError("MT4 command symbol is empty")
    execution = _nested(command, "execution")
    for key in ("enabled", "state", "side", "expiry_seconds", "amount_action", "time_sequence"):
        if key not in execution:
            raise ValueError(f"MT4 command execution missing {key}")
    if execution.get("amount_action") != "DO_NOT_CHANGE_AMOUNT":
        raise ValueError("MT4 command execution.amount_action must be DO_NOT_CHANGE_AMOUNT")
    allowance = _nested(command, "allowance_package")
    allowance_schema = str(allowance.get("schema_version") or "").strip().upper()
    ablation_profile = str(allowance.get("ablation_profile") or "").strip().upper()
    if allowance_schema == "SHADOW_ALLOWANCE_PACKAGE_V1":
        raise ValueError("MT4 command rejects SHADOW_ALLOWANCE_PACKAGE_V1")
    if allowance.get("live_executable") is False:
        raise ValueError("MT4 command rejects allowance_package.live_executable=false")
    if ablation_profile not in {"", "BASELINE_FULL_SAFETY"}:
        raise ValueError("MT4 command rejects non-baseline blocker ablation profiles")
    if allowance.get("schema_version") != "PG_ALLOWANCE_PACKAGE_V1":
        raise ValueError("MT4 command allowance_package schema_version mismatch")
    if allowance.get("source_present") is not True or allowance.get("inferred") is True:
        raise ValueError("MT4 command allowance_package must be explicit from Playbook final decider")
    if allowance.get("package_type") not in {"SWING", "SWING_ENTER_NOW", "INTRADAY_ENTER_NOW"}:
        raise ValueError("MT4 command allowance_package.package_type must be SWING, SWING_ENTER_NOW, or INTRADAY_ENTER_NOW")
    authority = str(allowance.get("execution_authority") or "").strip().upper()
    packet_authority = str(allowance.get("packet_authority") or "PG_EXECUTION_PACKET_V3").strip().upper()
    if authority != "PLAYBOOK_FINAL_DECIDER_V3":
        raise ValueError("MT4 command allowance_package.execution_authority must be PLAYBOOK_FINAL_DECIDER_V3")
    if packet_authority != "PG_EXECUTION_PACKET_V3":
        raise ValueError("MT4 command allowance_package.packet_authority must be PG_EXECUTION_PACKET_V3")
    if allowance.get("accepted") is not True:
        raise ValueError("MT4 command allowance_package.accepted must be true")
    if allowance.get("execution_ready") is not True:
        raise ValueError("MT4 command allowance_package.execution_ready must be true")
    if allowance.get("package_type") == "INTRADAY_ENTER_NOW":
        if allowance.get("entry_now_allowed") is not True:
            raise ValueError("MT4 command INTRADAY_ENTER_NOW allowance must keep entry_now_allowed=true")
        if str(allowance.get("timing_mode") or "").strip().upper() != "ENTER_NOW":
            raise ValueError("MT4 command INTRADAY_ENTER_NOW allowance must keep timing_mode=ENTER_NOW")
    professional_plan = _nested(allowance, "professional_trade_plan")
    if not professional_plan:
        raise ValueError("MT4 command allowance_package.professional_trade_plan is required")
    if professional_plan.get("professional_grade") is not True:
        raise ValueError("MT4 command professional_trade_plan.professional_grade must be true")
    professional_side = str(professional_plan.get("authority_side") or professional_plan.get("side") or "").strip().upper()
    if professional_side not in {"BUY", "SELL"}:
        raise ValueError("MT4 command professional_trade_plan authority side must be BUY or SELL")
    if professional_side != str(allowance.get("side") or "").strip().upper():
        raise ValueError("MT4 command professional_trade_plan side must match allowance side")
    professional_blocker = str(professional_plan.get("blocker") or "").strip().upper()
    if professional_blocker not in {"", "NONE"}:
        raise ValueError("MT4 command professional_trade_plan must not carry a blocker")
    expected_candles = _int(professional_plan.get("expected_candle_count"), 0)
    minimum_candles = max(1, _int(professional_plan.get("minimum_professional_candles"), 1))
    if expected_candles < minimum_candles:
        raise ValueError("MT4 command professional_trade_plan expected candles are below professional minimum")
    live = _nested(command, "live_integrity")
    if live.get("is_live") is not True:
        raise ValueError("MT4 command live_integrity.is_live must be true")
    for key in ("frame_advancing", "capture_advancing", "state_advancing"):
        if live.get(key) is not True:
            raise ValueError(f"MT4 command live_integrity.{key} must be true")
    if live.get("cache_status") != "fresh":
        raise ValueError("MT4 command live_integrity.cache_status must be fresh")
    if live.get("source") != "model_council":
        raise ValueError("MT4 command live_integrity.source must be model_council")
    if not str(live.get("input_frame_hash") or ""):
        raise ValueError("MT4 command live_integrity.input_frame_hash is empty")
    health = _nested(command, "runtime_model_health")
    if health.get("all_required_models_awake") is not True:
        raise ValueError("MT4 command runtime_model_health.all_required_models_awake must be true")
    trade_permission = _nested(command, "trade_permission")
    if trade_permission.get("executable_allowed") is not True:
        raise ValueError("MT4 command trade_permission.executable_allowed must be true")
    _json_dumps(command)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge PhoenixGuard execution packets into MT4 FILE_COMMON.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8793")
    parser.add_argument("--session-id", default="pocket-live-8788")
    parser.add_argument("--common-files-dir", default="")
    parser.add_argument("--signal-file", default=r"PhoenixGuard\mt4_execution_command.json")
    parser.add_argument("--status-file", default=r"PhoenixGuard\mt4_bridge_status.json")
    parser.add_argument("--metrics-file", default=r"PhoenixGuard\mt4_bridge_metrics.jsonl")
    parser.add_argument("--poll-sec", type=float, default=15.0)
    parser.add_argument("--timeout-sec", type=float, default=8.0)
    parser.add_argument("--symbol-override", default=os.getenv("PHOENIXGUARD_MT4_SYMBOL", ""))
    parser.add_argument("--timeframe-override", default=os.getenv("PHOENIXGUARD_MT4_TIMEFRAME", ""))
    parser.add_argument("--max-live-age-sec", type=float, default=_env_float("PHOENIXGUARD_MT4_MAX_LIVE_AGE_SEC", 180.0))
    parser.add_argument(
        "--max-packet-frame-lag",
        type=int,
        default=_env_int("PHOENIXGUARD_MT4_MAX_PACKET_FRAME_LAG", 8),
    )
    parser.add_argument("--print-every", type=float, default=30.0)
    parser.add_argument("--metrics-every", type=float, default=15.0)
    args = parser.parse_args()

    common_root = Path(args.common_files_dir) if args.common_files_dir else _default_common_files_dir()
    signal_path = common_root / args.signal_file
    status_path = common_root / args.status_file
    metrics_path = common_root / args.metrics_file if args.metrics_file else None
    url = (
        args.base_url.rstrip("/")
        + "/v1/mobile/model-council/sessions/"
        + urllib.parse.quote(str(args.session_id), safe="")
        + "/execution/latest"
    )
    live_url = (
        args.base_url.rstrip("/")
        + "/v1/mobile/live/state/v3/"
        + urllib.parse.quote(str(args.session_id), safe="")
        + "?compact=1&monitor=1"
    )
    performance_url = (
        args.base_url.rstrip("/")
        + "/v1/mobile/performance/trace/v3/"
        + urllib.parse.quote(str(args.session_id), safe="")
    )

    print(f"PhoenixGuard MT4 bridge polling: {url}", flush=True)
    print(f"PhoenixGuard MT4 bridge freshness monitor: {live_url}", flush=True)
    print(f"PhoenixGuard MT4 bridge performance witness: {performance_url}", flush=True)
    print(f"MT4 signal file: {signal_path}", flush=True)
    print(f"MT4 status file: {status_path}", flush=True)
    if metrics_path:
        print(f"MT4 metrics file: {metrics_path}", flush=True)

    last_print = 0.0
    last_metrics = 0.0
    last_status = ""
    last_packet_id = ""
    bridge_sequence = 0
    while True:
        now = time.time()
        bridge_sequence += 1
        http_ms = 0.0
        write_ms = 0.0
        metric_status = "UNKNOWN"
        metric_error = ""
        metric_http_status = 0
        try:
            http_started = time.perf_counter()
            status, payload = _get_json(url, args.timeout_sec)
            http_ms = (time.perf_counter() - http_started) * 1000.0
            metric_http_status = status
            if status == 200 and payload.get("schema_version") == "PG_EXECUTION_PACKET_V3":
                packet_id = str(payload.get("packet_id") or "")
                live_status, live_payload = _get_json(live_url, args.timeout_sec)
                performance_status, performance_payload = _get_json(performance_url, args.timeout_sec)
                performance_for_freshness: Mapping[str, object] | None = (
                    performance_payload if performance_status == 200 else None
                )
                live_ok, live_reason = (
                    _packet_current_in_live_monitor(
                        payload,
                        live_payload,
                        performance=performance_for_freshness,
                        now_epoch=time.time(),
                        max_live_age_sec=max(1.0, float(args.max_live_age_sec)),
                        max_packet_frame_lag=max(0, int(args.max_packet_frame_lag)),
                    )
                    if live_status == 200
                    else (False, f"live monitor unavailable HTTP {live_status}")
                )
                if not live_ok:
                    detail = f"freshness rejected packet {packet_id}: {live_reason}"
                    status_body = _json_dumps(
                        _status("NO_EXECUTION_PACKET", detail=detail, http_status=status, bridge_sequence=bridge_sequence),
                        sort_keys=True,
                    )
                    write_ms += _write_text_shared(signal_path, status_body)
                    write_ms += _write_text_shared(status_path, status_body)
                    last_status = "NO_EXECUTION_PACKET"
                    metric_status = "NO_EXECUTION_PACKET"
                    metric_error = detail
                else:
                    command = _compact_command(
                        payload,
                        bridge_sequence=bridge_sequence,
                        symbol_override=args.symbol_override,
                        timeframe_override=args.timeframe_override,
                        validation_now_epoch=time.time(),
                    )
                    command["bridge_live_freshness"] = {
                        "status": "PASS",
                        "detail": live_reason,
                        "monitor_frame_id": _monitor_frame_id(live_payload, performance_for_freshness),
                        "monitor_capture_count": _monitor_capture_count(
                            live_payload,
                            performance_for_freshness,
                            _monitor_frame_id(live_payload, performance_for_freshness),
                        ),
                        "performance_http_status": performance_status,
                        "performance_frame_id": (
                            _monitor_frame_id({}, performance_for_freshness) if performance_for_freshness else 0
                        ),
                        "max_live_age_sec": max(1.0, float(args.max_live_age_sec)),
                        "max_packet_frame_lag": max(0, int(args.max_packet_frame_lag)),
                    }
                    _validate_command(command)
                    body = _json_dumps(command, sort_keys=False)
                    write_ms += _write_text_shared(signal_path, body)
                    write_ms += _write_text_shared(
                        status_path,
                        _json_dumps(
                            _status(
                                "EXECUTION_PACKET",
                                detail=packet_id,
                                http_status=status,
                                bridge_sequence=bridge_sequence,
                            ),
                            sort_keys=True,
                        ),
                    )
                    last_status = f"EXECUTION_PACKET {packet_id}"
                    last_packet_id = packet_id
                    metric_status = "EXECUTION_PACKET"
            elif status == 404:
                detail = str(payload.get("detail") or "Model Council executable packet not found.")
                status_body = _json_dumps(
                    _status("NO_EXECUTION_PACKET", detail=detail, http_status=status, bridge_sequence=bridge_sequence),
                    sort_keys=True,
                )
                write_ms += _write_text_shared(signal_path, status_body)
                write_ms += _write_text_shared(status_path, status_body)
                last_status = "NO_EXECUTION_PACKET"
                metric_status = "NO_EXECUTION_PACKET"
            else:
                detail = str(payload.get("detail") or payload)[:500]
                status_body = _json_dumps(
                    _status(
                        "BRIDGE_ERROR",
                        detail=detail,
                        http_status=status,
                        bridge_sequence=bridge_sequence,
                        error=detail,
                    ),
                    sort_keys=True,
                )
                write_ms += _write_text_shared(signal_path, status_body)
                write_ms += _write_text_shared(status_path, status_body)
                last_status = f"BRIDGE_ERROR HTTP {status}"
                metric_status = "BRIDGE_ERROR"
                metric_error = detail
        except Exception as exc:
            detail = str(exc)[:500]
            metric_error = detail
            metric_status = "BRIDGE_ERROR"
            try:
                status_body = _json_dumps(
                    _status("BRIDGE_ERROR", detail=detail, bridge_sequence=bridge_sequence, error=detail),
                    sort_keys=True,
                )
                write_ms += _write_text_shared(signal_path, status_body)
                write_ms += _write_text_shared(status_path, status_body)
            except Exception as write_exc:
                metric_error = f"{detail}; status_write_failed={write_exc}"
            last_status = f"BRIDGE_ERROR {detail}"

        if metrics_path and (time.time() - last_metrics >= max(0.1, args.metrics_every) or metric_status == "EXECUTION_PACKET"):
            try:
                _append_jsonl(
                    metrics_path,
                    {
                        "at_epoch": time.time(),
                        "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "bridge_sequence": bridge_sequence,
                        "status": metric_status,
                        "http_status": metric_http_status,
                        "http_ms": round(http_ms, 3),
                        "write_ms": round(write_ms, 3),
                        "last_packet_id": last_packet_id,
                        "error": metric_error,
                    },
                )
            except Exception as metrics_exc:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} METRICS_WRITE_ERROR {metrics_exc}", flush=True)
            last_metrics = time.time()

        if now - last_print >= args.print_every:
            suffix = f" last_packet={last_packet_id}" if last_packet_id else ""
            print(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} {last_status}{suffix} "
                f"seq={bridge_sequence} http_ms={http_ms:.1f} write_ms={write_ms:.1f}",
                flush=True,
            )
            last_print = now
        time.sleep(max(0.05, args.poll_sec))


if __name__ == "__main__":
    raise SystemExit(main())
