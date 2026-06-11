from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import html
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Callable, Mapping, cast
from uuid import uuid4

import gradio as gr

import main as pg


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    value = str(raw).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(str(raw).strip())
    except Exception:
        return default


SHARE_MODE_ACTIVE = _env_bool("PHOENIXGUARD_SHARE_MODE", True)
SHARE_UI_TITLE = f"{pg.UI_BRAND_NAME} Elite Share Desk"
SHARE_UI_SUBTITLE = "Premium external signal desk with protected server-side inference, council review, and learning feedback."
DEFAULT_SHARE_RENDER = {
    "overlay_mode": "history-plus-projection",
    "min_conf_global": 0.42,
    "min_conf_latest": 0.50,
    "history_depth": 8,
    "label_density": 10,
    "projection_focus": 0.35,
    "debug_depth": 4,
}
SHARE_SESSION_TTL_SEC = _env_int("PHOENIXGUARD_SHARE_SESSION_TTL_SEC", 4 * 60 * 60)
SHARE_MAX_SESSIONS = _env_int("PHOENIXGUARD_SHARE_MAX_SESSIONS", 256)
SHARE_AUTH_MAX_FAILURES = max(1, _env_int("PHOENIXGUARD_SHARE_AUTH_MAX_FAILURES", 5))
SHARE_AUTH_LOCKOUT_SEC = max(30, _env_int("PHOENIXGUARD_SHARE_AUTH_LOCKOUT_SEC", 10 * 60))
SHARE_STRICT_PASSWORDS = _env_bool("PHOENIXGUARD_SHARE_STRICT_PASSWORDS", False)
SHARE_REASON_MAX_CHARS = max(80, _env_int("PHOENIXGUARD_SHARE_REASON_MAX_CHARS", 500))

SHARE_LOGGER = pg.setup_logger(pg.RUNTIME.logs_dir / "phoenixguard_share.log", name="phoenixguard.share")
SHARE_AUDIT_LOG_PATH = pg.RUNTIME.logs_dir / "phoenixguard_share_audit_hash_chain.log"
SHARE_EXTRA_CSS = """
.pg-share-hero {
  position: relative;
  overflow: hidden;
  border-radius: 28px;
  padding: 28px 30px;
  margin-bottom: 18px;
  background:
    radial-gradient(circle at top right, rgba(255, 196, 110, 0.20), transparent 36%),
    radial-gradient(circle at left center, rgba(88, 218, 123, 0.16), transparent 34%),
    linear-gradient(135deg, #07141d 0%, #102534 52%, #18364a 100%);
  border: 1px solid rgba(255, 255, 255, 0.10);
  box-shadow: 0 22px 48px rgba(4, 10, 18, 0.35);
}
.pg-share-hero::after {
  content: "";
  position: absolute;
  inset: 0;
  background:
    linear-gradient(115deg, rgba(255,255,255,0.03), transparent 30%),
    repeating-linear-gradient(
      135deg,
      rgba(255,255,255,0.02) 0px,
      rgba(255,255,255,0.02) 1px,
      transparent 1px,
      transparent 15px
    );
  pointer-events: none;
}
.pg-share-badges {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  margin-top: 14px;
}
.pg-share-badge {
  display: inline-flex;
  align-items: center;
  padding: 8px 12px;
  border-radius: 999px;
  background: rgba(255,255,255,0.08);
  border: 1px solid rgba(255,255,255,0.10);
  color: #f7fbff;
  font-size: 12px;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}
.pg-share-stack {
  display: flex;
  flex-direction: column;
  gap: 14px;
}
"""
SHARE_UI_CSS = f"{pg.UI_CSS}\n{SHARE_EXTRA_CSS}"


@dataclass(slots=True)
class ShareSession:
    session_id: str
    result: dict[str, Any] | None = None
    source_image_state: Any = None
    active_file_path: str = ""
    render_config: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_SHARE_RENDER))
    updated_at: float = field(default_factory=time.time)


_share_sessions: dict[str, ShareSession] = {}
_share_sessions_lock = threading.Lock()
_share_auth_failures: dict[str, dict[str, float | int]] = {}
_share_auth_lock = threading.Lock()


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _session_label(session_id: str | None) -> str:
    token = str(session_id or "").strip()
    if not token:
        return "none"
    return token[:12]


def _audit_safe_value(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_audit_safe_value(item) for item in value[:8]]
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 16:
                break
            safe[str(key)] = _audit_safe_value(item)
        return safe
    return str(value)


def _append_share_audit(payload: Mapping[str, Any]) -> None:
    if not SHARE_MODE_ACTIVE:
        return
    try:
        pg.append_hash_chain(
            SHARE_AUDIT_LOG_PATH,
            cast(dict[str, Any], {str(key): _audit_safe_value(value) for key, value in payload.items()}),
        )
    except Exception as exc:
        SHARE_LOGGER.exception("share audit append failed: %s", exc)


def _log_share_event(level: str, event: str, *, session_id: str | None = None, **fields: Any) -> None:
    session_tag = _session_label(session_id)
    message = f"{event} | session={session_tag}"
    if fields:
        message += " | " + ", ".join(f"{key}={_audit_safe_value(value)}" for key, value in fields.items())
    if level == "error":
        SHARE_LOGGER.error(message)
    elif level == "warning":
        SHARE_LOGGER.warning(message)
    else:
        SHARE_LOGGER.info(message)
    _append_share_audit(
        {
            "ts": pg.utc_now_iso(),
            "event": event,
            "level": level,
            "session": session_tag,
            **fields,
        }
    )


