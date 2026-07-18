from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any, Final, Literal, cast


TRACKING_EPISODE_SCHEMA_VERSION: Final = "PG_TRACKING_EPISODE_V1"
TRACKING_EPISODE_EVENT_SCHEMA_VERSION: Final = "PG_TRACKING_EPISODE_EVENT_V1"
TRACKING_EPISODE_HISTORY_SCHEMA_VERSION: Final = "PG_TRACKING_EPISODE_HISTORY_ENTRY_V1"
TRACKING_EPISODE_HORIZON: Final = 12
TRACKING_EPISODE_HISTORY_LIMIT: Final = 24

TrackingEpisodeState = Literal[
    "IDLE",
    "ARMING",
    "ACTIVE",
    "COMPLETED",
    "INVALIDATED",
    "STOPPED",
    "FAILED",
]

_ACTIVE_STATES: Final = frozenset({"ARMING", "ACTIVE"})
_TERMINAL_STATES: Final = frozenset(
    {"COMPLETED", "INVALIDATED", "STOPPED", "FAILED"}
)
_VALID_STATES: Final = _ACTIVE_STATES | _TERMINAL_STATES | {"IDLE"}
_DIRECTIONS: Final = frozenset({"BUY", "SELL", "HOLD"})
_OMITTED_KEYS: Final = frozenset(
    {
        "artifact_path",
        "artifact_paths",
        "config_path",
        "dense_history",
        "latent_vector",
        "raw_payload",
        "source_image",
        "source_path",
        "weights_path",
    }
)
_SEMANTIC_PATH_KEYS: Final = frozenset(
    {
        "forecast_path",
        "forecast_paths",
        "prediction_path",
        "progression_path",
        "scenario_path",
        "scenario_paths",
        "trajectory_path",
        "trajectory_paths",
    }
)


class TrackingEpisodeReadinessError(ValueError):
    """Raised when Start Tracking has no complete, event-locked baseline."""

    def __init__(self, reasons: Sequence[str]) -> None:
        normalized = tuple(str(reason or "").strip() for reason in reasons if str(reason or "").strip())
        self.reasons = normalized or ("Tracking episode is not ready.",)
        super().__init__(" ".join(self.reasons))


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def _rows(value: Any, *, limit: int = 24) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    output: list[dict[str, Any]] = []
    for item in cast(Sequence[Any], value)[: max(0, int(limit))]:
        row = _mapping(item)
        if row:
            output.append(row)
    return output


def _text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if result != result or result in {float("inf"), float("-inf")}:
        return None
    return result


