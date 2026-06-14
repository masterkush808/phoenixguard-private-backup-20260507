from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import time
from typing import Any, Mapping, cast


FRONTEND_HEARTBEAT_SCHEMA_VERSION = "PG_FRONTEND_HEARTBEAT_V3"
VISUAL_HEALTH_SCHEMA_VERSION = "PG_VISUAL_HEALTH_V3"

DEFAULT_HEARTBEAT_STORE_DIR = Path(".codex_runtime") / "frontend_heartbeat_v3"


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


def _slug(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip())[:120] or "default"


def _heartbeat_path(session_id: str, *, surface_id: str = "dashboard", store_dir: Path | str | None = None) -> Path:
    root = Path(store_dir or DEFAULT_HEARTBEAT_STORE_DIR)
    return root / f"{_slug(session_id)}__{_slug(surface_id)}.json"


def normalize_frontend_heartbeat(payload: Mapping[str, Any], *, now_ms: int | float | None = None) -> dict[str, Any]:
    session_id = _text(payload.get("session_id"))
    if not session_id:
        raise ValueError("frontend heartbeat requires session_id")
    surface_id = _text(payload.get("surface_id"), "dashboard")
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
        "route": _text(payload.get("route")),
        "overlay_mode": _text(payload.get("overlay_mode"), _text(payload.get("mode"), "CLEAN_LIVE")).upper(),
        "surface_mode": _text(payload.get("surface_mode"), "overlay"),
        "frame_id": _int(payload.get("frame_id")),
        "rendered_frame_id": _int(payload.get("rendered_frame_id", payload.get("frame_id"))),
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
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(heartbeat, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp_path, path)
    heartbeat["path"] = str(path)
    return heartbeat


def latest_frontend_heartbeat(
    session_id: str,
    *,
    surface_id: str = "dashboard",
    store_dir: Path | str | None = None,
) -> dict[str, Any] | None:
    path = _heartbeat_path(session_id, surface_id=surface_id, store_dir=store_dir)
    if not path.exists():
        return None
    try:
        if path.stat().st_size <= 0:
            return None
    except OSError:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, Mapping):
        return None
    heartbeat = dict(cast(Mapping[str, Any], payload))
    heartbeat["path"] = str(path)
    return heartbeat


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
                removed += 1
            except OSError:
                pass
    return removed


def _state_overlay_count(backend_state: Mapping[str, Any]) -> int:
    if isinstance(backend_state.get("overlay_objects"), list):
        return len(cast(list[Any], backend_state["overlay_objects"]))
    overlays = backend_state.get("overlays")
    if isinstance(overlays, Mapping) and isinstance(overlays.get("objects"), list):
        return len(cast(list[Any], overlays["objects"]))
    live_state = backend_state.get("live_visual_state")
    if isinstance(live_state, Mapping) and isinstance(live_state.get("overlay_objects"), list):
        return len(cast(list[Any], live_state["overlay_objects"]))
    return 0


def _state_chart_transform_id(backend_state: Mapping[str, Any]) -> str:
    for source in (backend_state, backend_state.get("chart_transform"), backend_state.get("live_visual_state")):
        if isinstance(source, Mapping):
            if _text(source.get("chart_transform_id")):
                return _text(source.get("chart_transform_id"))
            chart_transform = source.get("chart_transform")
            if isinstance(chart_transform, Mapping) and _text(chart_transform.get("chart_transform_id")):
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
    if backend_frame and heartbeat_frame and backend_frame != heartbeat_frame:
        mismatches.append(f"frame_id mismatch backend={backend_frame} frontend={heartbeat_frame}")
    rendered_frame = _int(heartbeat.get("rendered_frame_id"))
    if backend_frame and rendered_frame and backend_frame != rendered_frame:
        mismatches.append(f"rendered_frame_id mismatch backend={backend_frame} frontend={rendered_frame}")
    backend_transform = _state_chart_transform_id(backend_state)
    heartbeat_transform = _text(heartbeat.get("chart_transform_id"))
    if backend_transform and heartbeat_transform and backend_transform != heartbeat_transform:
        mismatches.append(f"chart_transform_id mismatch backend={backend_transform} frontend={heartbeat_transform}")
    backend_overlay_count = _state_overlay_count(backend_state)
    heartbeat_overlay_count = _int(heartbeat.get("overlay_count", heartbeat.get("visible_overlay_count", 0)))
    if backend_overlay_count != heartbeat_overlay_count:
        mismatches.append(f"overlay_count mismatch backend={backend_overlay_count} frontend={heartbeat_overlay_count}")
    backend_overlay_version = _text(backend_state.get("overlay_state_version"))
    heartbeat_overlay_version = _text(heartbeat.get("overlay_state_version"))
    if backend_overlay_version and heartbeat_overlay_version and backend_overlay_version != heartbeat_overlay_version:
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


def latest_frontend_heartbeat_v3(session_id: str | None = None) -> dict[str, Any]:
    if not session_id:
        root = DEFAULT_HEARTBEAT_STORE_DIR
        if not root.exists():
            return {"schema_version": FRONTEND_HEARTBEAT_SCHEMA_VERSION, "status": "missing"}
        candidates = sorted(root.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not candidates:
            return {"schema_version": FRONTEND_HEARTBEAT_SCHEMA_VERSION, "status": "missing"}
        try:
            payload = json.loads(candidates[0].read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"schema_version": FRONTEND_HEARTBEAT_SCHEMA_VERSION, "status": "missing"}
        return dict(payload) if isinstance(payload, Mapping) else {"schema_version": FRONTEND_HEARTBEAT_SCHEMA_VERSION, "status": "missing"}
    return latest_frontend_heartbeat(session_id) or {"schema_version": FRONTEND_HEARTBEAT_SCHEMA_VERSION, "session_id": session_id, "status": "missing"}


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