def _record_share_error(
    stage: str,
    message: str,
    exc: Exception | None = None,
    *,
    session_id: str | None = None,
    **fields: Any,
) -> None:
    payload = {
        "stage": stage,
        "message": message,
        "error_type": type(exc).__name__ if exc is not None else "UnknownError",
        **fields,
    }
    if exc is not None:
        SHARE_LOGGER.exception("%s | session=%s | %s", stage, _session_label(session_id), message)
    else:
        SHARE_LOGGER.error("%s | session=%s | %s", stage, _session_label(session_id), message)
    _append_share_audit(
        {
            "ts": pg.utc_now_iso(),
            "event": "share_error",
            "session": _session_label(session_id),
            **payload,
        }
    )


def _default_render_config() -> dict[str, Any]:
    return pg._build_render_config(
        overlay_mode=str(DEFAULT_SHARE_RENDER["overlay_mode"]),
        min_conf_global=float(DEFAULT_SHARE_RENDER["min_conf_global"]),
        min_conf_latest=float(DEFAULT_SHARE_RENDER["min_conf_latest"]),
        history_depth=int(DEFAULT_SHARE_RENDER["history_depth"]),
        label_density=int(DEFAULT_SHARE_RENDER["label_density"]),
        projection_focus=float(DEFAULT_SHARE_RENDER["projection_focus"]),
        debug_depth=int(DEFAULT_SHARE_RENDER["debug_depth"]),
    )


def _empty_share_outputs(session_id: str = "") -> tuple[Any, ...]:
    return (
        None,
        None,
        pg._placeholder_panel("Signal Overview", "Upload exactly two chart images to run the elite share desk."),
        pg._placeholder_panel("Forecast & Risk", "Forecast guidance will appear here after a signal run."),
        pg._placeholder_panel("Timeframe Overlays", "Higher and lower timeframe overlays will appear here after a run."),
        None,
        pg._placeholder_panel("Confidence Heatmap", "Confidence heat concentration will appear here after a signal run."),
        pg._placeholder_panel("Compare Desk", "Compare desk will appear here after the first shared inference."),
        pg._placeholder_panel("Adaptive Guidance", "The desk will guide the next best panel after the first signal run."),
        pg._placeholder_panel("Model Council", "Open the Model Council tab after a signal run to request the heavyweight refinement pass."),
        _share_status_html(
            "Elite share mode is active. Raw backend state stays on the server and only rendered outputs are sent to the browser.",
            render_config=_default_render_config(),
        ),
        session_id,
    )


def _share_feedback_placeholder() -> str:
    return "Feedback submitted here will be recorded for server-side learning and audit logging."


def _share_status_html(
    message: str,
    *,
    result: Mapping[str, Any] | None = None,
    render_config: Mapping[str, Any] | None = None,
) -> str:
    config = dict(render_config or {})
    chips = "".join(
        [
            pg._chip("Auth Enabled", "teal"),
            pg._chip("Server-Side State", "soft"),
            pg._chip("Audit Hash Chain", "amber"),
            pg._chip("Quiet Errors", "soft"),
        ]
    )
    rows = [
        "<div class='pg-panel'>",
        "<div class='pg-section-title'>Share Guard</div>",
        f"<div class='pg-chip-row'>{chips}</div>",
        f"<div class='pg-muted' style='margin-top:10px;'>{html.escape(str(message))}</div>",
    ]
    if result:
        action = str(result.get("action", "HOLD")).upper()
        confidence = float(result.get("confidence", 0.0) or 0.0)
        overlay_mode = str(config.get("overlay_mode", DEFAULT_SHARE_RENDER["overlay_mode"])).replace("-", " ")
        rows.append(
            (
                "<div class='pg-muted' style='margin-top:10px;'>"
                f"Current signal: {html.escape(action)} at {html.escape(pg._fmt_pct01(confidence))}. "
                f"Overlay view: {html.escape(overlay_mode)}."
                "</div>"
            )
        )
    rows.append(
        "<div class='pg-muted' style='margin-top:10px;'>"
        "Restricted response surface: signal summary, visual desk, model council, and learning feedback only."
        "</div>"
    )
    rows.append("</div>")
    return "".join(rows)


def _cleanup_share_sessions() -> None:
    now = time.time()
    removed = 0
    with _share_sessions_lock:
        expired_ids = [
            session_id
            for session_id, session in _share_sessions.items()
            if (now - float(session.updated_at)) > float(SHARE_SESSION_TTL_SEC)
        ]
        for session_id in expired_ids:
            _share_sessions.pop(session_id, None)
            removed += 1
        if len(_share_sessions) > SHARE_MAX_SESSIONS:
            ordered = sorted(_share_sessions.values(), key=lambda item: float(item.updated_at))
            overflow = max(0, len(ordered) - SHARE_MAX_SESSIONS)
            for session in ordered[:overflow]:
                _share_sessions.pop(session.session_id, None)
                removed += 1
    if removed:
        _log_share_event("info", "session_cleanup", removed=removed)


