from __future__ import annotations

import json
import os
from pathlib import Path
import re
import threading
import time
from collections.abc import Iterator
from typing import Annotated, Any, Mapping, Sequence, cast
import urllib.error
import urllib.request

from fastapi import Body, FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from phoenixguard.core.config import RUNTIME, VOICE, VoiceConfig
from phoenixguard.business import register_business_routes
from phoenixguard.execution.floating_state_reducer import build_floating_state
from phoenixguard.execution.sequence_context import sequence_context_readiness_report
from phoenixguard.execution.v3_language import public_language_scorecard
from phoenixguard.paths import FRONTEND_ROOT, PROJECT_ROOT
from phoenixguard.runtime.observability_v3 import (
    build_intelligence_health,
    build_model_council_health_from_session,
)
from phoenixguard.runtime.realtime_performance_v3 import build_frame_timing_trace_v3, build_performance_trace_v3
from phoenixguard.runtime.python_environment_v3 import build_python_environment_status
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
    active_objects_from_entries,
    load_recent_market_objects,
    query_recent_active_objects,
)
from phoenixguard.vision.overlay_layer_manager_v3 import OverlayLayerManagerV3
from phoenixguard.vision.renderer import render_overlays_on_chart
from phoenixguard.vision.v3_overlay_contract import (
    REQUIRED_FIELDS,
    normalize_v3_overlay_object,
    normalize_view_mode,
    overlay_is_visible,
    view_mode_profile,
)

from .live_state_v3 import (
    build_live_state_v3,
    build_live_state_v3_from_tracker_service,
    compact_session_payload,
)
from .model_strength import (
    model_strength_settings_to_execution_controls,
    read_model_strength_settings,
    write_model_strength_settings,
)
from .observer import SignalObserverService
from .realtime_sync_v3 import (
    build_visual_realtime_health,
    latest_frontend_heartbeat,
    record_frontend_heartbeat,
)
from .service import MobileApiService
from .window_tracker import (
    ContinuousWindowTrackerService,
    model_council_packet_from_payload,
    model_council_study_packet_from_payload,
)


_default_service: MobileApiService | None = None
_default_observer_service: SignalObserverService | None = None
_default_window_tracker_service: ContinuousWindowTrackerService | None = None
_WINDOW_TRACKER_DASHBOARD_TEMPLATE = (
    FRONTEND_ROOT / "dashboard" / "static" / "window_tracker_dashboard.html"
)
_WINDOW_TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC = 15.0
_WINDOW_TRACKER_MIN_CAPTURE_INTERVAL_SEC = 0.5
_WINDOW_TRACKER_MAX_CAPTURE_INTERVAL_SEC = 30.0
_WINDOW_TRACKER_BRAND_ASSET_DIR = (
    FRONTEND_ROOT / "assets" / "share" / "css-control"
)
_WINDOW_TRACKER_JS_ASSET_DIR = FRONTEND_ROOT / "assets" / "js"
_WINDOW_TRACKER_FLOATING_WINDOWS_DIR = FRONTEND_ROOT / "dashboard" / "static" / "floating_windows"
_WINDOW_TRACKER_OVERLAY_EDITOR_SETTINGS_PATH = (
    _WINDOW_TRACKER_FLOATING_WINDOWS_DIR / "overlay_editor_settings.json"
)
_OVERLAY_EDITOR_SETTINGS_SCHEMA_VERSION = 2
_WINDOW_TRACKER_BRAND_ASSETS = frozenset(
    {
        "landing-transition-lifestyle-suite.png",
        "landing-transition-lifestyle-travel.png",
        "landing-transition-market-vision-alt.png",
        "landing-transition-market-vision.png",
    }
)
_DEFAULT_WINDOW_TRACKER_DASHBOARD_SESSION_ID = "pocket-live-8788"
_RUNTIME_ROOT = Path(os.getenv("PHOENIXGUARD_RUNTIME_DIR") or PROJECT_ROOT / "runtime" / "live")
_SHOOTER_HANDSHAKE_PATH = _RUNTIME_ROOT / "shooter_handshake.json"
_PUBLISHED_PACKET_FALLBACK_TTL_SEC = 8.0


def _env_float_at_least(name: str, default: float, minimum: float) -> float:
    raw = os.getenv(name, str(default)) or str(default)
    try:
        return max(float(minimum), float(raw))
    except ValueError:
        return float(default)


def _env_int_at_least(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name, str(default)) or str(default)
    try:
        return max(int(minimum), int(float(raw)))
    except ValueError:
        return int(default)


_LIVE_STATE_V3_CACHE_TTL_SEC = _env_float_at_least("PHOENIXGUARD_LIVE_STATE_CACHE_TTL_SEC", 5.0, 0.0)
_LIVE_STATE_REGISTRY_CACHE_TTL_SEC = _env_float_at_least("PHOENIXGUARD_LIVE_STATE_REGISTRY_CACHE_TTL_SEC", 5.0, 0.0)
_COMPACT_LIVE_STATE_RESPONSE_CACHE_TTL_SEC = _env_float_at_least(
    "PHOENIXGUARD_COMPACT_LIVE_STATE_RESPONSE_CACHE_TTL_SEC",
    300.0,
    0.0,
)
_COMPACT_LIVE_STATE_RESPONSE_HOT_TTL_SEC = _env_float_at_least(
    "PHOENIXGUARD_COMPACT_LIVE_STATE_RESPONSE_HOT_TTL_SEC",
    20.0,
    0.0,
)
_LIVE_STATE_REGISTRY_MAX_LINES = _env_int_at_least("PHOENIXGUARD_LIVE_STATE_REGISTRY_MAX_LINES", 2000, 50)
_LIVE_STATE_V3_CACHE_LOCK = threading.Lock()
_LIVE_STATE_V3_CACHE: dict[tuple[str, str, str, bool], tuple[float, dict[str, object]]] = {}
_COMPACT_LIVE_STATE_RESPONSE_CACHE: dict[tuple[str, str, str], tuple[float, dict[str, object]]] = {}
_COMPACT_LIVE_STATE_BUILD_LOCKS_GUARD = threading.Lock()
_COMPACT_LIVE_STATE_BUILD_LOCKS: dict[tuple[str, str, str], threading.Lock] = {}
_LIVE_STATE_REGISTRY_CACHE: dict[str, tuple[float, list[Mapping[str, Any]], list[Mapping[str, Any]]]] = {}
_DIRECT_PERFORMANCE_TRACE_CACHE_LOCK = threading.Lock()
_DIRECT_PERFORMANCE_TRACE_CACHE: dict[str, tuple[float, dict[str, object]]] = {}
_NO_STORE_ARTIFACT_HEADERS = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}
_EMPTY_OBJECT_MAPPING: Mapping[str, object] = {}


def _as_mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else _EMPTY_OBJECT_MAPPING


def _as_object_mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else _EMPTY_OBJECT_MAPPING


def _as_object_dict(value: object) -> dict[str, object] | None:
    return cast(dict[str, object], value) if isinstance(value, dict) else None


def _as_sequence(value: object) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return cast(Sequence[Any], value)
    return ()


def _compact_live_state_build_lock(cache_key: tuple[str, str, str]) -> threading.Lock:
    with _COMPACT_LIVE_STATE_BUILD_LOCKS_GUARD:
        lock = _COMPACT_LIVE_STATE_BUILD_LOCKS.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _COMPACT_LIVE_STATE_BUILD_LOCKS[cache_key] = lock
            if len(_COMPACT_LIVE_STATE_BUILD_LOCKS) > 128:
                for stale_key in list(_COMPACT_LIVE_STATE_BUILD_LOCKS)[:32]:
                    _COMPACT_LIVE_STATE_BUILD_LOCKS.pop(stale_key, None)
        return lock


def _compact_live_state_renderable_count(payload: Mapping[str, object]) -> int:
    overlays = _as_mapping(payload.get("overlays"))
    objects = overlays.get("objects")
    if isinstance(objects, Sequence) and not isinstance(objects, (str, bytes, bytearray)):
        return len(cast(Sequence[object], objects))
    for key in ("renderable_count", "overlay_count"):
        value = overlays.get(key, payload.get(key))
        try:
            count = int(float(str(value or "0")))
        except ValueError:
            count = 0
        if count > 0:
            return count
    live_visual = _as_mapping(payload.get("live_visual_state"))
    if live_visual:
        return _compact_live_state_renderable_count(live_visual)
    return 0


def _compact_live_state_response_cache_candidates(
    cache_key: tuple[str, str, str],
) -> list[tuple[tuple[str, str, str], tuple[float, dict[str, object]]]]:
    exact = _COMPACT_LIVE_STATE_RESPONSE_CACHE.get(cache_key)
    candidates: list[tuple[tuple[str, str, str], tuple[float, dict[str, object]]]] = []
    if exact is not None:
        candidates.append((cache_key, exact))
    session_id, active_mode, _signature = cache_key
    previous = [
        (key, cached)
        for key, cached in _COMPACT_LIVE_STATE_RESPONSE_CACHE.items()
        if key != cache_key and key[0] == session_id and key[1] == active_mode
    ]
    previous.sort(key=lambda item: item[1][0], reverse=True)
    candidates.extend(previous[:4])
    return candidates


def _compact_live_state_cache_can_reuse(payload: Mapping[str, object], cached_age_sec: float) -> bool:
    if _compact_live_state_renderable_count(payload) > 0:
        return True
    return cached_age_sec <= 2.0


def _compact_overlay_object_frame_id(payload: Mapping[str, object]) -> int:
    candidates: list[object] = [
        payload.get("overlay_object_frame_id"),
        _as_mapping(payload.get("overlays")).get("overlay_object_frame_id"),
        _as_mapping(payload.get("live_visual_state")).get("overlay_object_frame_id"),
        _as_mapping(_as_mapping(payload.get("live_visual_state")).get("overlays")).get("overlay_object_frame_id"),
    ]
    for container in (
        _as_mapping(payload.get("overlays")),
        _as_mapping(_as_mapping(payload.get("live_visual_state")).get("overlays")),
    ):
        for key in ("objects", "all_objects"):
            rows = _as_sequence(container.get(key))
            if not rows:
                continue
            first = _as_mapping(rows[0])
            candidates.append(first.get("frame_id") or first.get("frame_index"))
    for candidate in candidates:
        frame_id = int(_epoch_float(candidate, 0.0))
        if frame_id > 0:
            return frame_id
    return 0


def _display_overlay_authority_frame_id(display_payload: Mapping[str, object]) -> int:
    return max(
        int(_epoch_float(display_payload.get("overlay_frame_id"), 0.0)),
        int(_epoch_float(display_payload.get("chart_frame_id"), 0.0)),
        int(_epoch_float(display_payload.get("full_overlay_frame_id"), 0.0)),
    )


def _compact_overlay_payload_stale_for_display(
    payload: Mapping[str, object],
    display_payload: Mapping[str, object] | None,
) -> bool:
    if display_payload is None or _compact_live_state_renderable_count(payload) <= 0:
        return False
    display_overlay_frame_id = _display_overlay_authority_frame_id(display_payload)
    if display_overlay_frame_id <= 0:
        return False
    payload_overlay_frame_id = _compact_overlay_object_frame_id(payload)
    if payload_overlay_frame_id <= 0:
        return True
    return payload_overlay_frame_id < display_overlay_frame_id


_DIRECT_DISPLAY_STATE_KEYS = frozenset(
    {
        "session_id",
        "capture_count",
        "frame_index",
        "chart_frame_id",
        "overlay_frame_id",
        "full_overlay_frame_id",
        "model_vote_frame_id",
        "state_version",
        "display_frame_id",
        "display_capture_epoch",
        "display_published_epoch",
        "last_display_capture_epoch",
        "last_display_published_epoch",
        "last_display_window_path",
        "last_chart_path",
        "last_overlay_path",
        "last_full_overlay_path",
        "last_display_surface_signature",
        "last_window_surface_signature",
        "last_study_surface_signature",
        "overlay_source_window_signature",
        "overlay_source_study_signature",
        "display_snapshot_only_v3",
        "display_fast_path_v3",
        "display_busy_reuse_heartbeat_v3",
        "display_reuse_only_heartbeat_v3",
    }
)
_DIRECT_DISPLAY_STATE_NONEMPTY_STRING_KEYS = frozenset(
    {
        "last_display_window_path",
        "last_chart_path",
        "last_overlay_path",
        "last_full_overlay_path",
        "last_display_surface_signature",
        "last_window_surface_signature",
        "last_study_surface_signature",
        "overlay_source_window_signature",
        "overlay_source_study_signature",
    }
)


def _slugify_session_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("._").lower() or "session"


def _runtime_data_dir_candidates() -> list[Path]:
    candidates: list[Path] = [Path(RUNTIME.data_dir)]
    candidates.append(_RUNTIME_ROOT / "data_live")
    lock_path = _RUNTIME_ROOT / "phoenixguard_stack.lock.json"
    try:
        lock_payload = json.loads(lock_path.read_text(encoding="utf-8"))
    except Exception:
        lock_payload = None
    if isinstance(lock_payload, Mapping):
        lock_data_dir = str(cast(Mapping[str, object], lock_payload).get("data_dir") or "").strip()
        if lock_data_dir:
            candidates.append(Path(lock_data_dir))
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve())
        except OSError:
            key = str(candidate)
        normalized = key.lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(candidate)
    return unique or [Path(RUNTIME.data_dir)]


def _direct_session_relative_path(session_id: str) -> Path:
    return Path("mobile_api") / "window_tracker" / "sessions" / _slugify_session_id(session_id) / "session.json"


def _direct_live_state_session_path(session_id: str) -> Path:
    relative_path = _direct_session_relative_path(session_id)
    candidates = _runtime_data_dir_candidates()
    primary_path = candidates[0] / relative_path
    if primary_path.with_name("display_state.json").exists():
        return primary_path
    for data_dir in candidates:
        path = data_dir / relative_path
        if path.exists():
            return path
    return primary_path


def _direct_live_state_compact_session_path(session_id: str) -> Path:
    session_path = _direct_live_state_session_path(session_id)
    compact_path = session_path.with_name("compact_live_state.json")
    if compact_path.exists():
        return compact_path
    return session_path


def _direct_window_tracker_display_state_path(session_id: str) -> Path:
    relative_path = _direct_session_relative_path(session_id).with_name("display_state.json")
    candidates = _runtime_data_dir_candidates()
    for data_dir in candidates:
        path = data_dir / relative_path
        if path.exists():
            return path
    return candidates[0] / relative_path


def _persisted_compact_overlay_response_path(session_id: str, mode: str) -> Path:
    safe_mode = re.sub(r"[^A-Za-z0-9_.-]+", "_", normalize_view_mode(mode).lower()).strip("._") or "clean_live"
    return _direct_live_state_session_path(session_id).with_name(f"compact_overlay_response_{safe_mode}.json")


def _surface_signature_from_payload(payload: Mapping[str, object]) -> str:
    return str(
        payload.get("last_display_surface_signature")
        or payload.get("last_window_surface_signature")
        or _as_mapping(payload.get("display_fast_path_v3")).get("surface_signature")
        or payload.get("overlay_source_window_signature")
        or ""
    ).strip()


def _load_persisted_compact_overlay_response(
    session_id: str,
    mode: str,
    display_payload: Mapping[str, object],
    *,
    now_epoch: float,
) -> dict[str, object] | None:
    path = _persisted_compact_overlay_response_path(session_id, mode)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, Mapping):
        return None
    payload = dict(cast(Mapping[str, object], raw))
    if _compact_live_state_renderable_count(payload) <= 0:
        return None
    display_signature = _surface_signature_from_payload(display_payload)
    cached_signature = str(payload.get("persisted_overlay_surface_signature") or "").strip()
    if display_signature and cached_signature and display_signature != cached_signature:
        return None
    if _compact_overlay_payload_stale_for_display(payload, display_payload):
        return None
    provider = _mapping_to_plain_dict(payload.get("provider_status"))
    provider.update(
        {
            "persisted_compact_overlay_warm_start_v3": True,
            "persisted_compact_overlay_age_ms": round(max(0.0, now_epoch - _epoch_float(payload.get("persisted_overlay_epoch"), now_epoch)) * 1000.0, 3),
        }
    )
    payload["provider_status"] = provider
    display_frame = int(
        _epoch_float(
            display_payload.get("display_frame_id") or display_payload.get("frame_index") or payload.get("frame_id"),
            0.0,
        )
    )
    if display_frame > 0:
        payload["frame_id"] = display_frame
        payload["display_frame_id"] = display_frame
    return payload


def _persist_compact_overlay_response(session_id: str, mode: str, payload: Mapping[str, object]) -> None:
    if _compact_live_state_renderable_count(payload) <= 0:
        return
    path = _persisted_compact_overlay_response_path(session_id, mode)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        row = dict(payload)
        display_signature = str(
            row.get("last_display_surface_signature")
            or row.get("last_window_surface_signature")
            or row.get("overlay_source_window_signature")
            or ""
        ).strip()
        if not display_signature:
            try:
                display_raw = json.loads(_direct_window_tracker_display_state_path(session_id).read_text(encoding="utf-8"))
            except Exception:
                display_raw = None
            if isinstance(display_raw, Mapping):
                display_signature = _surface_signature_from_payload(cast(Mapping[str, object], display_raw))
        if not display_signature:
            return
        row["persisted_overlay_surface_signature"] = display_signature
        row["persisted_overlay_epoch"] = time.time()
        tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp_path.write_text(json.dumps(row, separators=(",", ":"), default=str), encoding="utf-8")
        os.replace(tmp_path, path)
    except Exception:
        return


def _direct_market_registry_path(session_id: str) -> Path:
    relative_path = Path("market_registry") / f"{str(session_id or '').strip()}.jsonl"
    candidates = _runtime_data_dir_candidates()
    for data_dir in candidates:
        path = data_dir / relative_path
        if path.exists():
            return path
    return candidates[0] / relative_path


def _path_cache_signature(path: Path) -> str:
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    return f"{stat.st_mtime_ns}:{stat.st_size}"


def _json_field_cache_signature(path: Path, keys: Sequence[str]) -> str:
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _path_cache_signature(path)
    if not isinstance(raw, Mapping):
        return _path_cache_signature(path)
    payload: dict[str, object] = dict(cast(Mapping[str, object], raw))

    def nested_value(root: Mapping[str, object], dotted_key: str) -> object:
        current: object = root
        for part in dotted_key.split("."):
            current_dict = _as_object_dict(current)
            if current_dict is None:
                return None
            next_value: object = current_dict.get(part)
            current = next_value
        return current

    selected = {key: nested_value(payload, key) for key in keys}
    return json.dumps(selected, sort_keys=True, default=str, separators=(",", ":"))


def _direct_session_has_v3_overlay_sources(payload: Mapping[str, Any]) -> bool:
    counts = _direct_session_v3_overlay_source_counts(payload)
    if any(counts.get(key, 0) > 0 for key in counts):
        return True
    return False


def _direct_session_v3_overlay_source_counts(payload: Mapping[str, Any]) -> dict[str, int]:
    tracking = _as_mapping(payload.get("tracking_summary"))
    signal = _as_mapping(payload.get("latest_signal"))
    projection = _as_mapping(tracking.get("projection"))
    thesis = _as_mapping(payload.get("signal_thesis_v3") or signal.get("signal_thesis_v3"))
    source_keys = (
        "tracked_candles",
        "trendlines_v3",
        "structure_boxes",
        "historical_structure",
        "support_resistance_zones",
        "angle_vectors",
    )
    counts = {key: len(_as_sequence(tracking.get(key))) for key in source_keys}
    counts["projection_zones"] = len(_as_sequence(projection.get("zones")))
    counts["signal_thesis_fields"] = sum(
        1
        for key in ("entry", "target", "invalidation", "support", "resistance")
        if thesis.get(key) not in (None, "", [], {})
    )
    return counts


def _direct_session_needs_registry_context(payload: Mapping[str, Any], overlay_mode: str) -> bool:
    normalized_mode = normalize_view_mode(str(overlay_mode or "CLEAN_LIVE"))
    if normalized_mode not in {"CLEAN_LIVE", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "REPLAY"}:
        return False
    counts = _direct_session_v3_overlay_source_counts(payload)
    tracked_candles = counts.get("tracked_candles", 0)
    structural_count = sum(value for key, value in counts.items() if key != "tracked_candles")
    return bool(tracked_candles < 12 or structural_count <= 0)


def _direct_session_path_has_v3_overlay_sources(path: Path) -> bool:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(raw, Mapping):
        return False
    return _direct_session_has_v3_overlay_sources(cast(Mapping[str, Any], raw))


def _live_state_cache_signature(session_id: str, *, compact_public: bool = False) -> str:
    session_path = _direct_live_state_session_path(session_id)
    display_path = _direct_window_tracker_display_state_path(session_id)
    display_signature = _json_field_cache_signature(
        display_path,
        (
            "session_id",
            "frame_index",
            "chart_frame_id",
            "overlay_frame_id",
            "full_overlay_frame_id",
            "model_vote_frame_id",
            "last_display_window_path",
            "last_chart_path",
            "last_overlay_path",
            "last_full_overlay_path",
            "last_display_surface_signature",
            "last_window_surface_signature",
            "last_study_surface_signature",
            "overlay_source_window_signature",
            "overlay_source_study_signature",
            "display_snapshot_only_v3",
            "display_fast_path_v3.surface_signature",
        ),
    )
    if compact_public:
        context_path = _direct_live_state_compact_session_path(session_id)
        context_fields = (
            "session_id",
            "window_query",
            "locked_title",
            "locked_window.hwnd",
            "manual_focus_region.normalized_bbox",
            "tracking_summary.detected_market",
            "tracking_summary.detected_timeframe",
            "tracking_summary.market_selector_visual_fingerprint",
            "tracking_summary.market_selector_visual_changed",
            "tracking_summary.market_selector_rebind_required",
            "latest_signal.symbol",
            "latest_signal.pair",
            "latest_signal.market",
            "latest_signal.market_selector_visual_fingerprint",
            "latest_signal.market_selector_visual_changed",
            "latest_signal.market_selector_rebind_required",
            "latest_signal.timeframe",
            "broker_source.lock_id",
            "broker_source.status",
            "broker_source_lock.lock_id",
            "broker_source_lock.status",
        )
        context_signature = _json_field_cache_signature(
            context_path,
            context_fields,
        )
        registry_signature = ""
        if not _direct_session_path_has_v3_overlay_sources(context_path):
            registry_signature = f"|registry={_path_cache_signature(_direct_market_registry_path(session_id))}"
        return (
            f"compact=1|context={context_signature}"
            f"|display={display_signature}"
            f"{registry_signature}"
        )
    session_signature = _json_field_cache_signature(
        session_path,
        (
            "session_id",
            "tracking_enabled",
            "status",
            "frame_index",
            "capture_count",
            "state_version",
            "decision_version",
            "chart_frame_id",
            "overlay_frame_id",
            "full_overlay_frame_id",
            "model_vote_frame_id",
            "source_capture_id",
            "last_display_window_path",
            "last_chart_path",
            "last_overlay_path",
            "last_full_overlay_path",
            "last_display_surface_signature",
            "last_window_surface_signature",
            "last_study_surface_signature",
            "overlay_source_window_signature",
            "overlay_source_study_signature",
            "latest_signal.signal_id",
            "latest_signal.published_epoch",
            "latest_signal.model_council_packet.packet_id",
            "model_council_result.packet_id",
            "model_council_study_packet.packet_id",
            "model_council_packet.packet_id",
            "execution_packet.packet_id",
        ),
    )
    return f"session={session_signature}|display={display_signature}"


