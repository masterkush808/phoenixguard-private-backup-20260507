from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
from threading import Lock
import time
from typing import Any, Mapping, cast
from uuid import uuid4


FRONTEND_HEARTBEAT_SCHEMA_VERSION = "PG_FRONTEND_HEARTBEAT_V3"
VISUAL_HEALTH_SCHEMA_VERSION = "PG_VISUAL_HEALTH_V3"

DEFAULT_HEARTBEAT_STORE_DIR = Path(".codex_runtime") / "frontend_heartbeat_v3"
_HEARTBEAT_WRITE_LOCKS: dict[Path, Lock] = {}
_HEARTBEAT_WRITE_LOCKS_GUARD = Lock()
_HEARTBEAT_MEMORY_CACHE: dict[Path, dict[str, Any]] = {}
_HEARTBEAT_MEMORY_CACHE_GUARD = Lock()
_HEARTBEAT_SELECTION_CACHE: dict[tuple[str, str, str], tuple[float, dict[str, Any]]] = {}
_HEARTBEAT_SELECTION_CACHE_GUARD = Lock()


def _selection_cache_ttl_sec() -> float:
    try:
        return max(0.0, float(os.getenv("PHOENIXGUARD_FRONTEND_HEARTBEAT_SELECTION_CACHE_SEC", "5.0") or "5.0"))
    except ValueError:
        return 5.0


def _frame_skew_tolerance() -> int:
    try:
        return max(0, int(float(os.getenv("PHOENIXGUARD_FRONTEND_FRAME_SKEW_TOLERANCE", "5") or "5")))
    except ValueError:
        return 5


def _selection_cache_key(session_id: str, surface_id: str, store_dir: Path | str | None) -> tuple[str, str, str]:
    root = str(Path(store_dir or DEFAULT_HEARTBEAT_STORE_DIR))
    return (_slug(session_id), _slug(surface_id), root)


def _remember_selected_heartbeat(
    session_id: str,
    surface_id: str,
    store_dir: Path | str | None,
    heartbeat: Mapping[str, Any],
) -> None:
    key = _selection_cache_key(session_id, surface_id, store_dir)
    cached = dict(heartbeat)
    with _HEARTBEAT_SELECTION_CACHE_GUARD:
        _HEARTBEAT_SELECTION_CACHE[key] = (time.time(), cached)
        if len(_HEARTBEAT_SELECTION_CACHE) > 128:
            for stale_key in list(_HEARTBEAT_SELECTION_CACHE)[:32]:
                _HEARTBEAT_SELECTION_CACHE.pop(stale_key, None)


def _cached_selected_heartbeat(
    session_id: str,
    surface_id: str,
    store_dir: Path | str | None,
) -> dict[str, Any] | None:
    ttl_sec = _selection_cache_ttl_sec()
    if ttl_sec <= 0.0:
        return None
    key = _selection_cache_key(session_id, surface_id, store_dir)
    with _HEARTBEAT_SELECTION_CACHE_GUARD:
        cached = _HEARTBEAT_SELECTION_CACHE.get(key)
    if cached is None:
        return None
    cached_at, heartbeat = cached
    if time.time() - cached_at > ttl_sec:
        with _HEARTBEAT_SELECTION_CACHE_GUARD:
            _HEARTBEAT_SELECTION_CACHE.pop(key, None)
        return None
    received_at_ms = _float(heartbeat.get("received_at_ms"), 0.0)
    if received_at_ms <= 0.0:
        return None
    age_sec = max(0.0, (time.time() * 1000.0 - received_at_ms) / 1000.0)
    if age_sec > 30.0:
        return None
    selected = dict(heartbeat)
    selected.setdefault("selection_cache_reused_v3", True)
    return selected


def _heartbeat_write_lock(path: Path) -> Lock:
    key = path.absolute()
    with _HEARTBEAT_WRITE_LOCKS_GUARD:
        lock = _HEARTBEAT_WRITE_LOCKS.get(key)
        if lock is None:
            lock = Lock()
            _HEARTBEAT_WRITE_LOCKS[key] = lock
        return lock