def _get_share_session(session_id: str | None, *, create: bool = True) -> ShareSession | None:
    _cleanup_share_sessions()
    normalized = str(session_id or "").strip()
    created = False
    with _share_sessions_lock:
        session = _share_sessions.get(normalized)
        if session is None and create:
            normalized = normalized or uuid4().hex
            session = ShareSession(session_id=normalized, render_config=_default_render_config())
            _share_sessions[normalized] = session
            created = True
        if session is not None:
            session.updated_at = time.time()
    if created and session is not None:
        _log_share_event("info", "session_created", session_id=session.session_id)
    return session


def _update_share_session(
    session_id: str,
    *,
    result: dict[str, Any] | None = None,
    source_image_state: Any = None,
    active_file_path: str | None = None,
    render_config: Mapping[str, Any] | None = None,
) -> ShareSession:
    session = _get_share_session(session_id, create=True)
    assert session is not None
    with _share_sessions_lock:
        if result is not None:
            session.result = result
        if source_image_state is not None:
            session.source_image_state = source_image_state
        if active_file_path is not None:
            session.active_file_path = str(active_file_path)
        if render_config is not None:
            session.render_config = dict(render_config)
        session.updated_at = time.time()
    return session


def _build_share_render_config(
    overlay_mode: str,
    min_conf_global: float,
    min_conf_latest: float,
    history_depth: float,
    label_density: float,
    projection_focus: float,
) -> dict[str, Any]:
    return pg._build_render_config(
        overlay_mode=overlay_mode,
        min_conf_global=min_conf_global,
        min_conf_latest=min_conf_latest,
        history_depth=history_depth,
        label_density=label_density,
        projection_focus=projection_focus,
        debug_depth=int(DEFAULT_SHARE_RENDER["debug_depth"]),
    )


def _build_share_adaptive_guidance_html(result: Mapping[str, Any] | None) -> str:
    if not result:
        return pg._placeholder_panel("Adaptive Guidance", "The desk will guide the next best panel after the first signal run.")
    multi_timeframe = cast(dict[str, Any], result.get("multi_timeframe", {}))
    zone_learning = cast(dict[str, Any], result.get("zone_learning", {}))
    confidence = float(result.get("confidence", 0.0) or 0.0)
    recommended_panel = "Model Council"
    rationale = "Heavyweight council review is the fastest way to sharpen conviction on the current shared signal."
    tone = "soft"
    if multi_timeframe and not bool(multi_timeframe.get("aligned", False)):
        recommended_panel = "Compare Desk"
        rationale = "Higher and lower timeframe structure are disagreeing, so split compare is the cleanest next read."
        tone = "amber"
    elif 0.46 <= confidence <= 0.68:
        recommended_panel = "Confidence Heatmap"
        rationale = "The read is tradable but not decisive, so hotspot concentration is the best next visual filter."
        tone = "amber"
    elif int(zone_learning.get("match_count", 0) or 0) > 0:
        recommended_panel = "Timeframe Overlays"
        rationale = "Taught structural zones are intersecting the chart, so overlay context is the highest-value follow-up."
        tone = "teal"
    elif confidence >= 0.78:
        recommended_panel = "Feedback Feed"
        rationale = "This is a high-conviction read. After the outcome plays out, feedback will keep the live learner honest."
        tone = "teal"
    return (
        "<div class='pg-live-panel'>"
        "<div class='pg-section-title'>Adaptive Guidance</div>"
        f"<div class='pg-chip-row'>{pg._chip(f'Open {recommended_panel}', tone)}</div>"
        f"<div class='pg-muted'>{html.escape(rationale)}</div>"
        "</div>"
    )


def _render_share_outputs(
    result: dict[str, Any],
    source_image_state: Any,
    render_config: Mapping[str, Any],
    *,
    status_message: str,
) -> tuple[Any, ...]:
    source_image = pg._image_from_state(source_image_state)
    if source_image is None:
        return _empty_share_outputs()
    display_result = pg._sanitize_result_for_ui(result)
    overlay = pg._build_overlay_image(
        source_image,
        result,
        overlay_mode=str(render_config.get("overlay_mode", DEFAULT_SHARE_RENDER["overlay_mode"])),
        min_conf_global=float(render_config.get("min_conf_global", DEFAULT_SHARE_RENDER["min_conf_global"])),
        min_conf_latest=float(render_config.get("min_conf_latest", DEFAULT_SHARE_RENDER["min_conf_latest"])),
        history_limit=int(render_config.get("history_depth", DEFAULT_SHARE_RENDER["history_depth"])),
        label_budget=int(render_config.get("label_density", DEFAULT_SHARE_RENDER["label_density"])),
        projection_confidence_floor=float(render_config.get("projection_focus", DEFAULT_SHARE_RENDER["projection_focus"])),
    )
    gauge = pg._build_decision_gauge_from_result(display_result)
    heatmap_payload = pg._build_confidence_heatmap_payload(display_result, source_image)
    heatmap_image = pg._compose_confidence_heatmap_image(heatmap_payload, source_image)
    return (
        overlay,
        gauge,
        pg.build_signal_overview_html(display_result),
        pg.build_forecast_panel_html(display_result),
        pg._build_timeframe_overlay_gallery_html(display_result),
        heatmap_image,
        pg._build_heatmap_summary_html(display_result, source_image, heatmap_payload=heatmap_payload),
        pg._build_compare_desk_html(display_result, source_image, overlay, heatmap_image, render_config=render_config),
        _build_share_adaptive_guidance_html(display_result),
        pg.build_model_council_html(display_result),
        _share_status_html(status_message, result=display_result, render_config=render_config),
    )