def _compact_live_state_response_cache_signature(session_id: str) -> str:
    display_path = _direct_window_tracker_display_state_path(session_id)
    display_signature = _json_field_cache_signature(
        display_path,
        (
            "session_id",
            "frame_index",
            "display_frame_id",
            "chart_frame_id",
            "overlay_frame_id",
            "full_overlay_frame_id",
            "model_vote_frame_id",
            "last_display_window_path",
            "last_display_surface_signature",
            "last_window_surface_signature",
            "last_study_surface_signature",
            "overlay_source_window_signature",
            "overlay_source_study_signature",
        ),
    )
    return f"display={display_signature}"


def _mapping_to_plain_dict(value: object) -> dict[str, object]:
    return dict(cast(Mapping[str, object], value)) if isinstance(value, Mapping) else {}


def _registry_entry_overlay_id(entry: Mapping[str, Any]) -> str:
    overlay_value: object = entry.get("overlay")
    overlay = _mapping_to_plain_dict(overlay_value)
    return str(
        entry.get("overlay_id")
        or overlay.get("overlay_id")
        or overlay.get("id")
        or overlay.get("key")
        or ""
    ).strip()


def _registry_entry_sort_key(entry: Mapping[str, Any]) -> tuple[float, str]:
    overlay = _mapping_to_plain_dict(entry.get("overlay"))
    epoch = max(
        _epoch_float(entry.get("updated_at"), 0.0),
        _epoch_float(entry.get("timestamp"), 0.0),
        _epoch_float(entry.get("last_seen_at"), 0.0),
        _epoch_float(overlay.get("updated_at"), 0.0),
        _epoch_float(overlay.get("timestamp"), 0.0),
    )
    text = str(entry.get("updated_at") or entry.get("timestamp") or entry.get("last_seen_at") or "").strip()
    return (epoch, text)


