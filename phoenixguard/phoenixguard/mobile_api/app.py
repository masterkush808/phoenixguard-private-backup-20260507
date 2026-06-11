# pyright: reportUnusedFunction=none
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
import time
from typing import Annotated, Any, Mapping, cast
import urllib.error
import urllib.request

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from phoenixguard.core.config import RUNTIME, VOICE, VoiceConfig
from phoenixguard.execution.floating_state_reducer import build_floating_state
from phoenixguard.execution.sequence_context import sequence_context_readiness_report
from phoenixguard.execution.v3_language import public_language_scorecard
from phoenixguard.runtime.observability_v3 import (
    build_intelligence_health,
    build_model_council_health_from_session,
)
from phoenixguard.runtime.tracker_bootstrap import tracker_session_runtime_state
from phoenixguard.tracing import configure_tracing, instrument_fastapi_app
from phoenixguard.voice.control import (
    apply_voice_preferences,
    execute_voice_command,
    get_voice_runtime_snapshot,
    update_voice_state,
)
from phoenixguard.voice.intents import public_voice_command_catalog
from phoenixguard.voice.live import (
    LocalWindowTrackerVoiceController,
    build_market_context_from_tracker_session,
)

from phoenixguard.vision.market_registry import (
    load_recent_market_objects,
    promote_lifecycle,
    query_recent_active_objects,
)
from phoenixguard.vision.renderer import render_overlays_on_chart
from phoenixguard.vision.v3_overlay_contract import normalize_view_mode

from .live_state_v3 import _compact_session_payload, build_live_state_v3, build_live_state_v3_from_tracker_service
from .observer import SignalObserverService
from .realtime_sync_v3 import (
    build_visual_realtime_health,
    latest_frontend_heartbeat,
    record_frontend_heartbeat,
)
from .service import MobileApiService
from .window_tracker import ContinuousWindowTrackerService


_default_service: MobileApiService | None = None
_default_observer_service: SignalObserverService | None = None
_default_window_tracker_service: ContinuousWindowTrackerService | None = None
_WINDOW_TRACKER_DASHBOARD_TEMPLATE = (
    Path(__file__).resolve().parent / "static" / "window_tracker_dashboard.html"
)
_WINDOW_TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC = 1.0
_WINDOW_TRACKER_MIN_CAPTURE_INTERVAL_SEC = 0.5
_WINDOW_TRACKER_MAX_CAPTURE_INTERVAL_SEC = 10.0
_WINDOW_TRACKER_BRAND_ASSET_DIR = (
    Path(__file__).resolve().parents[2] / "assets" / "share" / "css-control"
)
_WINDOW_TRACKER_JS_ASSET_DIR = Path(__file__).resolve().parents[2] / "assets" / "js"
_WINDOW_TRACKER_BRAND_ASSETS = frozenset(
    {
        "landing-transition-lifestyle-suite.png",
        "landing-transition-lifestyle-travel.png",
        "landing-transition-market-vision-alt.png",
        "landing-transition-market-vision.png",
    }
)
_DEFAULT_WINDOW_TRACKER_DASHBOARD_SESSION_ID = "pocket-live-8788"
_SHOOTER_HANDSHAKE_PATH = Path(__file__).resolve().parents[2] / ".codex_runtime" / "shooter_handshake.json"
_PUBLISHED_PACKET_FALLBACK_TTL_SEC = 8.0
try:
    _LIVE_STATE_V3_CACHE_TTL_SEC = max(
        0.0,
        float(os.getenv("PHOENIXGUARD_LIVE_STATE_CACHE_TTL_SEC", "0.25") or "0.25"),
    )
except ValueError:
    _LIVE_STATE_V3_CACHE_TTL_SEC = 0.25
_LIVE_STATE_REGISTRY_CACHE_TTL_SEC = 30.0
_LIVE_STATE_V3_CACHE_LOCK = threading.Lock()
_LIVE_STATE_V3_CACHE: dict[tuple[str, str, str], tuple[float, dict[str, object]]] = {}
_LIVE_STATE_REGISTRY_CACHE: dict[str, tuple[float, list[Mapping[str, Any]], list[Mapping[str, Any]]]] = {}
_NO_STORE_ARTIFACT_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
_DIRECT_DISPLAY_STATE_KEYS = frozenset(
    {
        "session_id",
        "capture_count",
        "display_frame_id",
        "display_capture_epoch",
        "display_published_epoch",
        "last_display_capture_epoch",
        "last_display_published_epoch",
        "last_display_window_path",
        "last_window_path",
        "last_frame_path",
        "last_capture_started_at",
        "last_capture_started_epoch",
        "display_snapshot_only_v3",
        "display_fast_path_v3",
        "status",
        "updated_at",
        "locked_title",
        "locked_window",
    }
)


def _slugify_session_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("._").lower() or "session"


def _direct_live_state_session_path(session_id: str) -> Path:
    return Path(RUNTIME.data_dir) / "mobile_api" / "window_tracker" / "sessions" / _slugify_session_id(session_id) / "session.json"


def _direct_window_tracker_display_state_path(session_id: str) -> Path:
    return _direct_live_state_session_path(session_id).with_name("display_state.json")