def _render_share_session(session: ShareSession, *, status_message: str) -> tuple[Any, ...]:
    if session.result is None or session.source_image_state is None:
        return _empty_share_outputs(session.session_id)
    return (
        *_render_share_outputs(session.result, session.source_image_state, session.render_config, status_message=status_message),
        session.session_id,
    )


def _generic_user_error(
    message: str,
    exc: Exception | None = None,
    *,
    stage: str,
    session_id: str | None = None,
    **fields: Any,
) -> None:
    _record_share_error(stage, message, exc, session_id=session_id, **fields)
    raise gr.Error(message)


def _analyze_share_bundle(
    upload_paths: list[str],
    render_config: Mapping[str, Any],
    *,
    use_local_ensemble: bool | None = None,
    side_effect_free: bool = False,
) -> tuple[dict[str, Any], Any, str]:
    labels = ["Higher TF", "Lower TF", "Frame 3", "Frame 4"]
    analyzed: list[dict[str, Any]] = []
    for index, file_path in enumerate(upload_paths):
        result, overlay_image, _gauge_unused, _skill_unused = pg.pg_main.run_inference(
            file_path,
            annotation_text="",
            overlay_mode=str(render_config["overlay_mode"]),
            min_conf_global=float(render_config["min_conf_global"]),
            min_conf_latest=float(render_config["min_conf_latest"]),
            history_depth=int(render_config["history_depth"]),
            label_density=int(render_config["label_density"]),
            projection_focus=float(render_config["projection_focus"]),
            side_effect_free=side_effect_free,
            use_local_ensemble=use_local_ensemble,
        )
        source_image_state = pg._source_image_to_state(file_path)
        analyzed.append(
            {
                "result": result,
                "file_path": file_path,
                "source_image_state": source_image_state,
                "compare_entry": pg._build_timeframe_compare_entry(
                    result,
                    source_image_state,
                    file_path,
                    labels[min(index, len(labels) - 1)],
                    overlay_image=overlay_image,
                    render_config=render_config,
                ),
            }
        )
    bundle_result = (
        pg._build_multi_timeframe_result(analyzed)
        if len(analyzed) > 1
        else cast(dict[str, Any], analyzed[0]["result"])
    )
    source_image_state = analyzed[-1]["source_image_state"]
    active_file_path = str(analyzed[-1]["file_path"])
    return bundle_result, source_image_state, active_file_path


def run_share_signal(
    session_id: str,
    file_obj: Any,
    overlay_mode: str,
    min_conf_global: float,
    min_conf_latest: float,
    history_depth: float,
    label_density: float,
    projection_focus: float,
) -> tuple[Any, ...]:
    session = _get_share_session(session_id, create=True)
    assert session is not None
    upload_paths = pg._uploaded_file_paths(file_obj)
    if not upload_paths:
        return _empty_share_outputs(session.session_id)
    if len(upload_paths) != 2:
        _log_share_event("warning", "signal_rejected_bad_bundle", session_id=session.session_id, file_count=len(upload_paths))
        raise gr.Error("Upload exactly two chart images: higher timeframe first and lower timeframe second.")
    render_config = _build_share_render_config(
        overlay_mode,
        min_conf_global,
        min_conf_latest,
        history_depth,
        label_density,
        projection_focus,
    )
    try:
        result, source_image_state, active_file_path = _analyze_share_bundle(upload_paths[:2], render_config)
    except Exception as exc:
        _generic_user_error(
            "Signal run failed. Please try again.",
            exc,
            stage="signal_run",
            session_id=session.session_id,
            file_count=len(upload_paths),
        )
    _update_share_session(
        session.session_id,
        result=result,
        source_image_state=source_image_state,
        active_file_path=active_file_path,
        render_config=render_config,
    )
    display_result = pg._sanitize_result_for_ui(result)
    _log_share_event(
        "info",
        "signal_completed",
        session_id=session.session_id,
        action=str(display_result.get("action", "HOLD")).upper(),
        confidence=round(float(display_result.get("confidence", 0.0) or 0.0), 4),
        overlay_mode=str(render_config["overlay_mode"]),
        council_loaded=bool(cast(dict[str, Any], display_result.get("local_ensemble", {})).get("models")),
    )
    return _render_share_session(
        session,
        status_message="Signal run complete. Premium visuals are exposed to the browser while the backend remains server-side.",
    )