def _locked_registry_entries_from_entries(entries: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    latest_by_overlay: dict[str, Mapping[str, Any]] = {}
    for entry in entries:
        overlay_id = _registry_entry_overlay_id(entry)
        if not overlay_id:
            continue
        current = latest_by_overlay.get(overlay_id)
        if current is None or _registry_entry_sort_key(entry) >= _registry_entry_sort_key(current):
            latest_by_overlay[overlay_id] = entry
    locked: list[Mapping[str, Any]] = []
    for entry in latest_by_overlay.values():
        overlay = _mapping_to_plain_dict(entry.get("overlay"))
        lifecycle = str(entry.get("lifecycle_state") or overlay.get("lifecycle_state") or "").strip().upper()
        if lifecycle in {"BROKEN", "HIDDEN", "INVALIDATED", "MERGED"}:
            continue
        locked.append(entry)
    return locked


def _merge_direct_window_tracker_display_state(
    requested_session_id: str,
    payload: dict[str, object],
    *,
    display_path: Path | None = None,
) -> dict[str, object]:
    try:
        resolved_display_path = display_path or _direct_window_tracker_display_state_path(requested_session_id)
        display_raw = json.loads(resolved_display_path.read_text(encoding="utf-8"))
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
    if not display_state.get("last_display_window_path"):
        legacy_window_path = display_state.get("last_window_path") or display_state.get("last_frame_path")
        if legacy_window_path:
            display_state["last_display_window_path"] = legacy_window_path
    display_signature = str(
        display_state.get("last_display_surface_signature") or display_state.get("last_window_surface_signature") or ""
    ).strip()
    study_signature = str(display_state.get("last_study_surface_signature") or "").strip()
    for key, value in display_state.items():
        if key in _DIRECT_DISPLAY_STATE_KEYS:
            if key in _DIRECT_DISPLAY_STATE_NONEMPTY_STRING_KEYS:
                value_text = str(value or "").strip()
                if not value_text:
                    continue
                if key == "overlay_source_window_signature" and display_signature and value_text != display_signature:
                    continue
                if key == "overlay_source_study_signature" and study_signature and value_text != study_signature:
                    continue
            payload[str(key)] = value
    display_published = _epoch_float(
        display_state.get("display_published_epoch") or display_state.get("last_display_published_epoch"),
        0.0,
    )
    display_captured = _epoch_float(
        display_state.get("display_capture_epoch") or display_state.get("last_display_capture_epoch"),
        0.0,
    )
    display_window_path = str(
        display_state.get("last_display_window_path")
        or display_state.get("last_window_path")
        or display_state.get("last_frame_path")
        or ""
    ).strip()
    if display_published > 0.0:
        payload["last_capture_epoch"] = display_published
    if display_captured > 0.0:
        payload["last_capture_started_epoch"] = display_captured
    if display_window_path:
        payload["last_window_path"] = display_window_path
        payload["last_frame_path"] = display_window_path
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
    payload = cast(dict[str, object], compact_session_payload(raw_payload))
    payload = _merge_direct_window_tracker_display_state(
        requested_session_id,
        payload,
        display_path=path.with_name("display_state.json"),
    )
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


def _direct_model_council_fast_payload(session_id: str) -> dict[str, object] | None:
    requested_session_id = str(session_id or "").strip()
    if not requested_session_id:
        return None
    compact_path = _direct_live_state_compact_session_path(requested_session_id)
    session_path = _direct_live_state_session_path(requested_session_id)
    candidates: list[Path] = []
    try:
        compact_mtime = compact_path.stat().st_mtime if compact_path.exists() else 0.0
    except OSError:
        compact_mtime = 0.0
    try:
        session_mtime = session_path.stat().st_mtime if session_path.exists() else 0.0
    except OSError:
        session_mtime = 0.0
    if session_mtime > compact_mtime + 0.5:
        candidates.append(session_path)
    if compact_mtime > 0.0:
        candidates.append(compact_path)
    elif session_mtime > 0.0:
        candidates.append(session_path)
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(raw, Mapping):
            continue
        payload = dict(cast(Mapping[str, object], raw))
        if str(payload.get("session_id", requested_session_id) or requested_session_id) != requested_session_id:
            continue
        if payload.get("tracking_enabled") is False:
            continue
        return payload
    return None


def _direct_window_tracker_display_snapshot(
    session_id: str,
    *,
    require_overlay_model: bool = True,
) -> dict[str, object] | None:
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
    try:
        raw = json.loads(_direct_window_tracker_display_state_path(requested_session_id).read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(raw, Mapping):
        return None
    payload = dict(cast(Mapping[str, object], raw))
    display_frame = int(_epoch_float(payload.get("display_frame_id"), 0.0))
    frame_index = int(_epoch_float(payload.get("frame_index") or payload.get("chart_frame_id"), 0.0))
    raw_overlay_frame = int(_epoch_float(payload.get("overlay_frame_id") or payload.get("full_overlay_frame_id"), 0.0))
    raw_model_frame = int(_epoch_float(payload.get("model_vote_frame_id"), 0.0))
    if require_overlay_model and (raw_overlay_frame <= 0 or raw_model_frame <= 0):
        return None
    overlay_frame = int(
        _epoch_float(raw_overlay_frame or frame_index, 0.0)
    )
    model_frame = int(_epoch_float(raw_model_frame or frame_index or overlay_frame, 0.0))
    display_window = str(
        payload.get("last_display_window_path") or payload.get("last_window_path") or payload.get("last_frame_path") or ""
    ).strip()
    if display_frame <= 0 or not display_window:
        return None
    payload["session_id"] = requested_session_id
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    payload["display_frame_id"] = display_frame
    if overlay_frame > 0:
        payload["overlay_frame_id"] = overlay_frame
        payload["full_overlay_frame_id"] = overlay_frame
    if model_frame > 0:
        payload["model_vote_frame_id"] = model_frame
    payload.setdefault("frame_index", max(overlay_frame, model_frame))
    payload.setdefault("capture_count", display_frame)
    payload.setdefault("chart_frame_id", max(overlay_frame, model_frame))
    payload.setdefault("last_window_path", display_window)
    payload.setdefault("last_frame_path", display_window)
    display_published = _epoch_float(payload.get("display_published_epoch") or payload.get("last_display_published_epoch"), 0.0)
    display_capture = _epoch_float(payload.get("display_capture_epoch") or payload.get("last_display_capture_epoch"), 0.0)
    if display_published > 0.0:
        payload["last_capture_epoch"] = display_published
    if display_capture > 0.0:
        payload["last_capture_started_epoch"] = display_capture
    payload.setdefault("event_log_path", str(_direct_window_tracker_display_state_path(requested_session_id).with_name("events.jsonl")))
    payload.setdefault("next_capture_in_sec", 0.0)
    payload.setdefault("effective_capture_interval_sec", _WINDOW_TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC)
    return payload


def _direct_performance_trace_cache_ttl_sec() -> float:
    try:
        return max(
            0.0,
            float(os.getenv("PHOENIXGUARD_DIRECT_PERFORMANCE_TRACE_CACHE_TTL_SEC", "1.25") or "1.25"),
        )
    except ValueError:
        return 1.25


def _direct_performance_trace_direct_only() -> bool:
    return str(os.getenv("PHOENIXGUARD_PERFORMANCE_TRACE_DIRECT_ONLY", "1") or "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def _store_direct_performance_trace_cache(session_id: str, trace: Mapping[str, object], *, now_epoch: float) -> None:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return
    with _DIRECT_PERFORMANCE_TRACE_CACHE_LOCK:
        _DIRECT_PERFORMANCE_TRACE_CACHE[normalized_session_id] = (float(now_epoch), dict(trace))


def _bump_cached_age_ms(container: dict[str, object], key: str, elapsed_ms: int) -> None:
    number = _epoch_float(container.get(key), 0.0)
    if number <= 0.0:
        return
    container[key] = int(round(number + elapsed_ms))


def _cached_direct_performance_trace(session_id: str, *, now_epoch: float | None = None) -> dict[str, object] | None:
    normalized_session_id = str(session_id or "").strip()
    if not normalized_session_id:
        return None
    ttl_sec = _direct_performance_trace_cache_ttl_sec()
    if ttl_sec <= 0.0:
        return None
    now_value = time.time() if now_epoch is None else float(now_epoch)
    with _DIRECT_PERFORMANCE_TRACE_CACHE_LOCK:
        cached = _DIRECT_PERFORMANCE_TRACE_CACHE.get(normalized_session_id)
    if cached is None:
        return None
    cached_epoch, cached_trace = cached
    age_sec = max(0.0, now_value - float(cached_epoch))
    if age_sec > ttl_sec:
        return None
    elapsed_ms = int(round(age_sec * 1000.0))
    trace = dict(cached_trace)
    trace["generated_epoch"] = now_value
    trace["direct_trace_cache_reused_v3"] = {
        "schema_version": "PG_DIRECT_PERFORMANCE_TRACE_CACHE_V1",
        "reason": "direct_snapshot_read_race",
        "cached_age_ms": elapsed_ms,
        "ttl_ms": int(round(ttl_sec * 1000.0)),
    }
    for section_key in ("display_frame", "overlay_state", "model_state", "frontend_state"):
        section = trace.get(section_key)
        if isinstance(section, Mapping):
            section_copy = dict(cast(Mapping[str, object], section))
            _bump_cached_age_ms(section_copy, "age_ms", elapsed_ms)
            trace[section_key] = section_copy
    metrics = trace.get("metrics")
    if isinstance(metrics, Mapping):
        metrics_copy = dict(cast(Mapping[str, object], metrics))
        _bump_cached_age_ms(metrics_copy, "end_to_end_age_ms", elapsed_ms)
        trace["metrics"] = metrics_copy
    timing = trace.get("timing_trace")
    if isinstance(timing, Mapping):
        timing_copy = dict(cast(Mapping[str, object], timing))
        for key in (
            "frame_age_ms",
            "overlay_age_ms",
            "model_vote_age_ms",
            "frontend_render_age_ms",
            "state_publish_age_ms",
            "packet_age_ms",
        ):
            _bump_cached_age_ms(timing_copy, key, elapsed_ms)
        trace["timing_trace"] = timing_copy
    visual = trace.get("visual_health")
    if isinstance(visual, Mapping):
        visual_copy = dict(cast(Mapping[str, object], visual))
        for key in (
            "frame_age_ms",
            "overlay_age_ms",
            "model_vote_age_ms",
            "packet_age_ms",
            "frontend_render_age_ms",
        ):
            _bump_cached_age_ms(visual_copy, key, elapsed_ms)
        trace["visual_health"] = visual_copy
    return trace


def _direct_performance_overlay_rows(session_id: str, *, now_epoch: float) -> list[Mapping[str, Any]]:
    requested_session_id = str(session_id or "").strip()
    if not requested_session_id:
        return []
    with _LIVE_STATE_V3_CACHE_LOCK:
        cached_sources = _LIVE_STATE_REGISTRY_CACHE.get(requested_session_id)
        if cached_sources and now_epoch - cached_sources[0] <= _LIVE_STATE_REGISTRY_CACHE_TTL_SEC:
            registry_entries = list(cached_sources[2])
        else:
            registry_entries = []
    if not registry_entries:
        try:
            registry_entries = [
                item
                for item in load_recent_market_objects(
                    requested_session_id,
                    max_lines=_LIVE_STATE_REGISTRY_MAX_LINES,
                )
            ]
        except Exception:
            registry_entries = []
        if registry_entries:
            try:
                active_objects = [
                    item
                    for item in active_objects_from_entries(
                        registry_entries,
                        min_truth_score=0.0,
                        now_epoch=now_epoch,
                    )
                ]
            except Exception:
                active_objects = []
            with _LIVE_STATE_V3_CACHE_LOCK:
                _LIVE_STATE_REGISTRY_CACHE[requested_session_id] = (now_epoch, active_objects, registry_entries)
    rows: list[Mapping[str, Any]] = []
    for entry in _locked_registry_entries_from_entries(registry_entries):
        overlay = _mapping_to_plain_dict(entry.get("overlay"))
        if not overlay:
            continue
        overlay_id = _registry_entry_overlay_id(entry)
        if overlay_id and not overlay.get("overlay_id"):
            overlay["overlay_id"] = overlay_id
        rows.append(overlay)
    return rows


def _packet_id_from_endpoint(endpoint_result: Mapping[str, object]) -> str:
    payload = endpoint_result.get("payload")
    if not isinstance(payload, Mapping):
        return ""
    payload_mapping = cast(Mapping[str, object], payload)
    for key in ("authority_packet_id", "latest_execution_packet_id", "packet_id"):
        packet_id = payload_mapping.get(key)
        if packet_id:
            return str(packet_id)
    packet = payload_mapping.get("packet")
    if isinstance(packet, Mapping):
        packet_mapping = cast(Mapping[str, object], packet)
        return str(packet_mapping.get("id_short") or packet_mapping.get("packet_id") or "")
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
    published_epoch = max(
        [
            value
            for value in (
                _payload_created_epoch(latest_signal),
                _payload_created_epoch(result),
                _payload_created_epoch(packet),
                _epoch_float(payload.get("model_capture_epoch"), 0.0),
                _epoch_float(payload.get("display_published_epoch"), 0.0),
                _epoch_float(payload.get("last_display_published_epoch"), 0.0),
                _epoch_float(payload.get("last_capture_epoch"), 0.0),
            )
            if value > 0.0
        ],
        default=0.0,
    )
    max_latency = 0.0
    for value in pipeline.values():
        if isinstance(value, Mapping):
            row = cast(Mapping[str, object], value)
            max_latency = max(max_latency, _epoch_float(row.get("duration_ms"), 0.0))
    roles = [
        "global_structure",
        "local_micro_structure",
        "zone_liquidity",
        "angle_dynamics",
        "historical_pattern",
        "risk_opposing_force",
        "arbitration_synthesis",
    ]
    has_model_state = bool(
        result
        or packet
        or latest_signal
        or int(_epoch_float(payload.get("model_vote_frame_id"), 0.0)) > 0
    )
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
    schema_version = str(normalized.get("schema_version") or "").strip()
    if schema_version == "PG_SHOOTER_PACKAGE_REPORTER_HEARTBEAT_V1":
        updated_epoch = _epoch_float(normalized.get("updated_epoch_sec") or normalized.get("updated_epoch"), 0.0)
        if updated_epoch <= 0.0:
            raise KeyError("Shooter package reporter heartbeat has no freshness timestamp.")
        if time.time() > updated_epoch + _PUBLISHED_PACKET_FALLBACK_TTL_SEC:
            raise KeyError("Shooter package reporter heartbeat is stale.")
    if schema_version == "PG_SHOOTER_PACKAGE_REPORT_V1":
        updated_epoch = _epoch_float(normalized.get("updated_epoch_sec"), 0.0)
        fallback_valid_until = updated_epoch + _PUBLISHED_PACKET_FALLBACK_TTL_SEC if updated_epoch > 0.0 else 0.0
        valid_until = _epoch_float(normalized.get("valid_until_epoch_sec"), fallback_valid_until)
        if valid_until > 0.0 and time.time() > valid_until:
            raise KeyError("Shooter package report is stale.")
        allowance = _as_object_mapping(normalized.get("allowance_package") or normalized.get("allowed_package"))
        package_type = str(allowance.get("package_type") or "").strip().upper()
        if allowance.get("schema_version") != "PG_ALLOWANCE_PACKAGE_V1":
            raise KeyError("Shooter package report allowance schema mismatch.")
        if package_type not in {"INTRADAY_ENTER_NOW", "SWING"}:
            raise KeyError("Shooter package report allowance type is not allowed.")
        if allowance.get("execution_authority") != "PG_EXECUTION_PACKET_V3":
            raise KeyError("Shooter package report authority mismatch.")
        if allowance.get("accepted") is not True or allowance.get("execution_ready") is not True:
            raise KeyError("Shooter package report allowance is not execution-ready.")
        if package_type == "INTRADAY_ENTER_NOW" and allowance.get("entry_now_allowed") is not True:
            raise KeyError("Shooter package report intraday package is not entry-now allowed.")
    return normalized


def _missing_shooter_handshake_state(
    session_id: str | None = None,
    *,
    detail: str = "Shooter handshake not found.",
) -> dict[str, object]:
    return {
        "session_id": str(session_id or "").strip(),
        "state": "WAITING",
        "mode": "PACKAGE_REPORTER",
        "available": False,
        "reason": detail,
        "execution_removed": True,
        "broker_click_allowed": False,
        "will_click": False,
        "next_required": "fresh accepted intraday or swing allowance package",
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


def _normalize_v3_artifact_mode(value: object) -> str:
    text = str(value or "").strip().upper().replace("-", "_")
    if text in {
        "CLEAN_LIVE",
        "CANDLES",
        "GLOBAL",
        "LOCAL",
        "SUPPLY_DEMAND",
        "TRENDLINES",
        "TRIGGER",
        "TARGET",
        "INVALIDATION",
        "PATH",
        "ACTIVE_CONTEXT",
        "COUNCIL",
        "FULL_HISTORY_READ",
        "BROKER",
        "TWO_CANDLE_STUDY",
        "LSTM_STUDY",
        "DIAGNOSTICS",
        "CALIBRATION",
        "REPLAY",
    }:
        return text
    select_map = {
        "clean_live": "CLEAN_LIVE",
        "active_context": "ACTIVE_CONTEXT",
        "full_history_read": "FULL_HISTORY_READ",
        "supply_demand": "SUPPLY_DEMAND",
        "trendlines": "TRENDLINES",
        "triggers": "TRIGGER",
        "targets": "TARGET",
        "invalidation": "INVALIDATION",
        "replay": "REPLAY",
        "smc_council": "COUNCIL",
        "deep_debug": "DIAGNOSTICS",
    }
    return select_map.get(str(value or "").strip().lower().replace("-", "_"), "CLEAN_LIVE")


def _sequence_mappings(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    items = cast(Sequence[object], value)
    return [cast(Mapping[str, Any], item) for item in items if isinstance(item, Mapping)]


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
        "last_display_window_path",
        "last_window_path",
        "last_chart_path",
        "last_overlay_path",
        "last_full_overlay_path",
        "capture_once_result",
    )
    compact: dict[str, object] = {key: payload.get(key) for key in keep_keys if key in payload}
    latest_signal = payload.get("latest_signal")
    if isinstance(latest_signal, Mapping):
        latest_signal_row = cast(Mapping[str, object], latest_signal)
        compact["latest_signal"] = {
            key: latest_signal_row.get(key)
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
            if key in latest_signal_row
        }
    tracking_summary = payload.get("tracking_summary")
    if isinstance(tracking_summary, Mapping):
        tracking_summary_row = cast(Mapping[str, object], tracking_summary)
        compact["tracking_summary"] = {
            key: tracking_summary_row.get(key)
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
            if key in tracking_summary_row
        }
    broker_execution_state = payload.get("broker_execution_state")
    if isinstance(broker_execution_state, Mapping):
        broker_execution_row = cast(Mapping[str, object], broker_execution_state)
        compact["broker_execution_state"] = {
            key: broker_execution_row.get(key)
            for key in ("status", "side", "lane", "message", "actionable", "enabled", "mode", "expiry_seconds")
            if key in broker_execution_row
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


window_tracker_service = _window_tracker_service


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
    locked_hwnd: int = Field(default=0, ge=0)
    locked_title: str = ""
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


class WindowTrackerLockedWindowRequest(BaseModel):
    locked_hwnd: int = Field(default=0, ge=0)
    locked_title: str = ""


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
    two_candle_execution_allowed: bool | None = None
    swing_fallback_enabled: bool | None = None
    continuous_model_feed_enabled: bool | None = None
    model_confidence_floor: float | None = Field(default=None, ge=0.0, le=1.0)
    high_frequency_min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    high_frequency_entry_grace_sec: float | None = Field(default=None, ge=0.0, le=180.0)
    high_frequency_expiry_seconds: int | None = Field(default=None, ge=60, le=3600)
    high_frequency_horizon_candles: int | None = Field(default=None, ge=1, le=12)
    execution_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    overlay_min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    ai_contribution_strengths: dict[str, float] | None = None
    execution_lane_thresholds: dict[str, float] | None = None
    model_strength_profile: dict[str, object] | None = None
    allow_live_momentum_entries: bool | None = None
    allow_opposing_force_reactions: bool | None = None
    scenario_generation_enabled: bool | None = None
    auto_memory_projection: bool | None = None
    require_memory_projection: bool | None = None
    live_momentum_memory_advisory: bool | None = None
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
    min_primary_target_candles: int | None = Field(default=None, ge=1, le=72)
    max_primary_target_candles: int | None = Field(default=None, ge=1, le=120)
    min_location_sniper_target_candles: int | None = Field(default=None, ge=1, le=36)
    live_max_tracked_candles: int | None = Field(default=None, ge=8, le=256)
    support_resistance_max_zones_per_role: int | None = Field(default=None, ge=2, le=12)
    support_resistance_max_total_zones: int | None = Field(default=None, ge=4, le=24)
    support_resistance_max_significant_zones: int | None = Field(default=None, ge=4, le=24)
    smart_money_max_liquidity_pools: int | None = Field(default=None, ge=4, le=24)
    min_live_momentum_visible_candles: int | None = Field(default=None, ge=1, le=64)
    min_live_momentum_score: float | None = Field(default=None, ge=0.0, le=1.0)
    min_live_momentum_alignment: int | None = Field(default=None, ge=1, le=10)
    min_opposing_force_reaction_score: float | None = Field(default=None, ge=0.0, le=1.0)
    min_opposing_force_reaction_alignment: int | None = Field(default=None, ge=1, le=10)
    min_opposing_force_reaction_risk: float | None = Field(default=None, ge=0.0, le=1.0)
    min_opposing_force_reaction_entry_score: float | None = Field(default=None, ge=0.0, le=1.0)
    max_opposing_force_reaction_distance: float | None = Field(default=None, ge=0.0, le=1.0)
    min_dominance_margin: float | None = Field(default=None, ge=0.0, le=1.0)
    flip_flop_release_stable_reads: int | None = Field(default=None, ge=1, le=10)
    flip_flop_release_candidate_flips: int | None = Field(default=None, ge=0, le=10)
    reversal_capture_min_dominance: float | None = Field(default=None, ge=0.0, le=1.0)
    opportunity_capture_stable_reads: int | None = Field(default=None, ge=1, le=10)
    opportunity_capture_min_score: float | None = Field(default=None, ge=0.0, le=1.0)
    packet_valid_for_seconds: float | None = Field(default=None, ge=1.0, le=300.0)
    study_packet_valid_for_seconds: float | None = Field(default=None, ge=5.0, le=900.0)
    min_conf_global: float | None = Field(default=None, ge=0.0, le=1.0)
    min_conf_latest: float | None = Field(default=None, ge=0.0, le=1.0)
    history_depth: int | None = Field(default=None, ge=1, le=24)
    label_density: int | None = Field(default=None, ge=1, le=30)
    projection_focus: float | None = Field(default=None, ge=0.0, le=1.0)
    debug_depth: int | None = Field(default=None, ge=0, le=24)
    fuse_timeframe_overlays: bool | None = None
    min_actionable_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    min_thesis_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    signal_cooldown_sec: float | None = Field(default=None, ge=0.0, le=300.0)
    rl_track_interval_sec: float | None = Field(default=None, ge=0.05, le=300.0)
    consensus_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    gates_pass_minimum: int | None = Field(default=None, ge=1, le=20)
    conformal_max_interval_pct: float | None = Field(default=None, ge=0.0, le=1.0)
    risk_min_pct: float | None = Field(default=None, ge=0.0, le=10.0)
    risk_max_pct: float | None = Field(default=None, ge=0.0, le=10.0)
    recall_boost_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    recall_veto_threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    use_macro_local_alignment_gate: bool | None = None
    use_opposition_strength_gate: bool | None = None
    use_memory_ambiguity_penalty: bool | None = None
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


def _bounded_overlay_editor_number(raw: object, fallback: float, minimum: float, maximum: float) -> float:
    value = _epoch_float(raw, fallback)
    if value == fallback and raw != fallback:
        return fallback
    if not (minimum <= value <= maximum):
        return max(minimum, min(maximum, value))
    return value


def _overlay_editor_hex(raw: object, fallback: str) -> str:
    text = str(raw or "").strip().lower()
    return text if re.fullmatch(r"#[0-9a-f]{6}", text) else fallback


def _sanitize_overlay_editor_settings(raw: Mapping[str, object] | None, *, profile_saved: bool = False) -> dict[str, object]:
    payload: Mapping[str, object] = raw or {}
    raw_colors = payload.get("colors")
    colors: Mapping[str, object] = _as_object_mapping(raw_colors)
    return {
        "schemaVersion": _OVERLAY_EDITOR_SETTINGS_SCHEMA_VERSION,
        "profileSaved": bool(profile_saved or payload.get("profileSaved") is True),
        "panelOpen": bool(payload.get("panelOpen") is True),
        "opacityScale": _bounded_overlay_editor_number(payload.get("opacityScale"), 1.0, 0.20, 1.25),
        "borderScale": _bounded_overlay_editor_number(payload.get("borderScale"), 1.0, 0.45, 1.80),
        "lineScale": _bounded_overlay_editor_number(payload.get("lineScale"), 1.0, 0.40, 2.20),
        "fillScale": _bounded_overlay_editor_number(payload.get("fillScale"), 1.0, 0.0, 2.20),
        "labelScale": _bounded_overlay_editor_number(payload.get("labelScale"), 1.0, 0.60, 1.85),
        "labelOpacity": _bounded_overlay_editor_number(payload.get("labelOpacity"), 1.0, 0.10, 1.20),
        "labelMaxWidth": _bounded_overlay_editor_number(payload.get("labelMaxWidth"), 86.0, 48.0, 180.0),
        "hideLabels": bool(payload.get("hideLabels") is True),
        "labelsOnHover": bool(payload.get("labelsOnHover") is True),
        "panelX": None
        if payload.get("panelX") is None
        else _bounded_overlay_editor_number(payload.get("panelX"), 18.0, 0.0, 10000.0),
        "panelY": None
        if payload.get("panelY") is None
        else _bounded_overlay_editor_number(payload.get("panelY"), 78.0, 0.0, 10000.0),
        "panelLocked": bool(payload.get("panelLocked") is True),
        "layers": {},
        "colors": {
            "demand": _overlay_editor_hex(colors.get("demand"), "#4ed2ff"),
            "supply": _overlay_editor_hex(colors.get("supply"), "#f8ca5c"),
            "trigger": _overlay_editor_hex(colors.get("trigger"), "#b99aff"),
            "target": _overlay_editor_hex(colors.get("target"), "#4db9ff"),
            "invalid": _overlay_editor_hex(colors.get("invalid"), "#f5d778"),
            "council": _overlay_editor_hex(colors.get("council"), "#ffffff"),
        },
    }


def _read_overlay_editor_settings() -> dict[str, object]:
    try:
        raw = json.loads(_WINDOW_TRACKER_OVERLAY_EDITOR_SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _sanitize_overlay_editor_settings({}, profile_saved=False)
    if not isinstance(raw, Mapping):
        return _sanitize_overlay_editor_settings({}, profile_saved=False)
    return _sanitize_overlay_editor_settings(cast(Mapping[str, object], raw), profile_saved=True)


def _write_overlay_editor_settings(raw: Mapping[str, object]) -> dict[str, object]:
    settings = _sanitize_overlay_editor_settings(raw, profile_saved=True)
    settings["savedAtEpoch"] = time.time()
    _WINDOW_TRACKER_FLOATING_WINDOWS_DIR.mkdir(parents=True, exist_ok=True)
    tmp_path = _WINDOW_TRACKER_OVERLAY_EDITOR_SETTINGS_PATH.with_suffix(".json.tmp")
    tmp_path.write_text(json.dumps(settings, indent=2, sort_keys=True), encoding="utf-8")
    tmp_path.replace(_WINDOW_TRACKER_OVERLAY_EDITOR_SETTINGS_PATH)
    return settings


def _render_window_tracker_dashboard(session_id: str) -> str:
    template = _WINDOW_TRACKER_DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    return (
        template.replace("__SESSION_ID_JSON__", json.dumps(str(session_id)))
        .replace("__OVERLAY_EDITOR_SETTINGS_JSON__", json.dumps(_read_overlay_editor_settings()))
        .replace("__MODEL_STRENGTH_SETTINGS_JSON__", json.dumps(read_model_strength_settings()))
        .replace("__SESSION_LABEL__", str(session_id))
    )


def create_app(
    service: MobileApiService | None = None,
    observer_service: SignalObserverService | None = None,
    window_tracker_service: object | None = None,
    voice_config: VoiceConfig | None = None,
) -> FastAPI:
    resolved_voice_config = voice_config or VOICE
    configure_tracing("phoenixguard-mobile-api", service_version="1.0.0")
    with _LIVE_STATE_V3_CACHE_LOCK:
        _COMPACT_LIVE_STATE_RESPONSE_CACHE.clear()
    app = FastAPI(
        title="PhoenixGuard Mobile API",
        version="1.0.0",
        summary="Android-facing quartet analysis API and continuous observer surface for PhoenixGuard.",
    )
    app.state.mobile_service = service
    app.state.observer_service = observer_service
    app.state.window_tracker_service = window_tracker_service
    app.state.voice_config = resolved_voice_config
    register_business_routes(app)
    explicit_window_tracker_service = window_tracker_service is not None

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
        return cast(ContinuousWindowTrackerService, market_window_tracker)

    def read_window_tracker_session(session_id: str) -> dict[str, object]:
        tracker_service = get_window_tracker_service()
        if not explicit_window_tracker_service:
            direct_snapshot = _direct_window_tracker_session_snapshot(session_id)
            if direct_snapshot is not None:
                return direct_snapshot
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
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    async def runtime_python_environment_v3() -> dict[str, object]:
        return dict(build_python_environment_status(PROJECT_ROOT))

    app.add_api_route(
        "/v1/mobile/runtime/python-environment/v3",
        runtime_python_environment_v3,
        methods=["GET"],
    )

    @app.get("/v1/mobile/chart/state/v3")
    def v3_chart_state(session_id: str | None = None) -> dict[str, object]:
        # Return frame state including cache-busted URL for latest frame image
        try:
            tracker = get_window_tracker_service()
            resolved = resolve_model_council_session_payload(session_id)
            sid = str(resolved.get("session_id") or session_id or "")
            if not sid:
                sid = resolve_window_tracker_dashboard_session_id(None)
            frame_id = int(_epoch_float(
                resolved.get("display_frame_id")
                or resolved.get("frame_index")
                or resolved.get("capture_count")
                or 0,
                0.0,
            ))
            mtime = 0.0
            # latest artifact path
            try:
                path = tracker.latest_artifact_path(sid, "window")
                if not path.exists():
                    path = tracker.latest_artifact_path(sid, "chart")
                exists = path.exists()
                mtime = path.stat().st_mtime if exists else 0.0
                url = f"/v1/mobile/frame/latest.png?session_id={sid}&t={int(mtime)}"
            except Exception:
                exists = False
                url = ""
            return {
                "schema_version": "V3_CHART_STATE",
                "session_id": sid,
                "frame_id": frame_id,
                "frame_exists": exists,
                "frame_url": url,
                "frame_timestamp": float(mtime),
                "artifact_version_mtime": float(mtime),
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
                payload: object = json.loads(resp.read().decode("utf-8"))
            return dict(_as_object_mapping(payload))
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

    def latest_model_council_state_from_live_session(session_id: str) -> dict[str, object]:
        if not explicit_window_tracker_service:
            direct_payload = _direct_model_council_fast_payload(session_id)
            if direct_payload is not None:
                result = _mapping_to_plain_dict(direct_payload.get("model_council_result"))
                study_packet = model_council_study_packet_from_payload(cast(Mapping[str, Any], direct_payload))
                packet = model_council_packet_from_payload(cast(Mapping[str, Any], direct_payload))
                if result or study_packet or packet:
                    return {
                        "session_id": str(direct_payload.get("session_id", session_id) or session_id),
                        "model_council_result": result,
                        "model_council_study_packet": study_packet,
                        "model_council_packet": packet,
                        "execution_packet_present": bool(packet),
                        "execution_packet_id": str(packet.get("packet_id", "") or "") if packet else "",
                        "promotion_trace": _mapping_to_plain_dict(
                            result.get("promotion_trace")
                            or study_packet.get("promotion_trace")
                            or _mapping_to_plain_dict(result.get("model_council")).get("promotion_trace")
                        ),
                    }
        payload = resolve_model_council_session_payload(session_id)
        result = _mapping_to_plain_dict(payload.get("model_council_result"))
        study_packet = model_council_study_packet_from_payload(cast(Mapping[str, Any], payload))
        packet = model_council_packet_from_payload(cast(Mapping[str, Any], payload))
        if not result and not study_packet and not packet:
            return cast(dict[str, object], get_window_tracker_service().latest_model_council_state(session_id))
        return {
            "session_id": str(payload.get("session_id", session_id) or session_id),
            "model_council_result": result,
            "model_council_study_packet": study_packet,
            "model_council_packet": packet,
            "execution_packet_present": bool(packet),
            "execution_packet_id": str(packet.get("packet_id", "") or "") if packet else "",
            "promotion_trace": _mapping_to_plain_dict(
                result.get("promotion_trace")
                or study_packet.get("promotion_trace")
                or _mapping_to_plain_dict(result.get("model_council")).get("promotion_trace")
            ),
        }

    def latest_model_council_study_packet_from_live_session(session_id: str) -> dict[str, object]:
        if not explicit_window_tracker_service:
            direct_payload = _direct_model_council_fast_payload(session_id)
            if direct_payload is not None:
                direct_packet = model_council_study_packet_from_payload(cast(Mapping[str, Any], direct_payload))
                if direct_packet:
                    return cast(dict[str, object], direct_packet)
                raise KeyError(session_id)
        payload = resolve_model_council_session_payload(session_id)
        packet = model_council_study_packet_from_payload(cast(Mapping[str, Any], payload))
        if not packet:
            packet = get_window_tracker_service().latest_model_council_study_packet(session_id)
        return cast(dict[str, object], packet)

    def latest_model_council_execution_packet_from_live_session(session_id: str) -> dict[str, object]:
        if not explicit_window_tracker_service:
            direct_payload = _direct_model_council_fast_payload(session_id)
            if direct_payload is not None:
                direct_packet = model_council_packet_from_payload(cast(Mapping[str, Any], direct_payload))
                if direct_packet:
                    return cast(dict[str, object], direct_packet)
                raise KeyError(session_id)
        payload = resolve_model_council_session_payload(session_id)
        packet = model_council_packet_from_payload(cast(Mapping[str, Any], payload))
        if not packet:
            packet = get_window_tracker_service().latest_model_council_packet(session_id)
        return cast(dict[str, object], packet)

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

    def _direct_live_state_v3_for_session(
        requested_session_id: str,
        now_epoch: float,
        overlay_mode: str,
        *,
        compact_public: bool = False,
    ) -> dict[str, object] | None:
        direct_started = time.perf_counter()
        timing_marks: list[tuple[str, float]] = [("start", direct_started)]

        def mark_timing(name: str) -> None:
            timing_marks.append((name, time.perf_counter()))

        def timing_steps() -> dict[str, float]:
            steps: dict[str, float] = {}
            previous = timing_marks[0][1]
            for name, current in timing_marks[1:]:
                steps[name] = round((current - previous) * 1000.0, 3)
                previous = current
            return steps

        if str(os.getenv("PHOENIXGUARD_LIVE_STATE_DIRECT_READ", "1") or "1").strip().lower() in {"0", "false", "off", "no"}:
            return None
        path = _direct_live_state_session_path(requested_session_id)
        raw_session: object
        if compact_public:
            context_path = _direct_live_state_compact_session_path(requested_session_id)
            raw_context: dict[str, object] = {}
            try:
                raw_display = json.loads(_direct_window_tracker_display_state_path(requested_session_id).read_text(encoding="utf-8"))
            except Exception:
                raw_display = None
            raw_display_mapping: Mapping[str, object]
            if isinstance(raw_display, Mapping):
                raw_display_mapping = cast(Mapping[str, object], raw_display)
            else:
                raw_display_mapping = {}
            if (
                _epoch_float(raw_display_mapping.get("frame_index"), 0.0) > 0.0
                and str(raw_display_mapping.get("last_display_window_path") or raw_display_mapping.get("last_window_path") or "").strip()
            ):
                try:
                    raw_context_payload = json.loads(context_path.read_text(encoding="utf-8"))
                except Exception:
                    raw_context_payload = None
                if isinstance(raw_context_payload, Mapping):
                    raw_context = cast(dict[str, object], compact_session_payload(cast(Mapping[str, Any], raw_context_payload)))
                raw_session = {**raw_context, **dict(raw_display_mapping)}
            else:
                try:
                    raw_context_payload = json.loads(context_path.read_text(encoding="utf-8"))
                except Exception:
                    raw_context_payload = None
                if isinstance(raw_context_payload, Mapping):
                    raw_context = cast(dict[str, object], compact_session_payload(cast(Mapping[str, Any], raw_context_payload)))
                if raw_context:
                    raw_session = raw_context
                else:
                    return None
        else:
            try:
                raw_session = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return None
        mark_timing("read_session")
        if not isinstance(raw_session, Mapping):
            return None
        session_payload = _merge_direct_window_tracker_display_state(
            requested_session_id,
            dict(cast(Mapping[str, object], raw_session)),
            display_path=path.with_name("display_state.json"),
        )
        session_payload.setdefault(
            "effective_capture_interval_sec",
            session_payload.get("capture_interval_sec", _WINDOW_TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC),
        )
        mark_timing("merge_display")
        artifacts: dict[str, str] = {}
        for kind, key in {
            "window": "last_display_window_path",
            "chart": "last_chart_path",
            "overlay": "last_overlay_path",
            "full-overlay": "last_full_overlay_path",
            "projection": "last_projection_path",
            "memory-reference": "last_memory_reference_path",
        }.items():
            value = str(session_payload.get(key, "") or "").strip()
            if not value and kind == "window":
                value = str(session_payload.get("last_window_path") or session_payload.get("last_frame_path") or "").strip()
            if value:
                artifacts[kind] = value
        mark_timing("artifacts")
        registry_source = "skipped_session_v3_overlay_sources"
        active_objects: list[Mapping[str, Any]] = []
        registry_entries: list[Mapping[str, Any]] = []
        direct_has_overlay_sources = _direct_session_has_v3_overlay_sources(cast(Mapping[str, Any], session_payload))
        direct_needs_registry_context = _direct_session_needs_registry_context(
            cast(Mapping[str, Any], session_payload),
            overlay_mode,
        )
        with _LIVE_STATE_V3_CACHE_LOCK:
            if not direct_has_overlay_sources or direct_needs_registry_context:
                cached_sources = _LIVE_STATE_REGISTRY_CACHE.get(requested_session_id)
                if cached_sources and now_epoch - cached_sources[0] <= _LIVE_STATE_REGISTRY_CACHE_TTL_SEC:
                    active_objects = list(cached_sources[1])
                    registry_entries = list(cached_sources[2])
                    registry_source = "cache"
                else:
                    try:
                        registry_entries = [
                            item
                            for item in load_recent_market_objects(
                                requested_session_id,
                                max_lines=_LIVE_STATE_REGISTRY_MAX_LINES,
                            )
                        ]
                    except Exception:
                        registry_entries = []
                    active_objects = [
                        item
                        for item in active_objects_from_entries(
                            registry_entries,
                            min_truth_score=0.0,
                            now_epoch=now_epoch,
                        )
                    ]
                    _LIVE_STATE_REGISTRY_CACHE[requested_session_id] = (now_epoch, active_objects, registry_entries)
                    registry_source = "registry_context_for_thin_direct_sources" if direct_has_overlay_sources else "legacy_registry"
        if compact_public and registry_entries:
            active_objects = _locked_registry_entries_from_entries(registry_entries)
        mark_timing("registry")
        model_health = _live_model_health_summary(cast(Mapping[str, object], session_payload))
        mark_timing("model_health")
        shooter_state = _latest_shooter_handshake_or_waiting(requested_session_id)
        mark_timing("shooter")
        frontend_heartbeat = latest_frontend_heartbeat(requested_session_id)
        mark_timing("frontend_heartbeat")
        live_state = cast(
            dict[str, object],
            build_live_state_v3(
                session_payload,
                artifacts=artifacts,
                model_health=model_health,
                shooter_state=shooter_state,
                active_objects=active_objects,
                registry_entries=registry_entries,
                frontend_heartbeat=frontend_heartbeat,
                now_epoch=now_epoch,
                overlay_mode=overlay_mode,
                compact_public=compact_public,
            ),
        )
        mark_timing("build")
        live_state["provider_status"] = {
            **_mapping_to_plain_dict(live_state.get("provider_status")),
            "live_state_source": "direct_file",
            "direct_duration_ms": round((time.perf_counter() - direct_started) * 1000.0, 3),
            "direct_steps_ms": timing_steps(),
            "direct_registry_source": registry_source,
            "direct_registry_entries": len(registry_entries),
        }
        live_visual_state = live_state.get("live_visual_state")
        if isinstance(live_visual_state, Mapping):
            live_visual = dict(cast(Mapping[str, object], live_visual_state))
            live_visual["provider_status"] = live_state["provider_status"]
            live_state["live_visual_state"] = live_visual
        return live_state

    def compact_live_state_response(live_state: Mapping[str, object]) -> dict[str, object]:
        def compact_mapping(value: object, fields: set[str]) -> dict[str, object]:
            row = _mapping_to_plain_dict(value)
            return {
                key: row.get(key)
                for key in fields
                if row.get(key) not in (None, "", [], {})
            }

        def compact_promotion_trace(value: object) -> dict[str, object]:
            return compact_mapping(
                value,
                {
                    "state",
                    "final_state",
                    "promotion_result",
                    "denied_at",
                    "next_required",
                    "true_blocker",
                    "first_reason",
                    "runtime_release_condition",
                    "exact_field_preventing_execution_packet",
                },
            )

        overlay_object_fields = {
            "schema_version",
            "overlay_id",
            "id",
            "object_id",
            "track_id",
            "type",
            "side",
            "layer",
            "role",
            "display_label",
            "short_label",
            "label",
            "label_hidden",
            "label_visible",
            "label_anchor",
            "label_lane",
            "label_bounds",
            "display_state",
            "visual_weight",
            "style",
            "visible_modes",
            "visible_default",
            "bbox",
            "bounds",
            "coordinate_mode",
            "anchor_type",
            "line_points",
            "points",
            "path",
            "anchors",
            "start_point",
            "end_point",
            "truth_score",
            "confidence",
            "frame_id",
            "sequence_id",
            "chart_transform_id",
            "lifecycle_state",
            "ttl_ms",
            "reason",
            "source_agent",
            "source_version",
            "source_path",
            "source_key",
            "broker_source_lock_id",
            "anchor_candles",
            "anchor_candle_indices",
            "anchor_price_band",
            "anchor_time_span",
            "anchor_evidence",
            "anchor_evidence_status",
            "touch_points",
            "trendline_touch_points",
            "touch_count",
            "wick_probe_count",
            "line_obstruction_count",
            "body_cross_fraction",
            "close_distance_norm",
            "parent_overlay_id",
            "parent_type",
            "parent_label",
            "nesting_depth",
            "nesting_role",
            "group_id",
            "group_type",
            "replay_sequence",
            "replay_action",
            "story",
            "trendline_role",
            "semantic_family",
        }

        def compact_overlay_object(value: object) -> dict[str, object]:
            row = normalize_v3_overlay_object(_mapping_to_plain_dict(value), strict=False)

            def compact_anchor_candles() -> list[int]:
                raw_anchors = row.get("anchor_candles")
                if isinstance(raw_anchors, Sequence) and not isinstance(raw_anchors, (str, bytes, bytearray)):
                    anchors: list[int] = []
                    for item in cast(Sequence[object], raw_anchors):
                        if not isinstance(item, (int, float, str)):
                            continue
                        try:
                            anchors.append(int(float(item)))
                        except Exception:
                            continue
                    return sorted(set(anchors))
                candidates: list[int] = []
                for key in ("source_key", "track_id", "replay_sequence"):
                    text = str(row.get(key) or "").strip()
                    if not text:
                        continue
                    if re.fullmatch(r"\d+(?:\.0+)?", text):
                        candidates.append(int(float(text)))
                        continue
                    if key in {"source_key", "replay_sequence"}:
                        candidates.extend(int(match) for match in re.findall(r"\d+", text))
                source_path = str(row.get("source_path") or "").strip()
                if source_path:
                    candidates.extend(int(match) for match in re.findall(r"\[(\d+)\]", source_path))
                return sorted(set(value for value in candidates if value >= 0))

            required_overlay_fields = set(REQUIRED_FIELDS)
            compact_row = {
                key: row.get(key)
                for key in overlay_object_fields
                if key in row and (key in required_overlay_fields or row.get(key) not in (None, "", [], {}))
            }
            compact_row.setdefault("anchor_candles", compact_anchor_candles())
            bounds = row.get("bounds") or row.get("bbox")
            if bounds not in (None, "", [], {}):
                compact_row.setdefault("bounds", bounds)
                compact_row.setdefault("bbox", bounds)
            if str(row.get("type") or "").upper() in {
                "SUPPORT_TRENDLINE",
                "RESISTANCE_TRENDLINE",
                "INNER_TRENDLINE",
            }:
                anchor_evidence = _mapping_to_plain_dict(row.get("anchor_evidence"))
                evidence_touch_points = anchor_evidence.get("touch_points")
                if (
                    evidence_touch_points not in (None, "", [], {})
                    and compact_row.get("touch_points") in (None, "", [], {})
                ):
                    compact_row["touch_points"] = evidence_touch_points
                if (
                    evidence_touch_points not in (None, "", [], {})
                    and compact_row.get("trendline_touch_points") in (None, "", [], {})
                ):
                    compact_row["trendline_touch_points"] = evidence_touch_points
            return compact_row

        def compact_overlay_objects(value: object) -> list[object]:
            if not isinstance(value, list):
                return []
            items = cast(Sequence[object], value)
            rows: list[object] = []
            for item in items:
                if isinstance(item, Mapping):
                    rows.append(compact_overlay_object(cast(Mapping[str, object], item)))
            return rows

        def compact_overlays_payload(value: object) -> dict[str, object]:
            overlays = _mapping_to_plain_dict(value)
            objects = compact_overlay_objects(overlays.get("objects"))
            all_objects = compact_overlay_objects(overlays.get("all_objects"))
            output = compact_mapping(
                overlays,
                {
                    "requested_mode",
                    "active_mode",
                    "visible_layers",
                    "overlay_count",
                    "renderable_count",
                    "hidden_count",
                    "rejected_count",
                    "artifact_frame_id",
                    "overlay_object_frame_id",
                    "artifact_frame_aligned",
                    "artifact_authority_locked",
                    "artifact_mismatch_reason",
                    "reason_if_empty",
                    "unknown_or_unmapped_terms",
                    "overlay_state_version",
                    "overlay_frame_state_version",
                    "precision_audit",
                    "warnings",
                },
            )
            if objects:
                output["objects"] = objects
            else:
                output["objects"] = []
            if all_objects:
                output["all_objects"] = all_objects
            return output

        def compact_render_geometry_payload(value: object) -> dict[str, object]:
            row = _mapping_to_plain_dict(value)
            if not row:
                return {}
            return {
                key: row.get(key)
                for key in (
                    "exists",
                    "x",
                    "y",
                    "width",
                    "height",
                    "bbox",
                    "bounds",
                    "source",
                    "confidence",
                    "coordinate_mode",
                    "coordinate_space",
                    "manual_focus_region",
                    "chart_transform_id",
                    "frame_id",
                    "plot_area",
                    "broker_surface_bounds",
                    "chart_region_bounds",
                    "chart_region_chart_bounds",
                    "plot_area_bounds",
                    "plot_area_chart_bounds",
                    "right_order_panel_bounds",
                    "top_asset_tabs_bounds",
                    "left_menu_bounds",
                    "price_axis_bounds",
                    "time_axis_bounds",
                    "valid",
                    "reason",
                    "rules",
                )
                if row.get(key) not in (None, "", [], {})
            }

        def compact_chart_payload(value: object) -> dict[str, object]:
            row = _mapping_to_plain_dict(value)
            output = compact_mapping(row, {"frame", "plot_area", "chart_transform", "scene_graph"})
            plot_area_value = row.get("plot_area")
            if isinstance(plot_area_value, Mapping):
                output["plot_area"] = compact_render_geometry_payload(cast(Mapping[str, object], plot_area_value))
            chart_transform_value = row.get("chart_transform")
            if isinstance(chart_transform_value, Mapping):
                output["chart_transform"] = compact_render_geometry_payload(cast(Mapping[str, object], chart_transform_value))
            scene_graph_value = row.get("scene_graph")
            if isinstance(scene_graph_value, Mapping):
                output["scene_graph"] = compact_render_geometry_payload(cast(Mapping[str, object], scene_graph_value))
            return output

        def preserve_render_geometry(target: dict[str, object], source: Mapping[str, object]) -> None:
            for geometry_key in ("plot_area", "chart_transform", "scene_graph", "broker_scene_graph_v3"):
                value = source.get(geometry_key)
                if isinstance(value, Mapping):
                    target[geometry_key] = compact_render_geometry_payload(cast(Mapping[str, object], value))
            chart_payload = source.get("chart")
            if isinstance(chart_payload, Mapping):
                target["chart"] = compact_chart_payload(cast(Mapping[str, object], chart_payload))
            source_scene_graph = source.get("scene_graph")
            if "broker_scene_graph_v3" not in target and isinstance(source_scene_graph, Mapping):
                target["broker_scene_graph_v3"] = compact_render_geometry_payload(
                    cast(Mapping[str, object], source_scene_graph)
                )
            if "chart" not in target:
                chart_output: dict[str, object] = {}
                for chart_key in ("plot_area", "chart_transform", "scene_graph"):
                    chart_value = target.get(chart_key)
                    if isinstance(chart_value, Mapping):
                        chart_output[chart_key] = _mapping_to_plain_dict(cast(Mapping[str, object], chart_value))
                if chart_output:
                    target["chart"] = chart_output

        compact: dict[str, object] = dict(compact_session_payload(cast(Mapping[str, Any], live_state)))
        for scalar_key in (
            "schema_version",
            "session_id",
            "name",
            "status",
            "tracking_enabled",
            "frame_id",
            "display_frame_id",
            "chart_frame_id",
            "overlay_frame_id",
            "full_overlay_frame_id",
            "model_vote_frame_id",
            "capture_count",
            "frame_index",
            "state_version",
            "decision_version",
            "chart_transform_id",
            "broker_source_lock_id",
            "sequence_id",
            "overlay_object_frame_id",
            "overlay_state_version",
            "overlay_frame_state_version",
            "requested_mode",
            "active_mode",
            "visible_layers",
            "overlay_count",
            "renderable_count",
            "hidden_count",
            "rejected_count",
            "reason_if_empty",
        ):
            value = live_state.get(scalar_key)
            if value not in (None, "", [], {}):
                compact[scalar_key] = value
        for status_key in (
            "provider_status",
            "overlay_mode",
            "shooter_state",
            "model_state",
            "visual_health_v3",
            "frame_timing_trace_v3",
            "frame_timing",
            "performance_trace_v3",
            "frontend_heartbeat",
        ):
            value = live_state.get(status_key)
            if isinstance(value, Mapping):
                compact[status_key] = _mapping_to_plain_dict(cast(Mapping[str, object], value))
            elif value not in (None, "", [], {}):
                compact[status_key] = value
        preserve_render_geometry(compact, live_state)
        shooter_payload = live_state.get("shooter")
        if isinstance(shooter_payload, Mapping):
            compact["shooter"] = compact_mapping(
                cast(Mapping[str, object], shooter_payload),
                {
                    "available",
                    "state",
                    "status",
                    "mode",
                    "armed",
                    "will_click",
                    "next_required",
                    "reason",
                    "last_error",
                    "started_epoch",
                    "updated_epoch",
                    "poll_interval_sec",
                },
            )
        live_visual_state = live_state.get("live_visual_state")
        if isinstance(live_visual_state, Mapping):
            live_visual_mapping = cast(Mapping[str, object], live_visual_state)
            compact_visual: dict[str, object] = dict(compact_session_payload(cast(Mapping[str, Any], live_visual_mapping)))
            visual_overlays = live_visual_mapping.get("overlays")
            if isinstance(visual_overlays, Mapping):
                compact_visual["overlays"] = compact_overlays_payload(cast(Mapping[str, object], visual_overlays))
            preserve_render_geometry(compact_visual, live_visual_mapping)
            compact.update(compact_visual)
        overlays = compact.get("overlays")
        if isinstance(overlays, Mapping):
            compact_overlays = compact_overlays_payload(cast(Mapping[str, object], overlays))
            compact["overlays"] = compact_overlays
        if "overlays" not in compact and isinstance(live_state.get("overlay_objects"), list):
            fallback_objects = compact_overlay_objects(live_state["overlay_objects"])
            compact["overlays"] = {
                "count": len(fallback_objects),
                "total_count": len(fallback_objects),
                "renderable_count": len(fallback_objects),
                "objects": fallback_objects,
            }
        compact.pop("overlay_objects", None)
        frame_timing = _mapping_to_plain_dict(compact.get("frame_timing_trace_v3") or compact.get("frame_timing"))
        display_frame_id = int(_epoch_float(frame_timing.get("display_frame_id") or compact.get("display_frame_id"), 0.0))
        overlay_frame_id = int(
            _epoch_float(
                frame_timing.get("overlay_frame_id")
                or compact.get("overlay_frame_id")
                or compact.get("full_overlay_frame_id"),
                0.0,
            )
        )
        model_vote_frame_id = int(
            _epoch_float(frame_timing.get("model_vote_frame_id") or compact.get("model_vote_frame_id"), 0.0)
        )
        if display_frame_id > 0:
            compact["frame_id"] = display_frame_id
            compact["display_frame_id"] = display_frame_id
        if overlay_frame_id > 0:
            compact["overlay_frame_id"] = overlay_frame_id
            compact["full_overlay_frame_id"] = overlay_frame_id
        if model_vote_frame_id > 0:
            compact["model_vote_frame_id"] = model_vote_frame_id
        packets = compact.get("packets")
        if isinstance(packets, Mapping):
            packets_mapping = cast(Mapping[str, object], packets)
            study = packets_mapping.get("study")
            execution = packets_mapping.get("execution")
            if isinstance(study, Mapping):
                compact["study_packet_status"] = dict(cast(Mapping[str, object], study))
            if isinstance(execution, Mapping):
                compact["execution_packet_status"] = dict(cast(Mapping[str, object], execution))
        compact["latest_signal"] = compact_mapping(
            compact.get("latest_signal"),
            {
                "session_id",
                "signal_id",
                "side",
                "action",
                "execution_action",
                "confidence",
                "status",
                "final_state",
                "lane",
                "execution_lane",
                "symbol",
                "pair",
                "timeframe",
                "published_epoch",
                "signal_age_sec",
                "broker_source",
                "broker_source_lock",
                "promotion_failure_audit_v3",
            },
        )
        compact["tracking_summary"] = compact_mapping(
            compact.get("tracking_summary"),
            {
                "session_id",
                "detected_market",
                "detected_timeframe",
                "status",
                "frame_index",
                "capture_count",
                "display_frame_id",
                "last_capture_epoch",
                "broker_source",
                "broker_source_lock",
                "broker_surface",
                "pipeline_timing",
            },
        )
        model_result = _mapping_to_plain_dict(compact.get("model_council_result"))
        if model_result:
            compact["model_council_result"] = {
                **compact_mapping(
                    model_result,
                    {
                        "packet_id",
                        "state",
                        "final_state",
                        "side",
                        "final_side",
                        "lane",
                        "execution_lane",
                        "score",
                        "confidence",
                        "actionable",
                        "denied_at",
                        "next_required",
                        "promotion_failure_audit_v3",
                    },
                ),
                "promotion_trace": compact_promotion_trace(model_result.get("promotion_trace")),
                "model_council": compact_mapping(
                    model_result.get("model_council"),
                    {"final_state", "final_side", "state", "side", "lane", "execution_lane", "score", "actionable"},
                ),
            }
        broker_execution_state = compact_mapping(
            compact.get("broker_execution_state"),
            {"status", "message", "side", "lane", "actionable", "reason", "next_required"},
        )
        if broker_execution_state:
            compact["broker_execution_state"] = broker_execution_state
        for heavy_key in (
            "model_council_study_packet",
            "model_council_packet",
            "execution_packet",
            "recent_studies",
        ):
            compact.pop(heavy_key, None)
        return compact

    def _refresh_compact_cached_live_state(
        cached_live_state: Mapping[str, object],
        requested_session_id: str,
        *,
        now_epoch: float,
    ) -> dict[str, object]:
        refreshed: dict[str, object] = dict(cached_live_state)
        display_snapshot = _direct_window_tracker_display_snapshot(
            requested_session_id,
            require_overlay_model=False,
        )
        if display_snapshot is None:
            return refreshed
        display_payload = dict(display_snapshot)
        surface_mismatch_reason = _display_overlay_authority_mismatch_reason(
            requested_session_id,
            display_payload,
            allow_session_probe=True,
        )
        if surface_mismatch_reason:
            return _compact_studying_new_pair_live_state(
                requested_session_id,
                display_payload,
                requested_mode=str(refreshed.get("requested_mode") or refreshed.get("active_mode") or "CLEAN_LIVE"),
                reason=surface_mismatch_reason,
                now_epoch=now_epoch,
            )
        overlays_payload = _mapping_to_plain_dict(refreshed.get("overlays"))
        overlay_objects_raw = overlays_payload.get("objects")
        if not isinstance(overlay_objects_raw, list):
            overlay_objects_raw = refreshed.get("overlay_objects")
        overlay_objects: list[Mapping[str, Any]] = []
        if isinstance(overlay_objects_raw, list):
            for row in cast(Sequence[object], overlay_objects_raw):
                if isinstance(row, Mapping):
                    overlay_objects.append(cast(Mapping[str, Any], row))
        model_health = _live_model_health_summary(cast(Mapping[str, object], display_payload))
        frontend_heartbeat = latest_frontend_heartbeat(requested_session_id)
        frame_timing = build_frame_timing_trace_v3(
            cast(Mapping[str, Any], display_payload),
            overlays=overlay_objects,
            model_health=cast(Mapping[str, Any], model_health),
            frontend_heartbeat=frontend_heartbeat,
            now_epoch=now_epoch,
        )
        performance_state: dict[str, object] = {
            "session_id": requested_session_id,
            "frame_id": int(_epoch_float(
                display_payload.get("display_frame_id")
                or display_payload.get("frame_index")
                or display_payload.get("capture_count")
                or refreshed.get("frame_id")
                or 0,
                0.0,
            )),
            "state_version": int(_epoch_float(display_payload.get("state_version") or refreshed.get("state_version") or 0, 0.0)),
            "tracking_summary": _mapping_to_plain_dict(
                refreshed.get("tracking_summary") or display_payload.get("tracking_summary")
            ),
            "latest_signal": _mapping_to_plain_dict(refreshed.get("latest_signal") or display_payload.get("latest_signal")),
            "model_health": model_health,
            "frame_timing_trace_v3": frame_timing,
            "frame_timing": frame_timing,
            "broker_surface": _mapping_to_plain_dict(refreshed.get("broker_surface")),
            "frontend_heartbeat": frontend_heartbeat,
        }
        performance_trace = build_performance_trace_v3(performance_state, now_epoch=now_epoch)
        refreshed["frame_id"] = performance_state["frame_id"]
        refreshed["display_frame_id"] = frame_timing.get("display_frame_id", performance_state["frame_id"])
        refreshed["overlay_frame_id"] = frame_timing.get("overlay_frame_id", refreshed.get("overlay_frame_id"))
        refreshed["model_vote_frame_id"] = frame_timing.get("model_vote_frame_id", refreshed.get("model_vote_frame_id"))
        refreshed["frame_timing_trace_v3"] = frame_timing
        refreshed["frame_timing"] = frame_timing
        refreshed["performance_trace_v3"] = performance_trace
        refreshed["visual_health_v3"] = performance_trace.get("visual_health", refreshed.get("visual_health_v3"))
        refreshed["frontend_heartbeat"] = dict(frontend_heartbeat or {})
        provider: dict[str, object] = {
            **_mapping_to_plain_dict(refreshed.get("provider_status")),
            "compact_cache_reused_v3": True,
            "compact_cache_refreshed_epoch": now_epoch,
        }
        refreshed["provider_status"] = provider
        live_visual_state = refreshed.get("live_visual_state")
        if isinstance(live_visual_state, Mapping):
            live_visual: dict[str, object] = dict(cast(Mapping[str, object], live_visual_state))
            live_visual["frame_id"] = refreshed["frame_id"]
            live_visual["display_frame_id"] = refreshed["display_frame_id"]
            live_visual["overlay_frame_id"] = refreshed["overlay_frame_id"]
            live_visual["model_vote_frame_id"] = refreshed["model_vote_frame_id"]
            live_visual["frame_timing_trace_v3"] = frame_timing
            live_visual["frame_timing"] = frame_timing
            live_visual["performance_trace_v3"] = performance_trace
            live_visual["visual_health_v3"] = refreshed["visual_health_v3"]
            live_visual["frontend_heartbeat"] = refreshed["frontend_heartbeat"]
            live_visual["provider_status"] = provider
            refreshed["live_visual_state"] = live_visual
        return refreshed

    def _compact_mode_visible_layers(mode: str) -> list[str]:
        profile = view_mode_profile(mode)
        layer_visibility = _mapping_to_plain_dict(profile.get("layer_visibility"))
        ordered_layers = OverlayLayerManagerV3(mode).layer_order()
        return [layer for layer in ordered_layers if bool(layer_visibility.get(layer, False))]

    def _compact_overlay_pool_from_payload(payload: Mapping[str, object]) -> list[Mapping[str, object]]:
        overlays = _mapping_to_plain_dict(payload.get("overlays"))
        candidates = overlays.get("all_objects")
        if not isinstance(candidates, list):
            candidates = overlays.get("objects")
        if not isinstance(candidates, list):
            live_visual = _mapping_to_plain_dict(payload.get("live_visual_state"))
            live_overlays = _mapping_to_plain_dict(live_visual.get("overlays"))
            candidates = live_overlays.get("all_objects")
            if not isinstance(candidates, list):
                candidates = live_overlays.get("objects")
        rows: list[Mapping[str, object]] = []
        if not isinstance(candidates, list):
            return rows
        for item in cast(Sequence[object], candidates):
            if isinstance(item, Mapping):
                rows.append(cast(Mapping[str, object], item))
        return rows

    def _project_compact_live_state_response(
        payload: Mapping[str, object],
        requested_mode: str,
        *,
        now_epoch: float,
    ) -> dict[str, object] | None:
        source_objects = _compact_overlay_pool_from_payload(payload)
        if not source_objects:
            return None
        active_mode = normalize_view_mode(requested_mode)
        now_ms = int(now_epoch * 1000.0)
        visible_rows: list[dict[str, object]] = []
        all_rows: list[dict[str, object]] = []
        for source_object in source_objects:
            if bool(source_object.get("precision_rejected", False)):
                continue
            try:
                normalized = normalize_v3_overlay_object(source_object, strict=False)
            except Exception:
                continue
            all_rows.append(normalized)
            if active_mode == "CLEAN_LIVE" and normalized.get("visible_default") is False:
                continue
            created_at_ms = _epoch_float(normalized.get("created_at_ms"), 0.0)
            effective_now_ms = now_ms if created_at_ms > 0.0 else None
            try:
                if not overlay_is_visible(normalized, active_mode, now_ms=effective_now_ms):
                    continue
            except Exception:
                continue
            visible_rows.append(normalized)
        if not all_rows:
            return None
        layer_manager = OverlayLayerManagerV3(active_mode, now_ms=now_ms)
        layer_payload = layer_manager.as_dict()
        active_budget = int(_epoch_float(layer_payload.get("active_budget"), float(len(visible_rows))))
        if active_budget > 0:
            selected_rows = sorted(visible_rows, key=layer_manager.overlay_sort_key)[:active_budget]
        else:
            selected_rows = sorted(visible_rows, key=layer_manager.overlay_sort_key)
        renderable_count = len(selected_rows)
        total_count = len(all_rows)
        rejected_count = int(_epoch_float(_mapping_to_plain_dict(payload.get("overlays")).get("rejected_count"), 0.0))
        hidden_count = max(0, total_count - renderable_count - rejected_count)
        reason_if_empty = "" if renderable_count > 0 else f"no renderable overlays for mode {active_mode}"
        projected: dict[str, object] = dict(payload)
        projected["requested_mode"] = requested_mode
        projected["active_mode"] = active_mode
        projected["visible_layers"] = _compact_mode_visible_layers(active_mode)
        projected["overlay_count"] = total_count
        projected["renderable_count"] = renderable_count
        projected["hidden_count"] = hidden_count
        projected["rejected_count"] = rejected_count
        projected["reason_if_empty"] = reason_if_empty
        overlay_mode_payload = {
            **_mapping_to_plain_dict(projected.get("overlay_mode")),
            "requested": requested_mode,
            "active": active_mode,
            "visible_layers": projected["visible_layers"],
            "reason_if_empty": reason_if_empty,
        }
        projected["overlay_mode"] = overlay_mode_payload
        projected_overlays = {
            **_mapping_to_plain_dict(projected.get("overlays")),
            "requested_mode": requested_mode,
            "active_mode": active_mode,
            "visible_layers": projected["visible_layers"],
            "overlay_count": total_count,
            "total_count": total_count,
            "renderable_count": renderable_count,
            "hidden_count": hidden_count,
            "rejected_count": rejected_count,
            "reason_if_empty": reason_if_empty,
            "objects": selected_rows,
            "all_objects": all_rows,
            "source": "projected_compact_overlay_pool_v3",
        }
        projected["overlays"] = projected_overlays
        provider = {
            **_mapping_to_plain_dict(projected.get("provider_status")),
            "compact_overlay_mode_projection_v3": True,
            "compact_overlay_projection_epoch": now_epoch,
            "compact_overlay_projection_source_count": total_count,
        }
        projected["provider_status"] = provider
        live_visual_state = projected.get("live_visual_state")
        if isinstance(live_visual_state, Mapping):
            live_visual = dict(cast(Mapping[str, object], live_visual_state))
            live_visual["requested_mode"] = requested_mode
            live_visual["active_mode"] = active_mode
            live_visual["visible_layers"] = projected["visible_layers"]
            live_visual["overlay_count"] = total_count
            live_visual["renderable_count"] = renderable_count
            live_visual["hidden_count"] = hidden_count
            live_visual["rejected_count"] = rejected_count
            live_visual["reason_if_empty"] = reason_if_empty
            live_visual["overlay_mode"] = overlay_mode_payload
            live_visual["overlays"] = projected_overlays
            live_visual["provider_status"] = provider
            projected["live_visual_state"] = live_visual
        return projected

    def _public_compact_live_state_response(payload: Mapping[str, object]) -> dict[str, object]:
        public_payload: dict[str, object] = dict(payload)
        public_payload.pop("live_visual_state", None)
        omitted_all_objects = 0
        overlays = _mapping_to_plain_dict(public_payload.get("overlays"))
        if overlays:
            all_objects = overlays.get("all_objects")
            if isinstance(all_objects, list):
                omitted_all_objects = len(cast(Sequence[object], all_objects))
            overlays.pop("all_objects", None)
            public_payload["overlays"] = overlays
        provider = {
            **_mapping_to_plain_dict(public_payload.get("provider_status")),
            "compact_public_payload_v3": True,
            "compact_public_all_objects_omitted_v3": omitted_all_objects,
        }
        public_payload["provider_status"] = provider
        return public_payload

    def _compact_visible_overlay_pool_from_payload(payload: Mapping[str, object]) -> list[Mapping[str, object]]:
        rows: list[Mapping[str, object]] = []
        for container in (
            _mapping_to_plain_dict(payload.get("overlays")),
            _mapping_to_plain_dict(_mapping_to_plain_dict(payload.get("live_visual_state")).get("overlays")),
        ):
            candidates = container.get("objects")
            if not isinstance(candidates, list):
                continue
            for item in cast(Sequence[object], candidates):
                if isinstance(item, Mapping):
                    rows.append(cast(Mapping[str, object], item))
            if rows:
                return rows
        return _compact_overlay_pool_from_payload(payload)

    def _compact_overlay_identity_from_payload(payload: Mapping[str, object]) -> dict[str, object]:
        for overlay in _compact_visible_overlay_pool_from_payload(payload):
            frame_id = int(_epoch_float(overlay.get("frame_id") or overlay.get("frame_index"), 0.0))
            chart_transform_id = str(overlay.get("chart_transform_id") or "").strip()
            broker_source_lock_id = str(overlay.get("broker_source_lock_id") or "").strip()
            if frame_id <= 0 and not chart_transform_id and not broker_source_lock_id:
                continue
            identity: dict[str, object] = {}
            if frame_id > 0:
                identity["overlay_object_frame_id"] = frame_id
            if chart_transform_id:
                identity["chart_transform_id"] = chart_transform_id
            if broker_source_lock_id:
                identity["broker_source_lock_id"] = broker_source_lock_id
            for key in ("symbol", "timeframe", "sequence_id"):
                value = str(overlay.get(key) or "").strip()
                if value:
                    identity[key] = value
            return identity
        return {}

    def _apply_compact_overlay_identity(projected: dict[str, object]) -> dict[str, object]:
        identity = _compact_overlay_identity_from_payload(projected)
        if not identity:
            return projected
        projected.update(identity)
        overlays = projected.get("overlays")
        if isinstance(overlays, Mapping):
            overlay_payload = dict(cast(Mapping[str, object], overlays))
            for key, value in identity.items():
                overlay_payload.setdefault(key, value)
            projected["overlays"] = overlay_payload
        live_visual_state = projected.get("live_visual_state")
        if isinstance(live_visual_state, Mapping):
            live_visual = dict(cast(Mapping[str, object], live_visual_state))
            live_visual.update(identity)
            live_visual_overlays = live_visual.get("overlays")
            if isinstance(live_visual_overlays, Mapping):
                live_visual_overlay_payload = dict(cast(Mapping[str, object], live_visual_overlays))
                for key, value in identity.items():
                    live_visual_overlay_payload.setdefault(key, value)
                live_visual["overlays"] = live_visual_overlay_payload
            projected["live_visual_state"] = live_visual
        return projected

    def _apply_display_snapshot_to_projected_payload(
        payload: Mapping[str, object],
        display_snapshot: Mapping[str, object] | None,
        *,
        now_epoch: float,
    ) -> dict[str, object]:
        projected = _apply_compact_overlay_identity(dict(payload))
        if display_snapshot is None:
            return projected
        display_frame_id = int(
            _epoch_float(
                display_snapshot.get("display_frame_id")
                or display_snapshot.get("frame_index")
                or display_snapshot.get("capture_count")
                or projected.get("frame_id")
                or 0,
                0.0,
            )
        )
        if display_frame_id > 0:
            projected["frame_id"] = display_frame_id
            projected["display_frame_id"] = display_frame_id
        for key in (
            "capture_count",
            "frame_index",
            "chart_frame_id",
            "overlay_frame_id",
            "full_overlay_frame_id",
            "model_vote_frame_id",
            "state_version",
            "display_frame_id",
            "display_capture_epoch",
            "display_published_epoch",
            "last_display_window_path",
            "last_window_path",
            "last_frame_path",
            "last_chart_path",
            "last_overlay_path",
            "last_full_overlay_path",
            "last_display_surface_signature",
            "last_window_surface_signature",
            "last_study_surface_signature",
            "overlay_source_window_signature",
            "overlay_source_study_signature",
        ):
            value = display_snapshot.get(key)
            if value not in (None, "", [], {}):
                projected[key] = value
        display_published = _epoch_float(
            display_snapshot.get("display_published_epoch")
            or display_snapshot.get("last_display_published_epoch")
            or display_snapshot.get("last_capture_epoch"),
            0.0,
        )
        display_captured = _epoch_float(
            display_snapshot.get("display_capture_epoch")
            or display_snapshot.get("last_display_capture_epoch")
            or display_snapshot.get("last_capture_started_epoch"),
            0.0,
        )
        if display_published > 0.0:
            projected["last_capture_epoch"] = display_published
        if display_captured > 0.0:
            projected["last_capture_started_epoch"] = display_captured
        tracking_summary = projected.get("tracking_summary")
        if isinstance(tracking_summary, Mapping):
            tracking_payload = dict(cast(Mapping[str, object], tracking_summary))
            if display_published > 0.0:
                tracking_payload["last_capture_epoch"] = display_published
            projected["tracking_summary"] = tracking_payload
        _apply_compact_overlay_identity(projected)
        timing_source: dict[str, object] = dict(projected)
        for key, value in display_snapshot.items():
            timing_source[str(key)] = value
        overlay_rows = [cast(Mapping[str, Any], row) for row in _compact_visible_overlay_pool_from_payload(projected)]
        model_health = _live_model_health_summary(timing_source)
        frontend_heartbeat = latest_frontend_heartbeat(str(projected.get("session_id") or ""))
        frame_timing = build_frame_timing_trace_v3(
            cast(Mapping[str, Any], timing_source),
            overlays=overlay_rows,
            model_health=cast(Mapping[str, Any], model_health),
            frontend_heartbeat=frontend_heartbeat,
            now_epoch=now_epoch,
        )
        performance_state: dict[str, object] = {
            "session_id": str(projected.get("session_id") or ""),
            "frame_id": int(
                _epoch_float(
                    frame_timing.get("display_frame_id")
                    or projected.get("display_frame_id")
                    or projected.get("frame_id")
                    or 0,
                    0.0,
                )
            ),
            "state_version": int(_epoch_float(projected.get("state_version") or 0, 0.0)),
            "tracking_summary": _mapping_to_plain_dict(projected.get("tracking_summary")),
            "latest_signal": _mapping_to_plain_dict(projected.get("latest_signal")),
            "model_health": model_health,
            "frame_timing_trace_v3": frame_timing,
            "frame_timing": frame_timing,
            "broker_surface": _mapping_to_plain_dict(projected.get("broker_surface")),
            "frontend_heartbeat": frontend_heartbeat,
        }
        performance_trace = build_performance_trace_v3(performance_state, now_epoch=now_epoch)
        projected["frame_timing_trace_v3"] = frame_timing
        projected["frame_timing"] = frame_timing
        projected["performance_trace_v3"] = performance_trace
        projected["visual_health_v3"] = performance_trace.get("visual_health", projected.get("visual_health_v3"))
        projected["frontend_heartbeat"] = dict(frontend_heartbeat or {})
        provider = {
            **_mapping_to_plain_dict(projected.get("provider_status")),
            "compact_overlay_projection_light_refresh_v3": True,
        }
        projected["provider_status"] = provider
        live_visual_state = projected.get("live_visual_state")
        if isinstance(live_visual_state, Mapping):
            live_visual = dict(cast(Mapping[str, object], live_visual_state))
            for key in (
                "frame_id",
                "display_frame_id",
                "chart_frame_id",
                "overlay_frame_id",
                "full_overlay_frame_id",
                "model_vote_frame_id",
                "state_version",
                "last_capture_epoch",
                "last_capture_started_epoch",
                "frame_timing_trace_v3",
                "frame_timing",
                "performance_trace_v3",
                "visual_health_v3",
                "frontend_heartbeat",
                "provider_status",
            ):
                if key in projected:
                    live_visual[key] = projected[key]
            projected["live_visual_state"] = live_visual
        return projected

    def _projected_compact_warm_start(
        requested_session_id: str,
        requested_mode: str,
        cache_signature: str,
        display_snapshot: Mapping[str, object] | None,
        *,
        now_epoch: float,
    ) -> dict[str, object] | None:
        active_mode = normalize_view_mode(requested_mode)
        with _LIVE_STATE_V3_CACHE_LOCK:
            cached_candidates = [
                dict(cached_payload)
                for key, (_cached_epoch, cached_payload) in _COMPACT_LIVE_STATE_RESPONSE_CACHE.items()
                if key[0] == requested_session_id and key[2] == cache_signature
            ]
        for cached_payload in cached_candidates:
            if _compact_overlay_payload_stale_for_display(cached_payload, display_snapshot):
                continue
            projected = _project_compact_live_state_response(cached_payload, active_mode, now_epoch=now_epoch)
            if projected is not None:
                return _apply_display_snapshot_to_projected_payload(projected, display_snapshot, now_epoch=now_epoch)
        if display_snapshot is None:
            return None
        for source_mode in ("CLEAN_LIVE", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "REPLAY"):
            if source_mode == active_mode:
                continue
            persisted = _load_persisted_compact_overlay_response(
                requested_session_id,
                source_mode,
                display_snapshot,
                now_epoch=now_epoch,
            )
            if persisted is None:
                continue
            if _compact_overlay_payload_stale_for_display(persisted, display_snapshot):
                continue
            projected = _project_compact_live_state_response(persisted, active_mode, now_epoch=now_epoch)
            if projected is not None:
                return _apply_display_snapshot_to_projected_payload(projected, display_snapshot, now_epoch=now_epoch)
        return None

    def _artifact_surface_signature_from_path(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        name = Path(text).name
        match = re.match(
            r"^\d{1,12}_([^\\/]+?)_(?:window|chart|overlay|full_overlay|decision)(?:\.|$)",
            name,
            flags=re.IGNORECASE,
        )
        if match:
            return str(match.group(1) or "").strip()
        fallback = re.match(r"^\d{1,12}_([^_\\/]+)", name)
        return str(fallback.group(1) or "").strip() if fallback else ""

    def _market_selector_rebind_pending(payload: Mapping[str, object]) -> bool:
        for key in ("tracking_summary", "latest_signal"):
            row = _mapping_to_plain_dict(payload.get(key))
            if bool(
                row.get("market_selector_rebind_required")
                or row.get("market_selector_studying_new_pair")
                or row.get("market_selector_visual_changed")
            ):
                return True
        return bool(
            payload.get("market_selector_rebind_required")
            or payload.get("market_selector_studying_new_pair")
            or payload.get("market_selector_visual_changed")
        )

    def _direct_session_market_selector_rebind_pending(requested_session_id: str) -> bool:
        try:
            raw_payload = json.loads(_direct_live_state_session_path(requested_session_id).read_text(encoding="utf-8"))
        except Exception:
            return False
        if not isinstance(raw_payload, Mapping):
            return False
        return _market_selector_rebind_pending(cast(Mapping[str, object], raw_payload))

    def _display_overlay_authority_mismatch_reason(
        requested_session_id: str,
        display_payload: Mapping[str, object],
        *,
        allow_session_probe: bool = False,
    ) -> str:
        rebind_pending = _market_selector_rebind_pending(display_payload)
        if not rebind_pending and allow_session_probe:
            rebind_pending = _direct_session_market_selector_rebind_pending(requested_session_id)
        display_signature = str(
            display_payload.get("last_display_surface_signature")
            or display_payload.get("last_window_surface_signature")
            or _mapping_to_plain_dict(display_payload.get("display_fast_path_v3")).get("surface_signature")
            or _artifact_surface_signature_from_path(
                display_payload.get("last_display_window_path")
                or display_payload.get("last_window_path")
                or display_payload.get("last_frame_path")
            )
            or ""
        ).strip()
        overlay_signature = str(
            display_payload.get("overlay_source_window_signature")
            or display_payload.get("overlay_source_study_signature")
            or _artifact_surface_signature_from_path(
                display_payload.get("last_full_overlay_path") or display_payload.get("last_overlay_path")
            )
            or ""
        ).strip()
        if display_signature and overlay_signature and display_signature != overlay_signature:
            if rebind_pending:
                return (
                    "Studying new pair: the visible broker surface changed before overlay authority "
                    "rebuilt on the new frame."
                )
            return ""
        if not rebind_pending:
            return ""
        return ""

    def _display_frame_id(display_payload: Mapping[str, object]) -> int:
        return int(
            _epoch_float(
                display_payload.get("display_frame_id")
                or display_payload.get("frame_index")
                or display_payload.get("capture_count")
                or 0,
                0.0,
            )
        )

    def _compact_studying_new_pair_live_state(
        requested_session_id: str,
        display_payload: Mapping[str, object],
        *,
        requested_mode: str,
        reason: str,
        now_epoch: float,
    ) -> dict[str, object]:
        active_mode = normalize_view_mode(requested_mode)
        frame_id = _display_frame_id(display_payload)
        window_path = str(
            display_payload.get("last_display_window_path")
            or display_payload.get("last_window_path")
            or display_payload.get("last_frame_path")
            or ""
        ).strip()
        tracking_summary: dict[str, object] = {
            **_mapping_to_plain_dict(display_payload.get("tracking_summary")),
            "status": "studying_new_pair",
            "market_selector_rebind_required": True,
            "market_selector_studying_new_pair": True,
        }
        latest_signal: dict[str, object] = {
            **_mapping_to_plain_dict(display_payload.get("latest_signal")),
            "action": "HOLD",
            "execution_action": "HOLD",
            "status": "studying_new_pair",
            "summary": reason,
            "market_selector_rebind_required": True,
            "market_selector_studying_new_pair": True,
        }
        window_artifact: dict[str, object] = {
            "kind": "window",
            "path": window_path,
            "url": "",
            "exists": bool(window_path),
            "frame_id": frame_id,
        }
        overlays_payload: dict[str, object] = {
            "count": 0,
            "total_count": 0,
            "renderable_count": 0,
            "hidden_count": 0,
            "rejected_count": 0,
            "artifact_frame_id": int(_epoch_float(display_payload.get("overlay_frame_id"), 0.0)),
            "overlay_object_frame_id": int(_epoch_float(display_payload.get("overlay_frame_id"), 0.0)),
            "artifact_frame_aligned": False,
            "artifact_authority_locked": False,
            "artifact_mismatch_reason": reason,
            "objects": [],
        }
        provider_status: dict[str, object] = {
            "live_state_source": "compact_studying_new_pair_fast_path",
            "compact_studying_new_pair_fast_path_v3": True,
            "compact_cache_refreshed_epoch": now_epoch,
            "reason": reason,
        }
        base_payload: dict[str, object] = dict(display_payload)
        base_payload.update(
            {
                "schema_version": "PG_LIVE_STATE_V3",
                "session_id": requested_session_id,
                "status": "running",
                "tracking_enabled": True,
                "frame_id": frame_id,
                "display_frame_id": frame_id,
                "state_version": int(_epoch_float(display_payload.get("state_version"), 0.0)),
                "requested_mode": requested_mode,
                "active_mode": active_mode,
                "visible_layers": [],
                "overlay_count": 0,
                "renderable_count": 0,
                "hidden_count": 0,
                "rejected_count": 0,
                "reason_if_empty": reason,
                "overlay_mode": {
                    "requested": requested_mode,
                    "active": active_mode,
                    "reason_if_empty": reason,
                    "artifact_frame_aligned": False,
                    "artifact_authority_locked": False,
                },
                "tracking_summary": tracking_summary,
                "latest_signal": latest_signal,
                "overlays": overlays_payload,
                "overlay_objects": [],
                "artifacts": {"window": window_artifact},
                "surface": {
                    "selected_plane": "full_broker_surface",
                    "frame": window_artifact,
                    "overlay_frame": window_artifact,
                    "mode": "full_broker_surface",
                },
                "chart_frame": {
                    "artifact": window_artifact,
                    "url": "",
                    "image_url": "",
                    "frame_url": "",
                    "overlay_url": "",
                    "display_artifact": window_artifact,
                },
                "broker_surface": {
                    "status": "studying_new_pair",
                    "frame": window_artifact,
                    "image_url": "",
                    "url": "",
                    "frame_url": "",
                    "latest_window_url": "",
                },
                "provider_status": provider_status,
            }
        )
        base_payload["live_visual_state"] = {
            key: value
            for key, value in base_payload.items()
            if key
            in {
                "schema_version",
                "session_id",
                "frame_id",
                "state_version",
                "requested_mode",
                "active_mode",
                "visible_layers",
                "overlay_count",
                "renderable_count",
                "hidden_count",
                "rejected_count",
                "reason_if_empty",
                "overlay_mode",
                "tracking_summary",
                "latest_signal",
                "overlays",
                "overlay_objects",
                "artifacts",
                "surface",
                "chart_frame",
                "broker_surface",
                "provider_status",
            }
        }
        return base_payload

    def build_live_state_v3_for_session(
        session_id: str,
        overlay_mode: str = "CLEAN_LIVE",
        *,
        compact_public: bool = False,
    ) -> dict[str, object]:
        requested_session_id = str(session_id or "").strip()
        if not requested_session_id:
            requested_session_id = resolve_window_tracker_dashboard_session_id(None)
        active_overlay_mode = normalize_view_mode(overlay_mode)
        cache_signature = _live_state_cache_signature(requested_session_id, compact_public=compact_public)
        cache_enabled = _LIVE_STATE_V3_CACHE_TTL_SEC > 0.0 and not cache_signature.startswith("session=missing")
        cache_key = (requested_session_id, active_overlay_mode, cache_signature, bool(compact_public))
        now_epoch = time.time()
        if cache_enabled:
            with _LIVE_STATE_V3_CACHE_LOCK:
                cached = _LIVE_STATE_V3_CACHE.get(cache_key)
                if cached and now_epoch - cached[0] <= _LIVE_STATE_V3_CACHE_TTL_SEC:
                    cached_live_state = dict(cached[1])
                    if compact_public:
                        return _refresh_compact_cached_live_state(
                            cached_live_state,
                            requested_session_id,
                            now_epoch=now_epoch,
                        )
                    return cached_live_state

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

        direct_live_state = _direct_live_state_v3_for_session(
            requested_session_id,
            now_epoch,
            active_overlay_mode,
            compact_public=compact_public,
        )
        if direct_live_state is not None:
            store_live_state_cache(direct_live_state)
            return direct_live_state
        tracker = get_window_tracker_service()

        def model_health_builder(payload: Mapping[str, Any]) -> Mapping[str, Any]:
            return _live_model_health_summary(cast(Mapping[str, object], payload))

        def shooter_loader(resolved_session_id: str) -> Mapping[str, Any]:
            return _latest_shooter_handshake_or_waiting(resolved_session_id)

        registry_loader_cache: dict[str, list[Mapping[str, Any]]] = {}

        def registry_loader(resolved_session_id: str) -> list[Mapping[str, Any]]:
            cached_rows = registry_loader_cache.get(resolved_session_id)
            if cached_rows is not None:
                return list(cached_rows)
            rows = [
                item
                for item in load_recent_market_objects(
                    resolved_session_id,
                    max_lines=_LIVE_STATE_REGISTRY_MAX_LINES,
                )
            ]
            registry_loader_cache[resolved_session_id] = rows
            return list(rows)

        def active_object_loader(resolved_session_id: str) -> list[Mapping[str, Any]]:
            rows = registry_loader(resolved_session_id)
            if compact_public:
                return _locked_registry_entries_from_entries(rows)
            return [
                item
                for item in active_objects_from_entries(
                    rows,
                    min_truth_score=0.0,
                    now_epoch=now_epoch,
                )
            ]

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
                    compact_public=compact_public,
                ),
            )
            live_state["provider_status"] = {
                **_mapping_to_plain_dict(live_state.get("provider_status")),
                "live_state_source": "tracker_service",
            }
            store_live_state_cache(live_state)
            return live_state
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc

    @app.get("/v1/mobile/live/state/v3/{session_id}")
    def live_state_v3_for_session(session_id: str, mode: str = "CLEAN_LIVE", compact: bool = False) -> dict[str, object]:
        if compact:
            requested_session_id = str(session_id or "").strip() or resolve_window_tracker_dashboard_session_id(None)
            active_mode = normalize_view_mode(mode)
            cache_signature = _compact_live_state_response_cache_signature(requested_session_id)
            cache_key = (requested_session_id, active_mode, cache_signature)
            now_epoch = time.time()
            if _COMPACT_LIVE_STATE_RESPONSE_CACHE_TTL_SEC > 0.0:
                refresh_source: dict[str, object] | None = None
                with _LIVE_STATE_V3_CACHE_LOCK:
                    for cached_key, cached in _compact_live_state_response_cache_candidates(cache_key):
                        previous_signature_reused = cached_key != cache_key
                        cached_provider = _mapping_to_plain_dict(cached[1].get("provider_status"))
                        if previous_signature_reused and cached_provider.get("compact_studying_new_pair_fast_path_v3") is True:
                            continue
                        cached_age = now_epoch - cached[0]
                        cached_payload = cached[1]
                        cache_can_reuse = _compact_live_state_cache_can_reuse(cached_payload, cached_age)
                        if cache_can_reuse and cached_age <= _COMPACT_LIVE_STATE_RESPONSE_HOT_TTL_SEC:
                            display_snapshot = _direct_window_tracker_display_snapshot(
                                requested_session_id,
                                require_overlay_model=False,
                            )
                            if _compact_overlay_payload_stale_for_display(cached_payload, display_snapshot):
                                continue
                            compact_cached = _apply_display_snapshot_to_projected_payload(
                                dict(cached[1]),
                                display_snapshot,
                                now_epoch=now_epoch,
                            )
                            provider: dict[str, object] = {
                                **_mapping_to_plain_dict(compact_cached.get("provider_status")),
                                "compact_cache_hot_reused_v3": True,
                                "compact_cache_previous_signature_reused_v3": True,
                                "compact_cache_signature_key_changed_v3": previous_signature_reused,
                                "compact_cache_hot_age_ms": round(max(0.0, cached_age) * 1000.0, 3),
                            }
                            compact_cached["provider_status"] = provider
                            return _public_compact_live_state_response(compact_cached)
                        if cache_can_reuse and cached_age <= _COMPACT_LIVE_STATE_RESPONSE_CACHE_TTL_SEC:
                            refresh_source = dict(cached[1])
                            break
                display_snapshot: Mapping[str, object] | None = None
                if refresh_source is not None:
                    display_snapshot = _direct_window_tracker_display_snapshot(
                        requested_session_id,
                        require_overlay_model=False,
                    )
                    if _compact_overlay_payload_stale_for_display(refresh_source, display_snapshot):
                        refresh_source = None
                if refresh_source is not None:
                    compact_refreshed = _apply_display_snapshot_to_projected_payload(
                        refresh_source,
                        display_snapshot,
                        now_epoch=now_epoch,
                    )
                    provider = {
                        **_mapping_to_plain_dict(compact_refreshed.get("provider_status")),
                        "compact_cache_light_refreshed_v3": True,
                        "compact_cache_refreshed_epoch": now_epoch,
                        "compact_cache_refresh_skipped_observability_rebuild_v3": True,
                    }
                    compact_refreshed["provider_status"] = provider
                    live_visual_state = compact_refreshed.get("live_visual_state")
                    if isinstance(live_visual_state, Mapping):
                        live_visual = dict(cast(Mapping[str, object], live_visual_state))
                        live_visual["provider_status"] = provider
                        compact_refreshed["live_visual_state"] = live_visual
                    with _LIVE_STATE_V3_CACHE_LOCK:
                        _COMPACT_LIVE_STATE_RESPONSE_CACHE[cache_key] = (time.time(), dict(compact_refreshed))
                    return _public_compact_live_state_response(compact_refreshed)
            with _compact_live_state_build_lock(cache_key):
                now_epoch = time.time()
                if _COMPACT_LIVE_STATE_RESPONSE_CACHE_TTL_SEC > 0.0:
                    refresh_source = None
                    with _LIVE_STATE_V3_CACHE_LOCK:
                        for cached_key, cached in _compact_live_state_response_cache_candidates(cache_key):
                            previous_signature_reused = cached_key != cache_key
                            cached_provider = _mapping_to_plain_dict(cached[1].get("provider_status"))
                            if previous_signature_reused and cached_provider.get("compact_studying_new_pair_fast_path_v3") is True:
                                continue
                            cached_age = now_epoch - cached[0]
                            cached_payload = cached[1]
                            cache_can_reuse = _compact_live_state_cache_can_reuse(cached_payload, cached_age)
                            if cache_can_reuse and cached_age <= _COMPACT_LIVE_STATE_RESPONSE_HOT_TTL_SEC:
                                display_snapshot = _direct_window_tracker_display_snapshot(
                                    requested_session_id,
                                    require_overlay_model=False,
                                )
                                if _compact_overlay_payload_stale_for_display(cached_payload, display_snapshot):
                                    continue
                                compact_cached = _apply_display_snapshot_to_projected_payload(
                                    dict(cached[1]),
                                    display_snapshot,
                                    now_epoch=now_epoch,
                                )
                                provider = {
                                    **_mapping_to_plain_dict(compact_cached.get("provider_status")),
                                    "compact_cache_hot_reused_v3": True,
                                    "compact_cache_previous_signature_reused_v3": True,
                                    "compact_cache_signature_key_changed_v3": previous_signature_reused,
                                    "compact_cache_hot_age_ms": round(max(0.0, cached_age) * 1000.0, 3),
                                    "compact_cache_singleflight_waited_v3": True,
                                }
                                compact_cached["provider_status"] = provider
                                return _public_compact_live_state_response(compact_cached)
                            if cache_can_reuse and cached_age <= _COMPACT_LIVE_STATE_RESPONSE_CACHE_TTL_SEC:
                                refresh_source = dict(cached[1])
                                break
                    display_snapshot = None
                    if refresh_source is not None:
                        display_snapshot = _direct_window_tracker_display_snapshot(
                            requested_session_id,
                            require_overlay_model=False,
                        )
                        if _compact_overlay_payload_stale_for_display(refresh_source, display_snapshot):
                            refresh_source = None
                    if refresh_source is not None:
                        compact_refreshed = _apply_display_snapshot_to_projected_payload(
                            refresh_source,
                            display_snapshot,
                            now_epoch=now_epoch,
                        )
                        provider = {
                            **_mapping_to_plain_dict(compact_refreshed.get("provider_status")),
                            "compact_cache_light_refreshed_v3": True,
                            "compact_cache_refreshed_epoch": now_epoch,
                            "compact_cache_refresh_skipped_observability_rebuild_v3": True,
                            "compact_cache_singleflight_waited_v3": True,
                        }
                        compact_refreshed["provider_status"] = provider
                        live_visual_state = compact_refreshed.get("live_visual_state")
                        if isinstance(live_visual_state, Mapping):
                            live_visual = dict(cast(Mapping[str, object], live_visual_state))
                            live_visual["provider_status"] = provider
                            compact_refreshed["live_visual_state"] = live_visual
                        with _LIVE_STATE_V3_CACHE_LOCK:
                            _COMPACT_LIVE_STATE_RESPONSE_CACHE[cache_key] = (time.time(), dict(compact_refreshed))
                        return _public_compact_live_state_response(compact_refreshed)
                display_snapshot = _direct_window_tracker_display_snapshot(
                    requested_session_id,
                    require_overlay_model=False,
                )
                display_snapshot_mapping: Mapping[str, object] | None = None
                if display_snapshot is not None:
                    display_snapshot_mapping = display_snapshot
                    surface_mismatch_reason = _display_overlay_authority_mismatch_reason(
                        requested_session_id,
                        display_snapshot,
                        allow_session_probe=True,
                    )
                    if surface_mismatch_reason:
                        compact_response = _compact_studying_new_pair_live_state(
                            requested_session_id,
                            dict(display_snapshot),
                            requested_mode=mode,
                            reason=surface_mismatch_reason,
                            now_epoch=now_epoch,
                        )
                        if _COMPACT_LIVE_STATE_RESPONSE_CACHE_TTL_SEC > 0.0:
                            with _LIVE_STATE_V3_CACHE_LOCK:
                                _COMPACT_LIVE_STATE_RESPONSE_CACHE[cache_key] = (time.time(), dict(compact_response))
                        return _public_compact_live_state_response(compact_response)
                    warm_start = _load_persisted_compact_overlay_response(
                        requested_session_id,
                        active_mode,
                        display_snapshot,
                        now_epoch=now_epoch,
                    )
                    if warm_start is not None and _compact_overlay_payload_stale_for_display(warm_start, display_snapshot):
                        warm_start = None
                    if warm_start is not None:
                        warm_start = _apply_display_snapshot_to_projected_payload(
                            warm_start,
                            display_snapshot,
                            now_epoch=now_epoch,
                        )
                        with _LIVE_STATE_V3_CACHE_LOCK:
                            _COMPACT_LIVE_STATE_RESPONSE_CACHE[cache_key] = (time.time(), dict(warm_start))
                        return _public_compact_live_state_response(warm_start)
                projected_warm_start = _projected_compact_warm_start(
                    requested_session_id,
                    active_mode,
                    cache_signature,
                    display_snapshot_mapping,
                    now_epoch=now_epoch,
                )
                if projected_warm_start is not None:
                    projected_warm_start = _apply_compact_overlay_identity(projected_warm_start)
                    with _LIVE_STATE_V3_CACHE_LOCK:
                        _COMPACT_LIVE_STATE_RESPONSE_CACHE[cache_key] = (time.time(), dict(projected_warm_start))
                    _persist_compact_overlay_response(requested_session_id, active_mode, projected_warm_start)
                    return _public_compact_live_state_response(projected_warm_start)
                live_state = build_live_state_v3_for_session(session_id, overlay_mode=mode, compact_public=True)
                compact_response = _apply_compact_overlay_identity(compact_live_state_response(live_state))
                if _COMPACT_LIVE_STATE_RESPONSE_CACHE_TTL_SEC > 0.0:
                    with _LIVE_STATE_V3_CACHE_LOCK:
                        for stale_key in [
                            key
                            for key in _COMPACT_LIVE_STATE_RESPONSE_CACHE
                            if key[0] == requested_session_id and key[1] == active_mode and key != cache_key
                        ]:
                            _COMPACT_LIVE_STATE_RESPONSE_CACHE.pop(stale_key, None)
                        _COMPACT_LIVE_STATE_RESPONSE_CACHE[cache_key] = (time.time(), dict(compact_response))
                _persist_compact_overlay_response(requested_session_id, active_mode, compact_response)
                return _public_compact_live_state_response(compact_response)
        live_state = build_live_state_v3_for_session(session_id, overlay_mode=mode, compact_public=compact)
        return live_state

    @app.get("/v1/mobile/live/state/v3")
    def live_state_v3(session_id: str | None = None, mode: str = "CLEAN_LIVE", compact: bool = False) -> dict[str, object]:
        if compact:
            requested_session_id = session_id or resolve_window_tracker_dashboard_session_id(None)
            return live_state_v3_for_session(requested_session_id, mode=mode, compact=True)
        live_state = build_live_state_v3_for_session(
            session_id or resolve_window_tracker_dashboard_session_id(None),
            overlay_mode=mode,
            compact_public=compact,
        )
        return live_state

    def _performance_trace_overlay_count(trace: Mapping[str, object] | None) -> int:
        if not isinstance(trace, Mapping):
            return 0
        version = str(trace.get("overlay_state_version") or "").strip()
        match = re.match(r"^ovlock_(\d+)_", version)
        if match:
            try:
                return max(0, int(match.group(1)))
            except ValueError:
                return 0
        overlay_state = trace.get("overlay_state")
        if isinstance(overlay_state, Mapping):
            overlay_state_mapping = cast(Mapping[str, object], overlay_state)
            nested_version = str(overlay_state_mapping.get("overlay_state_version") or "").strip()
            nested_match = re.match(r"^ovlock_(\d+)_", nested_version)
            if nested_match:
                try:
                    return max(0, int(nested_match.group(1)))
                except ValueError:
                    return 0
        return 0

    def _canonical_performance_trace_v3_for_session(session_id: str) -> dict[str, object] | None:
        live_state = build_live_state_v3_for_session(
            session_id,
            overlay_mode="ACTIVE_CONTEXT",
            compact_public=True,
        )
        trace = live_state.get("performance_trace_v3")
        if isinstance(trace, Mapping):
            return dict(cast(Mapping[str, object], trace))
        compact = live_state.get("live_visual_state")
        if isinstance(compact, Mapping):
            compact_mapping = cast(Mapping[str, object], compact)
            performance_trace = compact_mapping.get("performance_trace_v3")
            if isinstance(performance_trace, Mapping):
                return dict(cast(Mapping[str, object], performance_trace))
        return None

    def _direct_performance_trace_v3_for_session(session_id: str) -> dict[str, object] | None:
        requested_session_id = str(session_id or "").strip()
        if not requested_session_id:
            return None
        session = _direct_window_tracker_session_snapshot(requested_session_id)
        if session is None:
            selected_session_path = _direct_live_state_session_path(requested_session_id)
            selected_display_path = selected_session_path.with_name("display_state.json")
            if selected_display_path.exists() or not selected_session_path.exists():
                session = _direct_window_tracker_display_snapshot(
                    requested_session_id,
                    require_overlay_model=False,
                )
        if session is None:
            return None
        now_epoch = time.time()
        model_health = _live_model_health_summary(cast(Mapping[str, object], session))
        frontend_heartbeat = latest_frontend_heartbeat(requested_session_id)
        overlay_rows = _direct_performance_overlay_rows(requested_session_id, now_epoch=now_epoch)
        frame_timing = build_frame_timing_trace_v3(
            cast(Mapping[str, Any], session),
            overlays=overlay_rows,
            model_health=cast(Mapping[str, Any], model_health),
            frontend_heartbeat=frontend_heartbeat,
            now_epoch=now_epoch,
        )
        live_state: dict[str, object] = {
            "session_id": requested_session_id,
            "frame_id": int(_epoch_float(session.get("display_frame_id") or session.get("frame_index") or session.get("capture_count") or 0, 0.0)),
            "state_version": int(_epoch_float(session.get("state_version") or 0, 0.0)),
            "capture_interval_sec": session.get("capture_interval_sec"),
            "effective_capture_interval_sec": session.get("effective_capture_interval_sec"),
            "tracking_summary": _mapping_to_plain_dict(session.get("tracking_summary")),
            "latest_signal": _mapping_to_plain_dict(session.get("latest_signal")),
            "model_health": model_health,
            "frame_timing_trace_v3": frame_timing,
            "frame_timing": frame_timing,
            "broker_surface": {
                "url": str(
                    session.get("last_display_window_path")
                    or session.get("last_window_path")
                    or session.get("last_frame_path")
                    or ""
                )
            },
            "frontend_heartbeat": frontend_heartbeat,
        }
        trace = cast(dict[str, object], build_performance_trace_v3(live_state, now_epoch=now_epoch))
        trace["frame_id"] = int(_epoch_float(frame_timing.get("display_frame_id") or live_state["frame_id"] or 0, 0.0))
        trace["direct_overlay_source_v3"] = {
            "source": "locked_registry",
            "count": len(overlay_rows),
            "canonical_rebuild_required": False,
        }
        _store_direct_performance_trace_cache(requested_session_id, trace, now_epoch=now_epoch)
        return trace

    @app.get("/v1/mobile/performance/trace/v3/{session_id}")
    async def performance_trace_v3_for_session(session_id: str) -> dict[str, object]:
        direct_trace = _direct_performance_trace_v3_for_session(session_id)
        if direct_trace is not None:
            if _performance_trace_overlay_count(direct_trace) > 0:
                return direct_trace
            try:
                canonical_trace = _canonical_performance_trace_v3_for_session(session_id)
            except Exception:
                canonical_trace = None
            if canonical_trace is not None and _performance_trace_overlay_count(canonical_trace) > 0:
                direct_frame_id = int(_epoch_float(
                    direct_trace.get("frame_id")
                    or _mapping_to_plain_dict(direct_trace.get("display_frame")).get("frame_id")
                    or 0,
                    0.0,
                ))
                canonical_frame_id = int(_epoch_float(
                    canonical_trace.get("frame_id")
                    or _mapping_to_plain_dict(canonical_trace.get("display_frame")).get("frame_id")
                    or 0,
                    0.0,
                ))
                if direct_frame_id > canonical_frame_id:
                    canonical_trace = dict(canonical_trace)
                    canonical_trace["frame_id"] = direct_frame_id
                    direct_display = _mapping_to_plain_dict(direct_trace.get("display_frame"))
                    if direct_display:
                        canonical_trace["display_frame"] = direct_display
                    direct_timing = _mapping_to_plain_dict(direct_trace.get("timing_trace"))
                    if direct_timing:
                        canonical_trace["timing_trace"] = direct_timing
                    direct_visual = _mapping_to_plain_dict(direct_trace.get("visual_health"))
                    if direct_visual:
                        canonical_trace["visual_health"] = direct_visual
                _store_direct_performance_trace_cache(session_id, canonical_trace, now_epoch=time.time())
                return canonical_trace
            return direct_trace
        cached_trace = _cached_direct_performance_trace(session_id)
        if cached_trace is not None and _performance_trace_overlay_count(cached_trace) > 0:
            return cached_trace
        if _direct_performance_trace_direct_only():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Direct performance trace temporarily unavailable.",
            )
        live_state = build_live_state_v3_for_session(session_id)
        trace = live_state.get("performance_trace_v3")
        if isinstance(trace, Mapping):
            return dict(cast(Mapping[str, object], trace))
        compact = live_state.get("live_visual_state")
        if isinstance(compact, Mapping):
            compact_mapping = cast(Mapping[str, object], compact)
            performance_trace = compact_mapping.get("performance_trace_v3")
            if isinstance(performance_trace, Mapping):
                return dict(cast(Mapping[str, object], performance_trace))
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Performance trace not available.")

    @app.get("/v1/mobile/performance/trace/v3")
    async def performance_trace_v3(session_id: str | None = None) -> dict[str, object]:
        return await performance_trace_v3_for_session(session_id or resolve_window_tracker_dashboard_session_id(None))

    @app.post("/v1/mobile/frontend/heartbeat/v3")
    async def frontend_heartbeat_v3(payload: dict[str, object] = Body(...)) -> dict[str, object]:
        try:
            heartbeat_session_id = str(payload.get("session_id") or "").strip() or resolve_window_tracker_dashboard_session_id(None)
            rendered_frame = int(_epoch_float(payload.get("rendered_frame_id") or payload.get("frame_id"), 0.0))
            heartbeat_overlay_version = str(payload.get("overlay_state_version") or "").strip()
            payload_overlay_count = int(_epoch_float(payload.get("overlay_count"), 0.0))
            payload_visible_count = (
                payload.get("visible_overlay_count")
                if payload.get("visible_overlay_count") is not None
                else payload.get("overlay_count")
            )
            visible_overlay_count = int(_epoch_float(payload_visible_count, 0.0))
            latest_heartbeat = latest_frontend_heartbeat(heartbeat_session_id)
            latest_heartbeat_payload = _mapping_to_plain_dict(latest_heartbeat)
            latest_visible_source = (
                latest_heartbeat_payload.get("visible_overlay_count")
                if latest_heartbeat_payload.get("visible_overlay_count") is not None
                else latest_heartbeat_payload.get("overlay_count")
            )
            latest_visible_count = int(
                _epoch_float(
                    latest_visible_source,
                    0.0,
                )
            )
            latest_received_ms = _epoch_float(latest_heartbeat_payload.get("received_at_ms"), 0.0)
            latest_age_ms = max(0.0, time.time() * 1000.0 - latest_received_ms) if latest_received_ms > 0.0 else 999999.0
            heartbeat_route = str(payload.get("route") or "").strip().lower()
            heartbeat_mode = str(payload.get("overlay_mode") or payload.get("mode") or "").strip().upper()
            if visible_overlay_count > 0 and not heartbeat_overlay_version:
                return {
                    "schema_version": "PG_FRONTEND_HEARTBEAT_V3",
                    "session_id": heartbeat_session_id,
                    "status": "ignored",
                    "reason": "missing_overlay_state_version",
                    "rendered_frame_id": rendered_frame,
                }
            if (latest_visible_count > 0 or payload_overlay_count > 0) and visible_overlay_count <= 0:
                if heartbeat_route == "live" and heartbeat_mode == "CLEAN_LIVE" and latest_visible_count > 0 and latest_age_ms <= 7000.0:
                    return {
                        "schema_version": "PG_FRONTEND_HEARTBEAT_V3",
                        "session_id": heartbeat_session_id,
                        "surface_id": str(latest_heartbeat_payload.get("surface_id") or "dashboard"),
                        "status": "ignored",
                        "reason": "transient_empty_overlay_heartbeat",
                        "rendered_frame_id": rendered_frame,
                        "latest_visible_overlay_count": latest_visible_count,
                        "latest_heartbeat_age_ms": round(latest_age_ms, 3),
                    }
                degraded_payload = dict(payload)
                degraded_payload["status"] = "DEGRADED"
                degraded_payload["degraded_reason"] = "degraded_overlay_heartbeat"
                heartbeat = record_frontend_heartbeat(degraded_payload)
                heartbeat["reason"] = "degraded_overlay_heartbeat"
                heartbeat["latest_visible_overlay_count"] = latest_visible_count
                return cast(dict[str, object], heartbeat)
            return cast(dict[str, object], record_frontend_heartbeat(payload))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/v1/mobile/frontend/heartbeat/v3")
    async def latest_frontend_heartbeat_v3(session_id: str | None = None, surface_id: str | None = None) -> dict[str, object]:
        resolved_session_id = str(session_id or "").strip() or resolve_window_tracker_dashboard_session_id(None)
        resolved_surface_id = str(surface_id or "dashboard").strip() or "dashboard"
        heartbeat = latest_frontend_heartbeat(resolved_session_id, surface_id=resolved_surface_id)
        if heartbeat is None:
            return {
                "schema_version": "PG_FRONTEND_HEARTBEAT_V3",
                "session_id": resolved_session_id,
                "surface_id": resolved_surface_id,
                "status": "missing",
            }
        return cast(dict[str, object], heartbeat)

    @app.get("/v1/mobile/model-council/sessions/{session_id}/latest")
    def latest_model_council_state_for_session(session_id: str) -> dict[str, object]:
        try:
            payload = latest_model_council_state_from_live_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model Council state not found.") from exc
        return payload

    @app.get("/v1/mobile/model-council/latest")
    def latest_model_council_state(session_id: str | None = None) -> dict[str, object]:
        requested_session_id = str(session_id or "").strip()
        if not requested_session_id:
            payload = resolve_model_council_session_payload(None)
            requested_session_id = str(payload.get("session_id", "") or "").strip()
        if not requested_session_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.")
        try:
            payload = latest_model_council_state_from_live_session(requested_session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model Council state not found.") from exc
        return payload

    @app.get("/v1/mobile/model-council/sessions/{session_id}/study/latest")
    def latest_model_council_study_packet_for_session(session_id: str) -> dict[str, object]:
        try:
            packet = latest_model_council_study_packet_from_live_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model Council study packet not found.") from exc
        _raise_if_stale_payload(cast(Mapping[str, object], packet), detail="Model Council study packet is stale.")
        return packet

    @app.get("/v1/mobile/model-council/study/latest")
    def latest_model_council_study_packet(session_id: str | None = None) -> dict[str, object]:
        requested_session_id = str(session_id or "").strip()
        if not requested_session_id:
            payload = resolve_model_council_session_payload(None)
            requested_session_id = str(payload.get("session_id", "") or "").strip()
        if not requested_session_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.")
        try:
            packet = latest_model_council_study_packet_from_live_session(requested_session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model Council study packet not found.") from exc
        _raise_if_stale_payload(cast(Mapping[str, object], packet), detail="Model Council study packet is stale.")
        return packet

    def build_floating_state_for_session(session_id: str | None = None, *, include_inspector: bool = False) -> dict[str, object]:
        requested_session_id = str(session_id or "").strip()
        tracker_payload: dict[str, object] | None = None
        if not explicit_window_tracker_service and requested_session_id:
            tracker_payload = _direct_model_council_fast_payload(requested_session_id)
        if tracker_payload is None:
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
                model_rows = cast(Sequence[object], models)
                total = len(model_rows)
                awake = sum(
                    1
                    for model in model_rows
                    if str(_mapping_to_plain_dict(model).get("status", "") or "").strip().upper()
                    in {"AWAKE", "RUNNING", "READY"}
                )
            elif council_health.get("all_required_models_awake") is True:
                required_roles = council_health.get("required_roles")
                total = len(cast(Sequence[object], required_roles)) if isinstance(required_roles, list) and required_roles else 7
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
            signal_payload = latest_model_council_execution_packet_from_live_session(resolved_session_id)
        except KeyError:
            try:
                signal_payload = latest_model_council_study_packet_from_live_session(resolved_session_id)
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
        tracking_summary = _mapping_to_plain_dict(tracker_payload.get("tracking_summary"))
        overlay_geometry = _mapping_to_plain_dict(tracking_summary.get("overlay_geometry"))
        overlay_geometry_boxes = overlay_geometry.get("boxes")
        overlay_geometry_count = (
            len(cast(Sequence[object], overlay_geometry_boxes))
            if isinstance(overlay_geometry_boxes, list)
            else 0
        )
        session_overlay_objects = tracker_payload.get("overlay_objects")
        session_overlay_count = (
            len(cast(Sequence[object], session_overlay_objects))
            if isinstance(session_overlay_objects, list)
            else 0
        )
        clean_object_count = max(overlay_geometry_count, session_overlay_count)
        renderable_count = int(
            _epoch_float(
                tracker_payload.get("renderable_count")
                or overlay_geometry.get("renderable_count")
                or tracking_summary.get("renderable_count")
                or tracker_payload.get("overlay_count")
                or overlay_geometry.get("overlay_count")
                or tracking_summary.get("overlay_count")
                or clean_object_count,
                0.0,
            )
        )
        if clean_object_count > 0 and renderable_count <= 0:
            renderable_count = clean_object_count
        if renderable_count > 0 or clean_object_count > 0:
            overlay_count = int(
                _epoch_float(
                    tracker_payload.get("overlay_count")
                    or overlay_geometry.get("overlay_count")
                    or tracking_summary.get("overlay_count")
                    or renderable_count,
                    float(renderable_count),
                )
            )
            state_payload["overlay_count"] = overlay_count
            state_payload["renderable_count"] = renderable_count
            state_payload["overlay_frame_id"] = tracker_payload.get("overlay_frame_id") or tracker_payload.get("overlay_object_frame_id")
            state_payload["frame_id"] = tracker_payload.get("frame_id") or tracker_payload.get("frame_index")
            state_payload["state_version"] = tracker_payload.get("state_version")
            state_payload["overlays"] = {
                "overlay_count": overlay_count,
                "renderable_count": renderable_count,
                "frame_id": state_payload.get("frame_id"),
                "overlay_frame_id": state_payload.get("overlay_frame_id"),
                "chart_transform_id": tracker_payload.get("chart_transform_id")
                or _mapping_to_plain_dict(overlay_geometry.get("chart_transform")).get("chart_transform_id"),
                "source": "tracker_payload_overlay_summary",
            }
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
        return _latest_shooter_handshake_or_waiting(session_id)

    @app.get("/v1/mobile/shooter/handshake")
    def latest_shooter_handshake(session_id: str | None = None) -> dict[str, object]:
        requested_session_id = str(session_id or "").strip() or None
        return _latest_shooter_handshake_or_waiting(requested_session_id)

    @app.get("/v1/mobile/model-council/sessions/{session_id}/execution/latest")
    def latest_model_council_execution_packet_for_session(session_id: str) -> dict[str, object]:
        try:
            packet = latest_model_council_execution_packet_from_live_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model Council executable packet not found.") from exc
        _raise_if_stale_payload(cast(Mapping[str, object], packet), detail="Model Council executable packet is stale.")
        return packet

    @app.get("/v1/mobile/model-council/execution/latest")
    def latest_model_council_execution_packet(session_id: str | None = None) -> dict[str, object]:
        requested_session_id = str(session_id or "").strip()
        if not requested_session_id:
            payload = resolve_model_council_session_payload(None)
            requested_session_id = str(payload.get("session_id", "") or "").strip()
        if not requested_session_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.")
        try:
            packet = latest_model_council_execution_packet_from_live_session(requested_session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model Council executable packet not found.") from exc
        _raise_if_stale_payload(cast(Mapping[str, object], packet), detail="Model Council executable packet is stale.")
        return packet

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

        def _model_council_state_from_trace_tracker() -> dict[str, object]:
            result = _mapping_to_plain_dict(tracker_payload.get("model_council_result"))
            study_packet = model_council_study_packet_from_payload(cast(Mapping[str, Any], tracker_payload))
            packet = model_council_packet_from_payload(cast(Mapping[str, Any], tracker_payload))
            if not result and not study_packet and not packet:
                return cast(dict[str, object], get_window_tracker_service().latest_model_council_state(resolved_session_id))
            return {
                "session_id": str(tracker_payload.get("session_id", resolved_session_id) or resolved_session_id),
                "model_council_result": result,
                "model_council_study_packet": study_packet,
                "model_council_packet": packet,
                "execution_packet_present": bool(packet),
                "execution_packet_id": str(packet.get("packet_id", "") or "") if packet else "",
                "promotion_trace": _mapping_to_plain_dict(
                    result.get("promotion_trace")
                    or study_packet.get("promotion_trace")
                    or _mapping_to_plain_dict(result.get("model_council")).get("promotion_trace")
                ),
            }

        def _study_packet_from_trace_tracker() -> dict[str, object]:
            packet = model_council_study_packet_from_payload(cast(Mapping[str, Any], tracker_payload))
            if packet:
                return cast(dict[str, object], packet)
            return cast(dict[str, object], get_window_tracker_service().latest_model_council_study_packet(resolved_session_id))

        def _execution_packet_from_trace_tracker() -> dict[str, object]:
            packet = model_council_packet_from_payload(cast(Mapping[str, Any], tracker_payload))
            if packet:
                return cast(dict[str, object], packet)
            return cast(dict[str, object], get_window_tracker_service().latest_model_council_packet(resolved_session_id))

        def _runtime_trace_floating_state_summary() -> dict[str, object]:
            tracking_summary = _mapping_to_plain_dict(tracker_payload.get("tracking_summary"))
            latest_signal = _mapping_to_plain_dict(tracker_payload.get("latest_signal"))
            overlay_geometry = _mapping_to_plain_dict(
                tracking_summary.get("overlay_geometry") or latest_signal.get("overlay_geometry")
            )
            overlay_truth_audit = _mapping_to_plain_dict(
                tracking_summary.get("overlay_truth_audit")
                or latest_signal.get("overlay_truth_audit")
                or overlay_geometry.get("truth_audit")
            )
            audit_objects = overlay_truth_audit.get("objects")
            audit_object_count = len(cast(Sequence[object], audit_objects)) if isinstance(audit_objects, list) else 0
            clean_live_state: dict[str, object] = {}
            try:
                clean_live_state = live_state_v3_for_session(resolved_session_id, mode="CLEAN_LIVE", compact=True)
            except Exception:
                clean_live_state = {}
            clean_overlays = _mapping_to_plain_dict(clean_live_state.get("overlays"))
            clean_objects = clean_overlays.get("objects")
            clean_object_count = len(cast(Sequence[object], clean_objects)) if isinstance(clean_objects, list) else 0
            renderable_count = int(
                _epoch_float(
                    clean_live_state.get("renderable_count")
                    or clean_overlays.get("renderable_count")
                    or clean_live_state.get("overlay_count")
                    or clean_overlays.get("overlay_count")
                    or clean_object_count,
                    0.0,
                )
            )
            if clean_object_count > 0 and renderable_count <= 0:
                renderable_count = clean_object_count
            if renderable_count <= 0:
                try:
                    registry_rows = _direct_performance_overlay_rows(resolved_session_id, now_epoch=trace_created_epoch_sec)
                except Exception:
                    registry_rows = []
                renderable_count = len(registry_rows) or audit_object_count
            if renderable_count <= 0:
                renderable_count = int(_epoch_float(overlay_geometry.get("visible_default_count") or 0, 0.0))
            hidden_count = int(
                _epoch_float(
                    clean_live_state.get("hidden_count")
                    or clean_overlays.get("hidden_count")
                    or overlay_geometry.get("hidden_default_count")
                    or 0,
                    0.0,
                )
            )
            rejected_count = int(
                _epoch_float(
                    clean_live_state.get("rejected_count")
                    or clean_overlays.get("rejected_count")
                    or overlay_truth_audit.get("invalid_object_count")
                    or overlay_truth_audit.get("decision_invalid_object_count")
                    or 0,
                    0.0,
                )
            )
            frontend_heartbeat = latest_frontend_heartbeat(resolved_session_id)
            frontend_payload = _mapping_to_plain_dict(frontend_heartbeat)
            received_at_epoch = _epoch_float(frontend_payload.get("received_at_ms"), 0.0) / 1000.0
            heartbeat_age_sec = trace_created_epoch_sec - received_at_epoch if received_at_epoch > 0 else 999999.0
            heartbeat_fresh = 0.0 <= heartbeat_age_sec <= 10.0
            frontend_overlay_mode = str(frontend_payload.get("overlay_mode") or "").strip().upper()
            frontend_count = renderable_count
            frontend_count_source = "backend_clean_live_authority"
            if heartbeat_fresh and frontend_overlay_mode == "CLEAN_LIVE":
                frontend_count = int(
                    _epoch_float(
                        frontend_payload.get("visible_overlay_count")
                        or frontend_payload.get("overlay_count"),
                        float(renderable_count),
                    )
                )
                frontend_count_source = "fresh_frontend_heartbeat"
            elif heartbeat_fresh and frontend_overlay_mode:
                frontend_count_source = f"ignored_frontend_heartbeat_mode_{frontend_overlay_mode.lower()}"
            if frontend_count <= 0 and renderable_count > 0:
                frontend_count = renderable_count
            source_packet = _mapping_to_plain_dict(
                tracker_payload.get("model_council_packet")
                or tracker_payload.get("execution_packet")
                or tracker_payload.get("model_council_study_packet")
                or tracker_payload.get("latest_signal")
            )
            return {
                "schema_version": "FloatingStateV2",
                "summary_schema_version": "PG_RUNTIME_TRACE_FLOATING_STATE_SUMMARY_V1",
                "session_id": resolved_session_id,
                "mode": "LIVE",
                "timestamp": trace_created_epoch_sec,
                "state_chip": "TRACE_SUMMARY",
                "packet_id": str(source_packet.get("packet_id") or source_packet.get("signal_id") or ""),
                "overlay_count": frontend_count,
                "renderable_count": renderable_count,
                "overlay_rejected_count": rejected_count,
                "overlays": {
                    "renderable_count": renderable_count,
                    "overlay_count": frontend_count,
                    "hidden_count": hidden_count,
                    "rejected_count": rejected_count,
                    "source": "clean_live_compact_state" if clean_live_state else "overlay_truth_audit",
                    "frontend_count_source": frontend_count_source,
                    "frontend_heartbeat_fresh": heartbeat_fresh,
                    "frontend_heartbeat_overlay_mode": frontend_overlay_mode,
                    "frontend_heartbeat_age_sec": round(max(0.0, heartbeat_age_sec), 3) if received_at_epoch > 0 else None,
                },
                "health": {
                    "tracker": str(tracker_runtime.get("state") or "RUNNING"),
                    "models_awake": _mapping_to_plain_dict(tracker_payload.get("model_health")).get("models_awake"),
                    "cache": str(tracker_payload.get("cache_status") or tracker_payload.get("cache") or "UNKNOWN"),
                    "latency_sec": round(max(0.0, trace_created_epoch_sec - _epoch_float(tracker_payload.get("updated_at_epoch") or tracker_payload.get("timestamp"), trace_created_epoch_sec)), 3),
                },
                "frontend_heartbeat": frontend_payload,
            }

        model_council_latest: dict[str, object] = collect(
            "model_council_latest",
            _model_council_state_from_trace_tracker,
        )
        study_latest: dict[str, object] = collect(
            "study_latest",
            _study_packet_from_trace_tracker,
        )
        execution_latest: dict[str, object] = collect(
            "execution_latest",
            _execution_packet_from_trace_tracker,
        )
        floating_state: dict[str, object] = collect(
            "floating_state",
            _runtime_trace_floating_state_summary,
        )
        shooter_handshake: dict[str, object] = collect("shooter_handshake", lambda: _latest_shooter_handshake(resolved_session_id))
        model_health: dict[str, object] = collect(
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

        package_reporter_status: dict[str, object] = {
            "status": "PASS" if shooter_handshake.get("status") == "PASS" else "WAITING",
            "schema_version": "PG_SHOOTER_PACKAGE_REPORT_V1",
            "mode": "PACKAGE_REPORTER",
            "execution_removed": True,
            "broker_click_allowed": False,
            "reported_package_type": str(shooter_handshake.get("package_type") or ""),
            "next_required": "fresh accepted intraday or swing allowance package"
            if shooter_handshake.get("status") != "PASS"
            else "",
        }
        cache_status: dict[str, object] = {
            "status": str(tracker_payload.get("cache_status") or tracker_payload.get("cache") or "UNKNOWN").upper(),
            "source": "tracker_latest",
        }
        endpoints: dict[str, Mapping[str, object]] = {
            "tracker_latest": tracker_latest,
            "model_council_latest": model_council_latest,
            "study_latest": study_latest,
            "execution_latest": execution_latest,
            "floating_state": floating_state,
            "shooter_handshake": shooter_handshake,
            "model_health": model_health,
            "package_reporter_status": package_reporter_status,
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
        shooter_payload_for_alignment = _mapping_to_plain_dict(shooter_handshake.get("payload"))
        shooter_packet_type_for_alignment = str(shooter_payload_for_alignment.get("packet_type") or "").strip().upper()
        if (
            execution_latest.get("status") == "PASS"
            and packet_ids["execution"]
            and packet_ids["shooter"]
            and packet_ids["execution"] != packet_ids["shooter"]
            and shooter_packet_type_for_alignment == "PG_EXECUTION_PACKET_V3"
        ):
            issues.append("execution_latest_shooter_packet_mismatch")
        if floating_state.get("status") == "PASS" and str(floating_state).lower().find("n/a") >= 0:
            issues.append("floating_state_contains_raw_na")

        def _endpoint_status(name: str) -> str:
            status_value = str(_mapping_to_plain_dict(endpoints.get(name)).get("status") or "MISSING").upper()
            if status_value == "PASS":
                return "PASS"
            if status_value == "STALE":
                return "STALE"
            return "MISSING"

        def _first_trace_mapping(*values: Any) -> dict[str, Any]:
            for value in values:
                candidate = _mapping_to_plain_dict(value)
                if candidate:
                    return candidate
            return {}

        def _first_trace_sequence(*values: Any) -> list[Any]:
            for value in values:
                if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                    rows = list(cast(Sequence[Any], value))
                    if rows:
                        return rows
            return []

        tracking_summary = _mapping_to_plain_dict(tracker_payload.get("tracking_summary"))
        latest_signal = _mapping_to_plain_dict(tracker_payload.get("latest_signal"))
        model_council_payload = _mapping_to_plain_dict(model_council_latest.get("payload"))
        model_council_result = _mapping_to_plain_dict(
            model_council_payload.get("model_council_result")
            or model_council_payload.get("result")
            or model_council_payload
        )
        study_payload = _mapping_to_plain_dict(study_latest.get("payload"))
        execution_payload = _mapping_to_plain_dict(execution_latest.get("payload"))
        study_model_council = _mapping_to_plain_dict(study_payload.get("model_council"))
        execution_model_council = _mapping_to_plain_dict(execution_payload.get("model_council"))
        broker_surface = _mapping_to_plain_dict(tracker_payload.get("broker_surface"))
        broker_surface_source = _mapping_to_plain_dict(broker_surface.get("broker_source"))
        broker_surface_lock = _mapping_to_plain_dict(broker_surface.get("broker_source_lock"))
        tracking_surface = _mapping_to_plain_dict(tracking_summary.get("broker_surface"))
        tracking_surface_source = _mapping_to_plain_dict(tracking_surface.get("broker_source"))
        tracking_surface_lock = _mapping_to_plain_dict(tracking_surface.get("broker_source_lock"))
        broker_source_lock = _first_trace_mapping(
            tracker_payload.get("broker_source_lock"),
            tracker_payload.get("broker_source"),
            tracking_summary.get("broker_source_lock"),
            tracking_summary.get("broker_source"),
            latest_signal.get("broker_source_lock"),
            latest_signal.get("broker_source"),
            broker_surface_lock,
            broker_surface_source,
            tracking_surface_lock,
            tracking_surface_source,
        )
        source_lock_status_text = str(broker_source_lock.get("status") or broker_source_lock.get("state") or "").strip().upper()
        source_lock_invalid_statuses = {
            "FAIL",
            "FAILED",
            "INVALID",
            "MISSING",
            "WRONG_SURFACE",
            "SURFACE_MISMATCH",
            "TITLE_MATCH_PIXEL_MISMATCH",
            "BROKER_CONTROL_PIXELS_MISSING",
            "CHART_SOURCE_PIXELS_MISSING",
        }
        source_lock_wrong_surface = bool(
            broker_source_lock.get("wrong_surface") is True
            or broker_source_lock.get("surface_wrong") is True
            or source_lock_status_text in source_lock_invalid_statuses
        )
        source_lock_valid = bool(
            not source_lock_wrong_surface
            and (
                broker_source_lock.get("valid") is True
                or source_lock_status_text in {"PASS", "VALID", "LOCKED", "BROKER_SOURCE_LOCKED"}
            )
        )
        source_lock_status = "PASS" if source_lock_valid else ("MISSING" if not broker_source_lock else "FAIL")
        locked_overlay_authority = bool(
            (tracker_payload.get("display_snapshot_only_v3") or tracker_payload.get("display_fast_path_v3"))
            and (
                tracker_payload.get("last_full_overlay_path")
                or tracker_payload.get("last_overlay_path")
                or tracking_summary.get("last_full_overlay_path")
                or tracking_summary.get("last_overlay_path")
            )
        )
        if source_lock_status == "FAIL" and not packet_ids["execution"] and locked_overlay_authority:
            broker_source_lock["display_only_overlay_authority_locked"] = True
            broker_source_lock["display_only_overlay_authority_status"] = "PASS"
            broker_source_lock["display_only_overlay_authority_reason"] = (
                "Visual overlay continuity is allowed, but broker source certification remains failed."
            )
        model_health_payload = _mapping_to_plain_dict(_mapping_to_plain_dict(model_health.get("payload")).get("runtime_model_health") or model_health.get("payload"))
        if not model_health_payload:
            model_health_payload = _mapping_to_plain_dict(model_health.get("payload"))
        model_warm_pass = bool(model_health_payload.get("all_required_models_awake") is True)
        overlay_payload = _mapping_to_plain_dict(floating_state.get("payload"))
        overlay_root = _mapping_to_plain_dict(overlay_payload.get("overlays"))
        overlay_rejected_count = int(_epoch_float(overlay_root.get("rejected_count") or overlay_payload.get("overlay_rejected_count") or 0, 0.0))
        overlay_backend_count = int(_epoch_float(overlay_root.get("renderable_count") or overlay_payload.get("renderable_count") or 0, 0.0))
        overlay_frontend_count = int(_epoch_float(overlay_payload.get("overlay_count") or overlay_backend_count or 0, 0.0))
        overlay_truth_pass = overlay_backend_count > 0 and overlay_backend_count == overlay_frontend_count
        promotion_trace = _first_trace_mapping(
            _mapping_to_plain_dict(study_latest.get("payload")).get("promotion_trace"),
            _mapping_to_plain_dict(execution_latest.get("payload")).get("promotion_trace"),
            _mapping_to_plain_dict(model_council_latest.get("payload")).get("promotion_trace"),
        )
        council_trace_pass = bool(
            promotion_trace.get("denied_at")
            or promotion_trace.get("next_required")
            or promotion_trace.get("release_condition")
            or packet_ids["execution"]
        )
        packet_contract_pass: bool = (
            (execution_latest.get("status") in {"PASS", "MISSING"} and not packet_ids["execution"])
            or execution_latest.get("status") == "PASS"
        )
        shooter_payload = _mapping_to_plain_dict(shooter_handshake.get("payload"))
        shooter_persistence_pass = shooter_handshake.get("status") in {"PASS", "MISSING"} and (
            str(shooter_payload.get("packet_type") or "").upper() != "STUDY_PACKET"
            or str(shooter_payload.get("reason") or "").strip()
        )
        burn_in_payload = _mapping_to_plain_dict(tracker_payload.get("burn_in") or tracker_payload.get("runtime_burn_in"))
        burn_in_pass = bool(
            _epoch_float(burn_in_payload.get("hours", 0.0) or 0.0, 0.0) >= 2.0
            and int(_epoch_float(burn_in_payload.get("crash_count", 0) or 0, 0.0)) == 0
        )

        def _gate(name: str, passed: bool, *, status_value: str = "", evidence: Mapping[str, Any] | None = None) -> dict[str, Any]:
            resolved_status = status_value or ("PASS" if passed else "FAIL")
            return {
                "gate": name,
                "status": resolved_status,
                "passed": bool(passed),
                "evidence": dict(evidence or {}),
            }

        certification_gates = {
            "source_lock": _gate("source_lock", source_lock_valid, status_value=source_lock_status, evidence=broker_source_lock),
            "frame_freshness": _gate("frame_freshness", _endpoint_status("tracker_latest") == "PASS", evidence=tracker_runtime),
            "sequence_context": _gate(
                "sequence_context",
                bool(sequence_context_readiness.get("ready")),
                status_value="PASS" if sequence_context_readiness.get("ready") else "INCOMPLETE",
                evidence=sequence_context_readiness,
            ),
            "model_warm_state": _gate("model_warm_state", model_warm_pass, evidence=model_health_payload),
            "overlay_truth": _gate(
                "overlay_truth",
                overlay_truth_pass,
                evidence={
                    "backend_renderable_count": overlay_backend_count,
                    "frontend_overlay_count": overlay_frontend_count,
                    "rejected_count": overlay_rejected_count,
                },
            ),
            "model_council_trace": _gate("model_council_trace", council_trace_pass, evidence=promotion_trace),
            "packet_contract": _gate(
                "packet_contract",
                bool(packet_contract_pass),
                status_value="PASS" if packet_contract_pass else "FAIL",
                evidence={"execution_packet_id": packet_ids["execution"], "execution_status": execution_latest.get("status")},
            ),
            "shooter_persistence": _gate("shooter_persistence", bool(shooter_persistence_pass), evidence=shooter_payload),
            "burn_in": _gate("burn_in", burn_in_pass, status_value="PASS" if burn_in_pass else "NOT_RUN", evidence=burn_in_payload),
        }
        reason = "none"
        if not packet_ids["execution"]:
            reason = str(
                promotion_trace.get("next_required")
                or sequence_context_readiness.get("next_required")
                or "execution packet not published"
            )
        market_object_evidence = _first_trace_mapping(
            tracking_summary.get("market_object_registry"),
            tracker_payload.get("market_object_registry"),
            tracking_summary.get("market_registry"),
            latest_signal.get("market_object_registry"),
        ) or (
            {"sequence_box_history_len": int(sequence_context_readiness.get("box_history_len") or 0)}
            if int(sequence_context_readiness.get("box_history_len") or 0) > 0
            else {}
        ) or (
            {"historical_structure": True}
            if _first_trace_sequence(
                tracking_summary.get("historical_structure"),
                latest_signal.get("historical_structure"),
                tracker_payload.get("historical_structure"),
            )
            else {}
        )
        regime_evidence = _first_trace_mapping(
            model_council_result.get("regime"),
            study_payload.get("regime"),
            execution_payload.get("regime"),
            study_model_council.get("regime"),
            execution_model_council.get("regime"),
            latest_signal.get("regime"),
            tracker_payload.get("regime"),
        )
        market_play_evidence = _first_trace_mapping(
            model_council_result.get("market_play"),
            study_payload.get("market_play"),
            execution_payload.get("market_play"),
            study_model_council.get("market_play"),
            execution_model_council.get("market_play"),
            latest_signal.get("market_play"),
            tracker_payload.get("market_play"),
        )
        price_location_evidence = _first_trace_mapping(
            model_council_result.get("price_location"),
            study_payload.get("price_location"),
            execution_payload.get("price_location"),
            study_model_council.get("price_location"),
            execution_model_council.get("price_location"),
            latest_signal.get("price_location"),
            tracker_payload.get("price_location"),
        )
        memory_evidence = _first_trace_mapping(
            model_council_result.get("memory_confirmation"),
            study_payload.get("memory_confirmation"),
            execution_payload.get("memory_confirmation"),
            latest_signal.get("memory_confirmation"),
            latest_signal.get("memory"),
            tracker_payload.get("memory_confirmation"),
        )
        pair_profile_evidence = _first_trace_mapping(
            model_council_result.get("pair_profile"),
            study_payload.get("pair_profile"),
            execution_payload.get("pair_profile"),
            latest_signal.get("pair_profile"),
            tracker_payload.get("pair_profile"),
        )
        skill_evidence = _first_trace_sequence(
            model_council_result.get("skill_contributions"),
            study_payload.get("skill_contributions"),
            execution_payload.get("skill_contributions"),
            latest_signal.get("skill_contributions"),
            tracker_payload.get("skill_contributions"),
            latest_signal.get("skill_gates"),
        )
        reasoning_evidence = _first_trace_mapping(
            model_council_result.get("reasoning_arbitration"),
            study_payload.get("reasoning_arbitration"),
            execution_payload.get("reasoning_arbitration"),
            latest_signal.get("reasoning_arbitration"),
            tracker_payload.get("reasoning_arbitration"),
        )
        lstm_evidence = _first_trace_mapping(
            model_council_result.get("lstm_contribution"),
            study_payload.get("lstm_contribution"),
            execution_payload.get("lstm_contribution"),
            latest_signal.get("lstm_contribution"),
            tracking_summary.get("lstm_contribution"),
        )
        two_candle_evidence = _first_trace_mapping(
            model_council_result.get("two_candle_study"),
            study_payload.get("two_candle_study"),
            execution_payload.get("two_candle_study"),
            latest_signal.get("two_candle_study"),
            tracking_summary.get("two_candle_study"),
            latest_signal.get("high_frequency_candle_cycle"),
            tracking_summary.get("high_frequency_candle_cycle"),
        )
        outcome_evidence = _first_trace_mapping(
            tracker_payload.get("outcome_feedback"),
            latest_signal.get("outcome_feedback"),
            tracking_summary.get("outcome_feedback"),
        )
        dataflow_nodes = {
            "BrokerSourceLockV3": source_lock_status,
            "LatestFrameBufferV3": _endpoint_status("tracker_latest"),
            "ChartSegmentationV3": "PASS" if tracking_summary or latest_signal else "MISSING",
            "CandleObjectTrackerV3": "PASS" if tracking_summary or latest_signal else "MISSING",
            "MarketObjectTrackerV3": "PASS" if market_object_evidence else "MISSING",
            "SequenceContextV3": "PASS" if sequence_context_readiness.get("ready") else "INCOMPLETE",
            "MultiModelRoleOutputsV3": "PASS" if model_warm_pass else _endpoint_status("model_health"),
            "RegimeEngineV3": "PASS" if regime_evidence else "MISSING",
            "MarketPlayEngineV3": "PASS" if market_play_evidence else "MISSING",
            "PriceLocationEngineV3": "PASS" if price_location_evidence else "MISSING",
            "VisualPlayMemoryBank": "PASS" if memory_evidence else "MISSING",
            "PairBehaviorProfileV3": "PASS" if pair_profile_evidence else "MISSING",
            "SkillContributionAggregatorV3": "PASS" if skill_evidence else "MISSING",
            "LSTM_CandleSequenceContributorV3": "PASS" if lstm_evidence else "MISSING",
            "TwoCandleStudyV3": "PASS" if two_candle_evidence else "MISSING",
            "ReasoningArbitratorV3": "PASS" if reasoning_evidence else "MISSING",
            "ModelCouncilV3": _endpoint_status("model_council_latest"),
            "STUDY_PACKET": _endpoint_status("study_latest"),
            "PG_EXECUTION_PACKET_V3": "PASS" if packet_ids["execution"] else "NOT_PUBLISHED",
            "PacketValidatorV3": "PASS" if packet_contract_pass else "FAIL",
            "OutcomeFeedbackV3": "PASS" if outcome_evidence else "WAITING",
            "RuntimeTraceV3": "PASS",
            "Dashboard/FloatingStateV2": _endpoint_status("floating_state"),
            "ShooterPackageReporter": "WAITING" if not packet_ids["execution"] else _endpoint_status("shooter_handshake"),
        }
        dataflow_contract_trace: dict[str, object] = {
            "schema_version": "PG_DATAFLOW_CONTRACT_TRACE_V3",
            "frame_id": int(_epoch_float(tracker_payload.get("frame_index") or tracker_payload.get("frame_id") or 0, 0.0)),
            "capture_count": int(_epoch_float(tracker_payload.get("capture_count") or 0, 0.0)),
            "state_version": int(_epoch_float(tracker_payload.get("state_version") or 0, 0.0)),
            "sequence_id": str(sequence_context_readiness.get("sequence_id") or ""),
            "nodes": dataflow_nodes,
            "reason": reason,
        }

        return {
            "schema_version": "PG_RUNTIME_TRACE_V3",
            "session_id": resolved_session_id,
            "trace_created_epoch_sec": trace_created_epoch_sec,
            "language_scorecard": public_language_scorecard(),
            "sequence_context_readiness": sequence_context_readiness,
            "dataflow_contract_trace": dataflow_contract_trace,
            "certification_gates": certification_gates,
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
                locked_hwnd=request.locked_hwnd,
                locked_title=request.locked_title,
                layout_profile=request.layout_profile,
                capture_interval_sec=request.capture_interval_sec,
                rl_track_interval_sec=request.rl_track_interval_sec,
                auto_start=request.auto_start,
                observer_settings=request.observer_settings,
                observer_policy=request.observer_policy,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.patch("/v1/mobile/window-tracker/sessions/{session_id}/locked-window")
    def update_tracker_session_locked_window(
        session_id: str,
        request: WindowTrackerLockedWindowRequest,
    ) -> dict[str, object]:
        try:
            return get_window_tracker_service().update_session_locked_window(
                session_id,
                locked_hwnd=request.locked_hwnd,
                locked_title=request.locked_title,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
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

    def _requested_overlay_layers(raw_layers: str | None) -> set[str] | None:
        if raw_layers is None:
            return None
        layers: set[str] = set()
        for item in re.split(r"[\s,]+", str(raw_layers or "").strip()):
            layer = item.strip().lower()
            if layer and re.fullmatch(r"[a-z0-9_:-]+", layer):
                layers.add(layer)
        return layers

    def render_v3_overlay_artifact_response(
        session_id: str,
        artifact_kind: str,
        overlay_mode: str | None = None,
        overlay_layers: str | None = None,
    ) -> Response | None:
        kind = str(artifact_kind or "").strip().lower()
        if kind not in {"overlay", "full-overlay"}:
            return None
        mode = _normalize_v3_artifact_mode(overlay_mode or "CLEAN_LIVE")
        requested_layers = _requested_overlay_layers(overlay_layers)
        try:
            tracker = get_window_tracker_service()
            live_state = build_live_state_v3_for_session(session_id, overlay_mode=mode, compact_public=False)
            chart_path = tracker.latest_artifact_path(session_id, "chart")
        except Exception:
            return None
        overlays_payload = _mapping_to_plain_dict(live_state.get("overlays"))
        overlay_dicts = _sequence_mappings(overlays_payload.get("objects"))
        if requested_layers is not None:
            overlay_dicts = [
                overlay
                for overlay in overlay_dicts
                if str(overlay.get("layer") or "").strip().lower() in requested_layers
            ]
        if not overlay_dicts and requested_layers is None:
            return None
        scene_graph = _mapping_to_plain_dict(live_state.get("scene_graph") or live_state.get("broker_scene_graph_v3"))
        chart_png = render_overlays_on_chart(
            chart_path,
            overlay_dicts,
            scene_graph=scene_graph,
            target_space="chart",
        )
        headers = {
            **dict(_NO_STORE_ARTIFACT_HEADERS),
            "X-PhoenixGuard-Overlay-Source": "live_state_v3",
            "X-PhoenixGuard-Overlay-Mode": mode,
        }
        if requested_layers is not None:
            headers["X-PhoenixGuard-Overlay-Layers"] = ",".join(sorted(requested_layers))
        if kind == "overlay":
            return Response(content=chart_png, media_type="image/png", headers=headers)
        try:
            window_path = tracker.latest_artifact_path(session_id, "window")
            full_png = render_overlays_on_chart(
                window_path,
                overlay_dicts,
                scene_graph=scene_graph,
                target_space="full",
            )
            return Response(content=full_png, media_type="image/png", headers=headers)
        except Exception:
            return None

    @app.get("/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-chart")
    def get_tracker_latest_chart(session_id: str) -> Response:
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
        if suffix in {".jpg", ".jpeg"}:
            try:
                from io import BytesIO

                from PIL import Image

                with Image.open(path) as image:
                    buffer = BytesIO()
                    image.convert("RGB").save(buffer, format="PNG")
                return Response(
                    content=buffer.getvalue(),
                    media_type="image/png",
                    headers=dict(_NO_STORE_ARTIFACT_HEADERS),
                )
            except Exception:
                media_type = "image/jpeg"
        return _safe_file_bytes_response(path, media_type=media_type)

    @app.get("/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-window")
    def get_tracker_latest_window(session_id: str) -> Response:
        try:
            path = get_window_tracker_service().latest_artifact_path(session_id, "window")
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        media_type = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        return _safe_file_bytes_response(path, media_type=media_type)

    @app.get("/v1/mobile/window-tracker/sessions/{session_id}/artifacts/files/{artifact_name}")
    def get_tracker_artifact_file(session_id: str, artifact_name: str) -> Response:
        safe_name = Path(str(artifact_name or "")).name
        if not safe_name or safe_name != str(artifact_name or ""):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid artifact name.")
        try:
            tracker = get_window_tracker_service()
            artifact_dir = tracker.session_dir(session_id) / "artifacts"
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        path = artifact_dir / safe_name
        try:
            if path.resolve().parent != artifact_dir.resolve():
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid artifact path.")
        except OSError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact is not readable.") from exc
        if not path.exists() or not path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.")
        media_type = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png" if path.suffix.lower() == ".png" else "application/json" if path.suffix.lower() == ".json" else None
        return _safe_file_bytes_response(path, media_type=media_type)

    @app.get("/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-{artifact_kind}")
    def get_tracker_latest_named_artifact(
        session_id: str,
        artifact_kind: str,
        mode: str | None = None,
        layers: str | None = None,
    ) -> Response:
        v3_response = render_v3_overlay_artifact_response(session_id, artifact_kind, mode, layers)
        if v3_response is not None:
            return v3_response
        try:
            path = get_window_tracker_service().latest_artifact_path(session_id, artifact_kind)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        media_type = "image/jpeg" if path.suffix.lower() in {".jpg", ".jpeg"} else "image/png"
        if path.suffix.lower() in {".jpg", ".jpeg"}:
            try:
                from io import BytesIO

                from PIL import Image

                with Image.open(path) as image:
                    buffer = BytesIO()
                    image.convert("RGB").save(buffer, format="PNG")
                return Response(
                    content=buffer.getvalue(),
                    media_type="image/png",
                    headers=dict(_NO_STORE_ARTIFACT_HEADERS),
                )
            except Exception:
                media_type = "image/jpeg"
        return _safe_file_bytes_response(path, media_type=media_type)

    @app.get("/v1/mobile/window-tracker/sessions/{session_id}/health")
    def get_tracker_health(session_id: str) -> dict[str, object]:
        try:
            tracker = get_window_tracker_service()
            # probe latest chart and overlay
            artifacts: dict[str, dict[str, object]] = {}
            result: dict[str, object] = {"session_id": session_id, "artifacts": artifacts, "registry_path": None}
            for kind in ("chart", "overlay", "window"):
                try:
                    path = tracker.latest_artifact_path(session_id, kind)
                    artifacts[kind] = {"path": str(path), "exists": path.exists()}
                except FileNotFoundError:
                    artifacts[kind] = {"path": None, "exists": False}
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
            active: list[Mapping[str, Any]] = query_recent_active_objects(session_id, min_truth_score=float(min_truth_score))
            chart_transform: object = None
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
            artifacts: dict[str, dict[str, object]] = {}
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
            overlay: dict[str, object] = {
                "count": total,
                "frame_matches_chart_frame": bool(total > 0 and not stale),
            }
            model_health: dict[str, object] = {"all_required_models_awake": True}
            try:
                study_packet = tracker.latest_model_council_study_packet(sid)
                study_packet_payload: dict[str, object] = {"exists": True, "packet_id": study_packet.get("packet_id")}
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
        live_state = live_state_v3_for_session(session_id, mode="CLEAN_LIVE", compact=True)
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
            overlay_dicts: list[Mapping[str, Any]] = []
            for entry in overlays:
                overlay = entry.get("overlay")
                if isinstance(overlay, Mapping):
                    overlay_dicts.append(cast(Mapping[str, Any], overlay))
            png = render_overlays_on_chart(chart_path if chart_path is not None else None, overlay_dicts)
            # optionally persist snapshot for golden/regression evidence
            save_dir = _RUNTIME_ROOT / "visual_evidence"
            try:
                save_dir.mkdir(parents=True, exist_ok=True)
                out_path = save_dir / f"{session_id}_render_latest.png"
                out_path.write_bytes(png)
                meta: dict[str, object] = {"session_id": session_id, "saved_at": time.time(), "path": str(out_path)}
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
        elif normalized_asset_path.startswith("floating-windows/"):
            relative_asset_path = normalized_asset_path.removeprefix("floating-windows/").replace("\\", "/")
            relative_parts = Path(relative_asset_path).parts
            if (
                not relative_asset_path
                or Path(relative_asset_path).is_absolute()
                or any(part in {"", ".", ".."} for part in relative_parts)
            ):
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard asset not found.")
            candidate = (_WINDOW_TRACKER_FLOATING_WINDOWS_DIR / relative_asset_path).resolve()
            asset_root = _WINDOW_TRACKER_FLOATING_WINDOWS_DIR.resolve()
            try:
                candidate.relative_to(asset_root)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard asset not found.") from exc
            suffix = candidate.suffix.lower()
            if suffix == ".css":
                media_type = "text/css"
            elif suffix == ".js":
                media_type = "application/javascript"
            else:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard asset not found.")
            path = candidate
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
        demo_path = FRONTEND_ROOT / "assets" / "share" / "overlay_demo.html"
        if not demo_path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard asset not found.")
        return HTMLResponse(demo_path.read_text(encoding="utf-8"))

    @app.get("/v1/mobile/window-tracker/floating-windows/overlay-editor/settings")
    def get_overlay_editor_settings() -> dict[str, object]:
        return _read_overlay_editor_settings()

    @app.post("/v1/mobile/window-tracker/floating-windows/overlay-editor/settings")
    def save_overlay_editor_settings(payload: dict[str, object] = Body(...)) -> dict[str, object]:
        settings = _write_overlay_editor_settings(payload)
        return {"status": "saved", "settings": settings}

    @app.get("/v1/mobile/window-tracker/floating-windows/model-strength/settings")
    def get_model_strength_settings() -> dict[str, object]:
        return read_model_strength_settings()

    @app.post("/v1/mobile/window-tracker/floating-windows/model-strength/settings")
    def save_model_strength_settings(payload: dict[str, object] = Body(...)) -> dict[str, object]:
        settings = write_model_strength_settings(payload)
        controls = model_strength_settings_to_execution_controls(settings)
        session_id = str(
            payload.get("session_id")
            or payload.get("sessionId")
            or _DEFAULT_WINDOW_TRACKER_DASHBOARD_SESSION_ID
        )
        applied = False
        if session_id:
            try:
                get_window_tracker_service().update_session_controls(session_id, **cast(Any, controls))
                applied = True
            except KeyError:
                applied = False
        return {"status": "saved", "settings": settings, "controls": controls, "applied": applied}

    @app.get("/v1/mobile/window-tracker/floating-windows/model-strength", response_class=HTMLResponse)
    def get_model_strength_window() -> HTMLResponse:
        window_path = _WINDOW_TRACKER_FLOATING_WINDOWS_DIR / "model_strength_window.html"
        if not window_path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Model strength window not found.")
        return HTMLResponse(window_path.read_text(encoding="utf-8"))

    @app.get("/v3/mobile/window-tracker/dashboard", response_class=HTMLResponse)
    @app.get("/v1/mobile/window-tracker/dashboard", response_class=HTMLResponse)
    def window_tracker_dashboard_default() -> HTMLResponse:
        session_id = resolve_window_tracker_dashboard_session_id()
        return HTMLResponse(_render_window_tracker_dashboard(session_id))

    @app.get("/v3/mobile/window-tracker/dashboard/{session_id}", response_class=HTMLResponse)
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
            latest_signal_raw: object = payload.get("latest_signal")
            latest_signal: Mapping[str, object]
            if isinstance(latest_signal_raw, Mapping):
                latest_signal = cast(Mapping[str, object], latest_signal_raw)
            else:
                latest_signal = _EMPTY_OBJECT_MAPPING
            tracking_raw: object = payload.get("tracking_summary")
            tracking: Mapping[str, object]
            if isinstance(tracking_raw, Mapping):
                tracking = cast(Mapping[str, object], tracking_raw)
            else:
                tracking = _EMPTY_OBJECT_MAPPING
            packet_raw: object = payload.get("model_council_packet") or payload.get("execution_packet")
            if isinstance(packet_raw, Mapping):
                packet_map: Mapping[str, object] = cast(Mapping[str, object], packet_raw)
            else:
                latest_packet: object = latest_signal.get("model_council_packet")
                packet_map = cast(Mapping[str, object], latest_packet) if isinstance(latest_packet, Mapping) else _EMPTY_OBJECT_MAPPING
            parts: dict[str, object] = {
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
                "last_display_window_path": payload.get("last_display_window_path"),
                "last_chart_path": payload.get("last_chart_path"),
                "last_overlay_path": payload.get("last_overlay_path"),
                "last_full_overlay_path": payload.get("last_full_overlay_path"),
                "signal_id": latest_signal.get("signal_id"),
                "published_epoch": latest_signal.get("published_epoch"),
                "hf_cycle": latest_signal.get("high_frequency_candle_cycle"),
                "tracking_updated": tracking.get("published_at"),
                "packet_id": packet_map.get("packet_id"),
            }
            return json.dumps(parts, sort_keys=True, default=str)

        def _events() -> Iterator[str]:
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
    def capture_tracker_session_once(session_id: str, display_only: bool = False) -> dict[str, object]:
        try:
            payload = get_window_tracker_service().capture_once(session_id, display_only=display_only)
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
                two_candle_execution_allowed=request.two_candle_execution_allowed,
                swing_fallback_enabled=request.swing_fallback_enabled,
                continuous_model_feed_enabled=request.continuous_model_feed_enabled,
                model_confidence_floor=request.model_confidence_floor,
                high_frequency_min_confidence=request.high_frequency_min_confidence,
                high_frequency_entry_grace_sec=request.high_frequency_entry_grace_sec,
                high_frequency_expiry_seconds=request.high_frequency_expiry_seconds,
                high_frequency_horizon_candles=request.high_frequency_horizon_candles,
                execution_threshold=request.execution_threshold,
                overlay_min_confidence=request.overlay_min_confidence,
                ai_contribution_strengths=request.ai_contribution_strengths,
                execution_lane_thresholds=request.execution_lane_thresholds,
                model_strength_profile=request.model_strength_profile,
                allow_live_momentum_entries=request.allow_live_momentum_entries,
                allow_opposing_force_reactions=request.allow_opposing_force_reactions,
                scenario_generation_enabled=request.scenario_generation_enabled,
                auto_memory_projection=request.auto_memory_projection,
                require_memory_projection=request.require_memory_projection,
                live_momentum_memory_advisory=request.live_momentum_memory_advisory,
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
                min_primary_target_candles=request.min_primary_target_candles,
                max_primary_target_candles=request.max_primary_target_candles,
                min_location_sniper_target_candles=request.min_location_sniper_target_candles,
                live_max_tracked_candles=request.live_max_tracked_candles,
                support_resistance_max_zones_per_role=request.support_resistance_max_zones_per_role,
                support_resistance_max_total_zones=request.support_resistance_max_total_zones,
                support_resistance_max_significant_zones=request.support_resistance_max_significant_zones,
                smart_money_max_liquidity_pools=request.smart_money_max_liquidity_pools,
                min_live_momentum_visible_candles=request.min_live_momentum_visible_candles,
                min_live_momentum_score=request.min_live_momentum_score,
                min_live_momentum_alignment=request.min_live_momentum_alignment,
                min_opposing_force_reaction_score=request.min_opposing_force_reaction_score,
                min_opposing_force_reaction_alignment=request.min_opposing_force_reaction_alignment,
                min_opposing_force_reaction_risk=request.min_opposing_force_reaction_risk,
                min_opposing_force_reaction_entry_score=request.min_opposing_force_reaction_entry_score,
                max_opposing_force_reaction_distance=request.max_opposing_force_reaction_distance,
                min_dominance_margin=request.min_dominance_margin,
                flip_flop_release_stable_reads=request.flip_flop_release_stable_reads,
                flip_flop_release_candidate_flips=request.flip_flop_release_candidate_flips,
                reversal_capture_min_dominance=request.reversal_capture_min_dominance,
                opportunity_capture_stable_reads=request.opportunity_capture_stable_reads,
                opportunity_capture_min_score=request.opportunity_capture_min_score,
                packet_valid_for_seconds=request.packet_valid_for_seconds,
                study_packet_valid_for_seconds=request.study_packet_valid_for_seconds,
                min_conf_global=request.min_conf_global,
                min_conf_latest=request.min_conf_latest,
                history_depth=request.history_depth,
                label_density=request.label_density,
                projection_focus=request.projection_focus,
                debug_depth=request.debug_depth,
                fuse_timeframe_overlays=request.fuse_timeframe_overlays,
                min_actionable_confidence=request.min_actionable_confidence,
                min_thesis_confidence=request.min_thesis_confidence,
                signal_cooldown_sec=request.signal_cooldown_sec,
                rl_track_interval_sec=request.rl_track_interval_sec,
                consensus_threshold=request.consensus_threshold,
                gates_pass_minimum=request.gates_pass_minimum,
                conformal_max_interval_pct=request.conformal_max_interval_pct,
                risk_min_pct=request.risk_min_pct,
                risk_max_pct=request.risk_max_pct,
                recall_boost_threshold=request.recall_boost_threshold,
                recall_veto_threshold=request.recall_veto_threshold,
                use_macro_local_alignment_gate=request.use_macro_local_alignment_gate,
                use_opposition_strength_gate=request.use_opposition_strength_gate,
                use_memory_ambiguity_penalty=request.use_memory_ambiguity_penalty,
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

    app.state.mobile_api_route_handlers = (
        health,
        v3_chart_state,
        v3_frame_latest_png,
        model_council_health,
        model_council_intelligence,
        live_state_v3,
        performance_trace_v3,
        frontend_heartbeat_v3,
        latest_frontend_heartbeat_v3,
        latest_model_council_state_for_session,
        latest_model_council_state,
        latest_model_council_study_packet_for_session,
        latest_model_council_study_packet,
        latest_floating_state,
        latest_floating_state_for_session,
        latest_shooter_handshake_for_session,
        latest_shooter_handshake,
        latest_model_council_execution_packet_for_session,
        latest_model_council_execution_packet,
        runtime_trace_v3,
        runtime_trace_v3_for_session,
        config,
        list_jobs,
        get_job,
        get_artifact,
        create_job,
        observer_config,
        list_observer_sessions,
        create_observer_session,
        get_observer_session,
        get_observer_latest_signal,
        get_observer_bundle,
        get_observer_artifact,
        submit_observer_bundle,
        list_tracker_windows,
        list_tracker_sessions,
        create_tracker_session,
        update_tracker_session_locked_window,
        get_tracker_session,
        set_tracker_focus_region,
        clear_tracker_focus_region,
        arm_tracker_focus_region,
        cancel_tracker_focus_region,
        get_tracker_latest_chart,
        get_tracker_latest_window,
        get_tracker_artifact_file,
        get_tracker_latest_named_artifact,
        get_tracker_health,
        get_active_registry,
        visual_health_v3,
        visual_health_v3_for_session,
        render_registry_snapshot,
        get_window_tracker_dashboard_asset,
        get_legacy_window_tracker_js_asset,
        get_overlay_demo_html,
        get_overlay_editor_settings,
        save_overlay_editor_settings,
        get_model_strength_settings,
        save_model_strength_settings,
        get_model_strength_window,
        window_tracker_dashboard_default,
        window_tracker_dashboard,
        dashboard_workspace,
        dashboard_workspace_for_session,
        stream_tracker_session,
        start_tracker_session,
        stop_tracker_session,
        emergency_stop_tracker_session,
        capture_tracker_session_once,
        execute_tracker_demo_random_trade,
        predict_tracker_session_from_memory,
        show_future_tracker_session_from_memory,
        update_tracker_session_controls,
        voice_status,
        voice_commands,
        voice_preferences,
        voice_command,
    )
    instrument_fastapi_app(app)
    return app