def _direction(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip().upper()
        aliases = {
            "BULL": "BUY",
            "BULLISH": "BUY",
            "LONG": "BUY",
            "UP": "BUY",
            "BEAR": "SELL",
            "BEARISH": "SELL",
            "DOWN": "SELL",
            "SHORT": "SELL",
            "NEUTRAL": "HOLD",
            "WAIT": "HOLD",
        }
        text = aliases.get(text, text)
        if text in _DIRECTIONS:
            return text
    return "HOLD"


def _explicit_bool(values: Sequence[Any], *, fallback: bool) -> bool:
    for value in values:
        if isinstance(value, bool):
            return value
    return fallback


def _json_safe(value: Any, *, depth: int = 0, field_name: str = "") -> Any:
    """Return a bounded, path-free JSON value for durable public episode state."""

    normalized_field = str(field_name or "").strip().lower()
    if normalized_field in _OMITTED_KEYS or (
        normalized_field not in _SEMANTIC_PATH_KEYS
        and normalized_field.endswith(("_path", "_paths", "_dir", "_directory"))
    ):
        return None
    if depth >= 8:
        return None
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in {float("inf"), float("-inf")} else None
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, item in list(cast(Mapping[Any, Any], value).items())[:96]:
            key = str(raw_key)
            normalized_key = key.strip().lower()
            if normalized_key in _OMITTED_KEYS or (
                normalized_key not in _SEMANTIC_PATH_KEYS
                and normalized_key.endswith(("_path", "_paths", "_dir", "_directory"))
            ):
                continue
            safe = _json_safe(item, depth=depth + 1, field_name=key)
            if safe not in (None, "", [], {}):
                output[key] = safe
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        output_list: list[Any] = []
        for item in cast(Sequence[Any], value)[:48]:
            safe = _json_safe(item, depth=depth + 1, field_name=field_name)
            if safe is not None:
                output_list.append(safe)
        return output_list
    return str(value)


def _safe_mapping(value: Any) -> dict[str, Any]:
    safe = _json_safe(value)
    return cast(dict[str, Any], safe) if isinstance(safe, dict) else {}


def _episode_permission(*, active: bool, reason: str, entry_state: str = "WAIT") -> dict[str, Any]:
    normalized_entry = str(entry_state or "WAIT").strip().upper() or "WAIT"
    entry_permitted = bool(active and normalized_entry in {"ENTER", "ENTER_NOW", "ALLOWED", "PERMITTED"})
    return {
        "active": bool(active),
        "entry_state": normalized_entry if active else "WAIT",
        "entry_permitted": entry_permitted,
        "execution_authority": "NONE",
        "reason": str(reason or "").strip(),
    }


def default_tracking_episode_v1(*, session_id: str = "") -> dict[str, Any]:
    """Build the persisted no-episode state without starting or stopping a worker."""

    return {
        "schema_version": TRACKING_EPISODE_SCHEMA_VERSION,
        "session_id": str(session_id or "").strip(),
        "episode_id": "",
        "state": "IDLE",
        "revision": 0,
        "event_horizon": TRACKING_EPISODE_HORIZON,
        "event_cursor": 0,
        "started_at": "",
        "updated_at": "",
        "stopped_at": "",
        "completed_at": "",
        "terminal_reason": "",
        "pair": "",
        "timeframe": "",
        "anchor": {},
        "committed_plan": {},
        "baseline_forecasts": {"scene": {}, "lstm": {}, "memory": {}},
        "candidate_revision": {},
        "events": [],
        "processed_closed_candle_keys": [],
        "last_processed_closed_candle_key": "",
        "last_processed_closed_candle_sequence": 0,
        "permission": _episode_permission(
            active=False,
            reason="Start Tracking to freeze a 12-event baseline.",
        ),
        "runtime_policy": {
            "capture_worker": "ALWAYS_WARM",
            "models": "ALWAYS_WARM",
            "stop_scope": "EPISODE_ONLY",
        },
    }


def normalize_tracking_episode_v1(
    value: Any,
    *,
    session_id: str = "",
) -> dict[str, Any]:
    """Normalize a persisted episode while retaining its immutable baseline."""

    source = _mapping(value)
    if source.get("schema_version") != TRACKING_EPISODE_SCHEMA_VERSION:
        return default_tracking_episode_v1(session_id=session_id)
    normalized = default_tracking_episode_v1(
        session_id=_text(source.get("session_id"), session_id)
    )
    state = str(source.get("state") or "IDLE").strip().upper()
    normalized["episode_id"] = _text(source.get("episode_id"))
    normalized["state"] = state if state in _VALID_STATES else "FAILED"
    normalized["revision"] = max(0, _integer(source.get("revision")))
    normalized["event_horizon"] = TRACKING_EPISODE_HORIZON
    events = _rows(source.get("events"), limit=TRACKING_EPISODE_HORIZON)
    normalized["events"] = [_safe_mapping(row) for row in events]
    # Events are the durable source of truth.  A stale/corrupt cursor must not
    # skip a future observation or make the episode appear more complete than
    # the event ledger actually proves.
    normalized["event_cursor"] = min(TRACKING_EPISODE_HORIZON, len(events))
    for key in (
        "started_at",
        "updated_at",
        "stopped_at",
        "completed_at",
        "terminal_reason",
        "pair",
        "timeframe",
        "last_processed_closed_candle_key",
    ):
        normalized[key] = _text(source.get(key))
    normalized["pair"] = str(normalized["pair"]).upper()
    normalized["timeframe"] = str(normalized["timeframe"]).upper()
    normalized["last_processed_closed_candle_sequence"] = max(
        0,
        _integer(source.get("last_processed_closed_candle_sequence")),
    )
    for key in ("anchor", "committed_plan", "candidate_revision"):
        normalized[key] = _safe_mapping(source.get(key))
    forecasts = _mapping(source.get("baseline_forecasts"))
    normalized["baseline_forecasts"] = {
        "scene": _safe_mapping(forecasts.get("scene")),
        "lstm": _safe_mapping(forecasts.get("lstm")),
        "memory": _safe_mapping(forecasts.get("memory")),
    }
    processed = [
        str(item or "").strip()
        for item in cast(Sequence[Any], source.get("processed_closed_candle_keys", []))
        if str(item or "").strip()
    ] if isinstance(source.get("processed_closed_candle_keys"), Sequence) and not isinstance(
        source.get("processed_closed_candle_keys"), (str, bytes, bytearray)
    ) else []
    normalized["processed_closed_candle_keys"] = list(dict.fromkeys(processed))[-(TRACKING_EPISODE_HORIZON + 1) :]
    permission = _mapping(source.get("permission"))
    is_active = str(normalized["state"]) in _ACTIVE_STATES
    normalized["permission"] = _episode_permission(
        active=is_active and bool(permission.get("active", True)),
        reason=_text(permission.get("reason"), "Tracking episode is active." if is_active else "Tracking episode is not active."),
        entry_state=_text(permission.get("entry_state"), "WAIT"),
    )
    return normalized


def _scene_forecast(session: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = _mapping(session.get("forecast_snapshot_v3"))
    latest = _mapping(session.get("latest_signal"))
    tracking = _mapping(session.get("tracking_summary"))
    return (
        _mapping(snapshot.get("scene_forecast_contribution"))
        or _mapping(latest.get("scene_forecast_contribution"))
        or _mapping(tracking.get("scene_forecast_contribution"))
    )


def _lstm_forecast(session: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = _mapping(session.get("forecast_snapshot_v3"))
    latest = _mapping(session.get("latest_signal"))
    tracking = _mapping(session.get("tracking_summary"))
    return (
        _mapping(snapshot.get("lstm_contribution"))
        or _mapping(latest.get("lstm_contribution"))
        or _mapping(tracking.get("lstm_contribution"))
    )


def _identity(session: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = _mapping(session.get("forecast_snapshot_v3"))
    scene = _scene_forecast(session)
    latest = _mapping(session.get("latest_signal"))
    tracking = _mapping(session.get("tracking_summary"))
    external = _mapping(session.get("external_frame_feed"))
    identity_state = _mapping(scene.get("closed_candle_identity_state"))
    pair = _text(
        snapshot.get("pair"),
        scene.get("pair"),
        latest.get("pair"),
        latest.get("symbol"),
        latest.get("market"),
        tracking.get("detected_market"),
        external.get("symbol"),
        session.get("market"),
    ).upper()
    timeframe = _text(
        snapshot.get("timeframe"),
        scene.get("timeframe"),
        latest.get("timeframe"),
        latest.get("focus_timeframe"),
        tracking.get("detected_timeframe"),
        external.get("timeframe"),
    ).upper()
    closed_key = _text(
        scene.get("closed_candle_key"),
        identity_state.get("event_key"),
        snapshot.get("closed_candle_key"),
    )
    closed_sequence = max(
        0,
        _integer(
            scene.get(
                "closed_candle_sequence",
                identity_state.get("event_sequence", snapshot.get("closed_candle_sequence", 0)),
            )
        ),
    )
    market_confirmed = _explicit_bool(
        (
            snapshot.get("market_identity_confirmed"),
            scene.get("market_identity_confirmed"),
        ),
        fallback=bool(pair),
    )
    timeframe_confirmed = _explicit_bool(
        (
            snapshot.get("timeframe_identity_confirmed"),
            scene.get("timeframe_identity_confirmed"),
        ),
        fallback=bool(timeframe),
    )
    return {
        "pair": pair,
        "timeframe": timeframe,
        "market_identity_confirmed": market_confirmed,
        "timeframe_identity_confirmed": timeframe_confirmed,
        "closed_candle_key": closed_key,
        "closed_candle_sequence": closed_sequence,
    }


def tracking_episode_readiness_v1(session: Mapping[str, Any]) -> dict[str, Any]:
    """Describe whether the latest complete frame can seed an episode."""

    identity = _identity(session)
    frame_id = max(
        0,
        _integer(
            session.get(
                "model_vote_frame_id",
                session.get("display_frame_id", session.get("frame_index", 0)),
            )
        ),
    )
    manual_focus = _mapping(session.get("manual_focus_region"))
    external = _mapping(session.get("external_frame_feed"))
    scene = _scene_forecast(session)
    lstm = _lstm_forecast(session)
    scene_steps = _rows(scene.get("forecast_candles"), limit=TRACKING_EPISODE_HORIZON)
    if not scene_steps:
        scene_steps = _rows(scene.get("forecast_path"), limit=TRACKING_EPISODE_HORIZON)
    lstm_steps = _rows(lstm.get("forecast_path"), limit=TRACKING_EPISODE_HORIZON)
    reasons: list[str] = []
    if session.get("tracking_enabled") is False:
        reasons.append("Wait for live chart tracking to be running.")
    if not bool(manual_focus.get("enabled", False)) and not external:
        reasons.append("Lock the broker chart focus before starting tracking.")
    if frame_id <= 0:
        reasons.append("Wait for the first complete analyzed frame.")
    if session.get("frame_bundle_complete_v3") is False:
        reasons.append("Wait for the current atomic frame bundle to finish publishing.")
    if not identity["pair"] or not bool(identity["market_identity_confirmed"]):
        reasons.append("Wait for confirmed market identity.")
    if not identity["timeframe"] or not bool(identity["timeframe_identity_confirmed"]):
        reasons.append("Wait for confirmed timeframe identity.")
    if not identity["closed_candle_key"]:
        reasons.append("Wait for a confirmed closed-candle event.")
    if len(scene_steps) != TRACKING_EPISODE_HORIZON and len(lstm_steps) != TRACKING_EPISODE_HORIZON:
        reasons.append("Wait for a complete 12-event Scene or LSTM forecast baseline.")
    return {
        "ready": not reasons,
        "reasons": reasons,
        "identity": identity,
        "frame_id": frame_id,
        "scene_horizon": len(scene_steps),
        "lstm_horizon": len(lstm_steps),
    }


def _committed_plan(session: Mapping[str, Any]) -> dict[str, Any]:
    latest = _mapping(session.get("latest_signal"))
    result = _mapping(session.get("model_council_result"))
    council = _mapping(result.get("model_council")) or _mapping(session.get("model_council"))
    professional_plan = (
        _mapping(result.get("professional_trade_plan"))
        or _mapping(council.get("professional_trade_plan"))
        or _mapping(latest.get("professional_trade_plan"))
    )
    decision = {
        key: latest.get(key)
        for key in (
            "action",
            "side",
            "execution_action",
            "execution_permission",
            "entry_state",
            "setup",
            "summary",
            "confidence",
            "effective_confidence",
            "target",
            "invalidation",
        )
        if latest.get(key) not in (None, "", [], {})
    }
    return {
        "decision": _safe_mapping(decision),
        "signal_thesis": _safe_mapping(session.get("signal_thesis_v3")),
        "opportunity_window": _safe_mapping(session.get("execution_opportunity_window_v3")),
        "professional_trade_plan": _safe_mapping(professional_plan),
        "model_council": _safe_mapping(
            {
                key: council.get(key)
                for key in (
                    "final_state",
                    "final_side",
                    "confidence",
                    "arbitration_reason",
                    "promotion_trace",
                )
                if council.get(key) not in (None, "", [], {})
            }
        ),
    }


def _baseline_forecasts(session: Mapping[str, Any]) -> dict[str, Any]:
    memory_predict = _safe_mapping(session.get("memory_projection_predict"))
    memory_future = _safe_mapping(session.get("memory_projection_future"))
    return {
        "scene": _safe_mapping(_scene_forecast(session)),
        "lstm": _safe_mapping(_lstm_forecast(session)),
        "memory": {
            "active_mode": _text(session.get("memory_projection_active_mode")),
            "predict": memory_predict,
            "future": memory_future,
        },
    }


def start_tracking_episode_v1(
    current: Any,
    session: Mapping[str, Any],
    *,
    episode_id: str,
    now_iso: str,
) -> dict[str, Any]:
    """Freeze the latest complete plan/forecast as one 12-event episode."""

    normalized = normalize_tracking_episode_v1(
        current,
        session_id=_text(session.get("session_id")),
    )
    if str(normalized["state"]) in _ACTIVE_STATES and normalized.get("episode_id"):
        return normalized
    readiness = tracking_episode_readiness_v1(session)
    if not bool(readiness["ready"]):
        raise TrackingEpisodeReadinessError(
            cast(Sequence[str], readiness["reasons"])
        )
    identity = cast(dict[str, Any], readiness["identity"])
    normalized_episode_id = str(episode_id or "").strip()
    if not normalized_episode_id:
        seed = "|".join(
            (
                _text(session.get("session_id")),
                str(identity["closed_candle_key"]),
                str(readiness["frame_id"]),
                str(now_iso),
            )
        )
        normalized_episode_id = f"episode-{sha256(seed.encode('utf-8')).hexdigest()[:20]}"
    latest = _mapping(session.get("latest_signal"))
    entry_state = _text(
        latest.get("execution_permission"),
        latest.get("entry_state"),
        "WAIT",
    )
    anchor = {
        "frame_id": int(readiness["frame_id"]),
        "capture_epoch": _number(
            session.get("model_capture_epoch", session.get("last_capture_epoch"))
        )
        or 0.0,
        "captured_at": _text(
            session.get("last_capture_at"),
            latest.get("published_at"),
            now_iso,
        ),
        "source_capture_id": _text(session.get("source_capture_id")),
        "surface_signature": _text(
            session.get("last_study_surface_signature"),
            session.get("last_display_surface_signature"),
        ),
        **identity,
    }
    started = {
        "schema_version": TRACKING_EPISODE_SCHEMA_VERSION,
        "session_id": _text(session.get("session_id")),
        "episode_id": normalized_episode_id,
        "state": "ACTIVE",
        "revision": 1,
        "event_horizon": TRACKING_EPISODE_HORIZON,
        "event_cursor": 0,
        "started_at": str(now_iso),
        "updated_at": str(now_iso),
        "stopped_at": "",
        "completed_at": "",
        "terminal_reason": "",
        "pair": str(identity["pair"]),
        "timeframe": str(identity["timeframe"]),
        "anchor": _safe_mapping(anchor),
        "committed_plan": _committed_plan(session),
        "baseline_forecasts": _baseline_forecasts(session),
        "candidate_revision": {},
        "events": [],
        "processed_closed_candle_keys": [str(identity["closed_candle_key"])],
        "last_processed_closed_candle_key": str(identity["closed_candle_key"]),
        "last_processed_closed_candle_sequence": int(identity["closed_candle_sequence"]),
        "permission": _episode_permission(
            active=True,
            reason="Episode is active; entry still depends on the committed plan and live safety gates.",
            entry_state=entry_state,
        ),
        "runtime_policy": {
            "capture_worker": "ALWAYS_WARM",
            "models": "ALWAYS_WARM",
            "stop_scope": "EPISODE_ONLY",
        },
    }
    return normalize_tracking_episode_v1(
        started,
        session_id=_text(session.get("session_id")),
    )


def stop_tracking_episode_v1(
    current: Any,
    *,
    session_id: str,
    now_iso: str,
    reason: str = "manual_stop",
) -> dict[str, Any]:
    """Stop only the episode and retain every baseline/event artifact."""

    normalized = normalize_tracking_episode_v1(current, session_id=session_id)
    state = str(normalized["state"])
    if state not in _ACTIVE_STATES:
        return normalized
    normalized["state"] = "STOPPED"
    normalized["revision"] = int(normalized["revision"]) + 1
    normalized["updated_at"] = str(now_iso)
    normalized["stopped_at"] = str(now_iso)
    normalized["terminal_reason"] = str(reason or "manual_stop").strip() or "manual_stop"
    normalized["permission"] = _episode_permission(
        active=False,
        reason="Tracking episode stopped; capture and models remain warm.",
    )
    return normalized


def _actual_closed_candle(session: Mapping[str, Any], identity: Mapping[str, Any]) -> dict[str, Any]:
    scene = _scene_forecast(session)
    identity_state = _mapping(scene.get("closed_candle_identity_state"))
    latest_closed = _mapping(identity_state.get("latest_closed"))
    tracking = _mapping(session.get("tracking_summary"))
    latest = _mapping(session.get("latest_signal"))
    tracked = _rows(tracking.get("tracked_candles"), limit=128)
    closed_rows = [row for row in tracked if row.get("is_closed") is not False]
    candle = closed_rows[-1] if closed_rows else (tracked[-1] if tracked else latest_closed)
    side = _direction(
        latest_closed.get("side"),
        latest_closed.get("direction"),
        candle.get("side"),
        candle.get("direction"),
        latest.get("action"),
        tracking.get("local_direction"),
    )
    close_norm = None
    close_space = ""
    for key, space in (
        ("close_y_norm", "CHART_Y_NORM"),
        ("normalized_close_y", "CHART_Y_NORM"),
        ("price_proxy", "PRICE_PROXY_NORM"),
        ("close_norm", "PRICE_NORM"),
    ):
        close_norm = _number(candle.get(key, latest_closed.get(key)))
        if close_norm is not None:
            close_space = space
            break
    return _safe_mapping(
        {
            "closed_candle_key": identity.get("closed_candle_key"),
            "closed_candle_sequence": identity.get("closed_candle_sequence"),
            "side": side,
            "close_norm": close_norm,
            "coordinate_space": close_space,
            "track_id": _text(candle.get("track_id"), latest_closed.get("track_id")),
        }
    )


def _predicted_block(episode: Mapping[str, Any], step: int) -> dict[str, Any]:
    forecasts = _mapping(episode.get("baseline_forecasts"))
    lstm = _mapping(forecasts.get("lstm"))
    scene = _mapping(forecasts.get("scene"))
    lstm_path = _rows(lstm.get("forecast_path"), limit=TRACKING_EPISODE_HORIZON)
    scene_path = _rows(scene.get("forecast_candles"), limit=TRACKING_EPISODE_HORIZON)
    if not scene_path:
        scene_path = _rows(scene.get("forecast_path"), limit=TRACKING_EPISODE_HORIZON)
    source = "LSTM" if len(lstm_path) >= step else "SCENE"
    row = lstm_path[step - 1] if len(lstm_path) >= step else (
        scene_path[step - 1] if len(scene_path) >= step else {}
    )
    expected_close = None
    coordinate_space = ""
    candidates = (
        ("expected_close_norm", "PRICE_NORM"),
        ("close_norm", "PRICE_NORM"),
        ("close_y_norm", "CHART_Y_NORM"),
        ("relative_close", "PRICE_PROXY_NORM"),
    )
    for key, space in candidates:
        expected_close = _number(row.get(key))
        if expected_close is not None:
            coordinate_space = space
            break
    return _safe_mapping(
        {
            "step": step,
            "source": source,
            "side": _direction(
                row.get("movement_side"),
                row.get("body_bias"),
                row.get("side"),
                lstm.get("trajectory_mode"),
                lstm.get("side"),
                scene.get("path_side"),
                scene.get("side"),
            ),
            "expected_close_norm": expected_close,
            "coordinate_space": coordinate_space,
            "confidence": _number(
                row.get(
                    "confidence",
                    lstm.get("path_confidence", lstm.get("confidence", scene.get("confidence"))),
                )
            ),
            "block": row,
        }
    )


def _candidate_revision(session: Mapping[str, Any], identity: Mapping[str, Any]) -> dict[str, Any]:
    scene = _scene_forecast(session)
    lstm = _lstm_forecast(session)
    latest = _mapping(session.get("latest_signal"))
    return _safe_mapping(
        {
            "source_frame_id": _integer(
                session.get("model_vote_frame_id", session.get("frame_index", 0))
            ),
            "closed_candle_key": identity.get("closed_candle_key"),
            "closed_candle_sequence": identity.get("closed_candle_sequence"),
            "scene_side": _direction(scene.get("path_side"), scene.get("side")),
            "lstm_side": _direction(lstm.get("trajectory_mode"), lstm.get("side")),
            "decision_side": _direction(latest.get("action"), latest.get("side")),
            "confidence": _number(
                latest.get("effective_confidence", latest.get("confidence"))
            ),
            "advisory_only": True,
        }
    )


def _progress_snapshot(session: Mapping[str, Any]) -> dict[str, Any]:
    latest = _mapping(session.get("latest_signal"))
    tracking = _mapping(session.get("tracking_summary"))
    kernel = _mapping(latest.get("decision_kernel")) or _mapping(tracking.get("decision_kernel"))
    return _safe_mapping(
        {
            "decision_state": _text(
                latest.get("phoenixguard_decision_state"),
                kernel.get("state"),
                latest.get("status"),
            ),
            "target_progress": _number(
                latest.get("target_progress", tracking.get("target_progress"))
            ),
            "invalidation_progress": _number(
                latest.get("invalidation_progress", tracking.get("invalidation_progress"))
            ),
            "candles_to_target": _integer(kernel.get("eta_target_after_trigger_candles")),
            "candles_to_invalidation": _integer(kernel.get("eta_invalidation_candles")),
        }
    )


def advance_tracking_episode_v1(
    current: Any,
    session: Mapping[str, Any],
    *,
    now_iso: str,
) -> dict[str, Any]:
    """Consume at most one new confirmed closed-candle event from a capture."""

    normalized = normalize_tracking_episode_v1(
        current,
        session_id=_text(session.get("session_id")),
    )
    if str(normalized["state"]) != "ACTIVE":
        return normalized
    identity = _identity(session)
    pair = str(identity["pair"])
    timeframe = str(identity["timeframe"])
    if (
        pair
        and timeframe
        and bool(identity["market_identity_confirmed"])
        and bool(identity["timeframe_identity_confirmed"])
        and (pair != normalized["pair"] or timeframe != normalized["timeframe"])
    ):
        normalized["state"] = "INVALIDATED"
        normalized["revision"] = int(normalized["revision"]) + 1
        normalized["updated_at"] = str(now_iso)
        normalized["completed_at"] = str(now_iso)
        normalized["terminal_reason"] = "PAIR_OR_TIMEFRAME_CHANGED"
        normalized["candidate_revision"] = _candidate_revision(session, identity)
        normalized["permission"] = _episode_permission(
            active=False,
            reason="Pair or timeframe changed; start a new tracking episode after identity stabilizes.",
        )
        return normalized
    closed_key = str(identity["closed_candle_key"] or "").strip()
    closed_sequence = int(identity["closed_candle_sequence"] or 0)
    last_closed_sequence = int(
        normalized["last_processed_closed_candle_sequence"] or 0
    )
    if (
        not closed_key
        or not bool(identity["market_identity_confirmed"])
        or not bool(identity["timeframe_identity_confirmed"])
        or closed_key == normalized["last_processed_closed_candle_key"]
        or closed_key in cast(Sequence[str], normalized["processed_closed_candle_keys"])
        or (
            closed_sequence > 0
            and last_closed_sequence > 0
            and closed_sequence <= last_closed_sequence
        )
    ):
        return normalized
    step = int(normalized["event_cursor"]) + 1
    if step > TRACKING_EPISODE_HORIZON:
        return normalized
    predicted = _predicted_block(normalized, step)
    actual = _actual_closed_candle(session, identity)
    predicted_side = _direction(predicted.get("side"))
    actual_side = _direction(actual.get("side"))
    predicted_close = _number(predicted.get("expected_close_norm"))
    actual_close = _number(actual.get("close_norm"))
    comparable = bool(
        predicted_close is not None
        and actual_close is not None
        and _text(predicted.get("coordinate_space"))
        == _text(actual.get("coordinate_space"))
    )
    anchor = _mapping(normalized.get("anchor"))
    frame_id = max(
        0,
        _integer(session.get("model_vote_frame_id", session.get("frame_index", 0))),
    )
    event = {
        "schema_version": TRACKING_EPISODE_EVENT_SCHEMA_VERSION,
        "episode_id": normalized["episode_id"],
        "event_id": f"{normalized['episode_id']}:E{step}",
        "step": step,
        "label": f"E{step}",
        "observed_at": str(now_iso),
        "closed_candle_key": closed_key,
        "closed_candle_sequence": int(identity["closed_candle_sequence"]),
        "predicted_block": predicted,
        "actual_block": actual,
        "direction_agreement": (
            predicted_side == actual_side
            if predicted_side in {"BUY", "SELL"} and actual_side in {"BUY", "SELL"}
            else None
        ),
        "displacement_error": (
            abs(cast(float, predicted_close) - cast(float, actual_close))
            if comparable
            else None
        ),
        "progress": _progress_snapshot(session),
        "before_reference": {
            "frame_id": _integer(anchor.get("frame_id")),
            "source_capture_id": _text(anchor.get("source_capture_id")),
            "surface_signature": _text(anchor.get("surface_signature")),
        },
        "after_reference": {
            "frame_id": frame_id,
            "source_capture_id": _text(session.get("source_capture_id")),
            "surface_signature": _text(
                session.get("last_study_surface_signature"),
                session.get("last_display_surface_signature"),
            ),
        },
    }
    events = cast(list[dict[str, Any]], normalized["events"])
    events.append(_safe_mapping(event))
    normalized["events"] = events[:TRACKING_EPISODE_HORIZON]
    normalized["event_cursor"] = len(normalized["events"])
    processed = list(cast(Sequence[str], normalized["processed_closed_candle_keys"]))
    processed.append(closed_key)
    normalized["processed_closed_candle_keys"] = list(dict.fromkeys(processed))[-(TRACKING_EPISODE_HORIZON + 1) :]
    normalized["last_processed_closed_candle_key"] = closed_key
    normalized["last_processed_closed_candle_sequence"] = int(identity["closed_candle_sequence"])
    normalized["candidate_revision"] = _candidate_revision(session, identity)
    normalized["revision"] = int(normalized["revision"]) + 1
    normalized["updated_at"] = str(now_iso)
    if int(normalized["event_cursor"]) >= TRACKING_EPISODE_HORIZON:
        normalized["state"] = "COMPLETED"
        normalized["completed_at"] = str(now_iso)
        normalized["terminal_reason"] = "EVENT_HORIZON_COMPLETE"
        normalized["permission"] = _episode_permission(
            active=False,
            reason="All 12 closed-candle events were observed; start a new episode for another baseline.",
        )
    return normalized


def tracking_episode_is_active_v1(value: Any) -> bool:
    return str(_mapping(value).get("state") or "").strip().upper() in _ACTIVE_STATES


def tracking_episode_history_entry_v1(value: Any) -> dict[str, Any]:
    """Build a bounded before/after summary for a terminal episode."""

    episode = normalize_tracking_episode_v1(value)
    episode_id = str(episode.get("episode_id", "") or "").strip()
    state = str(episode.get("state", "IDLE") or "IDLE").strip().upper()
    if not episode_id or state not in _TERMINAL_STATES:
        return {}
    events = _rows(episode.get("events"), limit=TRACKING_EPISODE_HORIZON)
    agreements = [
        bool(row["direction_agreement"])
        for row in events
        if isinstance(row.get("direction_agreement"), bool)
    ]
    displacement_errors = [
        value
        for row in events
        if (value := _number(row.get("displacement_error"))) is not None
    ]
    anchor = _mapping(episode.get("anchor"))
    last_event = events[-1] if events else {}
    public_events: list[dict[str, Any]] = []
    for index, event in enumerate(events[:TRACKING_EPISODE_HORIZON], start=1):
        predicted = _mapping(event.get("predicted_block"))
        actual = _mapping(event.get("actual_block"))
        after_reference = _mapping(event.get("after_reference"))
        agreement = event.get("direction_agreement")
        public_events.append(
            {
                "event_id": _text(
                    event.get("event_id"),
                    f"{episode_id}:E{index}",
                ),
                "step": max(
                    1,
                    min(
                        TRACKING_EPISODE_HORIZON,
                        _integer(event.get("step"), index),
                    ),
                ),
                "observed_at": _text(event.get("observed_at")),
                "predicted_side": _direction(predicted.get("side")),
                "actual_side": _direction(actual.get("side")),
                "direction_agreement": (
                    agreement if isinstance(agreement, bool) else None
                ),
                "frame_id": max(
                    0,
                    _integer(
                        after_reference.get("frame_id", event.get("frame_id"))
                    ),
                ),
            }
        )
    ended_at = _text(
        episode.get("completed_at"),
        episode.get("stopped_at"),
        episode.get("updated_at"),
    )
    return {
        "schema_version": TRACKING_EPISODE_HISTORY_SCHEMA_VERSION,
        "episode_id": episode_id,
        "state": state,
        "revision": int(episode.get("revision", 0) or 0),
        "pair": str(episode.get("pair", "") or ""),
        "timeframe": str(episode.get("timeframe", "") or ""),
        "started_at": str(episode.get("started_at", "") or ""),
        "ended_at": ended_at,
        "terminal_reason": str(episode.get("terminal_reason", "") or ""),
        "event_cursor": int(episode.get("event_cursor", 0) or 0),
        "event_horizon": TRACKING_EPISODE_HORIZON,
        "direction_agreement_count": sum(1 for value in agreements if value),
        "direction_observation_count": len(agreements),
        "mean_displacement_error": (
            sum(displacement_errors) / len(displacement_errors)
            if displacement_errors
            else None
        ),
        "anchor_frame_id": _integer(anchor.get("frame_id")),
        "last_event_id": str(last_event.get("event_id", "") or ""),
        "last_closed_candle_key": str(
            last_event.get("closed_candle_key", "") or ""
        ),
        # The durable session timeline intentionally stores only neutral
        # before/after event summaries. Forecast geometry, normalized prices,
        # model/provider fields, and source lineage remain in the private
        # per-episode record and never enter the public history contract.
        "events": public_events,
    }


def update_tracking_episode_history_v1(
    history: Any,
    episode: Any,
    *,
    limit: int = TRACKING_EPISODE_HISTORY_LIMIT,
) -> list[dict[str, Any]]:
    """Upsert one terminal episode into newest-first bounded history."""

    entry = tracking_episode_history_entry_v1(episode)
    rows = _rows(history, limit=max(TRACKING_EPISODE_HISTORY_LIMIT * 2, limit * 2))
    normalized_rows = [
        _safe_mapping(row)
        for row in rows
        if str(row.get("episode_id", "") or "").strip()
    ]
    if not entry:
        return normalized_rows[: max(1, int(limit))]
    episode_id = str(entry["episode_id"])
    deduplicated = [
        row
        for row in normalized_rows
        if str(row.get("episode_id", "") or "") != episode_id
    ]
    return [entry, *deduplicated][: max(1, int(limit))]


__all__ = [
    "TRACKING_EPISODE_EVENT_SCHEMA_VERSION",
    "TRACKING_EPISODE_HORIZON",
    "TRACKING_EPISODE_HISTORY_LIMIT",
    "TRACKING_EPISODE_HISTORY_SCHEMA_VERSION",
    "TRACKING_EPISODE_SCHEMA_VERSION",
    "TrackingEpisodeReadinessError",
    "TrackingEpisodeState",
    "advance_tracking_episode_v1",
    "default_tracking_episode_v1",
    "normalize_tracking_episode_v1",
    "start_tracking_episode_v1",
    "stop_tracking_episode_v1",
    "tracking_episode_is_active_v1",
    "tracking_episode_history_entry_v1",
    "tracking_episode_readiness_v1",
    "update_tracking_episode_history_v1",
]
