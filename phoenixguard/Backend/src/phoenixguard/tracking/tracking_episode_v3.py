from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any, Final, Literal, cast

from phoenixguard.decision.order_positioning_v3 import (
    ORDER_POSITIONING_ACTUAL_SCHEMA_VERSION,
    advance_order_positioning_plan_v3,
    build_order_positioning_candidates_v3,
    fit_order_positioning_reprojection_v3,
    freeze_order_positioning_plan_v3,
    inverse_reproject_order_positioning_y_v3,
    order_positioning_plan_anchors_valid_v3,
)
from phoenixguard.tracking.market_object_tracker_v3 import (
    build_market_object_registry_v3,
)
from phoenixguard.vision.v3_overlay_contract import normalize_v3_overlay_object


TRACKING_EPISODE_SCHEMA_VERSION: Final = "PG_TRACKING_EPISODE_V1"
TRACKING_EPISODE_EVENT_SCHEMA_VERSION: Final = "PG_TRACKING_EPISODE_EVENT_V1"
TRACKING_EPISODE_HISTORY_SCHEMA_VERSION: Final = "PG_TRACKING_EPISODE_HISTORY_ENTRY_V1"
TRACKING_EPISODE_OBSERVATION_SCHEMA_VERSION: Final = (
    "PG_TRACKING_EPISODE_OBSERVATION_V1"
)
ORDER_REFERENCE_MAP_SCHEMA_VERSION: Final = "PG_ORDER_REFERENCE_MAP_V1"
TRACKING_EPISODE_HORIZON: Final = 12
TRACKING_EPISODE_HISTORY_LIMIT: Final = 24
TRACKING_PATH_COMPARISON_SCHEMA_VERSION: Final = "PG_TRACKING_PATH_COMPARISON_V1"
_PATH_FAVOR_MIN_EVENTS: Final = 3
_PATH_EVENT_MIN_MARGIN: Final = 0.004
_PATH_EPISODE_MIN_MARGIN: Final = 0.008
_PATH_RELATIVE_MARGIN: Final = 0.15

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


class TrackingEpisodeStateError(ValueError):
    """Raised when an episode control is invalid for the current lifecycle state."""

    def __init__(self, state: str, message: str) -> None:
        self.state = str(state or "IDLE").strip().upper() or "IDLE"
        super().__init__(str(message or "Tracking episode state does not allow this action."))


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


def _default_observation_state() -> dict[str, Any]:
    return {
        "schema_version": TRACKING_EPISODE_OBSERVATION_SCHEMA_VERSION,
        "status": "WAITING_FOR_BASELINE",
        "reason": "NO_ACTIVE_BASELINE",
        "last_trusted_frame_id": 0,
        "last_trusted_at": "",
        "current_frame_id": 0,
        "current_at": "",
        "confirmed_event_count": 0,
        "recoverable_event_count": 0,
        "unresolved_gap": False,
        "coverage_status": "UNKNOWN",
    }


def _default_path_comparison() -> dict[str, Any]:
    return {
        "schema_version": TRACKING_PATH_COMPARISON_SCHEMA_VERSION,
        "paths": [],
        "verdict": "GEOMETRY_UNAVAILABLE",
        "favored_path_id": "",
        "verdict_summary": "Two comparable forecast paths are not available yet.",
        "anchor": {
            "status": "UNAVAILABLE",
            "label": "Latest completed candle",
            "direction": "HOLD",
        },
        "forming_at_start": {
            "status": "UNAVAILABLE",
            "label": "Candle forming when tracking started",
            "direction": "HOLD",
        },
        "forecast_bias": {
            "status": "UNAVAILABLE",
            "label": "Forecast bias at start",
            "summary": "No forecast bias was available when tracking started.",
            "direction": "HOLD",
        },
        "entry_thesis": {
            "status": "UNAVAILABLE",
            "label": "Entry idea at start",
            "summary": "No entry idea was available when tracking started.",
            "direction": "HOLD",
        },
        "trade_permission": {
            "status": "WAIT",
            "label": "Entry permission at start",
            "summary": "Tracking evidence does not grant permission to trade.",
        },
        "entry_location": {
            "status": "UNAVAILABLE",
            "label": "Saved entry area",
            "summary": "No verified entry area was available when tracking started.",
            "direction": "HOLD",
            "preferred_location": "",
            "top_level": None,
            "bottom_level": None,
            "progress": {"status": "UNKNOWN", "distance": None},
        },
        "transform_contract": {
            "status": "UNAVAILABLE",
            "reason": "NORMALIZED_CHART_TRANSFORM_NOT_PROVEN",
        },
    }


def _normalize_observation_state(value: Any) -> dict[str, Any]:
    source = _mapping(value)
    normalized = _default_observation_state()
    status = _text(source.get("status"), normalized["status"]).upper()
    if status not in {
        "WAITING_FOR_BASELINE",
        "WAITING_FOR_CLOSE",
        "LIVE",
        "REACQUIRING",
        "STOPPED",
    }:
        status = "REACQUIRING"
    coverage = _text(source.get("coverage_status"), "UNKNOWN").upper()
    if coverage not in {"STABLE", "DEGRADED", "UNKNOWN"}:
        coverage = "UNKNOWN"
    normalized.update(
        {
            "status": status,
            "reason": _text(source.get("reason"), normalized["reason"])[:96],
            "last_trusted_frame_id": max(
                0,
                _integer(source.get("last_trusted_frame_id")),
            ),
            "last_trusted_at": _text(source.get("last_trusted_at"))[:48],
            "current_frame_id": max(0, _integer(source.get("current_frame_id"))),
            "current_at": _text(source.get("current_at"))[:48],
            "confirmed_event_count": max(
                0,
                min(
                    TRACKING_EPISODE_HORIZON,
                    _integer(source.get("confirmed_event_count")),
                ),
            ),
            "recoverable_event_count": max(
                0,
                min(
                    TRACKING_EPISODE_HORIZON,
                    _integer(source.get("recoverable_event_count")),
                ),
            ),
            "unresolved_gap": source.get("unresolved_gap") is True,
            "coverage_status": coverage,
        }
    )
    return normalized


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
        "positioning_plan": {},
        "baseline_forecasts": {"scene": {}, "lstm": {}, "memory": {}},
        "candidate_revision": {},
        "events": [],
        "processed_closed_candle_keys": [],
        "last_processed_closed_candle_key": "",
        "last_processed_closed_candle_sequence": 0,
        "observation_state": _default_observation_state(),
        "path_comparison": _default_path_comparison(),
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
    for key in ("anchor", "committed_plan", "positioning_plan", "candidate_revision"):
        normalized[key] = _safe_mapping(source.get(key))
    normalized["observation_state"] = _normalize_observation_state(
        source.get("observation_state")
    )
    path_comparison = _safe_mapping(source.get("path_comparison"))
    normalized["path_comparison"] = (
        path_comparison
        if path_comparison.get("schema_version")
        == TRACKING_PATH_COMPARISON_SCHEMA_VERSION
        else _default_path_comparison()
    )
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
    confirmed_event_batch = _rows(
        identity_state.get("confirmed_event_batch"),
        limit=24,
    )
    match_scores = _mapping(scene.get("closed_candle_match_scores"))
    reacquisition = _mapping(identity_state.get("reacquisition"))
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
        "confirmed_event_batch": confirmed_event_batch,
        "transition_reason": _text(
            scene.get("closed_candle_transition_reason"),
            reacquisition.get("reason"),
        ),
        "transition_count": max(
            0,
            _integer(identity_state.get("transition_count")),
        ),
        "match_scores": match_scores,
        "reacquisition": reacquisition,
        "closed_candle_identity_state": _safe_mapping(identity_state),
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
    path_comparison = _freeze_two_path_comparison(session)
    transform = _transform_contract(session)
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
    if len(_rows(path_comparison.get("paths"), limit=2)) != 2:
        reasons.append(
            "Wait for Scene forecast paths with distinct geometry."
            if path_comparison.get("verdict") == "PATHS_OVERLAP"
            else "Wait for one selected Scene path and a complete alternative."
        )
    if transform.get("status") != "LOCKED":
        reasons.append("Wait for stable normalized chart geometry before starting tracking.")
    return {
        "ready": not reasons,
        "reasons": reasons,
        "identity": identity,
        "frame_id": frame_id,
        "scene_horizon": len(scene_steps),
        "lstm_horizon": len(lstm_steps),
        "path_count": len(_rows(path_comparison.get("paths"), limit=2)),
    }