def _path_cache_signature(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _live_state_cache_signature(session_id: str) -> str:
    session_path = _direct_live_state_session_path(session_id)
    display_path = _direct_window_tracker_display_state_path(session_id)
    return f"session={_path_cache_signature(session_path)}|display={_path_cache_signature(display_path)}"


def _mapping_to_plain_dict(value: Any) -> dict[str, object]:
    return dict(cast(Mapping[str, object], value)) if isinstance(value, Mapping) else {}


def _merge_direct_window_tracker_display_state(
    requested_session_id: str,
    payload: dict[str, object],
) -> dict[str, object]:
    try:
        display_raw = json.loads(_direct_window_tracker_display_state_path(requested_session_id).read_text(encoding="utf-8"))
    except Exception:
        return payload
    if not isinstance(display_raw, Mapping):
        return payload
    display_state = dict(cast(Mapping[str, object], display_raw))
    display_frame = int(_epoch_float(display_state.get("display_frame_id"), 0.0))
    payload_frame = int(_epoch_float(payload.get("display_frame_id") or payload.get("frame_index"), 0.0))
    display_epoch = _epoch_float(display_state.get("display_published_epoch"), 0.0)
    payload_epoch = _epoch_float(payload.get("display_published_epoch") or payload.get("last_capture_epoch"), 0.0)
    if display_frame <= 0:
        return payload
    if display_frame < payload_frame:
        return payload
    if display_frame == payload_frame and display_epoch + 0.001 < payload_epoch:
        return payload
    for key, value in display_state.items():
        if key in _DIRECT_DISPLAY_STATE_KEYS:
            payload[str(key)] = value
    return payload


def _direct_window_tracker_session_snapshot(session_id: str) -> dict[str, object] | None:
    if str(os.getenv("PHOENIXGUARD_WINDOW_TRACKER_DIRECT_READ", "1") or "1").strip().lower() in {
        "0",
        "false",
        "off",
        "no",
    }:
        return None
    requested_session_id = str(session_id or "").strip()
    if not requested_session_id:
        return None
    path = _direct_live_state_session_path(requested_session_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, Mapping):
        return None
    raw_payload = dict(cast(Mapping[str, Any], raw))
    if str(raw_payload.get("session_id", requested_session_id) or requested_session_id) != requested_session_id:
        return None
    if not bool(raw_payload.get("tracking_enabled", False)):
        return None
    payload = cast(dict[str, object], _compact_session_payload(raw_payload))
    payload = _merge_direct_window_tracker_display_state(requested_session_id, payload)
    now_epoch = time.time()
    latest_signal = _mapping_to_plain_dict(payload.get("latest_signal"))
    published_epoch = _epoch_float(latest_signal.get("published_epoch") or payload.get("last_capture_epoch"), 0.0)
    if published_epoch > 0.0:
        signal_age_sec = round(max(0.0, now_epoch - published_epoch), 3)
        latest_signal["signal_age_sec"] = signal_age_sec
        latest_signal.setdefault("published_epoch", published_epoch)
        payload["signal_age_sec"] = signal_age_sec
    if latest_signal:
        latest_signal["session_id"] = requested_session_id
        payload["latest_signal"] = latest_signal
    current_status = str(payload.get("status", "") or "").strip().lower()
    payload["status"] = current_status if current_status in {"running", "tracking"} else "running"
    payload["event_log_path"] = str(path.with_name("events.jsonl"))
    payload.setdefault("next_capture_in_sec", 0.0)
    payload.setdefault("effective_capture_interval_sec", payload.get("capture_interval_sec", _WINDOW_TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC))
    return payload


def _packet_id_from_endpoint(endpoint_result: Mapping[str, object]) -> str:
    payload = endpoint_result.get("payload")
    if not isinstance(payload, Mapping):
        return ""
    packet_id = payload.get("packet_id")
    if packet_id:
        return str(packet_id)
    packet = payload.get("packet")
    if isinstance(packet, Mapping):
        return str(packet.get("id_short") or packet.get("packet_id") or "")
    return ""


def _epoch_float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


def _payload_created_epoch(payload: Mapping[str, object]) -> float:
    for key in ("created_epoch_sec", "created_epoch", "published_epoch", "signal_created_epoch", "capture_started_epoch"):
        created = _epoch_float(payload.get(key), 0.0)
        if created > 0.0:
            return created
    for key in ("model_council_result", "model_council_study_packet", "study_packet", "execution_packet", "packet"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            created = _payload_created_epoch(cast(Mapping[str, object], nested))
            if created > 0.0:
                return created
    return 0.0


def _payload_ttl_sec(payload: Mapping[str, object]) -> float:
    for key in ("ttl_sec", "time_to_live_sec", "valid_for_seconds", "freshness_window_sec", "max_signal_age_sec"):
        ttl = _epoch_float(payload.get(key), 0.0)
        if ttl > 0.0:
            return ttl
    for key in ("model_council_result", "model_council_study_packet", "study_packet", "execution_packet", "packet"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            ttl = _payload_ttl_sec(cast(Mapping[str, object], nested))
            if ttl > 0.0:
                return ttl
    return 0.0


def _payload_looks_like_published_packet(payload: Mapping[str, object]) -> bool:
    packet_type = str(payload.get("packet_type") or "").strip().upper()
    schema_version = str(payload.get("schema_version") or "").strip().upper()
    if packet_type in {"STUDY_PACKET", "PG_EXECUTION_PACKET_V3"}:
        return True
    if schema_version in {"PG_MODEL_COUNCIL_STUDY_V3", "PG_EXECUTION_PACKET_V3"}:
        return True
    if payload.get("packet_id") and isinstance(payload.get("execution"), Mapping) and isinstance(payload.get("model_council"), Mapping):
        return True
    return any(isinstance(payload.get(key), Mapping) for key in ("model_council_study_packet", "study_packet", "execution_packet"))


def _payload_valid_until_epoch(payload: Mapping[str, object]) -> float:
    direct = _epoch_float(payload.get("valid_until_epoch_sec") or payload.get("valid_until_epoch"), 0.0)
    if direct > 0.0:
        return direct
    nested_valid_until = 0.0
    for key in ("model_council_result", "model_council_study_packet", "study_packet", "execution_packet"):
        nested = payload.get(key)
        if isinstance(nested, Mapping):
            nested_valid_until = max(
                nested_valid_until,
                _payload_valid_until_epoch(cast(Mapping[str, object], nested)),
            )
    if nested_valid_until > 0.0:
        return nested_valid_until
    if _payload_looks_like_published_packet(payload):
        created = _payload_created_epoch(payload)
        if created > 0.0:
            ttl = _payload_ttl_sec(payload) or _PUBLISHED_PACKET_FALLBACK_TTL_SEC
            return created + max(0.1, ttl)
    return 0.0


def _payload_is_stale(payload: Mapping[str, object], *, now_epoch: float | None = None) -> bool:
    valid_until = _payload_valid_until_epoch(payload)
    if valid_until <= 0.0:
        return False
    return valid_until <= (time.time() if now_epoch is None else float(now_epoch))


def _raise_if_stale_payload(payload: Mapping[str, object], *, detail: str) -> None:
    if _payload_is_stale(payload):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _live_model_health_summary(payload: Mapping[str, object]) -> dict[str, object]:
    latest_signal = _mapping_to_plain_dict(payload.get("latest_signal"))
    result = _mapping_to_plain_dict(payload.get("model_council_result"))
    packet = _mapping_to_plain_dict(payload.get("model_council_packet") or payload.get("execution_packet"))
    tracking = _mapping_to_plain_dict(payload.get("tracking_summary"))
    pipeline = _mapping_to_plain_dict(tracking.get("pipeline_timing") or latest_signal.get("pipeline_timing"))
    published_epoch = (
        _payload_created_epoch(latest_signal)
        or _payload_created_epoch(result)
        or _payload_created_epoch(packet)
        or _epoch_float(payload.get("last_capture_epoch"), 0.0)
    )
    max_latency = 0.0
    for value in pipeline.values():
        if isinstance(value, Mapping):
            max_latency = max(max_latency, _epoch_float(value.get("duration_ms"), 0.0))
    roles = [
        "global_structure",
        "local_micro_structure",
        "zone_liquidity",
        "angle_dynamics",
        "historical_pattern",
        "risk_opposing_force",
        "arbitration_synthesis",
    ]
    has_model_state = bool(result or packet or latest_signal)
    return {
        "schema_version": "PG_MODEL_HEALTH_LIGHT_V3",
        "session_id": str(payload.get("session_id", "") or ""),
        "all_required_models_awake": has_model_state,
        "council_status": "AWAKE" if has_model_state else "WARMING",
        "required_roles": roles,
        "queue_depth": int(_epoch_float(payload.get("queue_depth"), 0.0)),
        "max_model_latency_ms": round(float(max_latency), 3),
        "models": [
            {
                "name": role,
                "role": role,
                "status": "AWAKE" if has_model_state else "WARMING",
                "latency_ms": round(float(max_latency), 3),
                "queue_depth": int(_epoch_float(payload.get("queue_depth"), 0.0)),
                "last_inference_epoch": published_epoch,
                "device": "local",
            }
            for role in roles
        ],
        "runtime_telemetry": {},
    }


def _latest_shooter_handshake(session_id: str | None = None) -> dict[str, object]:
    requested_session_id = str(session_id or "").strip()
    if not _SHOOTER_HANDSHAKE_PATH.exists():
        raise KeyError("Shooter handshake not found.")
    try:
        payload = json.loads(_SHOOTER_HANDSHAKE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise KeyError("Shooter handshake not readable.") from exc
    if not isinstance(payload, Mapping):
        raise KeyError("Shooter handshake is not an object.")
    normalized = dict(cast(Mapping[str, object], payload))
    payload_session_id = str(normalized.get("session_id", "") or "").strip()
    if requested_session_id and payload_session_id and payload_session_id != requested_session_id:
        raise KeyError("Shooter handshake session mismatch.")
    if requested_session_id and not payload_session_id:
        normalized["session_id"] = requested_session_id
    return normalized


def _missing_shooter_handshake_state(
    session_id: str | None = None,
    *,
    detail: str = "Shooter handshake not found.",
) -> dict[str, object]:
    return {
        "session_id": str(session_id or "").strip(),
        "state": "WAITING",
        "mode": "LIVE_READY",
        "available": False,
        "reason": detail,
        "next_required": "shooter handshake publish",
    }


def _latest_shooter_handshake_or_waiting(session_id: str | None = None) -> dict[str, object]:
    try:
        return _latest_shooter_handshake(session_id)
    except KeyError as exc:
        detail = str(exc).strip("'") or "Shooter handshake not found."
        return _missing_shooter_handshake_state(session_id, detail=detail)


def _safe_file_bytes_response(path: Path, *, media_type: str | None = None) -> Response:
    try:
        content = path.read_bytes()
    except FileNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact expired before it could be served.") from exc
    except OSError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact is not readable.") from exc
    return Response(content=content, media_type=media_type, headers=dict(_NO_STORE_ARTIFACT_HEADERS))


def _compact_capture_once_response(payload: Mapping[str, Any]) -> dict[str, object]:
    keep_keys = (
        "schema_version",
        "session_id",
        "status",
        "tracking_enabled",
        "capture_count",
        "frame_index",
        "display_frame_id",
        "display_capture_epoch",
        "display_published_epoch",
        "chart_frame_id",
        "overlay_frame_id",
        "full_overlay_frame_id",
        "model_vote_frame_id",
        "model_capture_epoch",
        "source_capture_id",
        "last_capture_at",
        "last_capture_epoch",
        "last_window_path",
        "last_chart_path",
        "last_overlay_path",
        "last_full_overlay_path",
        "capture_once_result",
    )
    compact: dict[str, object] = {key: payload.get(key) for key in keep_keys if key in payload}
    latest_signal = payload.get("latest_signal")
    if isinstance(latest_signal, Mapping):
        compact["latest_signal"] = {
            key: latest_signal.get(key)
            for key in (
                "action",
                "execution_action",
                "candidate_action",
                "side",
                "status",
                "summary",
                "confidence",
                "effective_confidence",
                "market",
                "focus_timeframe",
                "timestamp",
                "published_at",
                "published_epoch",
                "capture_started_epoch",
                "pipeline_latency_sec",
                "signal_id",
            )
            if key in latest_signal
        }
    tracking_summary = payload.get("tracking_summary")
    if isinstance(tracking_summary, Mapping):
        compact["tracking_summary"] = {
            key: tracking_summary.get(key)
            for key in (
                "global_direction",
                "local_direction",
                "impulse_direction",
                "detected_market",
                "detected_timeframe",
                "entry_label",
                "entry_quality",
                "chart_region",
                "display_region",
                "pipeline_timing",
            )
            if key in tracking_summary
        }
    broker_execution_state = payload.get("broker_execution_state")
    if isinstance(broker_execution_state, Mapping):
        compact["broker_execution_state"] = {
            key: broker_execution_state.get(key)
            for key in ("status", "side", "lane", "message", "actionable", "enabled", "mode", "expiry_seconds")
            if key in broker_execution_state
        }
    return compact


def _service() -> MobileApiService:
    global _default_service
    if _default_service is None:
        _default_service = MobileApiService()
    return _default_service


def _observer_service(mobile_service: MobileApiService | None = None) -> SignalObserverService:
    global _default_observer_service
    if _default_observer_service is None:
        service = mobile_service or _service()
        root_dir = Path(getattr(service, "root_dir", RUNTIME.data_dir / "mobile_api")) / "observer"
        _default_observer_service = SignalObserverService(
            root_dir=root_dir,
            pipeline_adapter=service.pipeline_adapter,
        )
    return _default_observer_service


def _window_tracker_service(
    observer_service: SignalObserverService | None = None,
    mobile_service: MobileApiService | None = None,
) -> ContinuousWindowTrackerService:
    global _default_window_tracker_service
    if _default_window_tracker_service is None:
        service = mobile_service or _service()
        observer = observer_service or _observer_service(service)
        # Prefer the legacy sessions directory `data/window_tracker` only when
        # it contains real session payloads. Empty directories can be created by
        # probes and should not shadow the active mobile_api/window_tracker root.
        candidate_legacy = Path(RUNTIME.data_dir) / "window_tracker"
        candidate_default = Path(getattr(service, "root_dir", RUNTIME.data_dir / "mobile_api")) / "window_tracker"
        legacy_sessions = candidate_legacy / "sessions"
        if legacy_sessions.exists() and any(legacy_sessions.glob("*/session.json")):
            root_dir = candidate_legacy
        else:
            root_dir = candidate_default
        _default_window_tracker_service = ContinuousWindowTrackerService(
            observer_service=observer,
            root_dir=root_dir,
        )
    return _default_window_tracker_service


class ObserverSessionCreateRequest(BaseModel):
    session_id: str | None = None
    name: str = ""
    market: str = ""
    settings: dict[str, object] = Field(default_factory=dict)
    policy: dict[str, object] = Field(default_factory=dict)


class WindowTrackerSessionCreateRequest(BaseModel):
    session_id: str | None = None
    name: str = ""
    market: str = ""
    window_query: str = "Pocket Option"
    layout_profile: str = "auto"
    capture_interval_sec: float = Field(
        default=_WINDOW_TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC,
        ge=_WINDOW_TRACKER_MIN_CAPTURE_INTERVAL_SEC,
        le=_WINDOW_TRACKER_MAX_CAPTURE_INTERVAL_SEC,
    )
    rl_track_interval_sec: float = 30.0
    auto_start: bool = False
    observer_settings: dict[str, object] = Field(default_factory=dict)
    observer_policy: dict[str, object] = Field(default_factory=dict)


class WindowTrackerFocusRegionRequest(BaseModel):
    normalized_bbox: list[float] = Field(min_length=4, max_length=4)
    source: str = "dashboard_ctrl_v"


class WindowTrackerControlUpdateRequest(BaseModel):
    capture_interval_sec: float | None = Field(
        default=None,
        ge=_WINDOW_TRACKER_MIN_CAPTURE_INTERVAL_SEC,
        le=_WINDOW_TRACKER_MAX_CAPTURE_INTERVAL_SEC,
    )
    live_execution_enabled: bool | None = None
    execution_mode: str | None = None
    allow_countertrend_scalp: bool | None = None
    allow_location_sniper_entries: bool | None = None
    trade_profile: str | None = None
    high_frequency_enabled: bool | None = None
    swing_fallback_enabled: bool | None = None
    continuous_model_feed_enabled: bool | None = None
    high_frequency_min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    high_frequency_entry_grace_sec: float | None = Field(default=None, ge=0.0, le=180.0)
    high_frequency_expiry_seconds: int | None = Field(default=None, ge=600, le=600)
    scenario_generation_enabled: bool | None = None
    auto_memory_projection: bool | None = None
    require_memory_projection: bool | None = None
    require_market_identity: bool | None = None
    require_timeframe_identity: bool | None = None
    allow_locked_surface_identity_fallback: bool | None = None
    broker_surface_cache_sec: float | None = Field(default=None, ge=2.0, le=300.0)
    adaptive_timer_enabled: bool | None = None
    min_capture_interval_sec: float | None = Field(
        default=None,
        ge=_WINDOW_TRACKER_MIN_CAPTURE_INTERVAL_SEC,
        le=_WINDOW_TRACKER_MAX_CAPTURE_INTERVAL_SEC,
    )
    max_capture_interval_sec: float | None = Field(
        default=None,
        ge=_WINDOW_TRACKER_MIN_CAPTURE_INTERVAL_SEC,
        le=_WINDOW_TRACKER_MAX_CAPTURE_INTERVAL_SEC,
    )
    max_executions_per_window: int | None = Field(default=None, ge=1, le=20)
    execution_window_sec: float | None = Field(default=None, ge=60.0, le=3600.0)
    min_market_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    min_timeframe_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    cooldown_sec: float | None = Field(default=None, ge=5.0)
    loss_guard_enabled: bool | None = None
    loss_guard_max_consecutive_losses: int | None = Field(default=None, ge=1, le=10)
    loss_guard_window_sec: float | None = Field(default=None, ge=60.0, le=86400.0)
    loss_guard_pause_sec: float | None = Field(default=None, ge=60.0, le=86400.0)
    min_location_sniper_target_candles: int | None = Field(default=None, ge=1, le=36)
    phoenix_report_interval_sec: float | None = Field(default=None, ge=0.0, le=300.0)


class WindowTrackerDemoTradeRequest(BaseModel):
    side: str | None = None
    expiry_seconds: int = Field(default=180, ge=60, le=3600)
    force: bool = False


class VoicePreferenceUpdateRequest(BaseModel):
    voice_enabled: bool
    listening_enabled: bool
    automatic_timer_enabled: bool
    tracker_capture_interval_sec: float = Field(
        ge=_WINDOW_TRACKER_MIN_CAPTURE_INTERVAL_SEC,
        le=_WINDOW_TRACKER_MAX_CAPTURE_INTERVAL_SEC,
    )
    timezone_name: str = ""
    tracker_session_id: str | None = None


class VoiceCommandRequest(BaseModel):
    command: str = Field(min_length=1)
    tracker_session_id: str | None = None


def _render_window_tracker_dashboard(session_id: str) -> str:
    template = _WINDOW_TRACKER_DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    return (
        template.replace("__SESSION_ID_JSON__", json.dumps(str(session_id)))
        .replace("__SESSION_LABEL__", str(session_id))
    )


def create_app(
    service: MobileApiService | None = None,
    observer_service: SignalObserverService | None = None,
    window_tracker_service: ContinuousWindowTrackerService | None = None,
    voice_config: VoiceConfig | None = None,
) -> FastAPI:
    resolved_voice_config = voice_config or VOICE
    configure_tracing("phoenixguard-mobile-api", service_version="1.0.0")
    app = FastAPI(
        title="PhoenixGuard Mobile API",
        version="1.0.0",
        summary="Android-facing quartet analysis API and continuous observer surface for PhoenixGuard.",
    )
    app.state.mobile_service = service
    app.state.observer_service = observer_service
    app.state.window_tracker_service = window_tracker_service
    app.state.voice_config = resolved_voice_config

    def get_mobile_service() -> MobileApiService:
        mobile_service = getattr(app.state, "mobile_service", None)
        if mobile_service is None:
            mobile_service = _service()
            app.state.mobile_service = mobile_service
        return mobile_service

    def get_observer_service() -> SignalObserverService:
        market_observer = getattr(app.state, "observer_service", None)
        if market_observer is None:
            mobile_service = get_mobile_service()
            if service is not None:
                market_observer = SignalObserverService(
                    root_dir=Path(mobile_service.root_dir) / "observer",
                    pipeline_adapter=mobile_service.pipeline_adapter,
                )
            else:
                market_observer = _observer_service(mobile_service)
            app.state.observer_service = market_observer
        return market_observer

    def get_window_tracker_service() -> ContinuousWindowTrackerService:
        market_window_tracker = getattr(app.state, "window_tracker_service", None)
        if market_window_tracker is None:
            mobile_service = get_mobile_service()
            market_observer = get_observer_service()
            if service is not None or observer_service is not None:
                market_window_tracker = ContinuousWindowTrackerService(
                    observer_service=market_observer,
                    root_dir=Path(mobile_service.root_dir) / "window_tracker",
                )
            else:
                market_window_tracker = _window_tracker_service(market_observer, mobile_service)
            app.state.window_tracker_service = market_window_tracker
        return market_window_tracker

    def read_window_tracker_session(session_id: str) -> dict[str, object]:
        direct_snapshot = _direct_window_tracker_session_snapshot(session_id)
        if direct_snapshot is not None:
            return direct_snapshot
        tracker_service = get_window_tracker_service()
        snapshot_getter = getattr(tracker_service, "get_session_snapshot", None)
        if callable(snapshot_getter):
            return cast(dict[str, object], snapshot_getter(session_id))
        return cast(dict[str, object], tracker_service.get_session(session_id))

    def get_voice_config() -> VoiceConfig:
        return getattr(app.state, "voice_config", resolved_voice_config)

    def ensure_window_tracker_dashboard_session(session_id: str) -> dict[str, object]:
        tracker_service = get_window_tracker_service()
        normalized_session_id = str(session_id or "").strip() or _DEFAULT_WINDOW_TRACKER_DASHBOARD_SESSION_ID
        try:
            return read_window_tracker_session(normalized_session_id)
        except KeyError:
            return tracker_service.create_session(
                session_id=normalized_session_id,
                name=normalized_session_id,
                market="",
                window_query="Pocket Option",
                layout_profile="auto",
                capture_interval_sec=_WINDOW_TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC,
                rl_track_interval_sec=30.0,
                auto_start=False,
                observer_settings={},
                observer_policy={
                    "single_surface_mode": True,
                    "min_actionable_confidence": 0.58,
                    "min_thesis_confidence": 0.46,
                    "signal_cooldown_sec": 8.0,
                },
            )

    def resolve_window_tracker_dashboard_session_id(session_id: str | None = None) -> str:
        tracker_service = get_window_tracker_service()
        normalized_session_id = str(session_id or "").strip()
        if normalized_session_id:
            payload = ensure_window_tracker_dashboard_session(normalized_session_id)
            return str(payload.get("session_id", normalized_session_id) or normalized_session_id)
        sessions = tracker_service.list_sessions(limit=1)
        if sessions:
            latest_session_id = str(sessions[0].get("session_id", "") or "").strip()
            if latest_session_id:
                return latest_session_id
        payload = ensure_window_tracker_dashboard_session(_DEFAULT_WINDOW_TRACKER_DASHBOARD_SESSION_ID)
        return str(payload.get("session_id", _DEFAULT_WINDOW_TRACKER_DASHBOARD_SESSION_ID) or _DEFAULT_WINDOW_TRACKER_DASHBOARD_SESSION_ID)

    def resolve_voice_tracker_session_id(session_id: str | None = None) -> str:
        snapshot = get_voice_runtime_snapshot(config=get_voice_config())
        requested = str(session_id or snapshot.get("tracker_session_id", "") or "").strip()
        resolved = resolve_window_tracker_dashboard_session_id(
            requested or _DEFAULT_WINDOW_TRACKER_DASHBOARD_SESSION_ID
        )
        if str(snapshot.get("tracker_session_id", "") or "").strip() != resolved:
            update_voice_state(config=get_voice_config(), tracker_session_id=resolved)
        return resolved

    def get_voice_context_payload(tracker_session_id: str | None = None) -> tuple[dict[str, object], dict[str, str]]:
        resolved_session_id = resolve_voice_tracker_session_id(tracker_session_id)
        tracker_session = read_window_tracker_session(resolved_session_id)
        market_context = build_market_context_from_tracker_session(tracker_session)
        return tracker_session, market_context

    @app.get("/v1/mobile/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/mobile/chart/state/v3")
    def v3_chart_state(session_id: str | None = None) -> dict[str, object]:
        # Return frame state including cache-busted URL for latest frame image
        try:
            tracker = get_window_tracker_service()
            resolved = resolve_model_council_session_payload(session_id)
            sid = str(resolved.get("session_id") or session_id or "")
            if not sid:
                sid = resolve_window_tracker_dashboard_session_id(None)
            # latest artifact path
            try:
                path = tracker.latest_artifact_path(sid, "window")
                if not path.exists():
                    path = tracker.latest_artifact_path(sid, "chart")
                exists = path.exists()
                mtime = path.stat().st_mtime if exists else 0.0
                frame_id = int(mtime)
                url = f"/v1/mobile/frame/latest.png?session_id={sid}&t={int(mtime)}"
            except Exception:
                exists = False
                frame_id = 0
                url = ""
            return {
                "schema_version": "V3_CHART_STATE",
                "session_id": sid,
                "frame_id": frame_id,
                "frame_exists": exists,
                "frame_url": url,
                "frame_timestamp": float(mtime),
            }
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    @app.get("/v1/mobile/frame/latest.png")
    def v3_frame_latest_png(session_id: str) -> Response:
        try:
            tracker = get_window_tracker_service()
            path = tracker.latest_artifact_path(session_id, "window")
            if not path.exists():
                path = tracker.latest_artifact_path(session_id, "chart")
            return _safe_file_bytes_response(path, media_type="image/png")
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    def fetch_model_council_daemon_status(timeout_sec: float = 1.5) -> dict[str, object]:
        url = str(
            os.getenv("PHOENIXGUARD_MODEL_COUNCIL_DAEMON_STATUS_URL")
            or os.getenv("PHOENIXGUARD_MODEL_COUNCIL_DAEMON_URL")
            or "http://127.0.0.1:8767/status"
        ).strip()
        if not url:
            return {}
        if not url.endswith("/status"):
            url = url.rstrip("/") + "/status"
        try:
            req = urllib.request.Request(url=url, method="GET")
            with urllib.request.urlopen(req, timeout=float(timeout_sec)) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            return dict(payload) if isinstance(payload, Mapping) else {}
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return {}

    def resolve_model_council_session_payload(session_id: str | None = None) -> dict[str, object]:
        tracker_service = get_window_tracker_service()
        requested_session_id = str(session_id or "").strip()
        if requested_session_id:
            return read_window_tracker_session(requested_session_id)
        sessions = tracker_service.list_sessions(limit=1)
        if sessions:
            return dict(sessions[0])
        return {"session_id": ""}

    @app.get("/v1/mobile/model-council/health")
    def model_council_health(session_id: str | None = None) -> dict[str, object]:
        try:
            payload = resolve_model_council_session_payload(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        return build_model_council_health_from_session(
            cast(Mapping[str, Any], payload),
            daemon_status=fetch_model_council_daemon_status(),
        )

    @app.get("/v1/mobile/model-council/intelligence")
    def model_council_intelligence(session_id: str | None = None) -> dict[str, object]:
        try:
            payload = resolve_model_council_session_payload(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        return build_intelligence_health(cast(Mapping[str, Any], payload))

    def _direct_live_state_v3_for_session(requested_session_id: str, now_epoch: float, overlay_mode: str) -> dict[str, object] | None:
        if str(os.getenv("PHOENIXGUARD_LIVE_STATE_DIRECT_READ", "1") or "1").strip().lower() in {"0", "false", "off", "no"}:
            return None
        path = _direct_live_state_session_path(requested_session_id)
        try:
            raw_session = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(raw_session, Mapping):
            return None
        session_payload = _merge_direct_window_tracker_display_state(
            requested_session_id,
            dict(cast(Mapping[str, object], raw_session)),
        )
        artifacts: dict[str, str] = {}
        for kind, key in {
            "window": "last_window_path",
            "chart": "last_chart_path",
            "overlay": "last_overlay_path",
            "full-overlay": "last_full_overlay_path",
            "projection": "last_projection_path",
            "memory-reference": "last_memory_reference_path",
        }.items():
            value = str(session_payload.get(key, "") or "").strip()
            if value:
                artifacts[kind] = value
        with _LIVE_STATE_V3_CACHE_LOCK:
            cached_sources = _LIVE_STATE_REGISTRY_CACHE.get(requested_session_id)
            if cached_sources and now_epoch - cached_sources[0] <= _LIVE_STATE_REGISTRY_CACHE_TTL_SEC:
                active_objects = list(cached_sources[1])
                registry_entries = list(cached_sources[2])
            else:
                active_objects = []
                registry_entries = []
                try:
                    active_objects = [
                        cast(Mapping[str, Any], item)
                        for item in query_recent_active_objects(requested_session_id, min_truth_score=0.0)
                    ]
                except Exception:
                    active_objects = []
                try:
                    registry_entries = [
                        cast(Mapping[str, Any], item)
                        for item in load_recent_market_objects(requested_session_id)
                    ]
                except Exception:
                    registry_entries = []
                _LIVE_STATE_REGISTRY_CACHE[requested_session_id] = (now_epoch, active_objects, registry_entries)
        return cast(
            dict[str, object],
            build_live_state_v3(
                session_payload,
                artifacts=artifacts,
                model_health=_live_model_health_summary(cast(Mapping[str, object], session_payload)),
                shooter_state=_latest_shooter_handshake_or_waiting(requested_session_id),
                active_objects=active_objects,
                registry_entries=registry_entries,
                frontend_heartbeat=latest_frontend_heartbeat(requested_session_id),
                now_epoch=now_epoch,
                overlay_mode=overlay_mode,
            ),
        )

    def compact_live_state_response(live_state: Mapping[str, object]) -> dict[str, object]:
        compact = cast(dict[str, object], _compact_session_payload(live_state))
        live_visual_state = live_state.get("live_visual_state")
        if isinstance(live_visual_state, Mapping):
            compact.update(cast(Mapping[str, object], live_visual_state))
            compact["live_visual_state"] = dict(live_visual_state)
        overlays = compact.get("overlays")
        if isinstance(overlays, Mapping):
            objects = overlays.get("objects")
            if isinstance(objects, list):
                compact["overlay_objects"] = objects
        if "overlay_objects" not in compact and isinstance(live_state.get("overlay_objects"), list):
            compact["overlay_objects"] = cast(list[object], live_state["overlay_objects"])
        packets = compact.get("packets")
        if isinstance(packets, Mapping):
            study = packets.get("study")
            execution = packets.get("execution")
            if isinstance(study, Mapping):
                compact["study_packet_status"] = dict(study)
            if isinstance(execution, Mapping):
                compact["execution_packet_status"] = dict(execution)
        return compact

    def build_live_state_v3_for_session(session_id: str, overlay_mode: str = "CLEAN_LIVE") -> dict[str, object]:
        requested_session_id = str(session_id or "").strip()
        if not requested_session_id:
            requested_session_id = resolve_window_tracker_dashboard_session_id(None)
        active_overlay_mode = normalize_view_mode(overlay_mode)
        cache_signature = _live_state_cache_signature(requested_session_id)
        cache_enabled = _LIVE_STATE_V3_CACHE_TTL_SEC > 0.0 and not cache_signature.startswith("session=missing")
        cache_key = (requested_session_id, active_overlay_mode, cache_signature)
        now_epoch = time.time()
        if cache_enabled:
            with _LIVE_STATE_V3_CACHE_LOCK:
                cached = _LIVE_STATE_V3_CACHE.get(cache_key)
                if cached and now_epoch - cached[0] <= _LIVE_STATE_V3_CACHE_TTL_SEC:
                    return dict(cached[1])

        def store_live_state_cache(live_state: Mapping[str, object]) -> None:
            if not cache_enabled:
                return
            with _LIVE_STATE_V3_CACHE_LOCK:
                for stale_key in [
                    key
                    for key in _LIVE_STATE_V3_CACHE
                    if key[0] == requested_session_id and key[1] == active_overlay_mode and key != cache_key
                ]:
                    _LIVE_STATE_V3_CACHE.pop(stale_key, None)
                _LIVE_STATE_V3_CACHE[cache_key] = (time.time(), dict(live_state))

        direct_live_state = _direct_live_state_v3_for_session(requested_session_id, now_epoch, active_overlay_mode)
        if direct_live_state is not None:
            store_live_state_cache(direct_live_state)
            return direct_live_state
        tracker = get_window_tracker_service()

        def model_health_builder(payload: Mapping[str, Any]) -> Mapping[str, Any]:
            return _live_model_health_summary(cast(Mapping[str, object], payload))

        def shooter_loader(resolved_session_id: str) -> Mapping[str, Any]:
            return _latest_shooter_handshake_or_waiting(resolved_session_id)

        def active_object_loader(resolved_session_id: str) -> list[Mapping[str, Any]]:
            return [cast(Mapping[str, Any], item) for item in query_recent_active_objects(resolved_session_id, min_truth_score=0.0)]

        def registry_loader(resolved_session_id: str) -> list[Mapping[str, Any]]:
            return [cast(Mapping[str, Any], item) for item in load_recent_market_objects(resolved_session_id)]

        def heartbeat_loader(resolved_session_id: str) -> Mapping[str, Any] | None:
            return latest_frontend_heartbeat(resolved_session_id)

        try:
            live_state = cast(
                dict[str, object],
                build_live_state_v3_from_tracker_service(
                    tracker,
                    requested_session_id,
                    model_health_builder=model_health_builder,
                    shooter_state_loader=shooter_loader,
                    active_object_loader=active_object_loader,
                    registry_loader=registry_loader,
                    frontend_heartbeat_loader=heartbeat_loader,
                    now_epoch=now_epoch,
                    overlay_mode=active_overlay_mode,
                ),
            )
            store_live_state_cache(live_state)
            return live_state
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc

    @app.get("/v1/mobile/live/state/v3/{session_id}")
    def live_state_v3_for_session(session_id: str, mode: str = "CLEAN_LIVE", compact: bool = False) -> dict[str, object]:
        live_state = build_live_state_v3_for_session(session_id, overlay_mode=mode)
        return compact_live_state_response(live_state) if compact else live_state

    @app.get("/v1/mobile/live/state/v3")
    def live_state_v3(session_id: str | None = None, mode: str = "CLEAN_LIVE", compact: bool = False) -> dict[str, object]:
        live_state = build_live_state_v3_for_session(
            session_id or resolve_window_tracker_dashboard_session_id(None),
            overlay_mode=mode,
        )
        return compact_live_state_response(live_state) if compact else live_state

    @app.get("/v1/mobile/performance/trace/v3/{session_id}")
    def performance_trace_v3_for_session(session_id: str) -> dict[str, object]:
        live_state = build_live_state_v3_for_session(session_id)
        trace = live_state.get("performance_trace_v3")
        if isinstance(trace, Mapping):
            return cast(dict[str, object], trace)
        compact = live_state.get("live_visual_state")
        if isinstance(compact, Mapping) and isinstance(compact.get("performance_trace_v3"), Mapping):
            return cast(dict[str, object], compact["performance_trace_v3"])
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Performance trace not available.")

    @app.get("/v1/mobile/performance/trace/v3")
    def performance_trace_v3(session_id: str | None = None) -> dict[str, object]:
        return performance_trace_v3_for_session(session_id or resolve_window_tracker_dashboard_session_id(None))

    @app.post("/v1/mobile/frontend/heartbeat/v3")
    def frontend_heartbeat_v3(payload: dict[str, object] = Body(...)) -> dict[str, object]:
        try:
            return cast(dict[str, object], record_frontend_heartbeat(payload))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/v1/mobile/frontend/heartbeat/v3")
    def latest_frontend_heartbeat_v3(session_id: str | None = None) -> dict[str, object]:
        resolved_session_id = str(session_id or "").strip() or resolve_window_tracker_dashboard_session_id(None)
        heartbeat = latest_frontend_heartbeat(resolved_session_id)
        if heartbeat is None:
            return {
                "schema_version": "PG_FRONTEND_HEARTBEAT_V3",
                "session_id": resolved_session_id,
                "status": "missing",
            }
        return cast(dict[str, object], heartbeat)

    @app.get("/v1/mobile/model-council/sessions/{session_id}/latest")
    def latest_model_council_state_for_session(session_id: str) -> dict[str, object]:
        try:
            payload = get_window_tracker_service().latest_model_council_state(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model Council state not found.") from exc
        return cast(dict[str, object], payload)

    @app.get("/v1/mobile/model-council/latest")
    def latest_model_council_state(session_id: str | None = None) -> dict[str, object]:
        requested_session_id = str(session_id or "").strip()
        if not requested_session_id:
            payload = resolve_model_council_session_payload(None)
            requested_session_id = str(payload.get("session_id", "") or "").strip()
        if not requested_session_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.")
        try:
            payload = get_window_tracker_service().latest_model_council_state(requested_session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model Council state not found.") from exc
        return cast(dict[str, object], payload)

    @app.get("/v1/mobile/model-council/sessions/{session_id}/study/latest")
    def latest_model_council_study_packet_for_session(session_id: str) -> dict[str, object]:
        try:
            packet = get_window_tracker_service().latest_model_council_study_packet(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model Council study packet not found.") from exc
        _raise_if_stale_payload(cast(Mapping[str, object], packet), detail="Model Council study packet is stale.")
        return cast(dict[str, object], packet)

    @app.get("/v1/mobile/model-council/study/latest")
    def latest_model_council_study_packet(session_id: str | None = None) -> dict[str, object]:
        requested_session_id = str(session_id or "").strip()
        if not requested_session_id:
            payload = resolve_model_council_session_payload(None)
            requested_session_id = str(payload.get("session_id", "") or "").strip()
        if not requested_session_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.")
        try:
            packet = get_window_tracker_service().latest_model_council_study_packet(requested_session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model Council study packet not found.") from exc
        _raise_if_stale_payload(cast(Mapping[str, object], packet), detail="Model Council study packet is stale.")
        return cast(dict[str, object], packet)

    def build_floating_state_for_session(session_id: str | None = None, *, include_inspector: bool = False) -> dict[str, object]:
        requested_session_id = str(session_id or "").strip()
        try:
            tracker_payload = resolve_model_council_session_payload(requested_session_id or None)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        tracker_payload = dict(tracker_payload)
        resolved_session_id = str(tracker_payload.get("session_id", requested_session_id) or requested_session_id).strip()
        try:
            council_health = build_model_council_health_from_session(
                cast(Mapping[str, Any], tracker_payload),
                daemon_status=fetch_model_council_daemon_status(timeout_sec=0.1),
            )
            models = council_health.get("models")
            if isinstance(models, list) and models:
                total = len(models)
                awake = sum(
                    1
                    for model in models
                    if str(_mapping_to_plain_dict(model).get("status", "") or "").strip().upper()
                    in {"AWAKE", "RUNNING", "READY"}
                )
            elif council_health.get("all_required_models_awake") is True:
                required_roles = council_health.get("required_roles")
                total = len(required_roles) if isinstance(required_roles, list) and required_roles else 7
                awake = total
            else:
                awake = 0
                total = 0
            tracker_payload["model_health"] = {
                "models_awake": awake,
                "models_total": total,
                "all_required_models_awake": bool(council_health.get("all_required_models_awake")),
                "council_status": council_health.get("council_status", ""),
            }
        except Exception:
            pass
        signal_payload: dict[str, object] | None = None
        try:
            signal_payload = cast(dict[str, object], get_window_tracker_service().latest_model_council_packet(resolved_session_id))
        except KeyError:
            try:
                signal_payload = cast(dict[str, object], get_window_tracker_service().latest_model_council_study_packet(resolved_session_id))
            except KeyError:
                signal_payload = None
        if isinstance(signal_payload, Mapping) and _payload_is_stale(cast(Mapping[str, object], signal_payload)):
            signal_payload = None
        try:
            shooter_payload = _latest_shooter_handshake(resolved_session_id)
        except KeyError:
            shooter_payload = None
        cooldown_remaining = 0
        if isinstance(shooter_payload, Mapping):
            raw_cooldown = shooter_payload.get("cooldown_remaining_sec") or shooter_payload.get("cooldown_remaining_seconds")
            try:
                cooldown_remaining = max(0, int(float(str(raw_cooldown)))) if raw_cooldown is not None else 0
            except (TypeError, ValueError):
                cooldown_remaining = 0
        state_payload = cast(
            dict[str, object],
            build_floating_state(
                session_id=resolved_session_id or _DEFAULT_WINDOW_TRACKER_DASHBOARD_SESSION_ID,
                mode="LIVE",
                signal_payload=signal_payload,
                tracker_payload=cast(Mapping[str, object], tracker_payload),
                action_payload=shooter_payload,
                cooldown_remaining_seconds=cooldown_remaining,
            ),
        )
        if not include_inspector:
            state_payload.pop("inspector", None)
        return state_payload

    @app.get("/v1/mobile/floating/state")
    def latest_floating_state(session_id: str | None = None, inspector: bool = False) -> dict[str, object]:
        return build_floating_state_for_session(session_id, include_inspector=inspector)

    @app.get("/v1/mobile/floating/sessions/{session_id}/state")
    def latest_floating_state_for_session(session_id: str, inspector: bool = False) -> dict[str, object]:
        return build_floating_state_for_session(session_id, include_inspector=inspector)

    @app.get("/v1/mobile/shooter/sessions/{session_id}/handshake")
    def latest_shooter_handshake_for_session(session_id: str) -> dict[str, object]:
        try:
            return _latest_shooter_handshake(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shooter handshake not found.") from exc

    @app.get("/v1/mobile/shooter/handshake")
    def latest_shooter_handshake(session_id: str | None = None) -> dict[str, object]:
        requested_session_id = str(session_id or "").strip() or None
        try:
            return _latest_shooter_handshake(requested_session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Shooter handshake not found.") from exc

    @app.get("/v1/mobile/model-council/sessions/{session_id}/execution/latest")
    def latest_model_council_execution_packet_for_session(session_id: str) -> dict[str, object]:
        try:
            packet = get_window_tracker_service().latest_model_council_packet(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model Council executable packet not found.") from exc
        _raise_if_stale_payload(cast(Mapping[str, object], packet), detail="Model Council executable packet is stale.")
        return cast(dict[str, object], packet)

    @app.get("/v1/mobile/model-council/execution/latest")
    def latest_model_council_execution_packet(session_id: str | None = None) -> dict[str, object]:
        requested_session_id = str(session_id or "").strip()
        if not requested_session_id:
            payload = resolve_model_council_session_payload(None)
            requested_session_id = str(payload.get("session_id", "") or "").strip()
        if not requested_session_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.")
        try:
            packet = get_window_tracker_service().latest_model_council_packet(requested_session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model Council executable packet not found.") from exc
        _raise_if_stale_payload(cast(Mapping[str, object], packet), detail="Model Council executable packet is stale.")
        return cast(dict[str, object], packet)

    def build_runtime_trace_v3(session_id: str | None = None) -> dict[str, object]:
        requested_session_id = str(session_id or "").strip()
        trace_session_id = requested_session_id or _DEFAULT_WINDOW_TRACKER_DASHBOARD_SESSION_ID
        trace_created_epoch_sec = time.time()

        def collect(name: str, fn: Any) -> dict[str, object]:
            try:
                payload = fn()
                if isinstance(payload, Mapping):
                    plain = dict(cast(Mapping[str, object], payload))
                    if name in {"study_latest", "execution_latest", "model_council_latest"} and _payload_is_stale(
                        plain,
                        now_epoch=trace_created_epoch_sec,
                    ):
                        return {
                            "status": "STALE",
                            "payload": plain,
                            "detail": f"{name} valid_until_epoch has expired.",
                        }
                    return {"status": "PASS", "payload": plain}
                return {"status": "PASS", "payload": payload}
            except KeyError as exc:
                return {"status": "MISSING", "detail": str(exc)}
            except HTTPException as exc:
                return {"status": "MISSING" if exc.status_code == status.HTTP_404_NOT_FOUND else "FAIL", "detail": str(exc.detail)}
            except Exception as exc:
                return {"status": "FAIL", "detail": f"{name}: {exc}"}

        tracker_latest = collect("tracker_latest", lambda: resolve_model_council_session_payload(trace_session_id))
        tracker_payload = _mapping_to_plain_dict(tracker_latest.get("payload"))
        resolved_session_id = str(tracker_payload.get("session_id") or trace_session_id).strip() or trace_session_id
        tracker_runtime = tracker_session_runtime_state(
            cast(Mapping[str, Any], tracker_payload),
            now_epoch=trace_created_epoch_sec,
        )
        if tracker_latest.get("status") == "PASS":
            tracker_latest["runtime_state"] = tracker_runtime
            if bool(tracker_runtime.get("stale", False)):
                tracker_latest["status"] = "STALE"
                tracker_latest["detail"] = str(tracker_runtime.get("reason") or "tracker session is stale")

        model_council_latest = collect(
            "model_council_latest",
            lambda: get_window_tracker_service().latest_model_council_state(resolved_session_id),
        )
        study_latest = collect(
            "study_latest",
            lambda: get_window_tracker_service().latest_model_council_study_packet(resolved_session_id),
        )
        execution_latest = collect(
            "execution_latest",
            lambda: get_window_tracker_service().latest_model_council_packet(resolved_session_id),
        )
        floating_state = collect(
            "floating_state",
            lambda: build_floating_state_for_session(resolved_session_id, include_inspector=False),
        )
        shooter_handshake = collect("shooter_handshake", lambda: _latest_shooter_handshake(resolved_session_id))
        model_health = collect(
            "model_health",
                lambda: build_model_council_health_from_session(
                    cast(Mapping[str, Any], tracker_payload),
                    daemon_status=fetch_model_council_daemon_status(timeout_sec=0.1),
                ),
        )

        def _sequence_context_from_endpoint(endpoint: Mapping[str, object]) -> dict[str, Any]:
            payload = _mapping_to_plain_dict(endpoint.get("payload"))
            for path in (
                ("model_council", "sequence_context"),
                ("model_council_result", "model_council", "sequence_context"),
                ("model_council_study_packet", "model_council", "sequence_context"),
                ("study_packet", "model_council", "sequence_context"),
                ("execution_packet", "model_council", "sequence_context"),
                ("latest_signal", "model_council", "sequence_context"),
                ("sequence_context",),
                ("sequence_context_v3",),
            ):
                node: Any = payload
                for key in path:
                    node = _mapping_to_plain_dict(node).get(key)
                if isinstance(node, Mapping) and node:
                    return dict(cast(Mapping[str, Any], node))
            return {}

        sequence_source = "tracker"
        sequence_context_payload = _sequence_context_from_endpoint(study_latest)
        if sequence_context_payload:
            sequence_source = "model_council_resolver"
        else:
            sequence_context_payload = _sequence_context_from_endpoint(execution_latest)
            if sequence_context_payload:
                sequence_source = "packet_builder"
            else:
                sequence_context_payload = _sequence_context_from_endpoint(floating_state)
                if sequence_context_payload:
                    sequence_source = "tracker"
                else:
                    sequence_context_payload = _sequence_context_from_endpoint(tracker_latest)
                    sequence_source = "tracker"
        sequence_context_readiness = sequence_context_readiness_report(
            sequence_context_payload,
            source_module=sequence_source,
        )

        if execution_latest.get("status") == "STALE":
            execution_latest = {
                "status": "MISSING",
                "detail": "Stored executable packet is stale; no current executable packet is published.",
                "stale_packet_id": _packet_id_from_endpoint(execution_latest),
                "stale_payload": execution_latest.get("payload"),
            }

        project_root = Path(__file__).resolve().parents[2]
        calibration_status = {
            "status": "PASS"
            if (project_root / "808_shooter_boxes.json").exists()
            and (project_root / "user_calibration_manifest.json").exists()
            else "MISSING",
            "boxes_path": "808_shooter_boxes.json",
            "manifest_path": "user_calibration_manifest.json",
        }
        cache_status = {
            "status": str(tracker_payload.get("cache_status") or tracker_payload.get("cache") or "UNKNOWN").upper(),
            "source": "tracker_latest",
        }
        endpoints = {
            "tracker_latest": tracker_latest,
            "model_council_latest": model_council_latest,
            "study_latest": study_latest,
            "execution_latest": execution_latest,
            "floating_state": floating_state,
            "shooter_handshake": shooter_handshake,
            "model_health": model_health,
            "calibration_status": calibration_status,
            "cache_status": cache_status,
            "sequence_context": {
                "status": "PASS" if sequence_context_readiness.get("ready") else "INCOMPLETE",
                "payload": sequence_context_readiness,
            },
        }
        packet_ids = {
            "study": _packet_id_from_endpoint(study_latest),
            "execution": _packet_id_from_endpoint(execution_latest),
            "floating": _packet_id_from_endpoint(floating_state),
            "shooter": _packet_id_from_endpoint(shooter_handshake),
        }
        issues: list[str] = []
        for endpoint_name in ("tracker_latest", "model_council_latest", "study_latest"):
            if endpoints[endpoint_name].get("status") == "STALE":
                issues.append(f"{endpoint_name}_stale")
        if study_latest.get("status") == "PASS" and not packet_ids["study"]:
            issues.append("study_latest_missing_packet_id")
        if execution_latest.get("status") == "PASS" and packet_ids["execution"] and packet_ids["shooter"] and packet_ids["execution"] != packet_ids["shooter"]:
            issues.append("execution_latest_shooter_packet_mismatch")
        if floating_state.get("status") == "PASS" and str(floating_state).lower().find("n/a") >= 0:
            issues.append("floating_state_contains_raw_na")

        return {
            "schema_version": "PG_RUNTIME_TRACE_V3",
            "session_id": resolved_session_id,
            "trace_created_epoch_sec": trace_created_epoch_sec,
            "language_scorecard": public_language_scorecard(),
            "sequence_context_readiness": sequence_context_readiness,
            "endpoints": endpoints,
            "alignment": {
                "status": "PASS" if not issues else "FAIL",
                "issues": issues,
                "packet_ids": packet_ids,
            },
        }

    @app.get("/v1/mobile/runtime/trace/v3")
    def runtime_trace_v3(session_id: str | None = None) -> dict[str, object]:
        return build_runtime_trace_v3(session_id)

    @app.get("/v1/mobile/runtime/sessions/{session_id}/trace/v3")
    def runtime_trace_v3_for_session(session_id: str) -> dict[str, object]:
        return build_runtime_trace_v3(session_id)

    @app.get("/v1/mobile/config")
    def config() -> dict[str, object]:
        return get_mobile_service().describe()

    @app.get("/v1/mobile/jobs")
    def list_jobs(limit: int = 12) -> dict[str, object]:
        return {"jobs": get_mobile_service().list_jobs(limit=limit)}

    @app.get("/v1/mobile/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, object]:
        try:
            return get_mobile_service().get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc

    @app.get("/v1/mobile/jobs/{job_id}/artifacts/{artifact_name}")
    def get_artifact(job_id: str, artifact_name: str) -> FileResponse:
        try:
            path = get_mobile_service().artifact_path(job_id, artifact_name)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.") from exc
        return FileResponse(path)

    @app.post("/v1/mobile/jobs", status_code=status.HTTP_202_ACCEPTED)
    async def create_job(
        screenshots: Annotated[list[UploadFile], File(description="Exactly four ordered screenshots.")],
        overlay_mode: Annotated[str, Form()] = "history-plus-projection",
        min_conf_global: Annotated[float, Form()] = 0.42,
        min_conf_latest: Annotated[float, Form()] = 0.50,
        history_depth: Annotated[int, Form()] = 8,
        label_density: Annotated[int, Form()] = 10,
        projection_focus: Annotated[float, Form()] = 0.35,
        debug_depth: Annotated[int, Form()] = 6,
        fuse_timeframe_overlays: Annotated[bool, Form()] = False,
        higher_timeframe: Annotated[str, Form()] = "M15",
        lower_timeframe: Annotated[str, Form()] = "M5",
        council_scope: Annotated[str, Form()] = "standard",
    ) -> dict[str, object]:
        try:
            uploads = [(upload.filename or f"frame_{index + 1}.png", await upload.read()) for index, upload in enumerate(screenshots)]
            return get_mobile_service().create_job(
                uploads,
                settings={
                    "overlay_mode": overlay_mode,
                    "min_conf_global": min_conf_global,
                    "min_conf_latest": min_conf_latest,
                    "history_depth": history_depth,
                    "label_density": label_density,
                    "projection_focus": projection_focus,
                    "debug_depth": debug_depth,
                    "fuse_timeframe_overlays": fuse_timeframe_overlays,
                    "higher_timeframe": higher_timeframe,
                    "lower_timeframe": lower_timeframe,
                    "council_scope": council_scope,
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/v1/mobile/observer/config")
    def observer_config() -> dict[str, object]:
        return get_observer_service().describe()

    @app.get("/v1/mobile/observer/sessions")
    def list_observer_sessions(limit: int = 20) -> dict[str, object]:
        return {"sessions": get_observer_service().list_sessions(limit=limit)}

    @app.post("/v1/mobile/observer/sessions", status_code=status.HTTP_201_CREATED)
    def create_observer_session(request: ObserverSessionCreateRequest) -> dict[str, object]:
        try:
            return get_observer_service().create_session(
                session_id=request.session_id,
                name=request.name,
                market=request.market,
                settings=request.settings,
                policy=request.policy,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/v1/mobile/observer/sessions/{session_id}")
    def get_observer_session(session_id: str) -> dict[str, object]:
        try:
            return get_observer_service().get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observer session not found.") from exc

    @app.get("/v1/mobile/observer/sessions/{session_id}/signals/latest")
    def get_observer_latest_signal(session_id: str) -> dict[str, object]:
        try:
            return get_observer_service().latest_signal(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observer session not found.") from exc

    @app.get("/v1/mobile/observer/sessions/{session_id}/bundles/{bundle_id}")
    def get_observer_bundle(session_id: str, bundle_id: str) -> dict[str, object]:
        try:
            return get_observer_service().get_bundle(session_id, bundle_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observer bundle not found.") from exc

    @app.get("/v1/mobile/observer/sessions/{session_id}/bundles/{bundle_id}/artifacts/{artifact_name}")
    def get_observer_artifact(session_id: str, bundle_id: str, artifact_name: str) -> FileResponse:
        try:
            path = get_observer_service().artifact_path(session_id, bundle_id, artifact_name)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observer bundle not found.") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observer artifact not found.") from exc
        return FileResponse(path)

    @app.post("/v1/mobile/observer/sessions/{session_id}/bundles", status_code=status.HTTP_202_ACCEPTED)
    async def submit_observer_bundle(
        session_id: str,
        screenshots: Annotated[list[UploadFile], File(description="Exactly four ordered screenshots.")],
        overlay_mode: Annotated[str, Form()] = "history-plus-projection",
        min_conf_global: Annotated[float, Form()] = 0.42,
        min_conf_latest: Annotated[float, Form()] = 0.50,
        history_depth: Annotated[int, Form()] = 8,
        label_density: Annotated[int, Form()] = 10,
        projection_focus: Annotated[float, Form()] = 0.35,
        debug_depth: Annotated[int, Form()] = 6,
        fuse_timeframe_overlays: Annotated[bool, Form()] = False,
        higher_timeframe: Annotated[str, Form()] = "M15",
        lower_timeframe: Annotated[str, Form()] = "M5",
        council_scope: Annotated[str, Form()] = "standard",
    ) -> dict[str, object]:
        try:
            uploads = [(upload.filename or f"frame_{index + 1}.png", await upload.read()) for index, upload in enumerate(screenshots)]
            return get_observer_service().submit_bundle(
                session_id,
                uploads,
                settings={
                    "overlay_mode": overlay_mode,
                    "min_conf_global": min_conf_global,
                    "min_conf_latest": min_conf_latest,
                    "history_depth": history_depth,
                    "label_density": label_density,
                    "projection_focus": projection_focus,
                    "debug_depth": debug_depth,
                    "fuse_timeframe_overlays": fuse_timeframe_overlays,
                    "higher_timeframe": higher_timeframe,
                    "lower_timeframe": lower_timeframe,
                    "council_scope": council_scope,
                },
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observer session not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/v1/mobile/window-tracker/windows")
    def list_tracker_windows(query: str = "Pocket Option") -> dict[str, object]:
        return {"windows": get_window_tracker_service().list_windows(query)}

    @app.get("/v1/mobile/window-tracker/sessions")
    def list_tracker_sessions(limit: int = 20) -> dict[str, object]:
        return {"sessions": get_window_tracker_service().list_sessions(limit=limit)}

    @app.post("/v1/mobile/window-tracker/sessions", status_code=status.HTTP_201_CREATED)
    def create_tracker_session(request: WindowTrackerSessionCreateRequest) -> dict[str, object]:
        try:
            return get_window_tracker_service().create_session(
                session_id=request.session_id,
                name=request.name,
                market=request.market,
                window_query=request.window_query,
                layout_profile=request.layout_profile,
                capture_interval_sec=request.capture_interval_sec,
                rl_track_interval_sec=request.rl_track_interval_sec,
                auto_start=request.auto_start,
                observer_settings=request.observer_settings,
                observer_policy=request.observer_policy,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/v1/mobile/window-tracker/sessions/{session_id}")
    def get_tracker_session(session_id: str) -> dict[str, object]:
        try:
            return read_window_tracker_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc

    @app.put("/v1/mobile/window-tracker/sessions/{session_id}/focus-region")
    def set_tracker_focus_region(
        session_id: str,
        request: WindowTrackerFocusRegionRequest,
    ) -> dict[str, object]:
        try:
            return get_window_tracker_service().set_focus_region(
                session_id,
                request.normalized_bbox,
                source=request.source,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.delete("/v1/mobile/window-tracker/sessions/{session_id}/focus-region")
    def clear_tracker_focus_region(session_id: str) -> dict[str, object]:
        try:
            return get_window_tracker_service().clear_focus_region(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc

    @app.post("/v1/mobile/window-tracker/sessions/{session_id}/focus-region/arm")
    def arm_tracker_focus_region(session_id: str) -> dict[str, object]:
        try:
            return get_window_tracker_service().arm_focus_selector(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/v1/mobile/window-tracker/sessions/{session_id}/focus-region/cancel")
    def cancel_tracker_focus_region(session_id: str) -> dict[str, object]:
        try:
            return get_window_tracker_service().cancel_focus_selector(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc

    @app.get("/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-chart")
    def get_tracker_latest_chart(session_id: str) -> FileResponse:
        try:
            path = get_window_tracker_service().latest_artifact_path(session_id, "chart")
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        suffix = path.suffix.lower()
        if suffix in {".jpg", ".jpeg"}:
            media_type = "image/jpeg"
        elif suffix == ".png":
            media_type = "image/png"
        elif suffix == ".json":
            media_type = "application/json"
        else:
            media_type = None
        return _safe_file_bytes_response(path, media_type=media_type)

    @app.get("/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-window")
    def get_tracker_latest_window(session_id: str) -> FileResponse:
        try:
            path = get_window_tracker_service().latest_artifact_path(session_id, "window")
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        media_type = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        return _safe_file_bytes_response(path, media_type=media_type)

    @app.get("/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-{artifact_kind}")
    def get_tracker_latest_named_artifact(session_id: str, artifact_kind: str) -> FileResponse:
        try:
            path = get_window_tracker_service().latest_artifact_path(session_id, artifact_kind)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        media_type = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        return _safe_file_bytes_response(path, media_type=media_type)

    @app.get("/v1/mobile/window-tracker/sessions/{session_id}/health")
    def get_tracker_health(session_id: str) -> dict[str, object]:
        try:
            tracker = get_window_tracker_service()
            # probe latest chart and overlay
            result = {"session_id": session_id, "artifacts": {}, "registry_path": None}
            for kind in ("chart", "overlay", "window"):
                try:
                    path = tracker.latest_artifact_path(session_id, kind)
                    result["artifacts"][kind] = {"path": str(path), "exists": path.exists()}
                except FileNotFoundError:
                    result["artifacts"][kind] = {"path": None, "exists": False}
            worker_health = getattr(tracker, "capture_worker_health_v3", None)
            if callable(worker_health):
                result["capture_worker_v3"] = worker_health(session_id)
            # registry presence
            try:
                from phoenixguard.vision.object_registry import REGISTRY_DIR

                registry_file = REGISTRY_DIR / f"{session_id}.jsonl"
                result["registry_path"] = str(registry_file) if registry_file.exists() else None
            except Exception:
                result["registry_path"] = None
            return result
        except KeyError:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.")

    @app.get("/v1/mobile/registry/sessions/{session_id}/active")
    def get_active_registry(session_id: str, min_truth_score: float = 0.0) -> dict[str, object]:
        try:
            active = query_recent_active_objects(session_id, min_truth_score=float(min_truth_score))
            # best-effort: include the latest available chart_transform from the registry entries
            chart_transform = None
            try:
                entries = load_recent_market_objects(session_id)
                for e in reversed(entries or []):
                    ct = e.get("chart_transform")
                    if ct:
                        chart_transform = ct
                        break
            except Exception:
                chart_transform = None
            return {"session_id": session_id, "active_overlays": active, "count": len(active), "chart_transform": chart_transform}
        except Exception:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Registry session not found.")

    @app.get("/v1/mobile/visual/health/v3")
    def visual_health_v3(session_id: str | None = None) -> dict[str, object]:
        try:
            sid = str(session_id or "").strip()
            if not sid:
                sid = resolve_window_tracker_dashboard_session_id(None)
            tracker = get_window_tracker_service()
            artifacts = {}
            for kind in ("chart", "overlay", "window"):
                try:
                    path = tracker.latest_artifact_path(sid, kind)
                    artifacts[kind] = {"path": str(path), "exists": path.exists()}
                except Exception:
                    artifacts[kind] = {"path": None, "exists": False}
            try:
                entries = load_recent_market_objects(sid)
                total = len(entries)
                stalled = sum(1 for e in entries if str(e.get("lifecycle_state") or "").upper() == "STALE")
            except Exception:
                total = 0
                stalled = 0
            stale = bool(stalled > 0)
            overlay = {
                "count": total,
                "frame_matches_chart_frame": bool(total > 0 and not stale),
            }
            model_health = {"all_required_models_awake": True}
            try:
                study_packet = tracker.latest_model_council_study_packet(sid)
                study_packet_payload = {"exists": True, "packet_id": study_packet.get("packet_id")}
            except Exception:
                study_packet_payload = {"exists": False}
            return {
                "schema_version": "PG_VISUAL_HEALTH_V3",
                "session_id": sid,
                "artifacts": artifacts,
                "stale": stale,
                "study_packet": study_packet_payload,
                "overlay": overlay,
                "model_health": model_health,
                "registry": {"total_entries": total, "stale_entries": stalled},
            }
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    @app.get("/v1/mobile/visual/health/v3/{session_id}")
    def visual_health_v3_for_session(session_id: str) -> dict[str, object]:
        live_state = build_live_state_v3_for_session(session_id)
        visual_health_payload = _mapping_to_plain_dict(live_state.get("visual_health_v3") or live_state.get("visual_health"))
        heartbeat = latest_frontend_heartbeat(str(live_state.get("session_id", session_id) or session_id))
        realtime = build_visual_realtime_health(
            str(live_state.get("session_id", session_id) or session_id),
            live_state=cast(Mapping[str, Any], live_state),
            visual_health=cast(Mapping[str, Any], visual_health_payload),
            heartbeat=heartbeat,
        )
        return {
            **visual_health_payload,
            "frontend_realtime": realtime,
        }

    @app.get("/v1/mobile/registry/sessions/{session_id}/render/latest.png")
    def render_registry_snapshot(session_id: str) -> Response:
        try:
            tracker = get_window_tracker_service()
            try:
                chart_path = tracker.latest_artifact_path(session_id, "chart")
            except Exception:
                chart_path = None
            overlays = query_recent_active_objects(session_id, min_truth_score=0.0)
            # convert entries to overlay dicts
            overlay_dicts = [e.get("overlay") if isinstance(e.get("overlay"), Mapping) else {} for e in overlays]
            png = render_overlays_on_chart(chart_path if chart_path is not None else None, overlay_dicts)
            # optionally persist snapshot for golden/regression evidence
            save_dir = Path(__file__).resolve().parents[2] / ".codex_runtime" / "visual_evidence"
            try:
                save_dir.mkdir(parents=True, exist_ok=True)
                out_path = save_dir / f"{session_id}_render_latest.png"
                out_path.write_bytes(png)
                meta = {"session_id": session_id, "saved_at": time.time(), "path": str(out_path)}
                try:
                    (save_dir / f"{session_id}_render_latest.json").write_text(json.dumps(meta), encoding="utf-8")
                except Exception:
                    pass
            except Exception:
                pass
            return Response(content=png, media_type="image/png")
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))

    @app.get("/v1/mobile/window-tracker/assets/{asset_path:path}")
    def get_window_tracker_dashboard_asset(asset_path: str) -> FileResponse:
        normalized_asset_path = str(asset_path or "").lstrip("/\\")
        path = None
        media_type = "application/octet-stream"
        if normalized_asset_path.startswith("js/"):
            relative_asset_path = normalized_asset_path.removeprefix("js/").replace("\\", "/")
            relative_parts = Path(relative_asset_path).parts
            if (
                not relative_asset_path
                or Path(relative_asset_path).is_absolute()
                or any(part in {"", ".", ".."} for part in relative_parts)
            ):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard asset not found.")
            candidate = (_WINDOW_TRACKER_JS_ASSET_DIR / relative_asset_path).resolve()
            asset_root = _WINDOW_TRACKER_JS_ASSET_DIR.resolve()
            try:
                candidate.relative_to(asset_root)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard asset not found.") from exc
            path = candidate
            media_type = "application/javascript"
        elif normalized_asset_path in _WINDOW_TRACKER_BRAND_ASSETS:
            path = _WINDOW_TRACKER_BRAND_ASSET_DIR / normalized_asset_path
            media_type = "image/png"
        if path is None or not path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard asset not found.")
        return FileResponse(path, media_type=media_type)

    @app.get("/assets/js/{asset_path:path}")
    def get_legacy_window_tracker_js_asset(asset_path: str) -> FileResponse:
        normalized_asset_path = str(asset_path or "").lstrip("/\\")
        if not normalized_asset_path or Path(normalized_asset_path).is_absolute() or any(part in {"", ".", ".."} for part in Path(normalized_asset_path).parts):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard asset not found.")
        candidate = (_WINDOW_TRACKER_JS_ASSET_DIR / normalized_asset_path).resolve()
        asset_root = _WINDOW_TRACKER_JS_ASSET_DIR.resolve()
        try:
            candidate.relative_to(asset_root)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard asset not found.") from exc
        if not candidate.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard asset not found.")
        return FileResponse(candidate, media_type="application/javascript")

    @app.get("/assets/share/overlay_demo.html", response_class=HTMLResponse)
    def get_overlay_demo_html() -> HTMLResponse:
        demo_path = Path(__file__).resolve().parents[2] / "assets" / "share" / "overlay_demo.html"
        if not demo_path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard asset not found.")
        return HTMLResponse(demo_path.read_text(encoding="utf-8"))

    @app.get("/v1/mobile/window-tracker/dashboard", response_class=HTMLResponse)
    def window_tracker_dashboard_default() -> HTMLResponse:
        session_id = resolve_window_tracker_dashboard_session_id()
        return HTMLResponse(_render_window_tracker_dashboard(session_id))

    @app.get("/v1/mobile/window-tracker/dashboard/{session_id}", response_class=HTMLResponse)
    def window_tracker_dashboard(session_id: str) -> HTMLResponse:
        resolved_session_id = resolve_window_tracker_dashboard_session_id(session_id)
        return HTMLResponse(_render_window_tracker_dashboard(resolved_session_id))

    @app.get("/dashboard/{workspace}", response_class=HTMLResponse)
    def dashboard_workspace(workspace: str, session_id: str | None = None) -> HTMLResponse:
        _workspace = str(workspace or "live").strip().lower()
        resolved_session_id = resolve_window_tracker_dashboard_session_id(session_id)
        return HTMLResponse(_render_window_tracker_dashboard(resolved_session_id))

    @app.get("/dashboard/{workspace}/{session_id}", response_class=HTMLResponse)
    def dashboard_workspace_for_session(workspace: str, session_id: str) -> HTMLResponse:
        _workspace = str(workspace or "live").strip().lower()
        resolved_session_id = resolve_window_tracker_dashboard_session_id(session_id)
        return HTMLResponse(_render_window_tracker_dashboard(resolved_session_id))

    @app.get("/v1/mobile/window-tracker/sessions/{session_id}/events")
    @app.get("/v1/mobile/window-tracker/sessions/{session_id}/stream")
    def stream_tracker_session(session_id: str) -> StreamingResponse:
        """Low-latency session stream for the cockpit UI.

        The dashboard still has a polling fallback, but this SSE endpoint pushes
        every changed session snapshot as soon as the tracker publishes it.
        """

        def _fingerprint(payload: Mapping[str, Any]) -> str:
            latest_signal = payload.get("latest_signal") if isinstance(payload.get("latest_signal"), Mapping) else {}
            tracking = payload.get("tracking_summary") if isinstance(payload.get("tracking_summary"), Mapping) else {}
            packet = payload.get("model_council_packet") or payload.get("execution_packet")
            if not isinstance(packet, Mapping):
                packet = latest_signal.get("model_council_packet") if isinstance(latest_signal, Mapping) else {}
            parts = {
                "capture_count": payload.get("capture_count"),
                "last_capture_epoch": payload.get("last_capture_epoch"),
                "state_version": payload.get("state_version"),
                "display_frame_id": payload.get("display_frame_id"),
                "source_capture_id": payload.get("source_capture_id"),
                "chart_frame_id": payload.get("chart_frame_id"),
                "overlay_frame_id": payload.get("overlay_frame_id"),
                "full_overlay_frame_id": payload.get("full_overlay_frame_id"),
                "model_vote_frame_id": payload.get("model_vote_frame_id"),
                "last_window_path": payload.get("last_window_path") or payload.get("last_frame_path"),
                "last_chart_path": payload.get("last_chart_path"),
                "last_overlay_path": payload.get("last_overlay_path"),
                "last_full_overlay_path": payload.get("last_full_overlay_path"),
                "signal_id": latest_signal.get("signal_id") if isinstance(latest_signal, Mapping) else "",
                "published_epoch": latest_signal.get("published_epoch") if isinstance(latest_signal, Mapping) else "",
                "hf_cycle": latest_signal.get("high_frequency_candle_cycle") if isinstance(latest_signal, Mapping) else "",
                "tracking_updated": tracking.get("published_at") if isinstance(tracking, Mapping) else "",
                "packet_id": packet.get("packet_id") if isinstance(packet, Mapping) else "",
            }
            return json.dumps(parts, sort_keys=True, default=str)

        def _events() -> object:
            last_fingerprint = ""
            last_keepalive = 0.0
            while True:
                now = time.time()
                try:
                    payload = read_window_tracker_session(session_id)
                except KeyError:
                    error_payload = json.dumps(
                        {"session_id": session_id, "status": "error", "detail": "Window tracker session not found."},
                        default=str,
                    )
                    yield f"event: SESSION_ERROR\ndata: {error_payload}\n\n"
                    return
                fingerprint = _fingerprint(cast(Mapping[str, Any], payload))
                if fingerprint != last_fingerprint:
                    last_fingerprint = fingerprint
                    body = json.dumps(payload, default=str)
                    yield f"event: SESSION_UPDATE\ndata: {body}\n\n"
                    last_keepalive = now
                elif now - last_keepalive >= 2.0:
                    yield ": heartbeat\n\n"
                    last_keepalive = now
                time.sleep(0.2)

        return StreamingResponse(
            _events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/v1/mobile/window-tracker/sessions/{session_id}/start")
    def start_tracker_session(session_id: str) -> dict[str, object]:
        try:
            return get_window_tracker_service().start_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc

    @app.post("/v1/mobile/window-tracker/sessions/{session_id}/stop")
    def stop_tracker_session(session_id: str) -> dict[str, object]:
        try:
            return get_window_tracker_service().stop_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc

    @app.post("/v1/mobile/window-tracker/sessions/{session_id}/emergency-stop")
    def emergency_stop_tracker_session(session_id: str) -> dict[str, object]:
        try:
            return get_window_tracker_service().emergency_stop_session(
                session_id,
                reason="Emergency stop requested from dashboard/API.",
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc

    @app.post("/v1/mobile/window-tracker/sessions/{session_id}/capture-once")
    def capture_tracker_session_once(session_id: str) -> dict[str, object]:
        try:
            payload = get_window_tracker_service().capture_once(session_id)
            with _LIVE_STATE_V3_CACHE_LOCK:
                stale_session = str(session_id or "").strip()
                for cache_key in [key for key in _LIVE_STATE_V3_CACHE if key[0] == stale_session]:
                    _LIVE_STATE_V3_CACHE.pop(cache_key, None)
            return _compact_capture_once_response(payload)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except Exception as exc:
            return {
                "schema_version": "PG_CAPTURE_ONCE_RESULT_V3",
                "session_id": session_id,
                "capture_once_result": {
                    "schema_version": "PG_CAPTURE_ONCE_RESULT_V3",
                    "ok": False,
                    "status": "failed",
                    "attempted": False,
                    "advanced": False,
                    "duration_ms": 0.0,
                    "error": f"{type(exc).__name__}: {exc}",
                },
                "status": "error",
                "last_error": f"Capture-once failed: {exc}",
            }

    @app.post("/v1/mobile/window-tracker/sessions/{session_id}/demo-random-trade")
    def execute_tracker_demo_random_trade(
        session_id: str,
        request: WindowTrackerDemoTradeRequest | None = None,
    ) -> dict[str, object]:
        try:
            payload = request or WindowTrackerDemoTradeRequest()
            return get_window_tracker_service().execute_demo_random_trade(
                session_id,
                side=payload.side,
                expiry_seconds=payload.expiry_seconds,
                force=payload.force,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/v1/mobile/window-tracker/sessions/{session_id}/predict")
    def predict_tracker_session_from_memory(session_id: str) -> dict[str, object]:
        try:
            return get_window_tracker_service().run_memory_projection(session_id, mode="predict")
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/v1/mobile/window-tracker/sessions/{session_id}/show-future")
    def show_future_tracker_session_from_memory(session_id: str) -> dict[str, object]:
        try:
            return get_window_tracker_service().run_memory_projection(session_id, mode="future")
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.patch("/v1/mobile/window-tracker/sessions/{session_id}/controls")
    def update_tracker_session_controls(
        session_id: str,
        request: WindowTrackerControlUpdateRequest,
    ) -> dict[str, object]:
        try:
            return get_window_tracker_service().update_session_controls(
                session_id,
                capture_interval_sec=request.capture_interval_sec,
                live_execution_enabled=request.live_execution_enabled,
                execution_mode=request.execution_mode,
                allow_countertrend_scalp=request.allow_countertrend_scalp,
                allow_location_sniper_entries=request.allow_location_sniper_entries,
                trade_profile=request.trade_profile,
                high_frequency_enabled=request.high_frequency_enabled,
                swing_fallback_enabled=request.swing_fallback_enabled,
                continuous_model_feed_enabled=request.continuous_model_feed_enabled,
                high_frequency_min_confidence=request.high_frequency_min_confidence,
                high_frequency_entry_grace_sec=request.high_frequency_entry_grace_sec,
                high_frequency_expiry_seconds=request.high_frequency_expiry_seconds,
                scenario_generation_enabled=request.scenario_generation_enabled,
                auto_memory_projection=request.auto_memory_projection,
                require_memory_projection=request.require_memory_projection,
                require_market_identity=request.require_market_identity,
                require_timeframe_identity=request.require_timeframe_identity,
                allow_locked_surface_identity_fallback=request.allow_locked_surface_identity_fallback,
                broker_surface_cache_sec=request.broker_surface_cache_sec,
                adaptive_timer_enabled=request.adaptive_timer_enabled,
                min_capture_interval_sec=request.min_capture_interval_sec,
                max_capture_interval_sec=request.max_capture_interval_sec,
                max_executions_per_window=request.max_executions_per_window,
                execution_window_sec=request.execution_window_sec,
                min_market_confidence=request.min_market_confidence,
                min_timeframe_confidence=request.min_timeframe_confidence,
                cooldown_sec=request.cooldown_sec,
                loss_guard_enabled=request.loss_guard_enabled,
                loss_guard_max_consecutive_losses=request.loss_guard_max_consecutive_losses,
                loss_guard_window_sec=request.loss_guard_window_sec,
                loss_guard_pause_sec=request.loss_guard_pause_sec,
                min_location_sniper_target_candles=request.min_location_sniper_target_candles,
                phoenix_report_interval_sec=request.phoenix_report_interval_sec,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/v1/voice/status")
    def voice_status(tracker_session_id: str | None = None) -> dict[str, object]:
        tracker_session, market_context = get_voice_context_payload(tracker_session_id)
        return {
            "snapshot": get_voice_runtime_snapshot(config=get_voice_config()),
            "market_context": market_context,
            "tracker_session": tracker_session,
            "commands": public_voice_command_catalog(),
        }

    @app.get("/v1/voice/commands")
    def voice_commands() -> dict[str, object]:
        return {"commands": public_voice_command_catalog()}

    @app.post("/v1/voice/preferences")
    def voice_preferences(request: VoicePreferenceUpdateRequest) -> dict[str, object]:
        resolved_session_id = resolve_voice_tracker_session_id(request.tracker_session_id)
        update_voice_state(config=get_voice_config(), tracker_session_id=resolved_session_id)
        tracker_controller = LocalWindowTrackerVoiceController(get_window_tracker_service())
        snapshot = apply_voice_preferences(
            voice_enabled=bool(request.voice_enabled),
            listening_enabled=bool(request.listening_enabled),
            automatic_timer_enabled=bool(request.automatic_timer_enabled),
            tracker_capture_interval_sec=float(request.tracker_capture_interval_sec),
            timezone_name=str(request.timezone_name or ""),
            config=get_voice_config(),
            tracker_controller=tracker_controller,
        )
        tracker_session, market_context = get_voice_context_payload(resolved_session_id)
        return {
            "snapshot": snapshot,
            "market_context": market_context,
            "tracker_session": tracker_session,
        }

    @app.post("/v1/voice/command")
    def voice_command(request: VoiceCommandRequest) -> dict[str, object]:
        resolved_session_id = resolve_voice_tracker_session_id(request.tracker_session_id)
        tracker_session, market_context = get_voice_context_payload(resolved_session_id)
        tracker_controller = LocalWindowTrackerVoiceController(get_window_tracker_service())
        execution = execute_voice_command(
            request.command,
            market_context=market_context,
            config=get_voice_config(),
            tracker_controller=tracker_controller,
        )
        tracker_session, refreshed_market_context = get_voice_context_payload(resolved_session_id)
        match = execution["match"]
        payload = execution.get("payload", {})
        tracker_session_payload = dict(cast(Mapping[str, object], tracker_session))
        execution_payload: object = (
            dict(cast(Mapping[str, object], payload)) if isinstance(payload, Mapping) else payload
        )
        return {
            "response_text": str(execution.get("response_text", "") or ""),
            "match": {
                "name": str(match.name),
                "confidence": float(match.confidence),
                "slots": dict(cast(Mapping[str, object], match.slots)),
                "blocked_sensitive_request": bool(match.blocked_sensitive_request),
            },
            "snapshot": dict(execution.get("snapshot", get_voice_runtime_snapshot(config=get_voice_config()))),
            "market_context": refreshed_market_context,
            "tracker_session": tracker_session_payload,
            "payload": execution_payload,
        }

    instrument_fastapi_app(app)
    return app