def _remember_heartbeat(path: Path, heartbeat: Mapping[str, Any]) -> None:
    cached = dict(heartbeat)
    cached["path"] = str(path)
    with _HEARTBEAT_MEMORY_CACHE_GUARD:
        _HEARTBEAT_MEMORY_CACHE[path.absolute()] = cached


def _forget_heartbeat(path: Path) -> None:
    with _HEARTBEAT_MEMORY_CACHE_GUARD:
        _HEARTBEAT_MEMORY_CACHE.pop(path.absolute(), None)


def _cached_heartbeat(path: Path, *, max_age_sec: float = 30.0) -> dict[str, Any] | None:
    with _HEARTBEAT_MEMORY_CACHE_GUARD:
        cached = _HEARTBEAT_MEMORY_CACHE.get(path.absolute())
        if cached is None:
            return None
        heartbeat = dict(cached)
    received_at_ms = _float(heartbeat.get("received_at_ms"), 0.0)
    if received_at_ms <= 0.0:
        return None
    age_sec = max(0.0, (time.time() * 1000.0 - received_at_ms) / 1000.0)
    if age_sec > max_age_sec:
        _forget_heartbeat(path)
        return None
    heartbeat["path"] = str(path)
    heartbeat.setdefault("write_status", "MEMORY_FALLBACK")
    return heartbeat


def _now_iso_from_ms(now_ms: int | float | None = None) -> str:
    epoch = time.time() if now_ms is None else float(now_ms) / 1000.0
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if number != number:
        return float(default)
    return float(number)


def _int(value: Any, default: int = 0) -> int:
    return int(_float(value, float(default)))


def _mapping(value: object) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else {}


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip())[:120] or "default"


def _surface_key(value: str, default: str = "live") -> str:
    key = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return key or default


def _dashboard_heartbeat_surface_id(surface_id: str, route: str, overlay_mode: str, page_instance_id: str) -> str:
    if surface_id != "dashboard":
        return surface_id
    route_key = _surface_key(route)
    if route_key in {"live", "dashboard"} or "window_tracker_dashboard" in route_key:
        return f"dashboard_{page_instance_id}"[:120] if page_instance_id else "dashboard"
    mode_key = _surface_key(overlay_mode, default="clean_live")
    return f"dashboard_{route_key}_{mode_key}"[:120]


def _heartbeat_path(session_id: str, *, surface_id: str = "dashboard", store_dir: Path | str | None = None) -> Path:
    root = Path(store_dir or DEFAULT_HEARTBEAT_STORE_DIR)
    return root / f"{_slug(session_id)}__{_slug(surface_id)}.json"


def _is_live_dashboard_heartbeat(heartbeat: Mapping[str, Any]) -> bool:
    route_key = _surface_key(_text(heartbeat.get("route")))
    overlay_mode = _text(heartbeat.get("overlay_mode"), "CLEAN_LIVE").upper()
    route_live = route_key in {"live", "dashboard"} or "window_tracker_dashboard" in route_key
    return bool(route_live and overlay_mode == "CLEAN_LIVE")


def _heartbeat_visible_count(heartbeat: Mapping[str, Any]) -> int:
    source = heartbeat.get("visible_overlay_count") if heartbeat.get("visible_overlay_count") is not None else heartbeat.get("overlay_count")
    return _int(source)


def _heartbeat_age_sec(heartbeat: Mapping[str, Any]) -> float:
    received_at_ms = _float(heartbeat.get("received_at_ms"), 0.0)
    if received_at_ms <= 0.0:
        return float("inf")
    return max(0.0, (time.time() * 1000.0 - received_at_ms) / 1000.0)


def _heartbeat_rank(heartbeat: Mapping[str, Any]) -> tuple[int, float, int, int, int]:
    status_rank = 1 if _text(heartbeat.get("status")).upper() == "ALIVE" else 0
    age_sec = _heartbeat_age_sec(heartbeat)
    freshness_rank = 2 if age_sec <= 45.0 else 1 if age_sec <= 120.0 else 0
    visible_rank = 1 if _heartbeat_visible_count(heartbeat) > 0 else 0
    document_rank = 0 if bool(heartbeat.get("document_hidden") is True) else 1
    received_at_ms = _float(heartbeat.get("received_at_ms"), 0.0)
    return freshness_rank, received_at_ms, status_rank, visible_rank, document_rank