def _committed_plan(session: Mapping[str, Any]) -> dict[str, Any]:
    latest = _mapping(session.get("latest_signal"))
    tracking = _mapping(session.get("tracking_summary"))
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
    signal_thesis = (
        _mapping(session.get("signal_thesis_v3"))
        or _mapping(latest.get("signal_thesis_v3"))
        or _mapping(tracking.get("signal_thesis_v3"))
    )
    return {
        "decision": _safe_mapping(decision),
        "signal_thesis": _safe_mapping(signal_thesis),
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


_POSITIONING_SOURCE_TYPES: Final = frozenset(
    {
        "DEMAND_ZONE",
        "SUPPLY_ZONE",
        "ORDER_BLOCK",
        "RETEST_BOX",
        "SUPPORT_TRENDLINE",
        "RESISTANCE_TRENDLINE",
    }
)
_POSITIONING_SOURCE_LIMIT: Final = 24
_CANONICAL_ANCHOR_QUALITY_FIELDS: Final = frozenset(
    {
        "has_candle_anchor",
        "has_sequence_anchor",
        "inside_plot_area",
        "matches_symbol_timeframe",
        "chart_transform_valid",
    }
)
_POSITIONING_SOURCE_EXPORT_FIELDS: Final = frozenset(
    {
        "schema_version",
        "overlay_id",
        "id",
        "object_id",
        "track_id",
        "type",
        "side",
        "frame_id",
        "sequence_id",
        "chart_transform_id",
        "broker_source_lock_id",
        "coordinate_mode",
        "bounds",
        "bbox",
        "lifecycle_state",
        "anchor_evidence_status",
        "anchor_evidence",
        "anchor_quality",
        "confidence",
        "truth_score",
        "anchor_candle_indices",
        "anchor_candles",
        "projected_entry_band",
        "projected_price_band",
        "confirmation_evidence",
        "confirmation_state",
        "breakout_confirmation_state",
        "stop_entry_confirmation_valid",
        "confirmation_is_closed",
        "confirmation_closed_candle_key",
        "confirmed_candle_key",
        "confirmation_closed_candle_index",
        "confirmation_side",
        "confirmation_direction",
        "breakout_side",
        "confirmation_event",
        "confirmation_type",
        "reaction_type",
        "knowledge_tags",
    }
)
_ORDER_REFERENCE_SOURCE_TYPES: Final = frozenset(
    {"DEMAND_ZONE", "SUPPLY_ZONE", "SUPPORT_TRENDLINE", "RESISTANCE_TRENDLINE"}
)
_ORDER_REFERENCE_LIVE_STATES: Final = frozenset(
    {
        "ACTIVE",
        "CONFIRMED",
        "FRESH",
        "TESTED",
        "MITIGATED",
        "FRESH_ACTIVE",
        "MITIGATED_ACTIVE",
        "ROLE_FLIP_CONFIRMED",
    }
)


def _stable_broker_source_lock_id(session: Mapping[str, Any]) -> str:
    tracking = _mapping(session.get("tracking_summary"))
    latest = _mapping(session.get("latest_signal"))
    return _text(
        _mapping(tracking.get("broker_source")).get("lock_id"),
        _mapping(tracking.get("broker_source_lock")).get("lock_id"),
        _mapping(latest.get("broker_source")).get("lock_id"),
        _mapping(latest.get("broker_source_lock")).get("lock_id"),
        _mapping(session.get("broker_source")).get("lock_id"),
        _mapping(session.get("broker_source_lock")).get("lock_id"),
    )


def _current_positioning_frame_id(session: Mapping[str, Any]) -> int:
    for key in (
        "display_frame_id",
        "chart_frame_id",
        "overlay_frame_id",
        "model_vote_frame_id",
        "frame_index",
        "frame_id",
    ):
        value = session.get(key)
        if value not in (None, ""):
            return _integer(value)
    return 0


def _current_positioning_snapshot(
    session: Mapping[str, Any],
    *,
    frame_id: int,
) -> tuple[bool, list[dict[str, Any]]]:
    tracking = _mapping(session.get("tracking_summary"))
    snapshot = _mapping(tracking.get("order_positioning_sources_v3"))
    if not snapshot or snapshot.get("frame_id") in (None, ""):
        return False, []
    snapshot_frame_id = _integer(snapshot.get("frame_id"), -1)
    current_frame_ids = [
        _integer(session.get(key), -1)
        for key in (
            "display_frame_id",
            "chart_frame_id",
            "overlay_frame_id",
            "model_vote_frame_id",
            "frame_index",
            "frame_id",
        )
        if session.get(key) not in (None, "")
    ]
    if (
        snapshot_frame_id != frame_id
        or any(current != snapshot_frame_id for current in current_frame_ids)
    ):
        return False, []
    return True, _rows(snapshot.get("objects"), limit=_POSITIONING_SOURCE_LIMIT)


def _positioning_source_is_current(
    row: Mapping[str, Any],
    *,
    frame_id: int,
) -> bool:
    coordinate_mode = _text(row.get("coordinate_mode")).upper()
    precision_quality = _mapping(row.get("anchor_quality"))
    return bool(
        _text(row.get("frame_id")) == str(frame_id)
        and _text(row.get("schema_version")) == "PG_V3_OVERLAY_OBJECT_V1"
        and _text(row.get("type")).upper() in _POSITIONING_SOURCE_TYPES
        and "CHART" in coordinate_mode
        and "PLOT" not in coordinate_mode
        and row.get("precision_rejected") is not True
        and _text(precision_quality.get("status")).upper() != "REJECT"
    )


def _canonical_positioning_source(
    row: Mapping[str, Any],
    *,
    stable_source_lock_id: str,
    image_size: Sequence[Any] | None,
) -> dict[str, Any]:
    source = dict(row)
    quality = _mapping(source.get("anchor_quality"))
    if not _CANONICAL_ANCHOR_QUALITY_FIELDS.issubset(quality):
        # The market-object registry intentionally replaces the public V3
        # contract quality object with its precision-refinement report. Strip
        # that internal shape and rebuild the canonical, fail-closed quality
        # flags consumed by order_positioning_v3.
        source.pop("anchor_quality", None)
        source.pop("anchor_confidence", None)
        try:
            source = {
                str(key): value
                for key, value in normalize_v3_overlay_object(
                    source,
                    strict=False,
                    image_size=image_size,
                ).items()
            }
        except (TypeError, ValueError):
            return {}
    if stable_source_lock_id:
        source["broker_source_lock_id"] = stable_source_lock_id
    return source


def _project_positioning_source(row: Mapping[str, Any]) -> dict[str, Any]:
    return _safe_mapping(
        {
            key: value
            for key, value in row.items()
            if key in _POSITIONING_SOURCE_EXPORT_FIELDS
            and value not in (None, "", [], {})
        }
    )


def _explicit_positioning_overlay_rows(
    session: Mapping[str, Any],
) -> list[dict[str, Any]]:
    containers: list[Any] = [
        session.get("v3_overlay_objects"),
        session.get("overlay_objects"),
    ]
    for parent_key in ("overlays", "live_visual_state", "live_state_v3"):
        parent = _mapping(session.get(parent_key))
        overlay_container = _mapping(parent.get("overlays")) or parent
        containers.extend(
            (
                overlay_container.get("objects"),
                overlay_container.get("all_objects"),
            )
        )
    tracking = _mapping(session.get("tracking_summary"))
    containers.extend(
        (
            tracking.get("v3_overlay_objects"),
            tracking.get("overlay_objects"),
        )
    )
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for container in containers:
        for row in _rows(container, limit=512):
            key = _text(
                row.get("overlay_id"),
                row.get("id"),
                row.get("object_id"),
                row.get("track_id"),
            )
            if not key or key in seen:
                continue
            seen.add(key)
            output.append(row)
    return output


def order_positioning_source_rows_v3(
    session: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return bounded canonical positioning sources for the displayed frame.

    Persisted tracking sessions deliberately omit the full overlay arrays. In
    that compact state the market-object registry is the authoritative fallback
    because it reconstructs the same frame from retained vision artifacts.
    """

    frame_id = _current_positioning_frame_id(session)
    stable_source_lock_id = _stable_broker_source_lock_id(session)
    dimensions = _source_image_dimensions(_scene_forecast(session))
    image_size: Sequence[Any] | None = dimensions

    snapshot_is_current, snapshot_rows = _current_positioning_snapshot(
        session,
        frame_id=frame_id,
    )
    if snapshot_is_current:
        raw_sources = snapshot_rows
    else:
        raw_sources = [
            row
            for row in _explicit_positioning_overlay_rows(session)
            if _positioning_source_is_current(row, frame_id=frame_id)
        ]
    if not snapshot_is_current and not raw_sources:
        try:
            registry = build_market_object_registry_v3(session)
        except (AttributeError, KeyError, TypeError, ValueError):
            return []
        raw_sources = [
            row
            for row in _rows(registry.overlays, limit=512)
            if _positioning_source_is_current(row, frame_id=frame_id)
        ]

    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_sources:
        row = _canonical_positioning_source(
            raw,
            stable_source_lock_id=stable_source_lock_id,
            image_size=image_size,
        )
        if not row or not _positioning_source_is_current(row, frame_id=frame_id):
            continue
        key = _text(row.get("track_id"), row.get("object_id"), row.get("overlay_id"))
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(_project_positioning_source(row))
        if len(output) >= _POSITIONING_SOURCE_LIMIT:
            break
    output.sort(
        key=lambda row: (
            _text(row.get("sequence_id")),
            _text(row.get("chart_transform_id")),
            _text(row.get("broker_source_lock_id")),
            _text(row.get("track_id"), row.get("object_id")),
        )
    )
    return output


def _order_reference_unavailable(
    session: Mapping[str, Any],
    reason: str,
    *,
    current_y: float | None = None,
    price_basis: str = "",
) -> dict[str, Any]:
    identity = _identity(session)
    return {
        "schema_version": ORDER_REFERENCE_MAP_SCHEMA_VERSION,
        "status": "UNAVAILABLE",
        "availability_reason": reason,
        "frame_id": _current_positioning_frame_id(session),
        "sequence_id": "",
        "chart_transform_id": "",
        "broker_source_lock_id": _stable_broker_source_lock_id(session),
        "market": _text(identity.get("pair")).upper(),
        "timeframe": _text(identity.get("timeframe")).upper(),
        "coordinate_mode": "CHART_NORMALIZED",
        "chart_bounds": [0.0, 0.0, 1.0, 1.0],
        "current_price_y_norm": current_y,
        "current_price_basis": price_basis,
        "reference_count": 0,
        "rows": [],
        "observational_only": True,
        "execution_authority": "NONE",
    }


def _order_reference_geometry(
    session: Mapping[str, Any],
) -> tuple[float, float, float] | None:
    transform = _transform_contract(session)
    width = _number(transform.get("source_width"))
    height = _number(transform.get("source_height"))
    tolerance = _number(transform.get("close_tolerance"))
    if (
        transform.get("status") == "LOCKED"
        and width is not None
        and height is not None
        and tolerance is not None
        and width > 0.0
        and height > 0.0
        and tolerance > 0.0
    ):
        return width, height, round(min(0.012, tolerance), 6)

    tracking = _mapping(session.get("tracking_summary"))
    region = _mapping(tracking.get("chart_region") or tracking.get("display_region"))
    width = _number(region.get("width"))
    height = _number(region.get("height"))
    raw_bbox = region.get("pixel_bbox")
    bbox = (
        cast(Sequence[Any], raw_bbox)
        if isinstance(raw_bbox, Sequence)
        and not isinstance(raw_bbox, (str, bytes, bytearray))
        else ()
    )
    values = [_number(bbox[index]) for index in range(4)] if len(bbox) >= 4 else []
    focus = _mapping(session.get("manual_focus_region"))
    if (
        width is None
        or height is None
        or width <= 0.0
        or height <= 0.0
        or len(values) != 4
        or any(value is None for value in values)
        or focus.get("enabled") is not True
    ):
        return None
    left, top, right, bottom = [cast(float, value) for value in values]
    if (
        abs(left) > 1.0
        or abs(top) > 1.0
        or right <= left
        or bottom <= top
        or abs((right - left) - width) > 1.0
        or abs((bottom - top) - height) > 1.0
    ):
        return None
    return width, height, round(min(0.012, max(3.0 / height, 0.003)), 6)


def _order_reference_current_y(
    session: Mapping[str, Any],
    *,
    chart_height: float,
) -> tuple[float | None, str]:
    scene = _scene_forecast(session)
    identity_state = _mapping(scene.get("closed_candle_identity_state"))
    forming = _mapping(identity_state.get("forming"))
    forming_y = _number(_normalized_observation_geometry(forming, scene).get("chart_close_norm"))
    if forming_y is not None and 0.0 <= forming_y <= 1.0:
        return round(forming_y, 6), "CURRENT_CANDLE"
    actual_y = _number(_actual_closed_candle(session, _identity(session)).get("chart_close_norm"))
    if actual_y is not None and 0.0 <= actual_y <= 1.0:
        return round(actual_y, 6), "LATEST_COMPLETED_CANDLE"

    tracking = _mapping(session.get("tracking_summary"))
    for candle in reversed(_rows(tracking.get("tracked_candles"), limit=24)):
        close_y = _number(
            candle.get("close_y_px")
            if candle.get("close_y_px") is not None
            else candle.get("close_y")
        )
        if close_y is None:
            raw_bbox = candle.get("body_bbox") or candle.get("bbox") or candle.get("bounds")
            bbox = (
                cast(Sequence[Any], raw_bbox)
                if isinstance(raw_bbox, Sequence)
                and not isinstance(raw_bbox, (str, bytes, bytearray))
                else ()
            )
            if len(bbox) >= 4:
                top = _number(bbox[1])
                bottom = _number(bbox[3])
                side = _direction(candle.get("direction"), candle.get("side"))
                close_y = top if side == "BUY" else bottom if side == "SELL" else None
        if close_y is not None and 0.0 <= close_y <= chart_height:
            return round(close_y / chart_height, 6), "CURRENT_VISUAL_CANDLE"
    return None, ""


def _order_reference_bounds(
    row: Mapping[str, Any],
    *,
    chart_width: float,
    chart_height: float,
) -> list[float]:
    coordinate_mode = _text(row.get("coordinate_mode")).upper()
    raw_bounds: Any = row.get("bounds") or row.get("bbox")
    if _text(row.get("type")).upper() in {"SUPPORT_TRENDLINE", "RESISTANCE_TRENDLINE"}:
        band = _mapping(row.get("projected_entry_band") or row.get("projected_price_band"))
        if band.get("verified") is not True or _text(band.get("coordinate_mode")).upper() != coordinate_mode:
            return []
        raw_bounds = band.get("bounds") or band.get("bbox")
    if not isinstance(raw_bounds, Sequence) or isinstance(raw_bounds, (str, bytes, bytearray)):
        return []
    raw = cast(Sequence[Any], raw_bounds)
    values = [_number(raw[index]) for index in range(4)] if len(raw) >= 4 else []
    if len(values) != 4 or any(value is None for value in values):
        return []
    x0, y0, x1, y1 = [cast(float, value) for value in values]
    if x0 >= x1 or y0 >= y1:
        return []
    if coordinate_mode == "CHART_IMAGE_SPACE":
        if x0 < 0.0 or y0 < 0.0 or x1 > chart_width or y1 > chart_height:
            return []
        normalized = [x0 / chart_width, y0 / chart_height, x1 / chart_width, y1 / chart_height]
    elif coordinate_mode == "CHART_NORMALIZED":
        normalized = [x0, y0, x1, y1]
    else:
        return []
    return (
        [round(value, 6) for value in normalized]
        if all(0.0 <= value <= 1.0 for value in normalized)
        else []
    )


def _order_reference_source_valid(
    row: Mapping[str, Any],
    *,
    frame_id: int,
    source_lock_id: str,
) -> bool:
    quality = _mapping(row.get("anchor_quality"))
    evidence = _mapping(row.get("anchor_evidence"))
    quality_score = _number(quality.get("score"))
    confidence = _number(row.get("confidence"))
    truth = _number(row.get("truth_score"))
    return bool(
        _text(row.get("schema_version")) == "PG_V3_OVERLAY_OBJECT_V1"
        and _text(row.get("frame_id")) == str(frame_id)
        and _text(row.get("type")).upper() in _ORDER_REFERENCE_SOURCE_TYPES
        and _text(row.get("track_id"), row.get("object_id"), row.get("overlay_id"))
        and _text(row.get("sequence_id"))
        and _text(row.get("chart_transform_id"))
        and _text(row.get("broker_source_lock_id")) == source_lock_id
        and _text(row.get("coordinate_mode")).upper()
        in {"CHART_IMAGE_SPACE", "CHART_NORMALIZED"}
        and _text(row.get("lifecycle_state")).upper()
        in _ORDER_REFERENCE_LIVE_STATES
        and _text(row.get("anchor_evidence_status")).upper() == "VALID"
        and evidence.get("valid") is True
        and quality_score is not None
        and quality_score >= 0.65
        and all(
            quality.get(key) is True
            for key in _CANONICAL_ANCHOR_QUALITY_FIELDS
        )
        and confidence is not None
        and confidence >= 0.70
        and truth is not None
        and truth >= 0.70
    )


def _order_reference_row(
    source: Mapping[str, Any],
    *,
    order_kind: str,
    intent: str,
    side: str,
    role: str,
    bounds: Sequence[float],
    boundary_y: float,
    current_y: float,
) -> dict[str, Any]:
    source_key = _text(
        source.get("track_id"),
        source.get("object_id"),
        source.get("overlay_id"),
    )
    source_digest = sha256(f"order-source|{source_key}".encode()).hexdigest()
    source_reference_id = f"ors_{source_digest[:16]}"
    identity = f"{source_reference_id}|{order_kind}|{intent}"
    quality = _mapping(source.get("anchor_quality"))
    confidence = min(
        cast(float, _number(source.get("confidence"))),
        cast(float, _number(source.get("truth_score"))),
        cast(float, _number(quality.get("score"))),
    )
    payload: dict[str, Any] = {
        "reference_id": f"orr_{sha256(identity.encode()).hexdigest()[:18]}",
        "order_kind": order_kind,
        "intent": intent,
        "side": side,
        "location_role": role,
        "bounds": [round(float(value), 6) for value in bounds[:4]],
        "boundary_y_norm": round(boundary_y, 6),
        "distance_from_current_norm": round(abs(boundary_y - current_y), 6),
        "confidence": round(confidence, 4),
        "source_reference_id": source_reference_id,
        "observational_only": True,
        "execution_authority": "NONE",
    }
    return payload


def _order_reference_band(
    bounds: Sequence[float],
    *,
    above: bool,
    thickness: float,
) -> list[float]:
    x0, y0, x1, y1 = [float(value) for value in bounds[:4]]
    if above:
        outer = max(0.0, y0 - thickness)
        return [x0, outer, x1, y0] if outer < y0 else []
    outer = min(1.0, y1 + thickness)
    return [x0, y1, x1, outer] if outer > y1 else []


def build_tracking_order_reference_map_v3(session: Mapping[str, Any]) -> dict[str, Any]:
    """Return current visual order locations without granting execution authority."""

    geometry = _order_reference_geometry(session)
    if geometry is None:
        return _order_reference_unavailable(session, "TRANSFORM_NOT_LOCKED")
    chart_width, chart_height, band_thickness = geometry
    current_y, price_basis = _order_reference_current_y(session, chart_height=chart_height)
    if current_y is None:
        return _order_reference_unavailable(session, "CURRENT_PRICE_UNAVAILABLE")
    source_lock_id = _stable_broker_source_lock_id(session)
    if not source_lock_id:
        return _order_reference_unavailable(
            session,
            "CURRENT_SOURCE_LOCK_UNAVAILABLE",
            current_y=current_y,
            price_basis=price_basis,
        )
    frame_id = _current_positioning_frame_id(session)
    sources = [
        row
        for row in order_positioning_source_rows_v3(session)
        if _order_reference_source_valid(
            row,
            frame_id=frame_id,
            source_lock_id=source_lock_id,
        )
    ]
    lineages = {
        (
            _text(row.get("sequence_id")),
            _text(row.get("chart_transform_id")),
            _text(row.get("broker_source_lock_id")),
        )
        for row in sources
    }
    if len(lineages) != 1:
        return _order_reference_unavailable(
            session,
            "CURRENT_LINEAGE_UNAVAILABLE",
            current_y=current_y,
            price_basis=price_basis,
        )

    drafts: list[dict[str, Any]] = []
    for source in sources:
        source_type = _text(source.get("type")).upper()
        above = source_type in {"SUPPLY_ZONE", "RESISTANCE_TRENDLINE"}
        expected_side = "SELL" if above else "BUY"
        if _direction(source.get("side")) != expected_side:
            continue
        bounds = _order_reference_bounds(
            source,
            chart_width=chart_width,
            chart_height=chart_height,
        )
        if (
            not bounds
            or (above and bounds[3] >= current_y)
            or (not above and bounds[1] <= current_y)
        ):
            continue
        if source_type == "SUPPLY_ZONE":
            limit_kind = "SELL_LIMIT"
        elif source_type == "DEMAND_ZONE":
            limit_kind = "BUY_LIMIT"
        else:
            limit_kind = ""
        if limit_kind:
            drafts.append(
                _order_reference_row(
                    source,
                    order_kind=limit_kind,
                    intent="ENTRY_LIMIT",
                    side=expected_side,
                    role="UPPER_ENTRY" if above else "LOWER_ENTRY",
                    bounds=bounds,
                    boundary_y=bounds[3] if above else bounds[1],
                    current_y=current_y,
                )
            )
        stop_bounds = _order_reference_band(bounds, above=above, thickness=band_thickness)
        if not stop_bounds:
            continue
        stop_kind = "BUY_STOP" if above else "SELL_STOP"
        stop_side = "BUY" if above else "SELL"
        boundary_y = 0.5 * (stop_bounds[1] + stop_bounds[3])
        drafts.append(
            _order_reference_row(
                source,
                order_kind=stop_kind,
                intent="ENTRY_STOP",
                side=stop_side,
                role="UPPER_CONFIRMATION" if above else "LOWER_CONFIRMATION",
                bounds=stop_bounds,
                boundary_y=boundary_y,
                current_y=current_y,
            )
        )
    route_order = {
        ("ENTRY_LIMIT", "BUY_LIMIT"): 0,
        ("ENTRY_LIMIT", "SELL_LIMIT"): 1,
        ("ENTRY_STOP", "BUY_STOP"): 2,
        ("ENTRY_STOP", "SELL_STOP"): 3,
    }
    selected: list[dict[str, Any]] = []
    seen_routes: set[tuple[str, str]] = set()
    seen_geometries: set[tuple[float, ...]] = set()
    for row in sorted(
        drafts,
        key=lambda item: (
            route_order.get((_text(item.get("intent")), _text(item.get("order_kind"))), 99),
            _number(item.get("distance_from_current_norm")) or 0.0,
            -(_number(item.get("confidence")) or 0.0),
            _text(item.get("reference_id")),
        ),
    ):
        route = (_text(row.get("intent")), _text(row.get("order_kind")))
        geometry_key = tuple(
            float(value)
            for value in cast(Sequence[Any], row.get("bounds", []))[:4]
        )
        if route not in seen_routes and geometry_key not in seen_geometries:
            selected.append(row)
            seen_routes.add(route)
            seen_geometries.add(geometry_key)
    if not selected:
        return _order_reference_unavailable(
            session,
            "NO_CURRENT_STRUCTURAL_REFERENCES",
            current_y=current_y,
            price_basis=price_basis,
        )
    sequence_id, chart_transform_id, lineage_lock_id = next(iter(lineages))
    identity = _identity(session)
    return {
        "schema_version": ORDER_REFERENCE_MAP_SCHEMA_VERSION,
        "status": "READY",
        "availability_reason": "CURRENT_STRUCTURAL_REFERENCES_READY",
        "frame_id": frame_id,
        "sequence_id": sequence_id,
        "chart_transform_id": chart_transform_id,
        "broker_source_lock_id": lineage_lock_id,
        "market": _text(identity.get("pair")).upper(),
        "timeframe": _text(identity.get("timeframe")).upper(),
        "coordinate_mode": "CHART_NORMALIZED",
        "chart_bounds": [0.0, 0.0, 1.0, 1.0],
        "current_price_y_norm": current_y,
        "current_price_basis": price_basis,
        "reference_count": len(selected),
        "rows": selected,
        "observational_only": True,
        "execution_authority": "NONE",
    }


def _order_positioning_candidate_v3(
    session: Mapping[str, Any],
    *,
    frame_id: int,
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    overlays = order_positioning_source_rows_v3(session)
    current_frame = str(frame_id)
    lineage_rows = [
        row
        for row in overlays
        if _text(row.get("frame_id")) == current_frame
        and _text(row.get("schema_version")) == "PG_V3_OVERLAY_OBJECT_V1"
        and _text(row.get("type")).upper() in _POSITIONING_SOURCE_TYPES
        and "CHART" in _text(row.get("coordinate_mode")).upper()
        and "PLOT" not in _text(row.get("coordinate_mode")).upper()
    ]
    lineage_rows.sort(
        key=lambda row: (
            _text(row.get("sequence_id")),
            _text(row.get("chart_transform_id")),
            _text(row.get("broker_source_lock_id")),
            _text(row.get("track_id"), row.get("object_id")),
        )
    )
    lineage_keys = {
        (
            _text(row.get("sequence_id")),
            _text(row.get("chart_transform_id")),
            _text(row.get("broker_source_lock_id")),
            _text(row.get("coordinate_mode")).upper(),
        )
        for row in lineage_rows
    }
    lineage = lineage_rows[0] if len(lineage_keys) == 1 else {}
    coordinate_mode = _text(lineage.get("coordinate_mode")).upper()
    scene = _scene_forecast(session)
    raw_size = scene.get("source_image_size")
    size = (
        cast(Sequence[Any], raw_size)
        if isinstance(raw_size, Sequence)
        and not isinstance(raw_size, (str, bytes, bytearray))
        else ()
    )
    width = _number(size[0]) if len(size) >= 2 else None
    height = _number(size[1]) if len(size) >= 2 else None
    if "NORMALIZED" in coordinate_mode:
        chart_bounds: list[float] = [0.0, 0.0, 1.0, 1.0]
    elif width is not None and height is not None and width > 0.0 and height > 0.0:
        chart_bounds = [0.0, 0.0, width, height]
    else:
        chart_bounds = []
    actual = _actual_closed_candle(session, identity)
    close_level = _number(actual.get("chart_close_norm"))
    current_price_y = (
        close_level
        if close_level is not None and "NORMALIZED" in coordinate_mode
        else close_level * height
        if close_level is not None and height is not None
        else None
    )
    latest = _mapping(session.get("latest_signal"))
    tracking = _mapping(session.get("tracking_summary"))
    thesis = (
        _mapping(session.get("signal_thesis_v3"))
        or _mapping(latest.get("signal_thesis_v3"))
        or _mapping(tracking.get("signal_thesis_v3"))
    )
    side = _direction(thesis.get("side"), thesis.get("effective_side"))
    thesis_state = _text(thesis.get("state"), thesis.get("status")).upper()
    opportunity = (
        _mapping(session.get("execution_opportunity_window_v3"))
        or _mapping(latest.get("execution_opportunity_window_v3"))
    )
    maturity = (
        _mapping(latest.get("opportunity_maturity"))
        or _mapping(session.get("opportunity_maturity"))
    )
    book_strategy = (
        _mapping(latest.get("book_strategy"))
        or _mapping(session.get("book_strategy"))
    )
    allowance = (
        _mapping(latest.get("allowance_package"))
        or _mapping(session.get("allowance_package"))
    )
    promotion = (
        _mapping(latest.get("promotion_trace"))
        or _mapping(session.get("promotion_trace"))
    )
    execution_timing = _mapping(latest.get("execution_timing"))
    timing_decision = _mapping(latest.get("timing_decision"))
    timing_tokens = {
        _text(value).upper()
        for value in (
            thesis.get("entry_state"),
            thesis.get("maturity"),
            latest.get("entry_state"),
            latest.get("opportunity_maturity_state"),
            session.get("opportunity_maturity_state"),
            maturity.get("state"),
            book_strategy.get("state"),
            book_strategy.get("maturity_state"),
            latest.get("book_strategy_state"),
            allowance.get("timing_mode"),
            allowance.get("opportunity_maturity"),
            allowance.get("book_strategy_maturity"),
            promotion.get("timing_mode"),
            latest.get("timing_mode"),
            execution_timing.get("timing_mode"),
            execution_timing.get("state"),
            timing_decision.get("timing_mode"),
            timing_decision.get("state"),
            opportunity.get("status"),
            opportunity.get("state"),
        )
        if _text(value)
    }
    favorable_value = _text(
        opportunity.get("favorable_candles_since_origin"),
        maturity.get("favorable_candles_since_origin"),
        thesis.get("favorable_candles_since_origin"),
        latest.get("favorable_candles_since_origin"),
        session.get("favorable_candles_since_origin"),
    )
    explicit_favorable = max(
        0,
        _integer(favorable_value),
    )
    favorable_candles = (
        5
        if timing_tokens.intersection({"LATE", "LATE_CHASE", "MISSED"})
        else explicit_favorable
    )
    transform = _transform_contract(session)
    return build_order_positioning_candidates_v3(
        {
            "side": side,
            "thesis_verified": bool(
                thesis
                and side in {"BUY", "SELL"}
                and thesis_state not in {"", "INVALID", "INVALIDATED", "EXPIRED", "STALE"}
            ),
            "frame_id": frame_id,
            "sequence_id": lineage.get("sequence_id"),
            "chart_transform_id": lineage.get("chart_transform_id"),
            "broker_source_lock_id": lineage.get("broker_source_lock_id"),
            "market": identity.get("pair"),
            "timeframe": identity.get("timeframe"),
            "coordinate_mode": coordinate_mode,
            "price_axis_orientation": "SCREEN_Y_INCREASES_DOWN",
            "chart_bounds": chart_bounds,
            "current_price_y": current_price_y,
            "current_price_verified": close_level is not None,
            "timing_verified": bool(
                _text(identity.get("closed_candle_key"))
                and transform.get("status") == "LOCKED"
            ),
            "display_band_norm": transform.get("close_tolerance"),
            "display_band_verified": transform.get("status") == "LOCKED",
            "display_band_basis": "VERIFIED_MEDIAN_CANDLE_RANGE",
            "favorable_candles_since_origin": favorable_candles,
            "overlay_objects": overlays,
            "reprojection_anchors": tracking_reprojection_anchors_v3(session),
        }
    )


def build_tracking_order_positioning_candidate_v3(
    session: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the fail-closed positioning preview for the displayed frame."""

    return _order_positioning_candidate_v3(
        session,
        frame_id=_current_positioning_frame_id(session),
        identity=_identity(session),
    )


def _order_positioning_actual_v3(
    plan: Mapping[str, Any],
    session: Mapping[str, Any],
    actual: Mapping[str, Any],
    *,
    frame_id: int,
    closed_candle_key: str,
    transform_proven: bool,
) -> dict[str, Any]:
    current_frame = str(frame_id)
    lineage_rows = [
        row
        for row in order_positioning_source_rows_v3(session)
        if _text(row.get("frame_id")) == current_frame
        and _text(row.get("schema_version")) == "PG_V3_OVERLAY_OBJECT_V1"
        and _text(row.get("type")).upper() in _POSITIONING_SOURCE_TYPES
        and "CHART" in _text(row.get("coordinate_mode")).upper()
        and "PLOT" not in _text(row.get("coordinate_mode")).upper()
    ]
    lineage_keys = {
        (
            _text(row.get("sequence_id")),
            _text(row.get("chart_transform_id")),
            _text(row.get("broker_source_lock_id")),
        )
        for row in lineage_rows
    }
    lineage = next(iter(lineage_keys)) if len(lineage_keys) == 1 else ("", "", "")
    reprojection = fit_order_positioning_reprojection_v3(
        plan.get("reprojection_anchors"),
        tracking_reprojection_anchors_v3(session),
    )
    top = inverse_reproject_order_positioning_y_v3(
        actual.get("chart_top_norm"),
        reprojection,
    )
    bottom = inverse_reproject_order_positioning_y_v3(
        actual.get("chart_bottom_norm"),
        reprojection,
    )
    close = inverse_reproject_order_positioning_y_v3(
        actual.get("chart_close_norm"),
        reprojection,
    )
    identity = _identity(session)
    return {
        "schema_version": ORDER_POSITIONING_ACTUAL_SCHEMA_VERSION,
        "verified": bool(
            transform_proven
            and order_positioning_plan_anchors_valid_v3(plan)
            and reprojection.get("status") == "PROVEN"
            and top is not None
            and bottom is not None
            and close is not None
            and all(lineage)
        ),
        "is_closed": True,
        "frame_id": frame_id,
        "closed_candle_key": closed_candle_key,
        "sequence_id": lineage[0],
        "chart_transform_id": lineage[1],
        "broker_source_lock_id": lineage[2],
        "market": identity.get("pair"),
        "timeframe": identity.get("timeframe"),
        "coordinate_mode": "CHART_NORMALIZED",
        "chart_bounds": [0.0, 0.0, 1.0, 1.0],
        "high_y_norm": top,
        "low_y_norm": bottom,
        "close_y_norm": close,
        "reprojection_status": reprojection.get("status"),
        "reprojection_reason": reprojection.get("reason"),
        "reprojection_matched_anchor_count": reprojection.get("matched_anchor_count"),
    }


def _point_pairs(value: Any, *, limit: int = 13) -> list[list[float]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    output: list[list[float]] = []
    for item in cast(Sequence[Any], value)[:limit]:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes, bytearray)):
            return []
        pair = cast(Sequence[Any], item)
        if len(pair) < 2:
            return []
        x_value = _number(pair[0])
        y_value = _number(pair[1])
        if (
            x_value is None
            or y_value is None
            or not 0.0 <= x_value <= 1.0
            or not 0.0 <= y_value <= 1.0
        ):
            return []
        output.append([round(x_value, 6), round(y_value, 6)])
    if any(
        output[index][0] <= output[index - 1][0]
        for index in range(1, len(output))
    ):
        return []
    return output


def _chart_level(value: Any, *, price_axis: bool) -> float | None:
    number = _number(value)
    if number is None or not 0.0 <= number <= 1.0:
        return None
    return round(1.0 - number if price_axis else number, 6)


def _forecast_steps(value: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[list[float]]]:
    """Return only real 12-step normalized geometry from one forecast artifact."""

    points = _point_pairs(value.get("line_points"), limit=13)
    candle_rows = _rows(value.get("forecast_candles"), limit=TRACKING_EPISODE_HORIZON)
    if not candle_rows:
        candle_rows = _rows(value.get("forecast_path"), limit=TRACKING_EPISODE_HORIZON)
    steps: list[dict[str, Any]] = []
    if len(candle_rows) == TRACKING_EPISODE_HORIZON:
        for index, row in enumerate(candle_rows, start=1):
            open_level = _chart_level(row.get("open_y_norm"), price_axis=False)
            close_level = _chart_level(row.get("close_y_norm"), price_axis=False)
            if open_level is None:
                open_level = _chart_level(
                    row.get("expected_open_norm", row.get("open_norm")),
                    price_axis=True,
                )
            if close_level is None:
                close_level = _chart_level(
                    row.get(
                        "expected_close_norm",
                        row.get("close_norm", row.get("relative_close")),
                    ),
                    price_axis=True,
                )
            if open_level is None and len(points) == 13:
                open_level = points[index - 1][1]
            if close_level is None and len(points) == 13:
                close_level = points[index][1]
            if open_level is None or close_level is None:
                return [], []
            steps.append(
                {
                    "step": index,
                    "open_level": open_level,
                    "close_level": close_level,
                    "body_size": round(abs(close_level - open_level), 6),
                    "direction": _direction(
                        row.get("movement_side"),
                        row.get("movement_direction"),
                        row.get("body_bias"),
                        row.get("candle_body_direction"),
                        value.get("side"),
                    ),
                }
            )
    elif len(points) == 13:
        for index in range(1, TRACKING_EPISODE_HORIZON + 1):
            open_level = points[index - 1][1]
            close_level = points[index][1]
            steps.append(
                {
                    "step": index,
                    "open_level": open_level,
                    "close_level": close_level,
                    "body_size": round(abs(close_level - open_level), 6),
                    "direction": _direction(
                        value.get("side"),
                        "BUY" if close_level < open_level else "SELL" if close_level > open_level else "HOLD",
                    ),
                }
            )
    if len(steps) != TRACKING_EPISODE_HORIZON:
        return [], []
    return steps, points if len(points) == 13 else []


def _path_geometry_distance(left: Mapping[str, Any], right: Mapping[str, Any]) -> float | None:
    left_points = _point_pairs(left.get("points"), limit=13)
    right_points = _point_pairs(right.get("points"), limit=13)
    if len(left_points) != 13 or len(right_points) != 13:
        return None
    if (
        abs(left_points[0][0] - right_points[0][0]) > 1e-6
        or abs(left_points[0][1] - right_points[0][1]) > 1e-6
        or any(
            abs(left_point[0] - right_point[0]) > 1e-6
            for left_point, right_point in zip(
                left_points,
                right_points,
                strict=True,
            )
        )
    ):
        return None
    left_steps = _rows(left.get("steps"), limit=TRACKING_EPISODE_HORIZON)
    right_steps = _rows(right.get("steps"), limit=TRACKING_EPISODE_HORIZON)
    if len(left_steps) != TRACKING_EPISODE_HORIZON or len(right_steps) != TRACKING_EPISODE_HORIZON:
        return None
    differences: list[float] = []
    for left_step, right_step in zip(left_steps, right_steps, strict=True):
        left_value = _number(left_step.get("close_level"))
        right_value = _number(right_step.get("close_level"))
        if left_value is None or right_value is None:
            return None
        differences.append(abs(left_value - right_value))
    return round(
        (sum(difference**2 for difference in differences) / len(differences)) ** 0.5,
        6,
    )


def _path_separated_step_count(left: Mapping[str, Any], right: Mapping[str, Any]) -> int:
    left_steps = _rows(left.get("steps"), limit=TRACKING_EPISODE_HORIZON)
    right_steps = _rows(right.get("steps"), limit=TRACKING_EPISODE_HORIZON)
    if len(left_steps) != TRACKING_EPISODE_HORIZON or len(right_steps) != TRACKING_EPISODE_HORIZON:
        return 0
    return sum(
        1
        for left_step, right_step in zip(left_steps, right_steps, strict=True)
        if (
            (left_close := _number(left_step.get("close_level"))) is not None
            and (right_close := _number(right_step.get("close_level"))) is not None
            and abs(left_close - right_close) >= 0.008
        )
    )


def _selected_scene_geometry(
    scene: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[list[float]]]:
    """Return geometry only when exactly one complete Scene scenario is selected."""

    selected = [
        scenario
        for scenario in _rows(scene.get("forecast_scenarios"), limit=8)
        if scenario.get("selected") is True
    ]
    if len(selected) != 1:
        return [], []
    steps, points = _forecast_steps(selected[0])
    if len(steps) != TRACKING_EPISODE_HORIZON or len(points) != 13:
        return [], []
    return steps, points


def _median_positive(values: Sequence[float]) -> float | None:
    usable = sorted(value for value in values if 0.0 < value <= 1.0)
    if not usable:
        return None
    midpoint = len(usable) // 2
    if len(usable) % 2:
        return usable[midpoint]
    return (usable[midpoint - 1] + usable[midpoint]) / 2.0


def _derived_scene_event_step_x(scene: Mapping[str, Any]) -> float | None:
    _, points = _selected_scene_geometry(scene)
    if len(points) != 13:
        return None
    deltas = [
        points[index][0] - points[index - 1][0]
        for index in range(1, len(points))
    ]
    event_step = _median_positive(deltas)
    if event_step is None or len(deltas) != TRACKING_EPISODE_HORIZON:
        return None
    alignment_tolerance = max(1e-6, event_step * 0.02)
    if any(
        delta <= 0.0 or abs(delta - event_step) > alignment_tolerance
        for delta in deltas
    ):
        return None
    return round(event_step, 6)


def _derived_scene_vertical_scale(scene: Mapping[str, Any]) -> float | None:
    steps, _ = _selected_scene_geometry(scene)
    if len(steps) != TRACKING_EPISODE_HORIZON:
        return None
    bodies = [
        abs(close_level - open_level)
        for step in steps
        if (open_level := _number(step.get("open_level"))) is not None
        and (close_level := _number(step.get("close_level"))) is not None
        and abs(close_level - open_level) > 1e-6
    ]
    if len(bodies) < 4:
        return None
    scale = _median_positive(bodies)
    return round(scale, 6) if scale is not None else None


def _source_image_dimensions(
    scene: Mapping[str, Any],
) -> tuple[float, float] | None:
    identity_state = _mapping(scene.get("closed_candle_identity_state"))
    for value in (
        scene.get("source_image_size"),
        identity_state.get("source_image_size"),
    ):
        if isinstance(value, Mapping):
            dimensions = _mapping(value)
            width = _number(dimensions.get("width"))
            height = _number(dimensions.get("height"))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            size = cast(Sequence[Any], value)
            width = _number(size[0]) if len(size) >= 2 else None
            height = _number(size[1]) if len(size) >= 2 else None
        else:
            width = None
            height = None
        if width is not None and height is not None and width > 0.0 and height > 0.0:
            return width, height
    return None


def _source_image_height(scene: Mapping[str, Any]) -> float | None:
    dimensions = _source_image_dimensions(scene)
    return dimensions[1] if dimensions is not None else None


def _normalized_observation_geometry(
    candle: Mapping[str, Any],
    scene: Mapping[str, Any],
) -> dict[str, Any]:
    height = _source_image_height(scene)

    def chart_y(normalized_key: str, pixel_key: str) -> float | None:
        normalized = _chart_level(candle.get(normalized_key), price_axis=False)
        if normalized is not None:
            return normalized
        pixels = _number(candle.get(pixel_key))
        if pixels is None or height is None or not 0.0 <= pixels <= height:
            return None
        return round(pixels / height, 6)

    open_level = chart_y("open_y_norm", "open_y")
    close_level = chart_y("close_y_norm", "close_y")
    top_level = chart_y("top_y_norm", "top_y")
    bottom_level = chart_y("bottom_y_norm", "bottom_y")
    if close_level is None:
        # price_proxy is explicitly defined by the scene contributor as the
        # vertical inverse of normalized chart Y. Arbitrary close_norm values
        # have no such contract and are intentionally not transformed.
        close_level = _chart_level(candle.get("price_proxy"), price_axis=True)
    return {
        "chart_open_norm": open_level,
        "chart_close_norm": close_level,
        "chart_top_norm": top_level,
        "chart_bottom_norm": bottom_level,
        "chart_body_norm": (
            round(abs(close_level - open_level), 6)
            if open_level is not None and close_level is not None
            else None
        ),
    }


def tracking_reprojection_anchors_v3(
    session: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return stable, closed-candle anchors on the chart-normalized plane."""

    tracking = _mapping(session.get("tracking_summary"))
    scene = _scene_forecast(session)
    dimensions = _source_image_dimensions(scene)
    width = dimensions[0] if dimensions is not None else None
    anchors: list[dict[str, Any]] = []
    seen: set[str] = set()
    tracked_closed = [
        candle
        for candle in _rows(tracking.get("tracked_candles"), limit=256)
        if candle.get("is_closed") is True
    ]
    identity_state = _mapping(scene.get("closed_candle_identity_state"))
    closed_tail = _rows(identity_state.get("closed_tail"), limit=24)
    latest_closed = _mapping(identity_state.get("latest_closed"))
    closed_candidates = [*tracked_closed, *closed_tail]
    if latest_closed:
        closed_candidates.append(latest_closed)
    for candle in closed_candidates:
        anchor_id = _text(
            candle.get("track_id"),
            candle.get("object_id"),
            candle.get("candle_id"),
        )
        if not anchor_id or anchor_id in seen:
            continue
        x_norm = _number(
            candle.get("x_norm")
            if candle.get("x_norm") is not None
            else candle.get("normalized_x")
            if candle.get("normalized_x") is not None
            else candle.get("center_x_norm")
        )
        if x_norm is None and width is not None:
            center_x = _number(
                candle.get("center_x_px")
                if candle.get("center_x_px") is not None
                else candle.get("center_x")
                if candle.get("center_x") is not None
                else candle.get("x")
            )
            raw_bbox = candle.get("bbox") or candle.get("bounds")
            if (
                center_x is None
                and isinstance(raw_bbox, Sequence)
                and not isinstance(raw_bbox, (str, bytes, bytearray))
            ):
                bbox = cast(Sequence[Any], raw_bbox)
                left = _number(bbox[0]) if len(bbox) >= 4 else None
                right = _number(bbox[2]) if len(bbox) >= 4 else None
                if left is not None and right is not None:
                    center_x = 0.5 * (left + right)
            if center_x is not None:
                x_norm = center_x / width
        y_norm = _number(_normalized_observation_geometry(candle, scene).get("chart_close_norm"))
        if (
            x_norm is None
            or y_norm is None
            or not 0.0 <= x_norm <= 1.0
            or not 0.0 <= y_norm <= 1.0
        ):
            continue
        seen.add(anchor_id)
        anchors.append(
            {
                "anchor_id": anchor_id,
                "x_norm": round(x_norm, 6),
                "y_norm": round(y_norm, 6),
            }
        )
    anchors.sort(key=lambda row: (float(row["x_norm"]), str(row["anchor_id"])))
    return anchors[-24:]


def _entry_thesis(session: Mapping[str, Any]) -> dict[str, Any]:
    latest = _mapping(session.get("latest_signal"))
    tracking = _mapping(session.get("tracking_summary"))
    thesis = (
        _mapping(session.get("signal_thesis_v3"))
        or _mapping(latest.get("signal_thesis_v3"))
        or _mapping(tracking.get("signal_thesis_v3"))
    )
    direction = _direction(
        thesis.get("side"),
        thesis.get("direction"),
    )
    if direction in {"BUY", "SELL"}:
        status = "DIRECTIONAL"
        direction_word = "buy" if direction == "BUY" else "sell"
        fallback = f"The saved idea was to watch for a {direction_word} entry while live permission remained separate."
    elif thesis:
        status = "NEUTRAL"
        fallback = "The saved idea was to wait for clearer direction; it did not grant permission to trade."
    else:
        status = "UNAVAILABLE"
        fallback = "No entry idea was available when tracking started."
    return {
        "status": status,
        "label": "Entry idea at start",
        "summary": _text(thesis.get("summary"), thesis.get("thesis"), fallback)[:240],
        "direction": direction,
    }


def _forming_candle_at_start(session: Mapping[str, Any]) -> dict[str, Any]:
    tracking = _mapping(session.get("tracking_summary"))
    scene = _scene_forecast(session)
    identity_state = _mapping(scene.get("closed_candle_identity_state"))
    tracked = _rows(tracking.get("tracked_candles"), limit=128)
    forming_candidate = next(
        (row for row in reversed(tracked) if row.get("is_closed") is False),
        None,
    )
    forming: dict[str, Any] = forming_candidate or _mapping(
        identity_state.get("forming")
    )
    if not forming:
        return {
            "status": "UNAVAILABLE",
            "label": "Candle forming when tracking started",
            "direction": "HOLD",
        }
    geometry = _normalized_observation_geometry(forming, scene)
    return {
        "status": "OBSERVED",
        "label": "Candle forming when tracking started",
        "direction": _direction(forming.get("side"), forming.get("direction")),
        "open_level": geometry.get("chart_open_norm"),
        "current_level": geometry.get("chart_close_norm"),
    }


def _transform_contract(session: Mapping[str, Any]) -> dict[str, Any]:
    scene = _scene_forecast(session)
    identity_state = _mapping(scene.get("closed_candle_identity_state"))
    anchor = _mapping(scene.get("forecast_anchor"))
    image_size = scene.get("source_image_size")
    if not isinstance(image_size, Sequence) or isinstance(
        image_size,
        (str, bytes, bytearray),
    ):
        return {"status": "UNAVAILABLE", "reason": "SOURCE_DIMENSIONS_UNAVAILABLE"}
    values = cast(Sequence[Any], image_size)
    width = _number(values[0]) if len(values) >= 2 else None
    height = _number(values[1]) if len(values) >= 2 else None
    focus = _mapping(session.get("manual_focus_region"))
    raw_bbox = focus.get("normalized_bbox")
    bbox = (
        [
            _number(value)
            for value in cast(Sequence[Any], raw_bbox)[:4]
        ]
        if isinstance(raw_bbox, Sequence)
        and not isinstance(raw_bbox, (str, bytes, bytearray))
        else []
    )
    median_range = _number(identity_state.get("median_range"))
    normalized_range = (
        round(median_range / height, 6)
        if median_range is not None
        and height is not None
        and 0.0 < median_range <= height
        else None
    )
    target_scale = _number(anchor.get("target_scale_norm"))
    target_scale_source = "ANCHOR_METADATA"
    if target_scale is None:
        target_scale = normalized_range or _derived_scene_vertical_scale(scene)
        target_scale_source = (
            "MEDIAN_CANDLE_RANGE"
            if normalized_range is not None
            else "SELECTED_PATH_GEOMETRY"
        )
    event_step_x = _number(anchor.get("event_step_x_norm"))
    event_step_x_source = "ANCHOR_METADATA"
    if event_step_x is None:
        event_step_x = _derived_scene_event_step_x(scene)
        event_step_x_source = "SELECTED_PATH_GEOMETRY"
    comparison_scale = normalized_range or target_scale
    if (
        width is None
        or height is None
        or width <= 0.0
        or height <= 0.0
        or focus.get("enabled") is not True
        or len(bbox) != 4
        or any(value is None or not 0.0 <= value <= 1.0 for value in bbox)
        or comparison_scale is None
        or comparison_scale <= 0.0
        or comparison_scale > 1.0
        or target_scale is None
        or target_scale <= 0.0
        or target_scale > 1.0
        or event_step_x is None
        or event_step_x <= 0.0
        or event_step_x > 1.0
    ):
        return {
            "status": "UNAVAILABLE",
            "reason": "NORMALIZED_CHART_TRANSFORM_NOT_PROVEN",
        }
    normalized_bbox = [round(cast(float, value), 6) for value in bbox]
    identity_seed = "|".join(
        str(value)
        for value in (
            int(width),
            int(height),
            *normalized_bbox,
        )
    )
    transform_id = sha256(identity_seed.encode("utf-8")).hexdigest()[:20]
    close_tolerance = round(
        max(3.0 / height, min(0.02, comparison_scale * 0.35)),
        6,
    )
    return {
        "status": "LOCKED",
        "reason": "NORMALIZED_CHART_TRANSFORM_LOCKED",
        "source_width": int(width),
        "source_height": int(height),
        "surface_transform_identity": transform_id,
        "chart_transform_identity": transform_id,
        "normalized_focus_bounds": normalized_bbox,
        "median_range_norm": round(comparison_scale, 6),
        "target_scale_norm": round(target_scale, 6),
        "target_scale_source": target_scale_source,
        "event_step_x_norm": round(event_step_x, 6),
        "event_step_x_source": event_step_x_source,
        "close_tolerance": close_tolerance,
        "body_tolerance": close_tolerance,
    }


def _relative_change(left: Any, right: Any) -> float | None:
    left_number = _number(left)
    right_number = _number(right)
    if left_number is None or right_number is None or left_number <= 0.0:
        return None
    return abs(right_number - left_number) / left_number


def _transform_continuity_proven(
    frozen_value: Any,
    session: Mapping[str, Any],
) -> bool:
    frozen = _mapping(frozen_value)
    current = _transform_contract(session)
    if frozen.get("status") != "LOCKED" or current.get("status") != "LOCKED":
        return False
    if (
        _integer(frozen.get("source_width")) != _integer(current.get("source_width"))
        or _integer(frozen.get("source_height")) != _integer(current.get("source_height"))
        or _text(frozen.get("surface_transform_identity"))
        != _text(current.get("surface_transform_identity"))
        or _text(frozen.get("chart_transform_identity"))
        != _text(current.get("chart_transform_identity"))
    ):
        return False
    for key, tolerance in (
        ("median_range_norm", 0.35),
        ("target_scale_norm", 0.35),
        ("event_step_x_norm", 0.20),
    ):
        change = _relative_change(frozen.get(key), current.get(key))
        if change is None or change > tolerance:
            return False
    return True


def _trade_permission_at_start(session: Mapping[str, Any]) -> dict[str, Any]:
    latest = _mapping(session.get("latest_signal"))
    canonical_permission = _text(latest.get("execution_permission")).upper()
    packet = (
        _mapping(session.get("execution_packet"))
        or _mapping(session.get("latest_execution_packet"))
        or _mapping(latest.get("execution_packet"))
        or _mapping(latest.get("latest_execution_packet"))
    )
    validation = _mapping(packet.get("packet_validation"))
    validation_status = _text(validation.get("status")).upper()
    packet_valid = bool(
        validation.get("valid") is True
        or validation.get("passed") is True
        or validation_status in {"VALID", "PASSED", "AUTHORIZED"}
    )
    allowance = _mapping(packet.get("allowance_package"))
    allowance_status = _text(
        allowance.get("status"),
        allowance.get("state"),
        allowance.get("mode"),
    ).upper()
    allowance_valid = bool(
        allowance.get("allowed") is True
        or allowance.get("execution_allowed") is True
        or allowance_status in {"ALLOWED", "AUTHORIZED", "PERMITTED"}
    )
    permission_valid = canonical_permission in {
        "ENTER",
        "ENTER_NOW",
        "ALLOWED",
        "AUTHORIZED",
        "PERMITTED",
    }
    permitted = bool(permission_valid and packet_valid and allowance_valid)
    return {
        "status": "PERMITTED" if permitted else "WAIT",
        "label": "Entry permission at start",
        "summary": (
            "Entry permission and its validated allowance were present when tracking started."
            if permitted
            else "Entry was not permitted by a validated execution allowance when tracking started."
        ),
    }


def _entry_location_at_start(
    session: Mapping[str, Any],
    transform: Mapping[str, Any],
) -> dict[str, Any]:
    latest = _mapping(session.get("latest_signal"))
    tracking = _mapping(session.get("tracking_summary"))
    thesis = (
        _mapping(session.get("signal_thesis_v3"))
        or _mapping(latest.get("signal_thesis_v3"))
        or _mapping(tracking.get("signal_thesis_v3"))
    )
    thesis_side = _direction(thesis.get("side"), thesis.get("effective_side"))
    identity = _identity(session)
    window = (
        _mapping(session.get("execution_opportunity_window_v3"))
        or _mapping(latest.get("execution_opportunity_window_v3"))
    )
    raw_guidance = _mapping(window.get("entry_location_guidance_v3"))
    current_epoch = _number(
        session.get("model_capture_epoch", session.get("last_capture_epoch"))
    )
    valid_until = _number(
        window.get("valid_until_epoch", window.get("valid_until_epoch_sec"))
    )
    window_side = _direction(window.get("side"), raw_guidance.get("side"))
    window_pair = _text(window.get("symbol"), window.get("pair")).upper()
    window_timeframe = _text(window.get("timeframe")).upper()
    guidance_matches = bool(
        raw_guidance
        and thesis_side in {"BUY", "SELL"}
        and window_side == thesis_side
        and (not window_pair or window_pair == _text(identity.get("pair")).upper())
        and (
            not window_timeframe
            or window_timeframe == _text(identity.get("timeframe")).upper()
        )
        and window.get("integrity_valid") is True
        and window.get("lineage_rejected") is not True
        and _text(window.get("state")).upper() in {"OPEN", "ACTIVE"}
        and current_epoch is not None
        and valid_until is not None
        and valid_until >= current_epoch
    )
    guidance = raw_guidance if guidance_matches else {}
    timing = _mapping(latest.get("execution_timing")) or _mapping(
        tracking.get("execution_timing")
    )
    timing_side = _direction(timing.get("side"))
    zone = _mapping(thesis.get("entry_zone")) or (
        _mapping(timing.get("entry_area_zone"))
        if timing_side == thesis_side
        else {}
    )
    band = _mapping(zone.get("anchor_price_band"))
    height = _number(transform.get("source_height"))
    thesis_height = _number(thesis.get("chart_height_proxy"))
    height_matches = bool(
        height is not None
        and thesis_height is not None
        and abs(height - thesis_height) <= 1.0
    )
    top_pixels = _number(band.get("top_y"))
    if top_pixels is None:
        top_pixels = _number(zone.get("top_y"))
    bottom_pixels = _number(band.get("bottom_y"))
    if bottom_pixels is None:
        bottom_pixels = _number(zone.get("bottom_y"))
    top_level = (
        round(top_pixels / height, 6)
        if top_pixels is not None and height is not None and 0.0 <= top_pixels <= height
        else None
    )
    bottom_level = (
        round(bottom_pixels / height, 6)
        if bottom_pixels is not None and height is not None and 0.0 <= bottom_pixels <= height
        else None
    )
    if top_level is not None and bottom_level is not None and top_level > bottom_level:
        top_level, bottom_level = bottom_level, top_level
    if guidance:
        preferred_location = _text(guidance.get("preferred_price_location"))[:48]
        summary = _text(guidance.get("message"), guidance.get("short_label"))[:240]
    elif thesis_side == "BUY":
        preferred_location = "LOWER_PRICE"
        summary = "Watch for a lower-price buy inside the saved entry area; this does not authorize a trade."
    elif thesis_side == "SELL":
        preferred_location = "HIGHER_PRICE"
        summary = "Watch for a higher-price sell inside the saved entry area; this does not authorize a trade."
    else:
        preferred_location = ""
        summary = ""
    direction = thesis_side
    geometry_available = bool(
        transform.get("status") == "LOCKED"
        and height_matches
        and top_level is not None
        and bottom_level is not None
        and bottom_level > top_level
    )
    status = (
        "TRACKING"
        if geometry_available
        else "GUIDANCE_ONLY"
        if thesis_side in {"BUY", "SELL"}
        else "UNAVAILABLE"
    )
    return {
        "status": status,
        "label": "Saved entry area",
        "summary": summary
        or (
            "The verified entry area is tracked as evidence and does not authorize a trade."
            if geometry_available
            else "No verified entry area was available when tracking started."
        ),
        "direction": direction,
        "preferred_location": preferred_location,
        "top_level": top_level if geometry_available else None,
        "bottom_level": bottom_level if geometry_available else None,
        "progress": {"status": "UNKNOWN", "distance": None},
        "zone_key": _text(zone.get("key"))[:80],
        "source_thesis_id": _text(thesis.get("thesis_id"))[:80],
    }


def _freeze_two_path_comparison(session: Mapping[str, Any]) -> dict[str, Any]:
    """Freeze the selected Scene path and its farthest valid Scene alternative."""

    scene = _scene_forecast(session)
    candidates: list[dict[str, Any]] = []
    for scenario in _rows(scene.get("forecast_scenarios"), limit=8):
        steps, points = _forecast_steps(scenario)
        if len(steps) != TRACKING_EPISODE_HORIZON or len(points) != 13:
            continue
        candidates.append(
            {
                "selected": scenario.get("selected") is True,
                "direction": _direction(scenario.get("side")),
                "steps": steps,
                "points": points,
            }
        )
    selected = [candidate for candidate in candidates if candidate.get("selected") is True]
    if len(selected) != 1:
        return _default_path_comparison()
    primary = selected[0]
    alternatives = [candidate for candidate in candidates if candidate is not primary]
    distances = [
        (distance, _path_separated_step_count(primary, candidate), candidate)
        for candidate in alternatives
        if (distance := _path_geometry_distance(primary, candidate)) is not None
    ]
    if not distances:
        return _default_path_comparison()
    valid_distances = [row for row in distances if row[1] >= 4]
    if not valid_distances:
        unavailable = _default_path_comparison()
        unavailable["verdict"] = "PATHS_OVERLAP"
        unavailable["verdict_summary"] = "The available forecast paths overlap too closely to compare safely."
        return unavailable
    distance, _, alternative = max(valid_distances, key=lambda row: row[0])
    if distance < 0.01:
        unavailable = _default_path_comparison()
        unavailable["verdict"] = "PATHS_OVERLAP"
        unavailable["verdict_summary"] = "The available forecast paths overlap too closely to compare safely."
        return unavailable

    def frozen_path(candidate: Mapping[str, Any], *, path_id: str, label: str) -> dict[str, Any]:
        direction = _direction(candidate.get("direction"))
        direction_word = "upward" if direction == "BUY" else "downward" if direction == "SELL" else "sideways"
        return {
            "id": path_id,
            "label": label,
            "direction": direction,
            "summary": f"Saved {direction_word} progression from the starting candle.",
            "points": candidate.get("points", []),
            "steps": candidate.get("steps", []),
        }

    latest_closed = _actual_closed_candle(session, _identity(session))
    latest_close = _number(latest_closed.get("chart_close_norm"))
    latest_direction = _direction(latest_closed.get("side"))
    transform = _transform_contract(session)
    trade_permission = _trade_permission_at_start(session)
    primary_direction = _direction(primary.get("direction"))
    return {
        "schema_version": TRACKING_PATH_COMPARISON_SCHEMA_VERSION,
        "paths": [
            frozen_path(primary, path_id="PATH_A", label="Main forecast"),
            frozen_path(alternative, path_id="PATH_B", label="Alternative forecast"),
        ],
        "verdict": "WAITING",
        "favored_path_id": "",
        "verdict_summary": "Waiting for confirmed candles to compare the two saved forecasts.",
        "anchor": {
            "status": "CONFIRMED",
            "label": "Latest completed candle",
            "direction": latest_direction,
            "close_level": latest_close,
        },
        "forming_at_start": _forming_candle_at_start(session),
        "forecast_bias": {
            "status": "DIRECTIONAL" if primary_direction in {"BUY", "SELL"} else "NEUTRAL",
            "label": "Forecast bias at start",
            "summary": "This is the saved forecast direction, not permission to trade.",
            "direction": primary_direction,
        },
        "entry_thesis": _entry_thesis(session),
        "trade_permission": trade_permission,
        "entry_location": _entry_location_at_start(session, transform),
        "transform_contract": transform,
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
    path_comparison = _freeze_two_path_comparison(session)
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
        _mapping(path_comparison.get("trade_permission")).get("status"),
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
        "pair": str(identity["pair"]),
        "timeframe": str(identity["timeframe"]),
        "market_identity_confirmed": bool(
            identity["market_identity_confirmed"]
        ),
        "timeframe_identity_confirmed": bool(
            identity["timeframe_identity_confirmed"]
        ),
        "closed_candle_key": str(identity["closed_candle_key"]),
        "closed_candle_sequence": int(identity["closed_candle_sequence"]),
        "closed_candle_identity_state": _safe_mapping(
            identity.get("closed_candle_identity_state")
        ),
        "starting_candle": _actual_closed_candle(session, identity),
    }
    positioning_candidate = _order_positioning_candidate_v3(
        session,
        frame_id=int(readiness["frame_id"]),
        identity=identity,
    )
    positioning_plan = freeze_order_positioning_plan_v3(
        positioning_candidate,
        normalized_episode_id,
        str(identity["closed_candle_key"]),
        int(readiness["frame_id"]),
        str(now_iso),
    )
    if positioning_plan.get("status") == "BLOCKED":
        positioning_plan["candidate_blockers"] = list(
            cast(Sequence[Any], positioning_candidate.get("blockers", []))
        )[:16]
        positioning_plan["rejected_source_count"] = len(
            _rows(positioning_candidate.get("rejected_sources"), limit=128)
        )
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
        "positioning_plan": _safe_mapping(positioning_plan),
        "baseline_forecasts": _baseline_forecasts(session),
        "candidate_revision": {},
        "events": [],
        "processed_closed_candle_keys": [str(identity["closed_candle_key"])],
        "last_processed_closed_candle_key": str(identity["closed_candle_key"]),
        "last_processed_closed_candle_sequence": int(identity["closed_candle_sequence"]),
        "observation_state": {
            "schema_version": TRACKING_EPISODE_OBSERVATION_SCHEMA_VERSION,
            "status": "LIVE",
            "reason": "BASELINE_LOCKED_WAITING_FOR_NEXT_CLOSE",
            "last_trusted_frame_id": int(readiness["frame_id"]),
            "last_trusted_at": _text(
                session.get("last_capture_at"),
                now_iso,
            ),
            "current_frame_id": int(readiness["frame_id"]),
            "current_at": _text(session.get("last_capture_at"), now_iso),
            "confirmed_event_count": 0,
            "recoverable_event_count": 0,
            "unresolved_gap": False,
            "coverage_status": "STABLE",
        },
        "path_comparison": path_comparison,
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
    observation_state = _normalize_observation_state(
        normalized.get("observation_state")
    )
    observation_state["status"] = "STOPPED"
    observation_state["reason"] = "EPISODE_STOPPED"
    normalized["observation_state"] = observation_state
    normalized["permission"] = _episode_permission(
        active=False,
        reason="Tracking episode stopped; capture and models remain warm.",
    )
    return normalized


def reset_tracking_episode_v1(
    current: Any,
    *,
    session_id: str,
) -> dict[str, Any]:
    """Clear only the current terminal episode pointer while preserving its archive."""

    source = _mapping(current)
    if (
        source.get("schema_version") == TRACKING_EPISODE_SCHEMA_VERSION
        and str(source.get("state", "") or "").strip().upper() == "IDLE"
        and not _text(source.get("episode_id"))
    ):
        return default_tracking_episode_v1(session_id=session_id)
    normalized = normalize_tracking_episode_v1(current, session_id=session_id)
    state = str(normalized["state"])
    if state in _ACTIVE_STATES:
        raise TrackingEpisodeStateError(
            state,
            "Stop and save the active tracking episode before resetting it.",
        )
    if state == "IDLE":
        return normalized
    if state not in _TERMINAL_STATES:
        raise TrackingEpisodeStateError(
            state,
            "This tracking episode cannot be reset from its current state.",
        )
    return default_tracking_episode_v1(session_id=session_id)


def _actual_closed_candle(
    session: Mapping[str, Any],
    identity: Mapping[str, Any],
    event_observation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    scene = _scene_forecast(session)
    identity_state = _mapping(scene.get("closed_candle_identity_state"))
    latest_closed = _mapping(identity_state.get("latest_closed"))
    tracking = _mapping(session.get("tracking_summary"))
    latest = _mapping(session.get("latest_signal"))
    tracked = _rows(tracking.get("tracked_candles"), limit=128)
    closed_rows = [row for row in tracked if row.get("is_closed") is True]
    latest_track_id = _text(latest_closed.get("track_id"))
    identity_match = next(
        (
            row
            for row in reversed(tracked)
            if latest_track_id and _text(row.get("track_id")) == latest_track_id
        ),
        None,
    )
    explicit_observation = _mapping(event_observation)
    candle = (
        explicit_observation
        or identity_match
        or (closed_rows[-1] if closed_rows else latest_closed)
    )
    side = _direction(
        candle.get("side"),
        candle.get("direction"),
        latest_closed.get("side"),
        latest_closed.get("direction"),
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
    chart_geometry = _normalized_observation_geometry(candle, scene)
    return _safe_mapping(
        {
            "closed_candle_key": identity.get("closed_candle_key"),
            "closed_candle_sequence": identity.get("closed_candle_sequence"),
            "side": side,
            "close_norm": close_norm,
            "coordinate_space": close_space,
            "track_id": _text(candle.get("track_id"), latest_closed.get("track_id")),
            **chart_geometry,
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


def _path_fit_for_event(
    path: Mapping[str, Any],
    actual: Mapping[str, Any],
    *,
    step: int,
    unknown: bool,
    close_tolerance: float,
    body_tolerance: float,
) -> dict[str, Any]:
    path_steps = _rows(path.get("steps"), limit=TRACKING_EPISODE_HORIZON)
    expected = path_steps[step - 1] if len(path_steps) >= step else {}
    expected_close = _number(expected.get("close_level"))
    expected_open = _number(expected.get("open_level"))
    actual_close = _number(actual.get("chart_close_norm"))
    actual_open = _number(actual.get("chart_open_norm"))
    if unknown or expected_close is None or actual_close is None:
        return {
            "status": "UNKNOWN",
            "fit": None,
            "error": None,
            "direction_agreement": None,
        }
    close_error = abs(expected_close - actual_close)
    if expected_open is not None and actual_open is not None:
        expected_body = expected_close - expected_open
        actual_body = actual_close - actual_open
        body_error = abs(expected_body - actual_body)
        error = 0.7 * close_error + 0.3 * body_error
        fit_denominator = 4.0 * (
            0.7 * close_tolerance + 0.3 * body_tolerance
        )
    else:
        error = close_error
        fit_denominator = 4.0 * close_tolerance
    expected_side = _direction(expected.get("direction"), path.get("direction"))
    actual_side = _direction(actual.get("side"))
    return {
        "status": "MEASURED",
        "fit": round(
            max(0.0, min(1.0, 1.0 - error / max(fit_denominator, 1e-6))),
            6,
        ),
        "error": round(error, 6),
        "direction_agreement": (
            expected_side == actual_side
            if expected_side in {"BUY", "SELL"} and actual_side in {"BUY", "SELL"}
            else None
        ),
    }


def _event_path_evidence(
    episode: Mapping[str, Any],
    session: Mapping[str, Any],
    actual: Mapping[str, Any],
    *,
    step: int,
    unknown: bool,
) -> tuple[dict[str, Any], str]:
    comparison = _mapping(episode.get("path_comparison"))
    paths = _rows(comparison.get("paths"), limit=2)
    if len(paths) != 2:
        return {}, ""
    transform = _mapping(comparison.get("transform_contract"))
    continuity_proven = _transform_continuity_proven(transform, session)
    close_tolerance = _number(transform.get("close_tolerance")) or 0.01
    body_tolerance = _number(transform.get("body_tolerance")) or close_tolerance
    evidence_unknown = bool(unknown or not continuity_proven)
    evidence = {
        _text(path.get("id")): _path_fit_for_event(
            path,
            actual,
            step=step,
            unknown=evidence_unknown,
            close_tolerance=close_tolerance,
            body_tolerance=body_tolerance,
        )
        for path in paths
        if _text(path.get("id")) in {"PATH_A", "PATH_B"}
    }
    if set(evidence) != {"PATH_A", "PATH_B"}:
        return {}, ""
    left_error = _number(_mapping(evidence["PATH_A"]).get("error"))
    right_error = _number(_mapping(evidence["PATH_B"]).get("error"))
    if left_error is None or right_error is None:
        return evidence, ""
    margin = abs(left_error - right_error)
    threshold = max(
        _PATH_EVENT_MIN_MARGIN,
        close_tolerance * 0.5,
        max(left_error, right_error) * _PATH_RELATIVE_MARGIN,
    )
    if min(left_error, right_error) > close_tolerance * 4.0:
        return evidence, ""
    favored = (
        "PATH_A"
        if margin >= threshold and left_error < right_error
        else "PATH_B"
        if margin >= threshold and right_error < left_error
        else ""
    )
    return evidence, favored


def _entry_location_progress(
    comparison: Mapping[str, Any],
    session: Mapping[str, Any],
    actual: Mapping[str, Any],
    observed_close_level: float | None,
    previous_distance: float | None,
) -> dict[str, Any]:
    location = _mapping(comparison.get("entry_location"))
    top_level = _number(location.get("top_level"))
    bottom_level = _number(location.get("bottom_level"))
    if (
        location.get("status") != "TRACKING"
        or observed_close_level is None
        or top_level is None
        or bottom_level is None
        or bottom_level <= top_level
    ):
        return {"status": "UNKNOWN", "distance": None}
    latest = _mapping(session.get("latest_signal"))
    tracking = _mapping(session.get("tracking_summary"))
    current_thesis = (
        _mapping(session.get("signal_thesis_v3"))
        or _mapping(latest.get("signal_thesis_v3"))
        or _mapping(tracking.get("signal_thesis_v3"))
    )
    current_zone = _mapping(current_thesis.get("entry_zone"))
    explicit_match = bool(
        _text(location.get("source_thesis_id"))
        and _text(location.get("source_thesis_id"))
        == _text(current_thesis.get("thesis_id"))
        and _text(location.get("zone_key"))
        and _text(location.get("zone_key")) == _text(current_zone.get("key"))
        and _direction(location.get("direction"))
        == _direction(current_thesis.get("side"), current_thesis.get("effective_side"))
    )
    if explicit_match and current_thesis.get("invalidated") is True:
        return {"status": "INVALIDATED", "distance": None}
    if explicit_match and (
        current_thesis.get("entry_reached") is True
        or current_thesis.get("entry_confirmed") is True
    ):
        return {"status": "CONFIRMED", "distance": 0.0}
    candle_top = _number(actual.get("chart_top_norm"))
    candle_bottom = _number(actual.get("chart_bottom_norm"))
    intersects = bool(
        candle_top is not None
        and candle_bottom is not None
        and max(candle_top, top_level) <= min(candle_bottom, bottom_level)
    )
    if intersects or top_level <= observed_close_level <= bottom_level:
        return {"status": "INSIDE", "distance": 0.0}
    distance = min(
        abs(observed_close_level - top_level),
        abs(observed_close_level - bottom_level),
    )
    tolerance = _number(
        _mapping(comparison.get("transform_contract")).get("close_tolerance")
    ) or 0.005
    if previous_distance is not None and distance < previous_distance - tolerance * 0.25:
        status = "APPROACHING"
    elif previous_distance is not None and distance > previous_distance + tolerance * 0.25:
        status = "MOVED_AWAY"
    else:
        status = "OUTSIDE"
    return {"status": status, "distance": round(distance, 6)}


def _updated_path_comparison(
    comparison_value: Any,
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    comparison = _safe_mapping(comparison_value)
    if comparison.get("schema_version") != TRACKING_PATH_COMPARISON_SCHEMA_VERSION:
        return _default_path_comparison()
    common_errors: dict[str, list[float]] = {"PATH_A": [], "PATH_B": []}
    for event in events:
        evidence = _mapping(event.get("path_fit_by_id"))
        left_error = _number(_mapping(evidence.get("PATH_A")).get("error"))
        right_error = _number(_mapping(evidence.get("PATH_B")).get("error"))
        if left_error is None or right_error is None:
            continue
        common_errors["PATH_A"].append(left_error)
        common_errors["PATH_B"].append(right_error)
    count = len(common_errors["PATH_A"])
    comparison["favored_path_id"] = ""
    if events and _text(events[-1].get("geometry_status")).upper() != "AVAILABLE":
        comparison["verdict"] = "GEOMETRY_UNAVAILABLE"
        comparison["verdict_summary"] = (
            "The latest completed candle cannot be compared until normalized chart geometry is proven again."
        )
        return comparison
    if count < _PATH_FAVOR_MIN_EVENTS:
        observed_count = sum(
            1
            for event in events
            if event.get("result_available") is not False
        )
        if observed_count >= _PATH_FAVOR_MIN_EVENTS:
            comparison["verdict"] = "GEOMETRY_UNAVAILABLE"
            comparison["verdict_summary"] = (
                "Confirmed candles were recorded, but comparable normalized geometry was unavailable."
            )
        else:
            comparison["verdict"] = "WAITING"
            comparison["verdict_summary"] = (
                f"Waiting for {(_PATH_FAVOR_MIN_EVENTS - count)} more confirmed candle"
                f"{'s' if _PATH_FAVOR_MIN_EVENTS - count != 1 else ''} before favoring either path."
            )
        return comparison
    means = {
        path_id: sum(errors) / len(errors)
        for path_id, errors in common_errors.items()
    }
    best_id = min(means, key=means.__getitem__)
    other_id = "PATH_B" if best_id == "PATH_A" else "PATH_A"
    transform = _mapping(comparison.get("transform_contract"))
    close_tolerance = _number(transform.get("close_tolerance")) or 0.01
    if means[best_id] > close_tolerance * 4.0:
        comparison["verdict"] = "NEITHER_PATH_FITS"
        comparison["verdict_summary"] = (
            f"After {count} confirmed candles, the market is not following either saved path closely enough."
        )
        return comparison
    margin = means[other_id] - means[best_id]
    threshold = max(
        _PATH_EPISODE_MIN_MARGIN,
        close_tolerance * 0.75,
        means[other_id] * _PATH_RELATIVE_MARGIN,
    )
    if margin < threshold:
        comparison["verdict"] = "TOO_CLOSE"
        comparison["verdict_summary"] = (
            f"After {count} confirmed candles, both saved paths remain too close to call."
        )
        return comparison
    comparison["verdict"] = best_id
    comparison["favored_path_id"] = best_id
    label = "Main forecast" if best_id == "PATH_A" else "Alternative forecast"
    comparison["verdict_summary"] = (
        f"After {count} confirmed candles, the market is following the {label.lower()} more closely."
    )
    return comparison


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


def _episode_observation_state(
    episode: Mapping[str, Any],
    session: Mapping[str, Any],
    identity: Mapping[str, Any],
    *,
    now_iso: str,
    newly_confirmed: int = 0,
) -> dict[str, Any]:
    """Build the bounded operator-safe continuity state for this capture."""

    previous = _normalize_observation_state(episode.get("observation_state"))
    frame_id = max(
        0,
        _integer(session.get("model_vote_frame_id", session.get("frame_index", 0))),
    )
    captured_at = _text(session.get("last_capture_at"), now_iso)
    match_scores = _mapping(identity.get("match_scores"))
    reacquisition = _mapping(identity.get("reacquisition"))
    transition_reason = _text(identity.get("transition_reason")).upper()
    event_batch = _rows(
        identity.get("confirmed_event_batch"),
        limit=TRACKING_EPISODE_HORIZON,
    )
    coverage_degraded = match_scores.get("coverage_degradation_observed") is True
    last_sequence = max(
        0,
        _integer(episode.get("last_processed_closed_candle_sequence")),
    )
    current_sequence = max(0, _integer(identity.get("closed_candle_sequence")))
    sequence_gap_without_rows = bool(
        current_sequence > last_sequence + 1 and not event_batch
    )
    unresolved_gap = bool(
        sequence_gap_without_rows
        or (
            coverage_degraded
            and transition_reason
            in {
                "AMBIGUOUS_SCREENSHOT_REUSES_EVENT",
                "DETECTOR_COVERAGE_REBASE",
            }
        )
        or (
            reacquisition.get("status") == "NOT_CONFIRMED"
            and transition_reason == "AMBIGUOUS_SCREENSHOT_REUSES_EVENT"
        )
    )
    if newly_confirmed > 0:
        status = "LIVE"
        reason = (
            "MISSED_CANDLES_REACQUIRED"
            if newly_confirmed > 1
            else "CLOSED_CANDLE_CONFIRMED"
        )
        last_trusted_frame_id = frame_id
        last_trusted_at = captured_at
    elif unresolved_gap:
        status = "REACQUIRING"
        reason = "OBSERVATION_GAP_REACQUIRING"
        last_trusted_frame_id = int(previous["last_trusted_frame_id"])
        last_trusted_at = str(previous["last_trusted_at"])
    else:
        status = "WAITING_FOR_CLOSE"
        reason = "FORMING_CANDLE_IN_PROGRESS"
        last_trusted_frame_id = frame_id
        last_trusted_at = captured_at
    return _normalize_observation_state(
        {
            "status": status,
            "reason": reason,
            "last_trusted_frame_id": last_trusted_frame_id,
            "last_trusted_at": last_trusted_at,
            "current_frame_id": frame_id,
            "current_at": captured_at,
            "confirmed_event_count": min(
                TRACKING_EPISODE_HORIZON,
                _integer(episode.get("event_cursor")),
            ),
            "recoverable_event_count": len(event_batch),
            "unresolved_gap": unresolved_gap,
            "coverage_status": "DEGRADED" if coverage_degraded else "STABLE",
        }
    )


def _confirmed_episode_events(
    episode: Mapping[str, Any],
    identity: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return a contiguous, identity-proven batch or an explicit unknown gap.

    Historical observations are scored only when the resolver supplied the
    corresponding candle row.  If an authoritative monotonic sequence jumps
    without those rows, the absent steps become UNKNOWN_GAP records and only
    the currently confirmed candle is eligible for scoring.
    """

    last_sequence = max(
        0,
        _integer(episode.get("last_processed_closed_candle_sequence")),
    )
    current_sequence = max(0, _integer(identity.get("closed_candle_sequence")))
    current_key = _text(identity.get("closed_candle_key"))
    if not current_key or current_sequence <= last_sequence:
        return []

    raw_batch = _rows(
        identity.get("confirmed_event_batch"),
        limit=24,
    )
    if raw_batch:
        normalized_batch: list[dict[str, Any]] = []
        for row in raw_batch:
            key = _text(row.get("closed_candle_key"))
            sequence = max(0, _integer(row.get("closed_candle_sequence")))
            if not key or sequence <= 0:
                return []
            normalized_batch.append(
                {
                    "closed_candle_key": key,
                    "closed_candle_sequence": sequence,
                    "observation": _mapping(row.get("observation")),
                    "confirmation_reason": _text(
                        row.get("confirmation_reason"),
                        identity.get("transition_reason"),
                    ),
                    "reacquired": row.get("reacquired") is True,
                    "unknown_gap": False,
                }
            )
        if (
            normalized_batch[-1]["closed_candle_key"] != current_key
            or normalized_batch[-1]["closed_candle_sequence"] != current_sequence
        ):
            return []
        pending = [
            row
            for row in normalized_batch
            if int(row["closed_candle_sequence"]) > last_sequence
        ]
        expected = list(
            range(last_sequence + 1, last_sequence + 1 + len(pending))
        )
        if [int(row["closed_candle_sequence"]) for row in pending] != expected:
            return []
        if len({str(row["closed_candle_key"]) for row in pending}) != len(pending):
            return []
        return pending

    delta = current_sequence - last_sequence
    if delta == 1:
        return [
            {
                "closed_candle_key": current_key,
                "closed_candle_sequence": current_sequence,
                "observation": {},
                "confirmation_reason": _text(identity.get("transition_reason")),
                "reacquired": False,
                "unknown_gap": False,
            }
        ]

    # A monotonic source sequence proves how many closes were skipped, but it
    # does not prove their OHLC or direction.  Persist explicit unknown rows;
    # never reuse the current candle as their actual result.
    episode_id = _text(episode.get("episode_id"), "episode")
    output: list[dict[str, Any]] = []
    for sequence in range(last_sequence + 1, current_sequence):
        seed = (
            f"{episode_id}|UNKNOWN_CLOSED_CANDLE_GAP|{sequence}|{current_key}"
        )
        output.append(
            {
                "closed_candle_key": f"gap-{sha256(seed.encode('utf-8')).hexdigest()[:20]}",
                "closed_candle_sequence": sequence,
                "observation": {},
                "confirmation_reason": "AUTHORITATIVE_SEQUENCE_GAP",
                "reacquired": False,
                "unknown_gap": True,
            }
        )
    output.append(
        {
            "closed_candle_key": current_key,
            "closed_candle_sequence": current_sequence,
            "observation": {},
            "confirmation_reason": _text(identity.get("transition_reason")),
            "reacquired": False,
            "unknown_gap": False,
        }
    )
    return output


def advance_tracking_episode_v1(
    current: Any,
    session: Mapping[str, Any],
    *,
    now_iso: str,
) -> dict[str, Any]:
    """Consume each identity-proven close once while keeping the baseline frozen."""

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
        observation_state = _episode_observation_state(
            normalized,
            session,
            identity,
            now_iso=now_iso,
        )
        observation_state["status"] = "STOPPED"
        observation_state["reason"] = "MARKET_CONTEXT_CHANGED"
        normalized["observation_state"] = observation_state
        normalized["permission"] = _episode_permission(
            active=False,
            reason="Pair or timeframe changed; start a new tracking episode after identity stabilizes.",
        )
        return normalized
    if (
        not _text(identity.get("closed_candle_key"))
        or not bool(identity["market_identity_confirmed"])
        or not bool(identity["timeframe_identity_confirmed"])
    ):
        normalized["observation_state"] = _episode_observation_state(
            normalized,
            session,
            identity,
            now_iso=now_iso,
        )
        return normalized

    confirmed_events = _confirmed_episode_events(normalized, identity)
    if not confirmed_events:
        normalized["observation_state"] = _episode_observation_state(
            normalized,
            session,
            identity,
            now_iso=now_iso,
        )
        return normalized

    processed = list(cast(Sequence[str], normalized["processed_closed_candle_keys"]))
    if any(
        _text(row.get("closed_candle_key")) in processed
        for row in confirmed_events
    ):
        normalized["observation_state"] = _episode_observation_state(
            normalized,
            session,
            identity,
            now_iso=now_iso,
        )
        return normalized

    anchor = _mapping(normalized.get("anchor"))
    frame_id = max(
        0,
        _integer(session.get("model_vote_frame_id", session.get("frame_index", 0))),
    )
    events = cast(list[dict[str, Any]], normalized["events"])
    appended = 0
    for confirmed in confirmed_events:
        step = len(events) + 1
        if step > TRACKING_EPISODE_HORIZON:
            break
        closed_key = _text(confirmed.get("closed_candle_key"))
        closed_sequence = max(
            0,
            _integer(confirmed.get("closed_candle_sequence")),
        )
        unknown_gap = confirmed.get("unknown_gap") is True
        predicted = _predicted_block(normalized, step)
        event_identity = {
            **identity,
            "closed_candle_key": closed_key,
            "closed_candle_sequence": closed_sequence,
        }
        actual = (
            {
                "status": "UNKNOWN",
                "reason": "CANDLE_NOT_AVAILABLE_DURING_OBSERVATION_GAP",
            }
            if unknown_gap
            else _actual_closed_candle(
                session,
                event_identity,
                _mapping(confirmed.get("observation")),
            )
        )
        path_fit_by_id, favored_path_id = _event_path_evidence(
            normalized,
            session,
            actual,
            step=step,
            unknown=unknown_gap,
        )
        path_comparison = _mapping(normalized.get("path_comparison"))
        transform_proven = bool(
            not unknown_gap
            and _transform_continuity_proven(
                path_comparison.get("transform_contract"),
                session,
            )
        )
        observed_close_level = (
            _number(actual.get("chart_close_norm"))
            if transform_proven
            else None
        )
        previous_entry_progress = _mapping(
            events[-1].get("entry_location_progress")
        ) if events else {}
        entry_location_progress = _entry_location_progress(
            path_comparison,
            session,
            actual,
            observed_close_level,
            _number(previous_entry_progress.get("distance")),
        )
        predicted_side = _direction(predicted.get("side"))
        actual_side = _direction(actual.get("side"))
        predicted_close = _number(predicted.get("expected_close_norm"))
        actual_close = _number(actual.get("close_norm"))
        comparable = bool(
            not unknown_gap
            and predicted_close is not None
            and actual_close is not None
            and _text(predicted.get("coordinate_space"))
            == _text(actual.get("coordinate_space"))
        )
        event = {
            "schema_version": TRACKING_EPISODE_EVENT_SCHEMA_VERSION,
            "episode_id": normalized["episode_id"],
            "event_id": f"{normalized['episode_id']}:E{step}",
            "step": step,
            "label": f"E{step}",
            "observed_at": str(now_iso),
            "closed_candle_key": closed_key,
            "closed_candle_sequence": closed_sequence,
            "observation_kind": (
                "UNKNOWN_GAP"
                if unknown_gap
                else "REACQUIRED_HISTORY"
                if confirmed.get("reacquired") is True
                else "LIVE_CLOSE"
            ),
            "result_available": not unknown_gap,
            "predicted_block": predicted,
            "actual_block": actual,
            "path_fit_by_id": path_fit_by_id,
            "favored_path_id": favored_path_id,
            "observed_close_level": observed_close_level,
            "entry_location_progress": entry_location_progress,
            "geometry_status": "AVAILABLE" if observed_close_level is not None else "UNAVAILABLE",
            "direction_agreement": (
                predicted_side == actual_side
                if not unknown_gap
                and predicted_side in {"BUY", "SELL"}
                and actual_side in {"BUY", "SELL"}
                else None
            ),
            "displacement_error": (
                abs(cast(float, predicted_close) - cast(float, actual_close))
                if comparable
                else None
            ),
            "continuity_evidence": {
                "confirmation_reason": _text(
                    confirmed.get("confirmation_reason"),
                    identity.get("transition_reason"),
                ),
                "batch_size": len(confirmed_events),
                "reacquired": confirmed.get("reacquired") is True,
                "unknown_gap": unknown_gap,
            },
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
        positioning_plan = _mapping(normalized.get("positioning_plan"))
        if positioning_plan.get("frozen") is True:
            positioning_step = max(0, _integer(positioning_plan.get("step"))) + 1
            positioning_plan = advance_order_positioning_plan_v3(
                positioning_plan,
                _order_positioning_actual_v3(
                    positioning_plan,
                    session,
                    actual,
                    frame_id=frame_id,
                    closed_candle_key=closed_key,
                    transform_proven=transform_proven,
                ),
                positioning_step,
            )
            normalized["positioning_plan"] = _safe_mapping(positioning_plan)
            status_counts: dict[str, int] = {}
            for zone in _rows(positioning_plan.get("zones"), limit=24):
                zone_status = _text(zone.get("status"), "UNKNOWN").upper()
                status_counts[zone_status] = status_counts.get(zone_status, 0) + 1
            event["order_positioning_progress"] = {
                "advance_status": _text(
                    positioning_plan.get("advance_status"),
                    "REJECTED",
                ),
                "step": max(0, _integer(positioning_plan.get("step"))),
                "status_counts": status_counts,
            }
        events.append(_safe_mapping(event))
        processed.append(closed_key)
        normalized["last_processed_closed_candle_key"] = closed_key
        normalized["last_processed_closed_candle_sequence"] = closed_sequence
        appended += 1

    if appended <= 0:
        normalized["observation_state"] = _episode_observation_state(
            normalized,
            session,
            identity,
            now_iso=now_iso,
        )
        return normalized
    normalized["events"] = events[:TRACKING_EPISODE_HORIZON]
    normalized["event_cursor"] = len(normalized["events"])
    normalized["path_comparison"] = _updated_path_comparison(
        normalized.get("path_comparison"),
        cast(Sequence[Mapping[str, Any]], normalized["events"]),
    )
    updated_comparison = _mapping(normalized.get("path_comparison"))
    updated_entry_location = _mapping(updated_comparison.get("entry_location"))
    latest_event = _mapping(cast(Sequence[Any], normalized["events"])[-1])
    updated_entry_location["progress"] = _safe_mapping(
        latest_event.get("entry_location_progress")
    ) or {"status": "UNKNOWN"}
    updated_comparison["entry_location"] = updated_entry_location
    normalized["path_comparison"] = updated_comparison
    normalized["processed_closed_candle_keys"] = list(dict.fromkeys(processed))[-(TRACKING_EPISODE_HORIZON + 1) :]
    normalized["candidate_revision"] = _candidate_revision(session, identity)
    normalized["revision"] = int(normalized["revision"]) + 1
    normalized["updated_at"] = str(now_iso)
    normalized["observation_state"] = _episode_observation_state(
        normalized,
        session,
        identity,
        now_iso=now_iso,
        newly_confirmed=appended,
    )
    if int(normalized["event_cursor"]) >= TRACKING_EPISODE_HORIZON:
        normalized["state"] = "COMPLETED"
        normalized["completed_at"] = str(now_iso)
        normalized["terminal_reason"] = "EVENT_HORIZON_COMPLETE"
        normalized["permission"] = _episode_permission(
            active=False,
            reason="All 12 closed-candle events were observed; start a new episode for another baseline.",
        )
        observation_state = _normalize_observation_state(
            normalized.get("observation_state")
        )
        observation_state["status"] = "STOPPED"
        observation_state["reason"] = "EPISODE_HORIZON_COMPLETE"
        normalized["observation_state"] = observation_state
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
    "ORDER_REFERENCE_MAP_SCHEMA_VERSION",
    "TRACKING_EPISODE_EVENT_SCHEMA_VERSION",
    "TRACKING_EPISODE_HORIZON",
    "TRACKING_EPISODE_HISTORY_LIMIT",
    "TRACKING_EPISODE_HISTORY_SCHEMA_VERSION",
    "TRACKING_EPISODE_SCHEMA_VERSION",
    "TrackingEpisodeReadinessError",
    "TrackingEpisodeStateError",
    "TrackingEpisodeState",
    "advance_tracking_episode_v1",
    "build_tracking_order_reference_map_v3",
    "build_tracking_order_positioning_candidate_v3",
    "default_tracking_episode_v1",
    "normalize_tracking_episode_v1",
    "order_positioning_source_rows_v3",
    "reset_tracking_episode_v1",
    "start_tracking_episode_v1",
    "stop_tracking_episode_v1",
    "tracking_episode_is_active_v1",
    "tracking_episode_history_entry_v1",
    "tracking_episode_readiness_v1",
    "tracking_reprojection_anchors_v3",
    "update_tracking_episode_history_v1",
]