def refresh_share_preview(
    session_id: str,
    overlay_mode: str,
    min_conf_global: float,
    min_conf_latest: float,
    history_depth: float,
    label_density: float,
    projection_focus: float,
) -> tuple[Any, ...]:
    session = _get_share_session(session_id, create=True)
    assert session is not None
    render_config = _build_share_render_config(
        overlay_mode,
        min_conf_global,
        min_conf_latest,
        history_depth,
        label_density,
        projection_focus,
    )
    _update_share_session(session.session_id, render_config=render_config)
    if session.result is None or session.source_image_state is None:
        return _empty_share_outputs(session.session_id)
    _log_share_event(
        "info",
        "preview_refreshed",
        session_id=session.session_id,
        overlay_mode=str(render_config["overlay_mode"]),
        min_conf_global=round(float(render_config["min_conf_global"]), 2),
        min_conf_latest=round(float(render_config["min_conf_latest"]), 2),
    )
    return _render_share_session(
        session,
        status_message="Visual desk refreshed from server-side state.",
    )


def load_share_model_council(session_id: str) -> tuple[Any, ...]:
    session = _get_share_session(session_id, create=False)
    if session is None or session.result is None or session.source_image_state is None:
        return _empty_share_outputs(session_id)

    local_ensemble = cast(dict[str, Any], session.result.get("local_ensemble", {}))
    existing_models = cast(dict[str, Any], local_ensemble.get("models", {}))
    if existing_models:
        _log_share_event("info", "model_council_reused", session_id=session.session_id, models=len(existing_models))
        return _render_share_session(
            session,
            status_message="Model council already loaded for this share session.",
        )

    multi_timeframe = cast(dict[str, Any], session.result.get("multi_timeframe", {}))
    entries = cast(list[dict[str, Any]], multi_timeframe.get("entries", []))
    bundle_paths = [
        str(entry.get("file_path", "") or "").strip()
        for entry in entries
        if str(entry.get("file_path", "") or "").strip()
    ]
    if not bundle_paths and session.active_file_path:
        bundle_paths = [session.active_file_path]
    if not bundle_paths:
        _log_share_event("warning", "model_council_skipped_missing_bundle", session_id=session.session_id)
        return _render_share_session(
            session,
            status_message="No active chart bundle is available for the model council.",
        )

    try:
        refined_result, source_image_state, active_file_path = _analyze_share_bundle(
            bundle_paths[:2],
            session.render_config,
            use_local_ensemble=True,
            side_effect_free=True,
        )
    except Exception as exc:
        _generic_user_error(
            "Model council is unavailable right now. Please try again later.",
            exc,
            stage="model_council",
            session_id=session.session_id,
            bundle_size=len(bundle_paths),
        )
    _update_share_session(
        session.session_id,
        result=refined_result,
        source_image_state=source_image_state,
        active_file_path=active_file_path,
    )
    display_result = pg._sanitize_result_for_ui(refined_result)
    model_rows = cast(dict[str, Any], cast(dict[str, Any], display_result.get("local_ensemble", {})).get("models", {}))
    _log_share_event(
        "info",
        "model_council_completed",
        session_id=session.session_id,
        action=str(display_result.get("action", "HOLD")).upper(),
        confidence=round(float(display_result.get("confidence", 0.0) or 0.0), 4),
        models=len(model_rows),
    )
    return _render_share_session(
        session,
        status_message="Model council refinement complete. The browser still only receives rendered outputs.",
    )


def _sanitize_feedback_reason(reason: str) -> str:
    normalized = str(reason or "").strip()
    if len(normalized) > SHARE_REASON_MAX_CHARS:
        normalized = normalized[:SHARE_REASON_MAX_CHARS].rstrip()
    return normalized