def _load_heartbeat_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return _cached_heartbeat(path)
    try:
        if path.stat().st_size <= 0:
            return _cached_heartbeat(path)
    except OSError:
        return _cached_heartbeat(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _cached_heartbeat(path)
    if not isinstance(payload, Mapping):
        return _cached_heartbeat(path)
    heartbeat = dict(cast(Mapping[str, Any], payload))
    heartbeat["path"] = str(path)
    _remember_heartbeat(path, heartbeat)
    return heartbeat


def normalize_frontend_heartbeat(payload: Mapping[str, Any], *, now_ms: int | float | None = None) -> dict[str, Any]:
    session_id = _text(payload.get("session_id"))
    if not session_id:
        raise ValueError("frontend heartbeat requires session_id")
    route = _text(payload.get("route"))
    overlay_mode = _text(payload.get("overlay_mode"), _text(payload.get("mode"), "CLEAN_LIVE")).upper()
    page_instance_id = _surface_key(_text(payload.get("page_instance_id")), default="")
    surface_id = _dashboard_heartbeat_surface_id(_text(payload.get("surface_id"), "dashboard"), route, overlay_mode, page_instance_id)
    epoch_ms = int(_float(payload.get("sent_at_ms"), float(now_ms if now_ms is not None else time.time() * 1000.0)))
    viewport = dict(cast(Mapping[str, Any], payload.get("viewport"))) if isinstance(payload.get("viewport"), Mapping) else {}
    render_size = dict(cast(Mapping[str, Any], payload.get("render_size"))) if isinstance(payload.get("render_size"), Mapping) else {}
    return {
        "schema_version": FRONTEND_HEARTBEAT_SCHEMA_VERSION,
        "session_id": session_id,
        "surface_id": surface_id,
        "sent_at_ms": epoch_ms,
        "received_at": _now_iso_from_ms(now_ms),
        "received_at_ms": int(now_ms if now_ms is not None else time.time() * 1000.0),
        "route": route,
        "overlay_mode": overlay_mode,
        "surface_mode": _text(payload.get("surface_mode"), "overlay"),
        "page_instance_id": page_instance_id,
        "page_visibility": _text(payload.get("page_visibility"), "unknown"),
        "document_hidden": bool(payload.get("document_hidden", False)),
        "status": _text(payload.get("status"), "ALIVE").upper(),
        "degraded_reason": _text(payload.get("degraded_reason", payload.get("reason"))),
        "frame_id": _int(payload.get("frame_id")),
        "rendered_frame_id": _int(payload.get("rendered_frame_id", payload.get("frame_id"))),
        "display_frame_id": _int(payload.get("display_frame_id")),
        "chart_frame_id": _int(payload.get("chart_frame_id")),
        "overlay_render_frame_id": _int(payload.get("overlay_render_frame_id")),
        "chart_transform_id": _text(payload.get("chart_transform_id")),
        "overlay_state_version": _text(payload.get("overlay_state_version")),
        "overlay_frame_state_version": _text(payload.get("overlay_frame_state_version")),
        "state_version": _text(payload.get("state_version")),
        "overlay_count": _int(payload.get("overlay_count", payload.get("visible_overlay_count", 0))),
        "visible_overlay_count": _int(payload.get("visible_overlay_count", payload.get("overlay_count", 0))),
        "selected_overlay_id": _text(payload.get("selected_overlay_id")),
        "frontend_loaded_ms": _int(payload.get("frontend_loaded_ms", payload.get("image_loaded_ms", 0))),
        "frontend_overlay_drawn_ms": _int(payload.get("frontend_overlay_drawn_ms", payload.get("overlay_drawn_ms", 0))),
        "image_load_ms": _float(payload.get("image_load_ms"), 0.0),
        "overlay_draw_ms": _float(payload.get("overlay_draw_ms"), 0.0),
        "viewport": viewport,
        "render_size": render_size,
        "full_broker_surface_visible": bool(payload.get("full_broker_surface_visible", False)),
        "frontend_state_version": _text(payload.get("frontend_state_version")),
    }


def record_frontend_heartbeat(
    payload: Mapping[str, Any],
    *,
    store_dir: Path | str | None = None,
    now_ms: int | float | None = None,
) -> dict[str, Any]:
    heartbeat = normalize_frontend_heartbeat(payload, now_ms=now_ms)
    path = _heartbeat_path(
        str(heartbeat["session_id"]),
        surface_id=str(heartbeat.get("surface_id") or "dashboard"),
        store_dir=store_dir,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    heartbeat["path"] = str(path)
    with _heartbeat_write_lock(path):
        tmp_path = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        try:
            tmp_path.write_text(json.dumps(heartbeat, indent=2, sort_keys=True), encoding="utf-8")
            replace_error = ""
            for attempt in range(12):
                try:
                    os.replace(tmp_path, path)
                    _remember_heartbeat(path, heartbeat)
                    break
                except PermissionError as exc:
                    replace_error = str(exc)
                    time.sleep(min(0.25, 0.02 * float(attempt + 1)))
            else:
                heartbeat["write_status"] = "DEGRADED_MEMORY_ONLY"
                heartbeat["write_error"] = replace_error or "heartbeat file replace permission denied"
                _remember_heartbeat(path, heartbeat)
        finally:
            try:
                if tmp_path.exists():
                    tmp_path.unlink()
            except OSError:
                pass
    session_id = str(heartbeat.get("session_id") or "")
    surface_id = str(heartbeat.get("surface_id") or "dashboard")
    if session_id:
        _remember_selected_heartbeat(session_id, surface_id, store_dir, heartbeat)
        if surface_id == "dashboard" or (surface_id.startswith("dashboard_") and _is_live_dashboard_heartbeat(heartbeat)):
            _remember_selected_heartbeat(session_id, "dashboard", store_dir, heartbeat)
    return heartbeat


def latest_frontend_heartbeat(
    session_id: str,
    *,
    surface_id: str = "dashboard",
    store_dir: Path | str | None = None,
) -> dict[str, Any] | None:
    cached_selection = _cached_selected_heartbeat(session_id, surface_id, store_dir)
    if cached_selection is not None:
        return cached_selection
    path = _heartbeat_path(session_id, surface_id=surface_id, store_dir=store_dir)
    if surface_id != "dashboard":
        heartbeat = _load_heartbeat_file(path)
        if heartbeat is not None:
            _remember_selected_heartbeat(session_id, surface_id, store_dir, heartbeat)
        return heartbeat
    root = Path(store_dir or DEFAULT_HEARTBEAT_STORE_DIR)
    candidates = [path]
    if root.exists():
        prefix = f"{_slug(session_id)}__dashboard"
        candidates.extend(candidate for candidate in root.glob(f"{prefix}*.json") if candidate != path)
    heartbeats = [
        heartbeat
        for heartbeat in (_load_heartbeat_file(candidate) for candidate in candidates)
        if heartbeat is not None
    ]
    if not heartbeats:
        fallback = _cached_heartbeat(path)
        if fallback is not None:
            _remember_selected_heartbeat(session_id, surface_id, store_dir, fallback)
        return fallback
    live_heartbeats = [heartbeat for heartbeat in heartbeats if _is_live_dashboard_heartbeat(heartbeat)]
    fresh_live_heartbeats = [heartbeat for heartbeat in live_heartbeats if _heartbeat_age_sec(heartbeat) <= 45.0]
    if fresh_live_heartbeats:
        selected = max(fresh_live_heartbeats, key=_heartbeat_rank)
    else:
        fresh_heartbeats = [heartbeat for heartbeat in heartbeats if _heartbeat_age_sec(heartbeat) <= 45.0]
        selected = max(fresh_heartbeats or live_heartbeats or heartbeats, key=_heartbeat_rank)
    _remember_selected_heartbeat(session_id, surface_id, store_dir, selected)
    return selected


def prune_frontend_heartbeats(
    *,
    store_dir: Path | str | None = None,
    max_age_sec: float = 120.0,
    now_ms: int | float | None = None,
) -> int:
    root = Path(store_dir or DEFAULT_HEARTBEAT_STORE_DIR)
    if not root.exists():
        return 0
    now = float(now_ms if now_ms is not None else time.time() * 1000.0)
    removed = 0
    for path in root.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            age_sec = max(0.0, (now - _float(payload.get("received_at_ms"), 0.0)) / 1000.0)
        except (OSError, ValueError, AttributeError):
            age_sec = max_age_sec + 1.0
        if age_sec > max_age_sec:
            try:
                path.unlink()
                _forget_heartbeat(path)
                removed += 1
            except OSError:
                pass
    return removed


def _state_overlay_count(backend_state: Mapping[str, Any]) -> int:
    if isinstance(backend_state.get("overlay_objects"), list):
        return len(cast(list[Any], backend_state["overlay_objects"]))
    overlays = _mapping(backend_state.get("overlays"))
    if isinstance(overlays.get("objects"), list):
        return len(cast(list[Any], overlays["objects"]))
    live_state = _mapping(backend_state.get("live_visual_state"))
    if isinstance(live_state.get("overlay_objects"), list):
        return len(cast(list[Any], live_state["overlay_objects"]))
    return 0


def _state_chart_transform_id(backend_state: Mapping[str, Any]) -> str:
    sources: tuple[object, ...] = (backend_state, backend_state.get("chart_transform"), backend_state.get("live_visual_state"))
    for source in sources:
        if isinstance(source, Mapping):
            source_map = cast(Mapping[str, Any], source)
            if _text(source_map.get("chart_transform_id")):
                return _text(source_map.get("chart_transform_id"))
            chart_transform = _mapping(source_map.get("chart_transform"))
            if _text(chart_transform.get("chart_transform_id")):
                return _text(chart_transform.get("chart_transform_id"))
    return ""


def build_frontend_sync_status(
    session_id: str,
    *,
    backend_state: Mapping[str, Any],
    heartbeat: Mapping[str, Any] | None,
    now_ms: int | float | None = None,
    max_age_sec: float = 8.0,
) -> dict[str, Any]:
    mismatches: list[str] = []
    if heartbeat is None:
        return {
            "schema_version": "PG_FRONTEND_SYNC_STATUS_V3",
            "session_id": session_id,
            "status": "MISSING",
            "ok": False,
            "mismatches": ["frontend heartbeat missing"],
        }
    now = float(now_ms if now_ms is not None else time.time() * 1000.0)
    age_sec = max(0.0, (now - _float(heartbeat.get("received_at_ms"), now)) / 1000.0)
    backend_frame = _int(backend_state.get("frame_id"))
    heartbeat_frame = _int(heartbeat.get("frame_id"))
    rendered_frame = _int(heartbeat.get("rendered_frame_id"))
    heartbeat_is_live_dashboard = _is_live_dashboard_heartbeat(heartbeat)
    backend_transform = _state_chart_transform_id(backend_state)
    heartbeat_transform = _text(heartbeat.get("chart_transform_id"))
    if heartbeat_is_live_dashboard and backend_transform and heartbeat_transform and backend_transform != heartbeat_transform:
        mismatches.append(f"chart_transform_id mismatch backend={backend_transform} frontend={heartbeat_transform}")
    backend_overlay_count = _state_overlay_count(backend_state)
    heartbeat_overlay_count = _int(heartbeat.get("overlay_count", heartbeat.get("visible_overlay_count", 0)))
    backend_overlay_version = _text(backend_state.get("overlay_state_version"))
    heartbeat_overlay_version = _text(heartbeat.get("overlay_state_version"))
    overlay_count_matches = backend_overlay_count == heartbeat_overlay_count
    overlay_version_matches = (
        not backend_overlay_version
        or not heartbeat_overlay_version
        or backend_overlay_version == heartbeat_overlay_version
    )
    frame_skew_tolerated = bool(overlay_count_matches and overlay_version_matches)
    frame_skew_tolerance = _frame_skew_tolerance()
    if heartbeat_is_live_dashboard and backend_frame and heartbeat_frame and backend_frame != heartbeat_frame:
        frame_skew = abs(backend_frame - heartbeat_frame)
        if not frame_skew_tolerated or frame_skew > frame_skew_tolerance:
            mismatches.append(f"frame_id mismatch backend={backend_frame} frontend={heartbeat_frame}")
    if heartbeat_is_live_dashboard and backend_frame and rendered_frame and backend_frame != rendered_frame:
        rendered_skew = abs(backend_frame - rendered_frame)
        if not frame_skew_tolerated or rendered_skew > frame_skew_tolerance:
            mismatches.append(f"rendered_frame_id mismatch backend={backend_frame} frontend={rendered_frame}")
    if heartbeat_is_live_dashboard and not overlay_count_matches:
        mismatches.append(f"overlay_count mismatch backend={backend_overlay_count} frontend={heartbeat_overlay_count}")
    if heartbeat_is_live_dashboard and backend_overlay_version and heartbeat_overlay_version and backend_overlay_version != heartbeat_overlay_version:
        mismatches.append(f"overlay_state_version mismatch backend={backend_overlay_version} frontend={heartbeat_overlay_version}")
    if age_sec > max_age_sec:
        mismatches.append(f"frontend heartbeat stale age_sec={age_sec:.1f}")
    if heartbeat.get("full_broker_surface_visible") is False:
        mismatches.append("full broker surface not reported visible")
    return {
        "schema_version": "PG_FRONTEND_SYNC_STATUS_V3",
        "session_id": session_id,
        "status": "PASS" if not mismatches else "MISMATCH",
        "ok": not mismatches,
        "mismatches": mismatches,
        "age_sec": round(age_sec, 3),
        "frontend": dict(heartbeat),
        "backend": {
            "frame_id": backend_frame,
            "chart_transform_id": backend_transform,
            "overlay_count": backend_overlay_count,
            "overlay_state_version": backend_overlay_version,
            "frame_skew_tolerated": frame_skew_tolerated,
            "frame_skew_tolerance": frame_skew_tolerance,
            "heartbeat_live_dashboard": heartbeat_is_live_dashboard,
        },
    }


def build_visual_realtime_health(
    session_id: str,
    *,
    live_state: Mapping[str, Any],
    visual_health: Mapping[str, Any],
    heartbeat: Mapping[str, Any] | None,
    now_ms: int | float | None = None,
) -> dict[str, Any]:
    sync = build_frontend_sync_status(session_id, backend_state=live_state, heartbeat=heartbeat, now_ms=now_ms)
    artifacts = dict(cast(Mapping[str, Any], visual_health.get("artifacts"))) if isinstance(visual_health.get("artifacts"), Mapping) else {}
    artifact_ok = bool(
        dict(cast(Mapping[str, Any], artifacts.get("window", {}))).get("exists")
        or dict(cast(Mapping[str, Any], artifacts.get("chart", {}))).get("exists")
    )
    if not artifact_ok:
        frontend = dict(cast(Mapping[str, Any], sync.get("frontend"))) if isinstance(sync.get("frontend"), Mapping) else {}
        render_size = dict(cast(Mapping[str, Any], frontend.get("render_size"))) if isinstance(frontend.get("render_size"), Mapping) else {}
        render_width = _int(render_size.get("width"))
        render_height = _int(render_size.get("height"))
        live_artifact_path = any(
            _text(live_state.get(key))
            for key in (
                "last_display_window_path",
                "last_window_path",
                "last_frame_path",
                "last_chart_path",
            )
        )
        artifact_ok = bool(
            live_artifact_path
            or (
                frontend.get("full_broker_surface_visible") is True
                and render_width > 0
                and render_height > 0
            )
        )
    overlay = dict(cast(Mapping[str, Any], visual_health.get("overlay"))) if isinstance(visual_health.get("overlay"), Mapping) else {}
    overlay_ok = bool(overlay.get("frame_matches_chart_frame", True))
    ok = bool(sync.get("ok")) and artifact_ok and overlay_ok
    return {
        "schema_version": VISUAL_HEALTH_SCHEMA_VERSION,
        "session_id": session_id,
        "status": "PASS" if ok else "FAIL",
        "ok": ok,
        "artifact_ok": artifact_ok,
        "overlay_ok": overlay_ok,
        "frontend_sync": sync,
    }


def build_visual_health_v3(
    *,
    session_id: str,
    artifacts: Mapping[str, Mapping[str, Any]],
    overlay_objects: list[Mapping[str, Any]],
    sequence_context: Mapping[str, Any],
    model_health: Mapping[str, Any],
    frontend_heartbeat: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    source_status = dict(cast(Mapping[str, Any], sequence_context.get("source_status"))) if isinstance(sequence_context.get("source_status"), Mapping) else {}
    artifact_ok = bool(artifacts.get("window", {}).get("exists") or artifacts.get("chart", {}).get("exists"))
    contract_ok = all(str(obj.get("schema_version") or "") == "PG_V3_OVERLAY_OBJECT_V1" for obj in overlay_objects)
    return {
        "schema_version": VISUAL_HEALTH_SCHEMA_VERSION,
        "session_id": session_id,
        "status": "ok" if artifact_ok and contract_ok else "degraded",
        "ok": artifact_ok and contract_ok,
        "stale": not artifact_ok or not contract_ok,
        "artifacts": dict(artifacts),
        "overlay": {
            "count": len(overlay_objects),
            "contract_ok": contract_ok,
            "frame_matches_chart_frame": bool(len(overlay_objects) == 0 or source_status.get("tracking_summary") != "MISSING"),
        },
        "sequence": {
            "sequence_id": sequence_context.get("sequence_id", ""),
            "status": sequence_context.get("status", ""),
            "phase": sequence_context.get("phase", ""),
        },
        "model_health": dict(model_health),
        "frontend": dict(frontend_heartbeat or {}),
    }


def record_frontend_heartbeat_v3(payload: Mapping[str, Any]) -> dict[str, Any]:
    return record_frontend_heartbeat(payload)


def latest_frontend_heartbeat_v3(session_id: str | None = None, *, surface_id: str = "dashboard") -> dict[str, Any]:
    if not session_id:
        root = DEFAULT_HEARTBEAT_STORE_DIR
        if not root.exists():
            return {"schema_version": FRONTEND_HEARTBEAT_SCHEMA_VERSION, "status": "missing"}
        candidates = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            return {"schema_version": FRONTEND_HEARTBEAT_SCHEMA_VERSION, "status": "missing"}
        try:
            payload: object = json.loads(candidates[0].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"schema_version": FRONTEND_HEARTBEAT_SCHEMA_VERSION, "status": "missing"}
        return dict(cast(Mapping[str, Any], payload)) if isinstance(payload, Mapping) else {"schema_version": FRONTEND_HEARTBEAT_SCHEMA_VERSION, "status": "missing"}
    return latest_frontend_heartbeat(session_id, surface_id=surface_id) or {
        "schema_version": FRONTEND_HEARTBEAT_SCHEMA_VERSION,
        "session_id": session_id,
        "surface_id": surface_id,
        "status": "missing",
    }


__all__ = [
    "DEFAULT_HEARTBEAT_STORE_DIR",
    "FRONTEND_HEARTBEAT_SCHEMA_VERSION",
    "VISUAL_HEALTH_SCHEMA_VERSION",
    "build_frontend_sync_status",
    "build_visual_health_v3",
    "build_visual_realtime_health",
    "latest_frontend_heartbeat",
    "latest_frontend_heartbeat_v3",
    "normalize_frontend_heartbeat",
    "prune_frontend_heartbeats",
    "record_frontend_heartbeat",
    "record_frontend_heartbeat_v3",
]