def submit_share_feedback(
    session_id: str,
    verdict: str,
    reason: str,
    feedback_image: Any | None = None,
) -> str:
    session = _get_share_session(session_id, create=False)
    if session is None or not str(session.active_file_path).strip():
        return "Run a signal before submitting feedback."

    file_path = str(session.active_file_path)
    safe_reason = _sanitize_feedback_reason(reason)
    try:
        personal = pg._get_personal()
        continual_learning = pg._get_continual_learning()
        rl_engine = pg._get_rl_engine()
        _img_unused, meta = pg.load_any_file_as_image(file_path)
        chosen = str(verdict or "HOLD").upper()
        rejected = "SELL" if chosen == "BUY" else "BUY"
        feedback_asset = pg._save_feedback_result_image(str(meta.get("sha256", "")), chosen, feedback_image)
        feedback_image_path = str(feedback_asset.get("path", "") or "").strip()
        annotation_text = pg._build_feedback_annotation_text(feedback_asset)
        personal.record_feedback(str(meta.get("sha256", "")), chosen, rejected, safe_reason, annotation_text)
        replay_item = (
            continual_learning.record_feedback(
                str(meta.get("sha256", "")),
                chosen,
                safe_reason,
                feedback_image_path=feedback_image_path,
                feedback_image_sha256=str(feedback_asset.get("sha256", "") or "").strip(),
                feedback_image_meta=dict(feedback_asset),
            )
            if pg.RUNTIME.enable_replay_continual_learning
            else {}
        )
        rl_feedback = (
            rl_engine.record_feedback(
                str(meta.get("sha256", "")),
                chosen,
                safe_reason,
                feedback_image_path=feedback_image_path,
                feedback_image_sha256=str(feedback_asset.get("sha256", "") or "").strip(),
                feedback_image_meta=dict(feedback_asset),
            )
            if not pg.RUNTIME.pause_rl_updates
            else {}
        )
        if replay_item:
            personal.record_context_feedback(
                str(replay_item.get("context_key", "default")),
                str(replay_item.get("context_descriptor", "")),
                chosen,
                safe_reason,
                annotation_text,
            )

        bank = pg._get_memory_bank()
        if bank is not None:
            try:
                dpo_pairs = personal.generate_dpo_pairs(memory_bank=bank, n=50)
                personal.update_style_from_memory_bank(dpo_pairs)
            except Exception as exc:
                _record_share_error(
                    "feedback_style_refresh",
                    "style refresh failed during share feedback",
                    exc,
                    session_id=session.session_id,
                )

        pg._append_jsonl(
            pg._feedback_feed_path(),
            {
                "ts": pg.utc_now_iso(),
                "source_path": file_path,
                "source_image_hash": str(meta.get("sha256", "")),
                "verdict": chosen,
                "rejected": rejected,
                "reason": safe_reason,
                "feedback_image": dict(feedback_asset),
                "learning_snapshot_path": str(replay_item.get("snapshot_path", feedback_image_path)),
                "continual_learning_updated": bool(replay_item),
                "continual_learning_success": bool(replay_item.get("success", False)) if replay_item else False,
                "rl_feedback_updated": bool(rl_feedback),
                "rl_online_updated": bool(rl_feedback.get("updated", False)) if rl_feedback else False,
            },
        )
        _log_share_event(
            "info",
            "feedback_recorded",
            session_id=session.session_id,
            verdict=chosen,
            reason_hash=_hash_text(safe_reason)[:16] if safe_reason else "",
            reason_length=len(safe_reason),
            rl_updated=bool(rl_feedback.get("updated", False)) if rl_feedback else False,
            replay_updated=bool(replay_item),
        )
        if rl_feedback and bool(rl_feedback.get("updated", False)):
            return "Feedback captured and the online learner updated."
        if rl_feedback:
            return "Feedback captured and queued for server-side learning."
        return "Feedback captured for server-side learning."
    except Exception as exc:
        _record_share_error(
            "feedback_submit",
            "share feedback submission failed",
            exc,
            session_id=session.session_id,
            verdict=str(verdict or "HOLD").upper(),
        )
        return "Feedback could not be recorded right now. Please try again."


def _password_is_strong(password: str) -> bool:
    value = str(password or "")
    return (
        len(value) >= 12
        and any(char.islower() for char in value)
        and any(char.isupper() for char in value)
        and any(char.isdigit() for char in value)
    )


def _share_surface_is_public(host: str, *, tunnel_enabled: bool) -> bool:
    if tunnel_enabled:
        return True
    normalized = str(host or "").strip().lower()
    return normalized not in {"", "127.0.0.1", "localhost", "::1"}


def _share_credentials(*, strict_passwords: bool, public_surface: bool) -> list[tuple[str, str]]:
    raw_pairs = str(os.getenv("PHOENIXGUARD_SHARE_CREDENTIALS", "") or "").strip()
    parsed_pairs: list[tuple[str, str]] = []
    if raw_pairs:
        for chunk in raw_pairs.split(","):
            piece = chunk.strip()
            if not piece or ":" not in piece:
                continue
            username, password = piece.split(":", 1)
            username = username.strip()
            password = password.strip()
            if username and password:
                parsed_pairs.append((username, password))
    if not parsed_pairs:
        username = str(os.getenv("PHOENIXGUARD_SHARE_USERNAME", "operator") or "operator").strip() or "operator"
        password = str(os.getenv("PHOENIXGUARD_SHARE_PASSWORD", "") or "").strip()
        if password:
            parsed_pairs.append((username, password))
    if not parsed_pairs:
        raise RuntimeError(
            "Set PHOENIXGUARD_SHARE_PASSWORD or PHOENIXGUARD_SHARE_CREDENTIALS before launching share mode."
        )
    for username, password in parsed_pairs:
        is_strong = _password_is_strong(password)
        if not is_strong:
            _log_share_event("warning", "weak_share_password", user_hash=_hash_text(username)[:16], length=len(password))
            if strict_passwords:
                raise RuntimeError(
                    "Share credentials are too weak. Use at least 12 characters with upper, lower, and numeric characters."
                )
    _log_share_event(
        "info",
        "share_credentials_loaded",
        credential_count=len(parsed_pairs),
        strict_passwords=strict_passwords,
        public_surface=public_surface,
    )
    return parsed_pairs


def _build_share_auth(credentials: list[tuple[str, str]]) -> Callable[[str, str], bool]:
    credential_map = {username: password for username, password in credentials}

    def _authenticate(username: str, password: str) -> bool:
        normalized_user = str(username or "").strip()
        now = time.time()
        with _share_auth_lock:
            auth_state = dict(_share_auth_failures.get(normalized_user, {}))
            locked_until = float(auth_state.get("locked_until", 0.0) or 0.0)
            fail_count = int(auth_state.get("count", 0) or 0)
            if locked_until > now:
                _log_share_event(
                    "warning",
                    "auth_locked_out",
                    user_hash=_hash_text(normalized_user)[:16],
                    remaining_lock_sec=int(max(0.0, locked_until - now)),
                )
                return False
            expected_password = credential_map.get(normalized_user)
            success = expected_password is not None and secrets.compare_digest(str(password or ""), expected_password)
            if success:
                _share_auth_failures.pop(normalized_user, None)
                _log_share_event("info", "auth_success", user_hash=_hash_text(normalized_user)[:16])
                return True
            fail_count += 1
            next_locked_until = now + SHARE_AUTH_LOCKOUT_SEC if fail_count >= SHARE_AUTH_MAX_FAILURES else 0.0
            _share_auth_failures[normalized_user] = {
                "count": fail_count,
                "locked_until": next_locked_until,
            }
        _log_share_event(
            "warning",
            "auth_failure",
            user_hash=_hash_text(normalized_user)[:16],
            failure_count=fail_count,
            locked=bool(next_locked_until),
        )
        return False

    return _authenticate


def _share_blocked_paths() -> list[str]:
    project_root = Path(pg.RUNTIME.project_root)
    candidates = [
        project_root,
        project_root.parent / ".codex",
        project_root / ".venv",
        project_root / ".hf_cache",
        project_root / ".hf_offload",
    ]
    return [str(path) for path in candidates if path.exists()]


def launch_share_ui() -> None:
    share_host = str(os.getenv("PHOENIXGUARD_SHARE_HOST", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"
    share_port = _env_int("PHOENIXGUARD_SHARE_PORT", 7861)
    share_tunnel = _env_bool("PHOENIXGUARD_SHARE_TUNNEL", False)
    public_surface = _share_surface_is_public(share_host, tunnel_enabled=share_tunnel)
    strict_passwords = SHARE_STRICT_PASSWORDS or public_surface
    credentials = _share_credentials(strict_passwords=strict_passwords, public_surface=public_surface)
    share_auth = _build_share_auth(credentials)
    max_file_size = str(os.getenv("PHOENIXGUARD_SHARE_MAX_FILE_SIZE", "25mb") or "25mb").strip() or "25mb"

    with gr.Blocks(
        title=SHARE_UI_TITLE,
        fill_width=True,
        analytics_enabled=False,
        delete_cache=(24 * 60 * 60, 24 * 60 * 60),
    ) as demo:
        session_id_state = gr.State(value="")

        gr.HTML(
            f"""
            <div class="pg-share-hero">
              <div class="pg-kicker">{SHARE_UI_TITLE}</div>
              <h1>{SHARE_UI_SUBTITLE}</h1>
              <p>Signals, overlays, compare desk, confidence heatmap, model council, and feedback. Proprietary runtime stays protected on the host machine.</p>
              <div class="pg-share-badges">
                <span class="pg-share-badge">Private Auth</span>
                <span class="pg-share-badge">Server-Side State</span>
                <span class="pg-share-badge">Tamper-Evident Audit</span>
                <span class="pg-share-badge">Quiet Client Errors</span>
              </div>
            </div>
            """
        )

        with gr.Row():
            with gr.Column(scale=3):
                with gr.Group(elem_classes=["pg-panel", "pg-controls", "pg-control-board"]):
                    gr.Markdown("### Operator Controls")
                    gr.Markdown("Upload exactly two chart images in order: higher timeframe first, lower timeframe second.")
                    file_input = gr.File(
                        label="Upload Exactly Two Chart Images",
                        file_types=["image"],
                        file_count="multiple",
                    )
                    with gr.Accordion("Overlay Controls", open=True):
                        overlay_mode = gr.Dropdown(
                            choices=["debug-all", "latest-only", "global-only", "history-boxes", "history-plus-projection"],
                            value=str(DEFAULT_SHARE_RENDER["overlay_mode"]),
                            label="Overlay Mode",
                        )
                        min_conf_global = gr.Slider(
                            minimum=0.2,
                            maximum=0.95,
                            value=float(DEFAULT_SHARE_RENDER["min_conf_global"]),
                            step=0.01,
                            label="Global Min Confidence",
                        )
                        min_conf_latest = gr.Slider(
                            minimum=0.2,
                            maximum=0.95,
                            value=float(DEFAULT_SHARE_RENDER["min_conf_latest"]),
                            step=0.01,
                            label="Latest Min Confidence",
                        )
                        history_depth = gr.Slider(
                            minimum=1,
                            maximum=18,
                            value=int(DEFAULT_SHARE_RENDER["history_depth"]),
                            step=1,
                            label="Sequence History Depth",
                        )
                        label_density = gr.Slider(
                            minimum=2,
                            maximum=18,
                            value=int(DEFAULT_SHARE_RENDER["label_density"]),
                            step=1,
                            label="Overlay Label Density",
                        )
                        projection_focus = gr.Slider(
                            minimum=0.0,
                            maximum=0.9,
                            value=float(DEFAULT_SHARE_RENDER["projection_focus"]),
                            step=0.01,
                            label="Projection Visibility Floor",
                        )
                    run_btn = gr.Button("Run Elite Signal")
                status_html = gr.HTML(
                    value=_share_status_html(
                        "Elite share mode is active. Authenticate to use the desk and keep the backend on the host machine.",
                        render_config=_default_render_config(),
                    )
                )
            with gr.Column(scale=9):
                signal_html = gr.HTML(
                    value=pg._placeholder_panel("Signal Overview", "Upload exactly two chart screenshots to activate the elite share desk.")
                )
                with gr.Row():
                    with gr.Column(scale=7):
                        overlay_img = gr.Image(label="Annotated Chart", type="pil", height=560)
                    with gr.Column(scale=5, elem_classes=["pg-share-stack"]):
                        confidence_gauge = gr.Plot(label="Decision Gauge")
                        forecast_html = gr.HTML(
                            value=pg._placeholder_panel("Forecast & Risk", "Forecast guidance will appear here after a signal run.")
                        )
                        adaptive_guidance_html = gr.HTML(
                            value=pg._placeholder_panel("Adaptive Guidance", "The desk will guide the next best panel after the first signal run.")
                        )
                timeframe_overlay_html = gr.HTML(
                    value=pg._placeholder_panel("Timeframe Overlays", "Higher and lower timeframe overlays will appear here after a run.")
                )

        with gr.Tabs(elem_classes=["pg-tab-wrap"]):
            with gr.Tab("Visual Desk"):
                compare_desk_html = gr.HTML(
                    value=pg._placeholder_panel("Compare Desk", "Compare desk will appear here after the first shared inference.")
                )
                with gr.Row():
                    with gr.Column(scale=6):
                        heatmap_img = gr.Image(label="Confidence Heatmap", type="pil", height=470)
                    with gr.Column(scale=4):
                        heatmap_summary_html = gr.HTML(
                            value=pg._placeholder_panel("Confidence Heatmap", "Confidence heat concentration will appear here after a signal run.")
                        )
            with gr.Tab("Model Council") as model_council_tab:
                model_council_html = gr.HTML(
                    value=pg._placeholder_panel("Model Council", "Open this tab after a signal run to request the heavyweight refinement pass.")
                )
            with gr.Tab("Feedback Feed"):
                with gr.Group(elem_classes=["pg-panel", "pg-feedback"]):
                    gr.Markdown("### Outcome Feedback")
                    gr.Markdown("Submit the verdict and optional marked-up result image so the server-side learning feed keeps improving.")
                    verdict = gr.Dropdown(choices=["BUY", "SELL", "HOLD", "WRONG"], value="HOLD", label="Verdict")
                    feedback_result_image = gr.Image(label="Result Image For Learning", type="pil", height=320)
                    reason = gr.Textbox(label="Reason", lines=3, placeholder="Why are you submitting this feedback?")
                    fb_btn = gr.Button("Submit Feedback")
                    fb_status = gr.Textbox(label="Feedback Status", lines=2, interactive=False, value=_share_feedback_placeholder())

        signal_inputs = [
            session_id_state,
            file_input,
            overlay_mode,
            min_conf_global,
            min_conf_latest,
            history_depth,
            label_density,
            projection_focus,
        ]
        signal_outputs = [
            overlay_img,
            confidence_gauge,
            signal_html,
            forecast_html,
            timeframe_overlay_html,
            heatmap_img,
            heatmap_summary_html,
            compare_desk_html,
            adaptive_guidance_html,
            model_council_html,
            status_html,
            session_id_state,
        ]
        preview_inputs = [
            session_id_state,
            overlay_mode,
            min_conf_global,
            min_conf_latest,
            history_depth,
            label_density,
            projection_focus,
        ]

        run_btn.click(
            run_share_signal,
            inputs=signal_inputs,
            outputs=signal_outputs,
            api_visibility="private",
            show_progress="minimal",
        )
        model_council_tab.select(
            load_share_model_council,
            inputs=[session_id_state],
            outputs=signal_outputs,
            api_visibility="private",
            show_progress="minimal",
        )
        overlay_mode.change(
            refresh_share_preview,
            inputs=preview_inputs,
            outputs=signal_outputs,
            api_visibility="private",
            queue=False,
            show_progress="hidden",
        )
        for component in [
            min_conf_global,
            min_conf_latest,
            history_depth,
            label_density,
            projection_focus,
        ]:
            component.input(
                refresh_share_preview,
                inputs=preview_inputs,
                outputs=signal_outputs,
                api_visibility="private",
                queue=False,
                show_progress="hidden",
            )
        fb_btn.click(
            submit_share_feedback,
            inputs=[session_id_state, verdict, reason, feedback_result_image],
            outputs=[fb_status],
            api_visibility="private",
            show_progress="minimal",
        )

    demo.queue(default_concurrency_limit=2)
    _log_share_event(
        "info",
        "share_launch",
        host=share_host,
        port=share_port,
        tunnel=share_tunnel,
        strict_passwords=strict_passwords,
        public_surface=public_surface,
    )
    demo.launch(
        server_name=share_host,
        server_port=share_port,
        share=share_tunnel,
        auth=share_auth,
        auth_message="Sign in to use the restricted elite share desk.",
        debug=False,
        show_error=False,
        quiet=True,
        footer_links=[],
        allowed_paths=[],
        blocked_paths=_share_blocked_paths(),
        strict_cors=True,
        max_file_size=max_file_size,
        enable_monitoring=False,
        state_session_capacity=SHARE_MAX_SESSIONS,
        app_kwargs={"docs_url": None, "redoc_url": None, "openapi_url": None},
        pwa=False,
        mcp_server=False,
        theme="default",
        css=SHARE_UI_CSS,
        head=pg.UI_HEAD,
    )


if __name__ == "__main__":
    launch_share_ui()
