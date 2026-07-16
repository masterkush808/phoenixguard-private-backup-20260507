from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any, cast
from urllib.parse import quote

from phoenixguard.decision.entry_window_policy_v3 import entry_location_guidance_v3


OPERATOR_WORKSPACE_SCHEMA_VERSION = "PG_OPERATOR_WORKSPACE_V1"

_DIRECTIONAL_SIDES = frozenset({"BUY", "SELL"})
_FORECAST_BELIEF_STATES = frozenset(
    {"RESET", "REACQUIRING", "STABLE", "REVERSAL_PENDING"}
)
_TOP_LEVEL_KEYS = (
    "schema_version",
    "session_id",
    "revision",
    "market",
    "tracking",
    "freshness",
    "current_move",
    "forecast",
    "permission",
    "pressure_event",
    "surface",
    "overlays",
    "history",
)

_OVERLAY_PRESENTATION: dict[str, tuple[str, str, str]] = {
    "CHART_BOUNDS": ("chart", "Chart area", "structure"),
    "CURRENT_CANDLE": ("price", "Current price", "movement"),
    "IMPULSE_BOX": ("movement", "Strong move", "movement"),
    "PULLBACK_BOX": ("pullback", "Pullback", "movement"),
    "CONTINUATION_BOX": ("continuation", "Continuation", "movement"),
    "SUPPORT_TRENDLINE": ("trend", "Support trend", "structure"),
    "RESISTANCE_TRENDLINE": ("trend", "Resistance trend", "structure"),
    "INNER_TRENDLINE": ("trend", "Local trend", "structure"),
    "SUPPLY_ZONE": ("zone", "Supply area", "zones"),
    "DEMAND_ZONE": ("zone", "Demand area", "zones"),
    "OPPOSING_FORCE": ("barrier", "Opposing area", "zones"),
    "SNIPER_ENTRY_BOX": ("entry", "Entry area", "plan"),
    "RETEST_BOX": ("entry", "Retest area", "plan"),
    "TRIGGER_BOX": ("entry", "Entry trigger", "plan"),
    "TRIGGER_ZONE": ("entry", "Entry trigger", "plan"),
    "TARGET_ZONE_BOX": ("target", "Target area", "plan"),
    "INVALIDATION_BOX": ("risk", "Risk limit", "plan"),
    "INVALIDATION_ZONE": ("risk", "Risk limit", "plan"),
    "PROGRESSION_PATH": ("path", "Observed path", "history"),
    "PREDICTION_PATH": ("path", "Possible path", "outlook"),
    "ANGLE_VECTOR": ("path", "Possible path", "outlook"),
    "REPLAY_ENTRY": ("entry", "Past entry", "history"),
    "REPLAY_EXIT": ("exit", "Past exit", "history"),
    "MODEL_COUNCIL_MARKER": ("plan", "Council read", "plan"),
    "REGIME_MARKER": ("context", "Market phase", "structure"),
    "MARKET_PLAY_MARKER": ("setup", "Active setup", "plan"),
    "PRICE_LOCATION_MARKER": ("context", "Price location", "structure"),
    "TWO_CANDLE_STUDY": ("outlook", "Two-candle study", "outlook"),
    "LSTM_STUDY": ("outlook", "LSTM study", "outlook"),
    "SCENE_FORECAST_STUDY": ("outlook", "Scene forecaster", "outlook"),
    "ORDER_BLOCK": ("zone", "SMC order block", "zones"),
    "FAIR_VALUE_GAP": ("zone", "SMC fair value gap", "zones"),
    "LIQUIDITY_POOL": ("zone", "SMC liquidity pool", "zones"),
    "LIQUIDITY_SWEEP": ("movement", "SMC liquidity sweep", "movement"),
    "MARKET_STRUCTURE_SHIFT": ("movement", "SMC structure shift", "structure"),
    "ZONE": ("zone", "Price area", "zones"),
    "TREND": ("trend", "Trend", "structure"),
    "ENTRY": ("entry", "Entry area", "plan"),
    "TARGET": ("target", "Target area", "plan"),
    "RISK": ("risk", "Risk limit", "plan"),
    "MOVEMENT": ("movement", "Price movement", "movement"),
    "OUTLOOK": ("outlook", "Possible path", "outlook"),
}

_LAYER_GROUPS = {
    "chart_bounds": "structure",
    "major_swings": "structure",
    "local_swings": "structure",
    "trendlines": "structure",
    "supply_demand": "zones",
    "recent_candles": "movement",
    "trigger_zones": "plan",
    "target_zones": "plan",
    "invalidation": "plan",
    "active_council_decision": "plan",
    "prediction_path": "outlook",
    "historical_replay": "history",
}

# This is the complete public overlay vocabulary.  It intentionally omits the
# broker-control and diagnostics planes even when an all-layer backend view is
# requested.  `family` is the stable operator toggle key; `layer` preserves the
# safe visual plane so consumers can reason about placement without receiving
# scene-graph, renderer, or detector internals.
_LAYER_FAMILIES = {
    "chart_bounds": "chart_bounds",
    "recent_candles": "current_candles",
    "major_swings": "major_swings",
    "local_swings": "local_swings",
    "supply_demand": "supply_demand",
    "trendlines": "trendlines",
    "trigger_zones": "triggers",
    "target_zones": "targets",
    "invalidation": "invalidation",
    "active_council_decision": "council",
    "prediction_path": "prediction",
    "historical_replay": "history",
    "smart_money": "smc",
}

_TYPE_FAMILIES = {
    "CHART_BOUNDS": "chart_bounds",
    "CURRENT_CANDLE": "current_candles",
    "IMPULSE_BOX": "major_swings",
    "PULLBACK_BOX": "local_swings",
    "CONTINUATION_BOX": "local_swings",
    "SUPPORT_TRENDLINE": "trendlines",
    "RESISTANCE_TRENDLINE": "trendlines",
    "INNER_TRENDLINE": "trendlines",
    "SUPPLY_ZONE": "supply_demand",
    "DEMAND_ZONE": "supply_demand",
    "OPPOSING_FORCE": "supply_demand",
    "SNIPER_ENTRY_BOX": "triggers",
    "RETEST_BOX": "triggers",
    "TRIGGER_BOX": "triggers",
    "TRIGGER_ZONE": "triggers",
    "TARGET_ZONE_BOX": "targets",
    "INVALIDATION_BOX": "invalidation",
    "INVALIDATION_ZONE": "invalidation",
    "TWO_CANDLE_STUDY": "two_candle",
    "LSTM_STUDY": "lstm",
    # Stable family alias retained until the dashboard toggle contract moves
    # from its historical `lstm` key to a scene-forecaster key.
    "SCENE_FORECAST_STUDY": "lstm",
    "MODEL_COUNCIL_MARKER": "council",
    "REGIME_MARKER": "council",
    "MARKET_PLAY_MARKER": "council",
    "PRICE_LOCATION_MARKER": "council",
    "PREDICTION_PATH": "prediction",
    "ANGLE_VECTOR": "prediction",
    "PROGRESSION_PATH": "history",
    "REPLAY_ENTRY": "history",
    "REPLAY_EXIT": "history",
    "ORDER_BLOCK": "smc",
    "FAIR_VALUE_GAP": "smc",
    "LIQUIDITY_POOL": "smc",
    "LIQUIDITY_SWEEP": "smc",
    "MARKET_STRUCTURE_SHIFT": "smc",
    "ZONE": "supply_demand",
    "TREND": "trendlines",
    "ENTRY": "triggers",
    "TARGET": "targets",
    "RISK": "invalidation",
    "MOVEMENT": "current_candles",
    "OUTLOOK": "prediction",
}

_FAMILY_FALLBACK_LAYERS = {
    "chart_bounds": "chart_bounds",
    "current_candles": "recent_candles",
    "major_swings": "major_swings",
    "local_swings": "local_swings",
    "supply_demand": "supply_demand",
    "trendlines": "trendlines",
    "triggers": "trigger_zones",
    "targets": "target_zones",
    "invalidation": "invalidation",
    "council": "active_council_decision",
    "two_candle": "active_council_decision",
    "lstm": "active_council_decision",
    "prediction": "prediction_path",
    "history": "historical_replay",
    "smc": "smart_money",
}

_GROUP_FALLBACK_FAMILIES = {
    "movement": "current_candles",
    "structure": "major_swings",
    "zones": "supply_demand",
    "plan": "council",
    "outlook": "prediction",
    "history": "history",
}

_HISTORICAL_TYPES = frozenset({"PROGRESSION_PATH", "REPLAY_ENTRY", "REPLAY_EXIT"})
_PAST_STATES = frozenset(
    {
        "ARCHIVED",
        "ENDED",
        "EXPIRED",
        "HISTORICAL",
        "INVALIDATED",
        "PAST",
        "REPLAY",
        "STALE",
        "SUPERSEDED",
    }
)


def _mapping(value: object) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else {}


def _rows(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    rows = cast(Sequence[object], value)
    return [cast(Mapping[str, Any], item) for item in rows if isinstance(item, Mapping)]


def _text(value: object, default: str = "", *, limit: int = 160) -> str:
    if value is None:
        return default
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit] or default


def _safe_public_text(value: object, default: str = "Unknown", *, limit: int = 80) -> str:
    text = _text(value, "", limit=limit)
    if not text:
        return default
    lowered = text.lower()
    if (
        re.match(r"^[a-z]:[\\/]", text, flags=re.IGNORECASE)
        or "\\" in text
        or text.startswith(("/", "~"))
        or "://" in text
        or lowered.startswith(("file:", "data:", "javascript:", "http:", "https:", "ws:", "wss:"))
        or text.count("/") > 1
    ):
        return default
    return text


def _stable_public_digest(value: object, *, prefix: str) -> str:
    """Return a deterministic revision token without publishing its inputs."""

    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(encoded).hexdigest()[:16]}"


def _safe_session_id(value: object) -> str:
    text = _text(value, "", limit=160)
    lowered = text.lower()
    if (
        not text
        or re.match(r"^[a-z]:[\\/]", text, flags=re.IGNORECASE)
        or "\\" in text
        or text.startswith("/")
        or lowered.startswith("file:")
    ):
        return ""
    return text


def _safe_identifier(value: object, default: str) -> str:
    text = _text(value, "", limit=120)
    lowered = text.lower()
    if (
        not text
        or re.match(r"^[a-z]:[\\/]", text, flags=re.IGNORECASE)
        or "/" in text
        or "\\" in text
        or text.startswith(("~", ".\\"))
        or re.match(r"^[a-z][a-z0-9+.-]*:", text, flags=re.IGNORECASE)
        or re.search(r"%(?:2f|3a|5c)", lowered)
    ):
        return default
    sanitized = re.sub(r"[^a-zA-Z0-9_.:-]+", "-", text).strip("-")
    return sanitized or default


def _number(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        # Runtime payloads can contain Decimal, NumPy scalars, or other values
        # implementing the numeric conversion protocol.  Keep that permissive
        # behavior while making the dynamic boundary explicit to Pyright.
        number = float(cast(Any, value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _epoch(*values: object) -> float | None:
    for value in values:
        number = _number(value)
        if number is not None and number > 0.0:
            return round(number, 6)
    return None


def _integer(*values: object) -> int:
    for value in values:
        number = _number(value)
        if number is not None and number >= 0.0:
            return int(number)
    return 0


def _frame_id(*values: object) -> int | str | None:
    for value in values:
        number = _number(value)
        if number is not None and number >= 0.0:
            return int(number)
        text = _text(value, "", limit=80)
        if text and re.fullmatch(r"[a-zA-Z0-9_.:-]+", text):
            return text
    return None


def _explicit_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    text = _text(value, "").upper()
    if text in {"1", "TRUE", "YES", "ON", "PASS", "FRESH"}:
        return True
    if text in {"0", "FALSE", "NO", "OFF", "FAIL", "FAILED", "STALE"}:
        return False
    return None


def _confidence(*values: object) -> float | None:
    for value in values:
        number = _number(value)
        if number is None:
            continue
        if number > 1.0 and number <= 100.0:
            number /= 100.0
        return round(max(0.0, min(1.0, number)), 4)
    return None


def _side(*values: object) -> str:
    aliases = {
        "BULL": "BUY",
        "BULLISH": "BUY",
        "LONG": "BUY",
        "UP": "BUY",
        "UPWARD": "BUY",
        "BEAR": "SELL",
        "BEARISH": "SELL",
        "DOWN": "SELL",
        "DOWNWARD": "SELL",
        "SHORT": "SELL",
    }
    for value in values:
        candidate = _text(value, "").upper().replace("-", "_").replace(" ", "_")
        candidate = aliases.get(candidate, candidate)
        if candidate in _DIRECTIONAL_SIDES:
            return candidate
    return "NEUTRAL"


def _belief_side(*values: object) -> str:
    for value in values:
        candidate = _text(value, "").upper()
        if candidate in {"BUY", "SELL", "HOLD"}:
            return candidate
    return "HOLD"


def _is_scene_forecast_source(*sources: Mapping[str, Any]) -> bool:
    for source in sources:
        tokens = " ".join(
            _text(source.get(key), "", limit=120).upper()
            for key in (
                "schema_version",
                "provider",
                "skill",
                "source_agent",
                "source_key",
                "role",
                "forecast_engine",
            )
        )
        if any(
            token in tokens
            for token in (
                "SCENE_FORECAST",
                "CHRONOS_SCENE",
                "SCENE FORECAST",
                "SCENE_FORECASTER",
            )
        ):
            return True
    return False


def _forecast_belief_contract(*sources: Mapping[str, Any]) -> dict[str, object]:
    source: Mapping[str, Any] = next(
        (
            item
            for item in sources
            if item
            and (
                any(
                    key in item
                    for key in (
                        "belief_state",
                        "committed_side",
                        "candidate_side",
                        "belief_revision",
                    )
                )
                or _mapping(item.get("forecast_belief"))
                or _mapping(item.get("belief_update"))
                or _mapping(item.get("belief"))
            )
        ),
        _mapping(None),
    )
    if not source:
        return {}
    belief = (
        _mapping(source.get("forecast_belief"))
        or _mapping(source.get("belief_update"))
        or _mapping(source.get("belief"))
    )
    status = _text(source.get("belief_state") or belief.get("status"), "RESET").upper()
    if status not in _FORECAST_BELIEF_STATES:
        status = "RESET"
    result: dict[str, object] = {
        "belief_state": status,
        "committed_side": _belief_side(
            source.get("committed_side"),
            belief.get("active_side"),
            belief.get("committed_side"),
        ),
        "candidate_side": _belief_side(
            source.get("candidate_side"),
            belief.get("candidate_side"),
            belief.get("pending_side"),
        ),
        "confirmation_events": _integer(
            source.get("confirmation_events"),
            belief.get("pending_count"),
        ),
        "required_events": _integer(
            source.get("required_events"),
            belief.get("required_count"),
        ),
        "belief_revision": _integer(
            source.get("belief_revision"),
            belief.get("revision"),
        ),
    }
    change_probability = _number(
        source.get("change_probability")
        if source.get("change_probability") is not None
        else belief.get("change_probability")
    )
    if change_probability is not None:
        result["change_probability"] = round(
            max(0.0, min(1.0, change_probability)),
            6,
        )
    forecast_id = _safe_identifier(source.get("forecast_id"), "")
    if forecast_id:
        result["forecast_id"] = forecast_id
    if any(key in source for key in ("forecast_revision", "revision")):
        result["forecast_revision"] = _integer(
            source.get("forecast_revision"),
            source.get("revision"),
        )
    closed_candle_key = _safe_identifier(
        source.get("closed_candle_key") or belief.get("closed_candle_key"),
        "",
    )
    if closed_candle_key:
        result["closed_candle_key"] = closed_candle_key
    if any(
        value is not None
        for value in (
            source.get("closed_candle_sequence"),
            belief.get("closed_candle_sequence"),
        )
    ):
        result["closed_candle_sequence"] = _integer(
            source.get("closed_candle_sequence"),
            belief.get("closed_candle_sequence"),
        )
    return result


def _scene_forecast_boundary_contract(
    *sources: Mapping[str, Any],
) -> dict[str, object]:
    source: Mapping[str, Any] = next(
        (item for item in sources if item and _is_scene_forecast_source(item)),
        _mapping(None),
    )
    if not source:
        return {}

    def item_count(value: object) -> int:
        return (
            len(cast(Sequence[object], value))
            if isinstance(value, Sequence)
            and not isinstance(value, (str, bytes, bytearray))
            else 0
        )

    result: dict[str, object] = {"forecast_engine": "SCENE_FORECASTER_V3"}
    provider = _safe_identifier(
        source.get("forecast_provider") or source.get("provider"),
        "",
    ).upper()
    provider_status = _safe_identifier(
        source.get("forecast_provider_status") or source.get("provider_status"),
        "",
    ).upper()
    if provider:
        result["forecast_provider"] = provider
    if provider_status:
        result["forecast_provider_status"] = provider_status
    for key in (
        "geometry_frame_match_verified",
        "geometry_reprojected_from_cache",
        "detector_coverage_rebase_applied",
        "cache_replaced_for_detector_coverage_rebase",
    ):
        if key in source:
            result[key] = _explicit_bool(source.get(key)) is True
    for key in (
        "forecast_computed_frame_id",
        "source_forecast_frame_id",
        "geometry_projected_frame_id",
    ):
        if source.get(key) not in (None, ""):
            result[key] = _integer(source.get(key))
    geometry_provenance = _mapping(source.get("geometry_projection_provenance"))
    if geometry_provenance:
        result["geometry_projection_provenance"] = {
            key: geometry_provenance[key]
            for key in (
                "status",
                "method",
                "source_forecast_frame_id",
                "source_geometry_frame_id",
                "projected_frame_id",
                "verified",
                "source_anchor",
                "target_anchor",
                "x_gain",
                "y_gain",
                "pointwise_clipping_applied",
            )
            if key in geometry_provenance
        }
    audit = _mapping(source.get("scene_feature_audit"))
    if audit:
        source_presence = _mapping(audit.get("source_presence"))
        causal_exclusions = _mapping(audit.get("causal_exclusions"))
        result["scene_feature_audit"] = {
            "consumed_field_count": _integer(
                audit.get("consumed_field_count"),
                item_count(audit.get("consumed_fields")),
            ),
            "missing_field_count": _integer(
                audit.get("missing_field_count"),
                item_count(audit.get("missing_fields")),
            ),
            "rejected_field_count": _integer(
                audit.get("rejected_field_count"),
                item_count(audit.get("rejected_fields")),
            ),
            "source_presence": {
                key: _explicit_bool(source_presence.get(key)) is True
                for key in (
                    "candles",
                    "projection",
                    "candle_statistics",
                    "behavior_payload",
                    "decision_kernel",
                    "smart_money_context",
                    "support_resistance_context",
                    "support_resistance_zones",
                    "trend_slopes",
                    "trend_directions",
                    "timeframe",
                    "pair",
                )
                if key in source_presence
            },
            "causal_exclusions": {
                "forming_candles": _integer(
                    causal_exclusions.get("forming_candles")
                ),
                "history_rows_outside_window": _integer(
                    causal_exclusions.get("history_rows_outside_window")
                ),
                "projected_geometry_is_feature": (
                    _explicit_bool(
                        causal_exclusions.get("projected_geometry_is_feature")
                    )
                    is True
                ),
                "future_outcome_fields_are_feature": (
                    _explicit_bool(
                        causal_exclusions.get("future_outcome_fields_are_feature")
                    )
                    is True
                ),
            },
        }
    return result


def _first_mapping(source: Mapping[str, Any], *paths: tuple[str, ...]) -> Mapping[str, Any]:
    for path in paths:
        current: object = source
        for key in path:
            current = _mapping(current).get(key)
        candidate = _mapping(current)
        if candidate:
            return candidate
    return {}


def _frame_matches(candidate: object, current: object) -> bool:
    candidate_frame = _frame_id(candidate)
    current_frame = _frame_id(current)
    if candidate_frame is None or current_frame is None:
        return True
    return str(candidate_frame) == str(current_frame)


def _event_state(event: Mapping[str, Any], display_frame: object) -> str:
    raw_state = _text(
        event.get("state") or event.get("status") or event.get("lifecycle") or event.get("lifecycle_state"),
        "",
    ).upper()
    if _explicit_bool(event.get("stale")) is True or raw_state in {"STALE", "OUTDATED"}:
        return "STALE"
    if (
        _explicit_bool(event.get("ended")) is True
        or _epoch(event.get("ended_at"), event.get("ended_epoch"), event.get("ended_epoch_sec")) is not None
        or raw_state in {"ENDED", "COMPLETE", "COMPLETED", "EXPIRED", "INVALIDATED", "SUPERSEDED"}
    ):
        return "ENDED"
    event_frame = event.get("frame_id") or event.get("display_frame_id")
    if event_frame not in (None, "") and not _frame_matches(event_frame, display_frame):
        return "STALE"
    if _explicit_bool(event.get("active")) is True or raw_state in {
        "ACTIVE",
        "CURRENT",
        "LIVE",
        "OPEN",
        "STARTED",
    }:
        return "ACTIVE"
    return "UNKNOWN"


def _movement_summary(direction: str, state: str, *, pressure: bool = False) -> str:
    movement_word = "upward" if direction == "BUY" else "downward" if direction == "SELL" else "directional"
    subject = "pressure" if pressure else "movement"
    if state == "ACTIVE" and direction in _DIRECTIONAL_SIDES:
        return f"{movement_word.capitalize()} {subject} is active now."
    if state == "ENDED" and direction in _DIRECTIONAL_SIDES:
        return f"The previous {movement_word} {subject} has ended."
    if state == "STALE" and direction in _DIRECTIONAL_SIDES:
        return f"An older {movement_word} {subject} is no longer treated as current."
    if state == "STALE":
        return f"The latest {subject} read is out of date."
    return f"Current {subject} is not confirmed."


def _sanitize_event(
    event: Mapping[str, Any],
    display_frame: object,
    *,
    pressure: bool,
) -> dict[str, object]:
    direction = _side(event.get("direction"), event.get("side"), event.get("movement"))
    state = _event_state(event, display_frame) if event else "UNKNOWN"
    display_direction = direction if pressure or state == "ACTIVE" else "NEUTRAL"
    return {
        "direction": display_direction,
        "state": state,
        "confidence": _confidence(event.get("confidence"), event.get("strength")),
        "observed_at": _epoch(
            event.get("observed_at"),
            event.get("observed_epoch"),
            event.get("updated_at"),
            event.get("updated_epoch"),
        ),
        "started_at": _epoch(
            event.get("started_at"), event.get("started_epoch"), event.get("opened_epoch")
        ),
        "ended_at": _epoch(event.get("ended_at"), event.get("ended_epoch"), event.get("ended_epoch_sec")),
        "frame_id": _frame_id(event.get("frame_id"), event.get("display_frame_id")),
        "summary": _movement_summary(direction, state, pressure=pressure),
    }


def _canonical_candle_movement_fallback(
    payload: Mapping[str, Any],
    display_frame: object,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Project observed candle-leg context without treating predictions as current.

    The candle context is useful when the command center has not yet published an
    explicit ``current_movement`` event.  It is accepted only when model/capture
    metadata ties it to the frame currently on screen and supplies an observation
    timestamp.  A completed earlier leg remains an ended pressure event so it can
    never be mistaken for pressure that is still happening now.
    """

    tracking_summary = _mapping(payload.get("tracking_summary"))
    latest_signal = _mapping(payload.get("latest_signal"))
    context: Mapping[str, Any] = {}
    context_container: Mapping[str, Any] = {}
    for container in (tracking_summary, latest_signal):
        candidate = _mapping(
            container.get("candle_movement_context_v3")
            or container.get("candle_movement_context")
        )
        if candidate:
            context = candidate
            context_container = container
            break
    if not context:
        return {}, {}

    current_leg = _mapping(context.get("current_leg"))
    previous_leg = _mapping(context.get("previous_leg"))
    current_side = _side(current_leg.get("side"), current_leg.get("direction"))
    transition_state = _text(current_leg.get("transition_state"), "").upper()
    current_frame = _frame_id(display_frame)
    evidence_frame = _frame_id(
        current_leg.get("frame_id"),
        current_leg.get("display_frame_id"),
        context.get("frame_id"),
        context.get("display_frame_id"),
        context_container.get("frame_id"),
        context_container.get("display_frame_id"),
        payload.get("model_vote_frame_id"),
    )
    observed_at = _epoch(
        current_leg.get("observed_at"),
        current_leg.get("observed_epoch"),
        context.get("observed_at"),
        context.get("observed_epoch"),
        context_container.get("published_epoch"),
        context_container.get("last_capture_epoch"),
        payload.get("model_capture_epoch"),
        payload.get("display_published_epoch"),
        tracking_summary.get("last_capture_epoch"),
        payload.get("last_capture_epoch"),
    )
    frame_corroborated = (
        current_frame not in (None, 0, "0")
        and evidence_frame not in (None, 0, "0")
        and _frame_matches(evidence_frame, current_frame)
    )
    transition_confirmed = transition_state not in {"FORMING", "PENDING", "TRANSITION"}
    current_confirmed = (
        current_side in _DIRECTIONAL_SIDES
        and transition_confirmed
        and frame_corroborated
        and observed_at is not None
    )
    if current_confirmed:
        current_event: Mapping[str, Any] = {
            "side": current_side,
            "state": "ACTIVE",
            "observed_at": observed_at,
            "started_at": _epoch(
                current_leg.get("started_at"),
                current_leg.get("started_epoch"),
            ),
            "frame_id": current_frame,
            "confidence": _confidence(
                current_leg.get("confidence"),
                current_leg.get("strength"),
                context.get("confidence"),
            ),
        }
    elif current_side in _DIRECTIONAL_SIDES and evidence_frame not in (None, 0, "0"):
        current_event = {
            "side": current_side,
            "state": "STALE" if not frame_corroborated else "UNKNOWN",
            "observed_at": observed_at,
            "frame_id": evidence_frame,
        }
    else:
        current_event = {}

    previous_side = _side(previous_leg.get("side"), previous_leg.get("direction"))
    pressure_event: Mapping[str, Any] = {}
    if previous_side in _DIRECTIONAL_SIDES:
        pressure_event = {
            "side": previous_side,
            "state": "ENDED",
            "observed_at": _epoch(
                previous_leg.get("observed_at"),
                previous_leg.get("observed_epoch"),
                previous_leg.get("started_at"),
                previous_leg.get("started_epoch"),
                observed_at,
            ),
            "ended_at": _epoch(
                previous_leg.get("ended_at"),
                previous_leg.get("ended_epoch"),
                observed_at,
            ),
            "frame_id": _frame_id(
                previous_leg.get("frame_id"),
                previous_leg.get("display_frame_id"),
                current_frame,
            ),
            "confidence": _confidence(
                previous_leg.get("confidence"),
                previous_leg.get("strength"),
            ),
        }
    return current_event, pressure_event


def _event_observed_at(event: Mapping[str, Any]) -> float | None:
    return _epoch(
        event.get("observed_at"),
        event.get("observed_epoch"),
        event.get("updated_at"),
        event.get("updated_epoch"),
        event.get("started_at"),
        event.get("started_epoch"),
    )


def _event_is_display_aligned(event: Mapping[str, Any], display_frame: object) -> bool:
    event_frame = _frame_id(event.get("frame_id"), event.get("display_frame_id"))
    current_frame = _frame_id(display_frame)
    return bool(
        event
        and event_frame not in (None, 0, "0")
        and current_frame not in (None, 0, "0")
        and _frame_matches(event_frame, current_frame)
        and _event_observed_at(event) is not None
    )


def _event_is_display_current(event: Mapping[str, Any], display_frame: object) -> bool:
    return bool(
        _event_is_display_aligned(event, display_frame)
        and _event_state(event, display_frame) == "ACTIVE"
    )


def _reconcile_current_event(
    explicit_event: Mapping[str, Any],
    canonical_event: Mapping[str, Any],
    display_frame: object,
) -> Mapping[str, Any]:
    if not explicit_event:
        return canonical_event
    if not canonical_event or not _event_is_display_current(canonical_event, display_frame):
        return explicit_event
    if not _event_is_display_current(explicit_event, display_frame):
        return canonical_event
    explicit_observed = _event_observed_at(explicit_event)
    canonical_observed = _event_observed_at(canonical_event)
    if (
        explicit_observed is not None
        and canonical_observed is not None
        and explicit_observed < canonical_observed
    ):
        return canonical_event
    return explicit_event


def _reconcile_pressure_event(
    explicit_event: Mapping[str, Any],
    canonical_event: Mapping[str, Any],
    current_event: Mapping[str, Any],
    display_frame: object,
) -> Mapping[str, Any]:
    if not explicit_event:
        return canonical_event
    if not canonical_event:
        return explicit_event
    canonical_side = _side(canonical_event.get("side"), canonical_event.get("direction"))
    explicit_side = _side(explicit_event.get("side"), explicit_event.get("direction"))
    current_side = _side(current_event.get("side"), current_event.get("direction"))
    canonical_closes_previous_leg = bool(
        _event_state(canonical_event, display_frame) == "ENDED"
        and canonical_side in _DIRECTIONAL_SIDES
        and explicit_side == canonical_side
        and current_side in _DIRECTIONAL_SIDES
        and current_side != canonical_side
        and _event_is_display_current(current_event, display_frame)
    )
    if not canonical_closes_previous_leg:
        return explicit_event
    explicit_state = _event_state(explicit_event, display_frame)
    if explicit_state == "ENDED":
        return explicit_event
    explicit_observed = _event_observed_at(explicit_event)
    current_observed = _event_observed_at(current_event)
    explicit_is_newer_current_pressure = bool(
        _event_is_display_aligned(explicit_event, display_frame)
        and explicit_state in {"ACTIVE", "UNKNOWN"}
        and explicit_observed is not None
        and current_observed is not None
        and explicit_observed > current_observed
    )
    return explicit_event if explicit_is_newer_current_pressure else canonical_event


def _freshness_contract(
    payload: Mapping[str, Any],
    command: Mapping[str, Any],
    current_move: Mapping[str, object],
    pressure_event: Mapping[str, object],
    *,
    now_epoch: float,
) -> dict[str, object]:
    tracking_summary = _mapping(payload.get("tracking_summary"))
    visual_observation = _mapping(payload.get("visual_observation_v3"))
    visual_status = _text(
        visual_observation.get("status") or tracking_summary.get("visual_observation_status"),
        "",
    ).upper()
    waiting_for_new_frame = visual_status == "WAITING_FOR_NEW_FRAME"
    if waiting_for_new_frame:
        created_at = _epoch(
            visual_observation.get("last_observed_epoch"),
            current_move.get("observed_at"),
            pressure_event.get("observed_at"),
            tracking_summary.get("last_capture_epoch"),
            payload.get("last_capture_epoch"),
        )
    else:
        created_at = _epoch(
            command.get("created_epoch"),
            command.get("created_epoch_sec"),
            current_move.get("observed_at"),
            pressure_event.get("observed_at"),
            tracking_summary.get("last_capture_epoch"),
            payload.get("last_capture_epoch"),
        )
    valid_until = _epoch(
        command.get("valid_until_epoch"),
        command.get("valid_until_epoch_sec"),
        command.get("expires_at"),
    )
    fresh_flag = _explicit_bool(command.get("fresh"))
    fresh_status = _text(command.get("freshness_status"), "").upper()
    payload_status = _text(payload.get("stale_status"), "").upper()
    if waiting_for_new_frame:
        state = "WAITING"
        valid_until = None
    elif valid_until is not None and valid_until <= now_epoch:
        state = "STALE"
    elif fresh_flag is True or fresh_status in {"PASS", "FRESH", "CURRENT"}:
        state = "FRESH"
    elif fresh_flag is False or fresh_status in {"STALE", "EXPIRED", "FAIL", "FAILED"}:
        state = "STALE"
    elif payload_status in {"STALE", "EXPIRED", "FAIL", "FAILED"}:
        state = "STALE"
    else:
        state = "UNKNOWN"
    age_seconds = round(max(0.0, now_epoch - created_at), 3) if created_at is not None else None
    label = (
        _safe_public_text(
            visual_observation.get("message") or tracking_summary.get("visual_observation_message"),
            "Waiting for a new broker frame",
            limit=120,
        )
        if waiting_for_new_frame
        else ""
    )
    return {
        "state": state,
        "label": label,
        "observed_at": created_at,
        "valid_until": valid_until,
        "age_seconds": age_seconds,
    }


def _forecast_contract(
    payload: Mapping[str, Any],
    command: Mapping[str, Any],
    freshness: Mapping[str, object],
    display_frame: object,
    overlays: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    explicit_forecast = _first_mapping(
        command,
        ("forecast",),
        ("outlook",),
        ("prediction",),
        ("horizon",),
    )
    snapshot = _mapping(payload.get("forecast_snapshot_v3"))
    high_frequency = _first_mapping(
        payload,
        ("high_frequency_forecast",),
        ("micro_candle_forecast",),
        ("latest_signal", "high_frequency_forecast"),
        ("latest_signal", "micro_candle_forecast"),
        ("tracking_summary", "high_frequency_forecast"),
        ("tracking_summary", "micro_candle_forecast"),
        ("forecast_snapshot_v3", "high_frequency_forecast"),
    )
    two_candle = _first_mapping(
        payload,
        ("two_candle_study",),
        ("latest_signal", "two_candle_study"),
        ("tracking_summary", "two_candle_study"),
        ("model_council_result", "two_candle_study"),
        ("high_frequency_forecast", "two_candle_study"),
        ("forecast_snapshot_v3", "two_candle_study"),
    ) or _mapping(high_frequency.get("two_candle_study"))
    scene_forecast = _first_mapping(
        payload,
        ("scene_forecast_contribution",),
        ("latest_signal", "scene_forecast_contribution"),
        ("tracking_summary", "scene_forecast_contribution"),
        ("model_council_result", "scene_forecast_contribution"),
        ("high_frequency_forecast", "scene_forecast_contribution"),
        ("forecast_snapshot_v3", "scene_forecast_contribution"),
    ) or _mapping(two_candle.get("scene_forecast_contribution"))
    lstm = _first_mapping(
        payload,
        ("lstm_contribution",),
        ("latest_signal", "lstm_contribution"),
        ("tracking_summary", "lstm_contribution"),
        ("model_council_result", "lstm_contribution"),
        ("high_frequency_forecast", "lstm_contribution"),
        ("forecast_snapshot_v3", "lstm_contribution"),
    ) or _mapping(two_candle.get("lstm_contribution"))
    forecaster = scene_forecast or lstm
    next_candle = _mapping(two_candle.get("next_candle_forecast"))

    display_frame_id = _frame_id(display_frame)

    def frame_aligned(candidate: Mapping[str, Any], parent: Mapping[str, Any]) -> bool:
        if not candidate or display_frame_id is None:
            return False
        candidate_frame = _frame_id(
            candidate.get("frame_id"),
            candidate.get("display_frame_id"),
            parent.get("frame_id"),
            parent.get("display_frame_id"),
            snapshot.get("source_frame_id"),
        )
        return candidate_frame is not None and _frame_matches(candidate_frame, display_frame_id)

    aligned_explicit_forecast: Mapping[str, Any] = (
        explicit_forecast
        if frame_aligned(explicit_forecast, command)
        else {}
    )
    aligned_two_candle: Mapping[str, Any] = (
        two_candle if frame_aligned(two_candle, two_candle) else {}
    )
    aligned_forecaster: Mapping[str, Any] = (
        forecaster if frame_aligned(forecaster, forecaster) else {}
    )
    if (
        not scene_forecast
        and _explicit_bool(aligned_forecaster.get("legacy_restored")) is True
        and _explicit_bool(aligned_forecaster.get("direction_conflict")) is True
    ):
        # A known-collapsed legacy decoder is retained in private diagnostics,
        # but it must not set the public forecast card to permanent SELL.
        aligned_forecaster = {}

    # The compact-state builder and the tracker snapshot can briefly cross at
    # a frame hand-off.  The raw forecast payload is intentionally discarded in
    # that case, while already-sanitized exact-frame path geometry can survive.
    # Use only the public centre path as a display fallback, and reject any row
    # whose declared side contradicts its plotted price trajectory.
    center_path: Mapping[str, object] = {}
    center_path_status = ""
    for overlay in overlays:
        if (
            _text(overlay.get("family"), "").lower()
            not in {"lstm", "scene_forecaster", "prediction"}
            or _text(overlay.get("forecast_role"), "").lower() not in {"center", "composite"}
            or _text(overlay.get("lifecycle"), "").lower() != "current"
        ):
            continue
        status = _text(overlay.get("forecast_status"), "").upper()
        if status not in {"AUTHORIZED", "NO_EDGE", "LOW_CONFIDENCE", "DIAGNOSTIC"}:
            continue
        overlay_frame = _frame_id(overlay.get("frame_id"))
        if (
            overlay_frame is None
            or display_frame_id is None
            or not _frame_matches(overlay_frame, display_frame_id)
        ):
            continue
        points = _point_pairs(overlay.get("line_points") or overlay.get("points"))
        if len(points) < 2:
            continue
        delta_y = points[-1][1] - points[0][1]
        if abs(delta_y) <= 1e-9:
            continue
        geometry_side = "SELL" if delta_y > 0.0 else "BUY"
        declared_side = _side(
            overlay.get("forecast_direction"),
            overlay.get("side"),
        )
        if declared_side != geometry_side:
            continue
        center_path = overlay
        center_path_status = status
        break

    candidate_pairs = (
        ((scene_forecast, scene_forecast),)
        if scene_forecast
        else ()
    ) + (
        (next_candle, two_candle),
        (high_frequency, high_frequency),
    ) + (
        ((lstm, lstm),)
        if lstm and not scene_forecast
        else ()
    )
    candidates = [
        candidate
        for candidate, parent in candidate_pairs
        if candidate and frame_aligned(candidate, parent)
    ]
    forecast = aligned_explicit_forecast or (candidates[0] if candidates else {})
    direction = (
        _side(
            # A sequence contributor can predict bullish candle bodies while
            # its plotted price path still moves down.  The operator forecast
            # describes the path movement shown on the chart, so path_side is
            # authoritative whenever the contributor supplies it.
            forecast.get("path_side"),
            forecast.get("direction"),
            forecast.get("side"),
            forecast.get("direction_bias"),
            forecast.get("selected_side"),
            aligned_two_candle.get("primary_pressure"),
            aligned_forecaster.get("path_side"),
            aligned_forecaster.get("side"),
        )
        if forecast
        else "NEUTRAL"
    )
    center_path_direction = (
        _side(center_path.get("forecast_direction"), center_path.get("side"))
        if center_path
        else "NEUTRAL"
    )
    belief_contract = _forecast_belief_contract(
        aligned_forecaster,
        center_path,
    )
    committed_direction = _side(belief_contract.get("committed_side"))
    belief_state = _text(belief_contract.get("belief_state"), "").upper()
    belief_path_conflict = bool(
        center_path_direction in _DIRECTIONAL_SIDES
        and committed_direction in _DIRECTIONAL_SIDES
        and center_path_direction != committed_direction
    )
    if belief_path_conflict:
        # A committed belief and selected geometry are one revision.  Do not
        # narrate either side when those two public truths crossed in flight.
        direction = "NEUTRAL"
        center_path = {}
        center_path_direction = "NEUTRAL"
        belief_contract = {}
        belief_state = ""
    elif (
        belief_state in {"STABLE", "REVERSAL_PENDING"}
        and committed_direction in _DIRECTIONAL_SIDES
    ):
        direction = committed_direction
    if center_path_direction in _DIRECTIONAL_SIDES:
        # The simple forecast card narrates the path drawn beside it.  A
        # shorter two-candle forecast can legitimately disagree, but it has
        # its own toggle and must not make the primary card describe the
        # opposite direction from the visible centre path.
        direction = center_path_direction
    horizon_seconds = _number(
        forecast.get("horizon_seconds")
        or forecast.get("duration_sec")
        or forecast.get("optimized_duration_sec")
    )
    if horizon_seconds is not None:
        horizon_seconds = round(max(0.0, horizon_seconds), 3)
    confidence = _confidence(forecast.get("confidence"), forecast.get("probability"))
    if confidence is None and direction in _DIRECTIONAL_SIDES:
        for candidate in candidates:
            candidate_side = _side(
                candidate.get("path_side"),
                candidate.get("direction"),
                candidate.get("side"),
                candidate.get("direction_bias"),
            )
            if candidate_side == direction:
                confidence = _confidence(
                    candidate.get("confidence"),
                    candidate.get("probability"),
                    candidate.get("contribution"),
                )
                if confidence is not None:
                    break
    center_path_supports_direction = bool(
        center_path
        and direction in _DIRECTIONAL_SIDES
        and _side(center_path.get("forecast_direction"), center_path.get("side")) == direction
    )
    if center_path_supports_direction:
        center_path_confidence = _confidence(center_path.get("confidence"))
        if center_path_confidence is not None:
            confidence = center_path_confidence
        # LSTM paths are candle-event sequences unless the public path itself
        # provides a validated wall-clock horizon.  Do not reuse a shorter
        # model's duration for a different path.
        horizon_seconds = None
    visual_observation = _mapping(payload.get("visual_observation_v3"))
    waiting_for_new_frame = bool(
        _text(visual_observation.get("status"), "").upper()
        == "WAITING_FOR_NEW_FRAME"
        and _explicit_bool(visual_observation.get("new_visual_evidence")) is not True
    )
    aligned_forecaster_is_explicitly_current = bool(
        aligned_forecaster
        and _explicit_bool(aligned_forecaster.get("fresh")) is True
        and _explicit_bool(aligned_forecaster.get("stale")) is not True
        and _explicit_bool(aligned_forecaster.get("diagnostic_only")) is not True
    )
    snapshot_is_stale_source = bool(
        (
            _explicit_bool(snapshot.get("stale")) is True
            or _explicit_bool(snapshot.get("diagnostic_only")) is True
        )
        and not aligned_forecaster_is_explicitly_current
    )
    # Decision-command freshness governs entry permission, not the age of an
    # exact-frame forecast study.  A NO_EDGE scene path often has no execution
    # command at all, so its command freshness is UNKNOWN even though the path
    # is current and frame-aligned.  Keep those two contracts separate: the
    # path remains a current diagnostic outlook while permission still waits.
    forecast_is_current = bool(
        not waiting_for_new_frame
        and not snapshot_is_stale_source
        and (
            freshness.get("state") == "FRESH"
            or center_path_supports_direction
        )
    )
    state = (
        "CURRENT"
        if forecast_is_current and direction in _DIRECTIONAL_SIDES
        else "STALE"
        if direction in _DIRECTIONAL_SIDES
        else "UNKNOWN"
    )
    if belief_state == "REVERSAL_PENDING" and direction in _DIRECTIONAL_SIDES:
        candidate = _side(belief_contract.get("candidate_side"))
        confirmation_events = _integer(belief_contract.get("confirmation_events"))
        required_events = _integer(belief_contract.get("required_events"))
        candidate_phrase = (
            f"{candidate} is under review"
            if candidate in _DIRECTIONAL_SIDES and candidate != direction
            else "a possible change is under review"
        )
        count_phrase = (
            f" ({confirmation_events}/{required_events} closed candles)"
            if required_events > 0
            else ""
        )
        summary = (
            f"{direction} remains the committed forecast; {candidate_phrase}{count_phrase}. "
            "Wait for confirmation. This forecast never grants entry permission."
        )
    elif center_path_supports_direction and center_path_status in {
        "NO_EDGE",
        "LOW_CONFIDENCE",
        "DIAGNOSTIC",
    }:
        movement_word = "upward" if direction == "BUY" else "downward"
        summary = (
            f"The current model path leans {movement_word}, but input quality is low "
            "and its risk gate found no reliable edge. It remains visible, is diagnostic only, "
            "and never grants entry permission."
            if center_path_status == "LOW_CONFIDENCE"
            else f"The current model path leans {movement_word}, but its risk gate "
            "found no reliable edge. It is diagnostic only and never grants entry permission."
        )
    elif state == "STALE":
        movement_word = "upward" if direction == "BUY" else "downward"
        summary = (
            f"The last valid model outlook pointed {movement_word}. "
            "It is diagnostic only while waiting for a new broker frame."
        )
    elif direction in _DIRECTIONAL_SIDES:
        movement_word = "upward" if direction == "BUY" else "downward"
        if horizon_seconds:
            minutes = max(1, round(horizon_seconds / 60.0))
            summary = f"Price may move {movement_word} over about {minutes} minute{'s' if minutes != 1 else ''}."
        else:
            summary = f"Price may move {movement_word}; this is an outlook, not entry permission."
    else:
        summary = "No reliable next direction is confirmed."
    result: dict[str, object] = {
        "direction": direction,
        "state": state,
        "confidence": confidence,
        "horizon_seconds": horizon_seconds,
        "summary": summary,
    }
    if belief_contract:
        result.update(belief_contract)
    scene_boundary = _scene_forecast_boundary_contract(
        scene_forecast,
        aligned_forecaster,
        center_path,
    )
    if scene_boundary:
        result.update(scene_boundary)
        if center_path_status:
            result["forecast_status"] = center_path_status
            result["forecast_authorized"] = center_path_status == "AUTHORIZED"
    return result


def _execution_present(payload: Mapping[str, Any], command: Mapping[str, Any], now_epoch: float) -> bool:
    if _explicit_bool(command.get("execution_packet_present")) is True:
        return True
    status = _mapping(payload.get("execution_packet_status"))
    if (
        _explicit_bool(status.get("present")) is True
        and _explicit_bool(status.get("current")) is True
        and _explicit_bool(status.get("fresh")) is True
    ):
        return True
    for key in ("execution_packet", "model_council_packet"):
        candidate = _mapping(payload.get(key))
        kind = _text(candidate.get("packet_type") or candidate.get("schema_version"), "").upper()
        expiry = _epoch(candidate.get("valid_until_epoch_sec"), candidate.get("valid_until_epoch"))
        if candidate and "EXECUTION" in kind and expiry is not None and expiry > now_epoch:
            return True
    return False


def _opportunity_contract(
    payload: Mapping[str, Any], command: Mapping[str, Any], now_epoch: float
) -> tuple[bool, float | None]:
    opportunity = _first_mapping(
        command,
        ("execution_opportunity_window_v3",),
        ("opportunity",),
    ) or _first_mapping(payload, ("execution_opportunity_window_v3",))
    state = _text(opportunity.get("state") or opportunity.get("status"), "").upper()
    expires_at = _epoch(
        opportunity.get("valid_until_epoch_sec"),
        opportunity.get("valid_until_epoch"),
        opportunity.get("expires_at"),
    )
    if expires_at is None:
        remaining = _number(opportunity.get("remaining_sec"))
        if remaining is not None and remaining > 0.0:
            expires_at = round(now_epoch + remaining, 6)
    rejected = any(
        _explicit_bool(opportunity.get(key)) is True
        for key in ("lineage_rejected", "out_of_order_ignored")
    )
    integrity = _explicit_bool(opportunity.get("integrity_valid"))
    is_open = (
        state in {"ACTIVE", "AUTHORIZED_NOW", "OPEN", "READY"}
        and expires_at is not None
        and expires_at > now_epoch
        and integrity is not False
        and not rejected
    )
    return is_open, expires_at


def _entry_location_contract(side: str) -> tuple[str, str]:
    guidance = entry_location_guidance_v3(side)
    return (
        guidance["preferred_price_location"],
        guidance["message"],
    )


def _window_label(*, is_open: bool, valid_for_seconds: float | None) -> str:
    if not is_open or valid_for_seconds is None:
        return "Closed"
    whole_seconds = max(1, math.ceil(valid_for_seconds))
    minutes, seconds = divmod(whole_seconds, 60)
    if minutes:
        return f"Open · {minutes}m {seconds:02d}s remaining"
    return f"Open · {seconds}s remaining"


def _permission_contract(
    payload: Mapping[str, Any],
    command: Mapping[str, Any],
    freshness: Mapping[str, object],
    current_move: Mapping[str, object],
    pressure_event: Mapping[str, object],
    *,
    now_epoch: float,
) -> dict[str, object]:
    selected_side = _side(command.get("selected_side"))
    command_fresh = freshness.get("state") == "FRESH"
    execution_present = _execution_present(payload, command, now_epoch)
    controls = _mapping(payload.get("execution_controls"))
    live_execution_enabled = _explicit_bool(controls.get("live_execution_enabled")) is True
    opportunity_open, expires_at = _opportunity_contract(payload, command, now_epoch)
    valid_for_seconds = (
        round(max(0.0, expires_at - now_epoch), 3)
        if opportunity_open and expires_at is not None
        else None
    )
    entry_location, entry_guidance = _entry_location_contract(selected_side)
    movement_matches = (
        current_move.get("state") == "ACTIVE"
        and current_move.get("direction") == selected_side
        and current_move.get("observed_at") is not None
        and selected_side in _DIRECTIONAL_SIDES
    )
    pressure_side = _text(pressure_event.get("direction"), "NEUTRAL").upper()
    pressure_state = _text(pressure_event.get("state"), "UNKNOWN").upper()
    contradictory_pressure = (
        pressure_side in _DIRECTIONAL_SIDES
        and pressure_side != selected_side
        and pressure_state in {"ACTIVE", "UNKNOWN"}
    )
    allowed = bool(
        command_fresh
        and execution_present
        and live_execution_enabled
        and opportunity_open
        and movement_matches
        and not contradictory_pressure
    )
    action = f"{selected_side}_NOW" if allowed else "WAIT"
    if allowed:
        movement_word = "buy" if selected_side == "BUY" else "sell"
        message = f"A verified {movement_word} entry window is open. {entry_guidance}"
        next_condition = (
            "Use only the current verified window; stop and wait if live truth changes."
        )
    elif not command_fresh:
        message = "Wait. The latest decision is not fresh enough to act on."
        next_condition = "Wait for a fresh live read."
    elif contradictory_pressure:
        message = "Wait. A conflicting pressure reading must be resolved first."
        next_condition = "Wait until current movement and the entry direction agree."
    elif not movement_matches:
        message = "Wait. Current movement does not confirm the entry direction."
        next_condition = "Wait for current movement to confirm the planned direction."
    elif not opportunity_open:
        message = "Wait. No verified entry window is open."
        next_condition = "Wait for a fresh, open entry window."
    elif not live_execution_enabled:
        message = "Wait. Trade entry is not enabled."
        next_condition = "Keep observing until entry is deliberately enabled."
    elif not execution_present:
        message = (
            "Wait. The setup window remains open, but current-frame permission is "
            "refreshing."
        )
        next_condition = (
            "Wait for fresh current-frame permission; the setup window alone is not "
            "permission to enter."
        )
    else:
        message = "Wait for all entry checks to align."
        next_condition = "Wait for the next confirmed live update."
    return {
        "action": action,
        "allowed": allowed,
        "side": selected_side if allowed else "NEUTRAL",
        "message": message,
        "next_condition": next_condition,
        "expires_at": expires_at if opportunity_open else None,
        "window_open": opportunity_open,
        "valid_for_seconds": valid_for_seconds,
        "window_label": _window_label(
            is_open=opportunity_open,
            valid_for_seconds=valid_for_seconds,
        ),
        "entry_location": entry_location,
        "entry_guidance": entry_guidance,
    }


def _coordinate_space(overlay: Mapping[str, Any]) -> str:
    raw = _text(overlay.get("coordinate_space") or overlay.get("coordinate_mode"), "").upper()
    return "window" if "WINDOW" in raw or "FULL_BROKER" in raw else "chart"


def _coordinate_units(overlay: Mapping[str, Any]) -> str:
    explicit = _text(overlay.get("coordinate_units"), "").lower()
    if explicit in {"normalized", "pixels"}:
        return explicit
    raw = _text(overlay.get("coordinate_space") or overlay.get("coordinate_mode"), "").upper()
    return "normalized" if "NORMALIZED" in raw else "pixels"


def _current_tracked_close_point(
    payload: Mapping[str, Any],
    overlay: Mapping[str, Any],
    *,
    display_frame_id: int | str | None,
    chart_plane_bounds: tuple[float, float, float, float] | None,
) -> list[list[float]]:
    """Return the latest tracker close only on the current chart-pixel plane.

    A candle box includes its wick and therefore cannot identify the observed
    close precisely.  This public point is sourced only from the atomic tracker
    bundle; producer-supplied overlay points are not treated as close evidence.
    """

    if (
        display_frame_id is None
        or _coordinate_space(overlay) != "chart"
        or _coordinate_units(overlay) != "pixels"
        or chart_plane_bounds is None
    ):
        return []

    tracking_summary = _mapping(payload.get("tracking_summary"))
    artifact_integrity = _mapping(tracking_summary.get("artifact_integrity"))
    if _explicit_bool(artifact_integrity.get("matches_selected_plane")) is False:
        return []

    # Tracker candle rows currently inherit their frame from the atomic study
    # bundle.  Require at least one chart-frame authority and reject the point
    # if any published authority disagrees with the surface frame.
    frame_authorities = [
        _frame_id(tracking_summary.get("frame_id"), tracking_summary.get("frame_index")),
        _frame_id(payload.get("chart_frame_id")),
        _frame_id(artifact_integrity.get("chart_artifact_frame_id")),
        _frame_id(artifact_integrity.get("artifact_frame_id")),
    ]
    declared_frames = [frame for frame in frame_authorities if frame is not None]
    if not declared_frames or any(
        not _frame_matches(frame, display_frame_id) for frame in declared_frames
    ):
        return []

    tracked_candles = _rows(tracking_summary.get("tracked_candles"))
    candidates: list[tuple[float, float, Mapping[str, Any]]] = []
    for candle in tracked_candles:
        row_frame = _frame_id(candle.get("frame_id"), candle.get("frame_index"))
        if row_frame is not None and not _frame_matches(row_frame, display_frame_id):
            continue
        center_x = _number(candle.get("center_x_px"))
        close_y = _number(candle.get("close_y_px"))
        if (
            center_x is None
            or close_y is None
            or not math.isfinite(center_x)
            or not math.isfinite(close_y)
        ):
            continue
        candidates.append((center_x, close_y, candle))
    if not candidates:
        return []

    center_x, close_y, latest = max(candidates, key=lambda row: row[0])
    left, top, right, bottom = chart_plane_bounds
    if not (left <= center_x <= right and top <= close_y <= bottom):
        return []

    # Tie the tracker row to the public current-candle object.  This prevents a
    # stale or differently cropped candle list from supplying a plausible but
    # detached point on the same numeric plane.
    tracker_bounds = _bounds(latest.get("bbox") or latest.get("bounds"))
    overlay_bounds = _bounds(overlay.get("bounds") or overlay.get("bbox"))
    tolerance = 1.0
    for bounds in (tracker_bounds, overlay_bounds):
        if len(bounds) != 4:
            return []
        x0, x1 = sorted((bounds[0], bounds[2]))
        y0, y1 = sorted((bounds[1], bounds[3]))
        if not (
            x0 - tolerance <= center_x <= x1 + tolerance
            and y0 - tolerance <= close_y <= y1 + tolerance
        ):
            return []

    return [[round(center_x, 6), round(close_y, 6)]]


def _bounds(value: object) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    values = cast(Sequence[object], value)
    if len(values) < 4:
        return []
    numbers = [_number(item) for item in values[:4]]
    if any(number is None for number in numbers):
        return []
    return [round(float(cast(float, number)), 6) for number in numbers]


def _normalized_rectangle(value: object) -> list[float]:
    bounds = _bounds(value)
    if len(bounds) < 4:
        return []
    x0 = max(0.0, min(1.0, min(bounds[0], bounds[2])))
    x1 = max(0.0, min(1.0, max(bounds[0], bounds[2])))
    y0 = max(0.0, min(1.0, min(bounds[1], bounds[3])))
    y1 = max(0.0, min(1.0, max(bounds[1], bounds[3])))
    if x1 - x0 <= 0.000001 or y1 - y0 <= 0.000001:
        return []
    return [round(x0, 6), round(y0, 6), round(x1, 6), round(y1, 6)]


def _image_dimensions(*candidates: object) -> tuple[float, float] | None:
    for value in candidates:
        candidate = _mapping(value)
        width = _number(candidate.get("width") or candidate.get("image_width"))
        height = _number(candidate.get("height") or candidate.get("image_height"))
        if width is not None and height is not None and width > 0.0 and height > 0.0:
            return width, height
    return None


def _coordinate_plane_bounds(
    value: object,
    fallback_dimensions: tuple[float, float] | None,
) -> tuple[float, float, float, float] | None:
    bounds = _bounds(value)
    if len(bounds) >= 4:
        left, right = sorted((bounds[0], bounds[2]))
        top, bottom = sorted((bounds[1], bounds[3]))
        if right > left and bottom > top:
            return left, top, right, bottom
    if fallback_dimensions is None:
        return None
    width, height = fallback_dimensions
    if width <= 0.0 or height <= 0.0:
        return None
    return 0.0, 0.0, width, height


def _pixel_rectangle_as_normalized(
    value: object,
    dimensions: tuple[float, float] | None,
) -> list[float]:
    bounds = _bounds(value)
    if len(bounds) < 4 or dimensions is None:
        return []
    width, height = dimensions
    if width <= 0.0 or height <= 0.0:
        return []
    return _normalized_rectangle(
        [
            bounds[0] / width,
            bounds[1] / height,
            bounds[2] / width,
            bounds[3] / height,
        ]
    )


def _overlay_viewport_contract(
    payload: Mapping[str, Any],
    tracking_summary: Mapping[str, Any],
) -> dict[str, object]:
    """Describe where chart-image coordinates land on the full broker frame.

    Only a normalized display rectangle crosses the public boundary. Detector
    provenance, window handles, raw image dimensions, and internal transforms
    remain private.
    """

    artifact_integrity = _mapping(tracking_summary.get("artifact_integrity"))
    broker_source_lock = _mapping(tracking_summary.get("broker_source_lock"))
    selected_target = _mapping(broker_source_lock.get("selected_target"))
    dimensions = _image_dimensions(
        artifact_integrity.get("full_window"),
        payload.get("locked_window"),
        selected_target.get("viewport"),
    )
    focus_region = _mapping(tracking_summary.get("focus_region"))
    normalized = _normalized_rectangle(focus_region.get("normalized_bbox"))
    if not normalized:
        normalized = _pixel_rectangle_as_normalized(
            focus_region.get("pixel_bbox"),
            dimensions,
        )

    # During an atomic frame hand-off the compact display state can be one
    # frame ahead of the full study snapshot. The configured focus rectangle
    # remains a valid display transform when the exact study focus is not yet
    # present, provided that it is still enabled.
    if not normalized:
        manual_focus = _mapping(payload.get("manual_focus_region"))
        if _explicit_bool(manual_focus.get("enabled")) is not False:
            normalized = _normalized_rectangle(manual_focus.get("normalized_bbox"))

    return {
        "source_space": "chart",
        "target_space": "window",
        "coordinate_units": "normalized",
        "bounds": normalized,
    }


def _point_pairs(value: object, *, limit: int = 256) -> list[list[float]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    values = cast(Sequence[object], value)
    points: list[list[float]] = []
    for item in values[:limit]:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes, bytearray)):
            continue
        pair = cast(Sequence[object], item)
        if len(pair) < 2:
            continue
        x = _number(pair[0])
        y = _number(pair[1])
        if x is not None and y is not None:
            points.append([round(x, 6), round(y, 6)])
    return points


def _forecast_candle_rows(value: object, *, limit: int = 12) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    numeric_keys = (
        "x_norm",
        "open_y_norm",
        "high_y_norm",
        "low_y_norm",
        "close_y_norm",
        "interval_top_y_norm",
        "interval_bottom_y_norm",
    )
    for raw in _rows(value)[:limit]:
        step = _integer(raw.get("step"))
        if step <= 0:
            continue
        numeric: dict[str, float] = {}
        valid = True
        for key in numeric_keys:
            number = _number(raw.get(key))
            if number is None:
                if key.startswith("interval_"):
                    continue
                valid = False
                break
            numeric[key] = round(max(0.0, min(1.0, number)), 6)
        if not valid:
            continue
        rows.append(
            {
                "step": step,
                "label": _text(raw.get("label"), f"E{step}", limit=8),
                **numeric,
                "movement_side": _side(raw.get("movement_side")),
                "body_bias": _side(raw.get("body_bias")),
                "direction_conflict": _explicit_bool(raw.get("direction_conflict")) is True,
            }
        )
    rows.sort(key=lambda row: _integer(row.get("step")))
    if [_integer(row.get("step")) for row in rows] != list(range(1, 13)):
        return []
    return rows


def _forecast_scenario_rows(value: object, *, limit: int = 3) -> list[dict[str, object]]:
    scenarios: list[dict[str, object]] = []
    for raw in _rows(value)[:limit]:
        points = _point_pairs(raw.get("line_points"), limit=13)
        if len(points) != 13:
            continue
        scenario_candles = _forecast_candle_rows(raw.get("forecast_candles"))
        if "forecast_candles" in raw and len(scenario_candles) != 12:
            continue
        probability = _number(raw.get("probability"))
        side = _side(raw.get("side"))
        scenario: dict[str, object] = {
                "side": side,
                "label": _text(raw.get("label"), f"{side} PATH", limit=32),
                "probability": round(max(0.0, min(1.0, probability or 0.0)), 6),
                "probability_calibrated": (
                    _explicit_bool(raw.get("probability_calibrated")) is True
                ),
                "selected": _explicit_bool(raw.get("selected")) is True,
                "raw_selected": _explicit_bool(raw.get("raw_selected")) is True,
                "candidate": _explicit_bool(raw.get("candidate")) is True,
                "role": _text(raw.get("role"), "", limit=24).lower(),
                "line_points": points,
                "event_count": 12,
            }
        if scenario_candles:
            scenario["forecast_candles"] = scenario_candles
        scenarios.append(scenario)
    scenarios.sort(
        key=lambda scenario: (
            not bool(scenario["selected"]),
            -float(cast(float, scenario["probability"])),
        )
    )
    roles = {_text(scenario.get("role"), "").lower() for scenario in scenarios}
    sides = {str(scenario["side"]) for scenario in scenarios}
    if (
        len(scenarios) != 3
        or not (
            roles == {"base", "bull", "bear"}
            or sides == {"BUY", "SELL", "NEUTRAL"}
        )
        or sum(bool(scenario["selected"]) for scenario in scenarios) != 1
    ):
        return []
    return scenarios


def _forecast_anchor(value: object) -> dict[str, object]:
    raw = _mapping(value)
    x_norm = _number(raw.get("x_norm"))
    y_norm = _number(raw.get("y_norm"))
    if (
        x_norm is None
        or y_norm is None
        or not 0.0 <= x_norm <= 1.0
        or not 0.0 <= y_norm <= 1.0
    ):
        return {}
    source = _text(raw.get("source"), "MODEL_CAUSAL_CANDLE", limit=32).upper()
    if source not in {
        "TRACKER_LATEST_CLOSE",
        "TRACKER_LATEST_CLOSED_CANDLE",
        "MODEL_CAUSAL_CANDLE",
    }:
        source = "MODEL_CAUSAL_CANDLE"
    return {
        "x_norm": round(x_norm, 6),
        "y_norm": round(y_norm, 6),
        "verified_latest_close": (
            _explicit_bool(raw.get("verified_latest_close")) is True
        ),
        "source": source,
    }


def _points_as_normalized(
    points: Sequence[Sequence[object]],
    *,
    units: str,
    plane_bounds: tuple[float, float, float, float] | None,
    tolerance: float = 1e-6,
) -> list[list[float]]:
    normalized: list[list[float]] = []
    left = top = 0.0
    width = height = 1.0
    if units == "pixels":
        if plane_bounds is None:
            return []
        left, top, right, bottom = plane_bounds
        width = right - left
        height = bottom - top
        if width <= 0.0 or height <= 0.0:
            return []
    elif units != "normalized":
        return []
    for point in points:
        if len(point) < 2:
            return []
        x = _number(point[0])
        y = _number(point[1])
        if x is None or y is None:
            return []
        normalized_x = (x - left) / width
        normalized_y = (y - top) / height
        if (
            not math.isfinite(normalized_x)
            or not math.isfinite(normalized_y)
            or normalized_x < -tolerance
            or normalized_x > 1.0 + tolerance
            or normalized_y < -tolerance
            or normalized_y > 1.0 + tolerance
        ):
            return []
        normalized.append(
            [
                round(max(0.0, min(1.0, normalized_x)), 6),
                round(max(0.0, min(1.0, normalized_y)), 6),
            ]
        )
    return normalized


def _bounds_as_normalized(
    bounds: Sequence[object],
    *,
    units: str,
    plane_bounds: tuple[float, float, float, float] | None,
) -> list[float]:
    if len(bounds) < 4:
        return []
    points = _points_as_normalized(
        [bounds[:2], bounds[2:4]],
        units=units,
        plane_bounds=plane_bounds,
    )
    if len(points) != 2:
        return []
    left, right = sorted((points[0][0], points[1][0]))
    top, bottom = sorted((points[0][1], points[1][1]))
    if right - left <= 1e-9 or bottom - top <= 1e-9:
        return []
    return [left, top, right, bottom]


def _forecast_paths_match(
    left: Sequence[Sequence[object]],
    right: Sequence[Sequence[object]],
    *,
    left_units: str = "normalized",
    right_units: str = "normalized",
    plane_bounds: tuple[float, float, float, float] | None = None,
    tolerance: float = 1e-6,
) -> bool:
    if len(left) != 13 or len(right) != 13:
        return False

    # The precision resolver projects the public centerline into chart pixels,
    # while nested model scenarios intentionally stay normalized so clients can
    # redraw them without a backend round trip. Compare both paths on one
    # normalized plane instead of rejecting a complete atomic bundle merely
    # because the two declared coordinate units differ.
    left_normalized = _points_as_normalized(
        left,
        units=left_units,
        plane_bounds=plane_bounds,
        tolerance=tolerance,
    )
    right_normalized = _points_as_normalized(
        right,
        units=right_units,
        plane_bounds=plane_bounds,
        tolerance=tolerance,
    )
    if len(left_normalized) != 13 or len(right_normalized) != 13:
        return False
    for left_point, right_point in zip(left_normalized, right_normalized):
        left_x, left_y = left_point
        right_x, right_y = right_point
        if (
            abs(left_x - right_x) > tolerance
            or abs(left_y - right_y) > tolerance
        ):
            return False
    return True


def _forecast_anchor_matches_path(
    anchor: Mapping[str, object],
    path: Sequence[Sequence[object]],
    *,
    path_units: str,
    plane_bounds: tuple[float, float, float, float] | None,
    tolerance: float = 1e-6,
) -> bool:
    if not anchor or len(path) != 13:
        return False
    anchor_x = _number(anchor.get("x_norm"))
    anchor_y = _number(anchor.get("y_norm"))
    if anchor_x is None or anchor_y is None:
        return False
    first = path[0]
    if len(first) < 2:
        return False
    first_x = _number(first[0])
    first_y = _number(first[1])
    if first_x is None or first_y is None:
        return False
    if path_units == "pixels":
        if plane_bounds is None:
            return False
        left, top, right, bottom = plane_bounds
        width = right - left
        height = bottom - top
        if width <= 0.0 or height <= 0.0:
            return False
        first_x = (first_x - left) / width
        first_y = (first_y - top) / height
    elif path_units != "normalized":
        return False
    return bool(
        abs(anchor_x - first_x) <= tolerance
        and abs(anchor_y - first_y) <= tolerance
    )


def _forecast_interval(value: object) -> dict[str, object]:
    raw = _mapping(value)
    status = _text(raw.get("status"), "UNAVAILABLE", limit=32).upper()
    calibrated = bool(_explicit_bool(raw.get("calibrated")) is True and status == "READY")
    output: dict[str, object] = {
        "status": "READY" if calibrated else "UNAVAILABLE",
        "calibrated": calibrated,
        "method": _text(raw.get("method"), "UNAVAILABLE", limit=48).upper(),
        "source_count": _integer(raw.get("source_count")),
    }
    level = _number(raw.get("level"))
    coverage = _number(raw.get("coverage"))
    if level is not None:
        output["level"] = round(max(0.0, min(1.0, level)), 4)
    if coverage is not None:
        output["coverage"] = round(max(0.0, min(1.0, coverage)), 4)
    return output


def _geometry_is_on_declared_plane(
    *,
    bounds: Sequence[float],
    points: Sequence[Sequence[float]],
    line_points: Sequence[Sequence[float]],
    coordinate_units: str,
) -> bool:
    """Fail closed when public geometry cannot belong to its chart plane."""

    coordinates: list[float] = []
    if len(bounds) >= 4:
        left, right = sorted((float(bounds[0]), float(bounds[2])))
        top, bottom = sorted((float(bounds[1]), float(bounds[3])))
        if right - left <= 1e-9 or bottom - top <= 1e-9:
            return False
        coordinates.extend((left, top, right, bottom))
    for pair in [*points, *line_points]:
        if len(pair) < 2:
            continue
        coordinates.extend((float(pair[0]), float(pair[1])))
    if not coordinates or not all(math.isfinite(value) for value in coordinates):
        return False
    if coordinate_units == "normalized":
        return all(-0.000001 <= value <= 1.000001 for value in coordinates)
    return all(value >= 0.0 for value in coordinates)


def _overlay_identity_and_revisions(
    overlay: Mapping[str, Any],
    public_overlay: Mapping[str, object],
) -> dict[str, str]:
    stable_source = _safe_identifier(
        overlay.get("track_id")
        or overlay.get("source_key")
        or public_overlay.get("id"),
        str(public_overlay.get("id") or "overlay"),
    )
    anchor_indices = sorted(
        {
            int(value)
            for value in (
                _number(item)
                for item in [
                    *_rows(overlay.get("anchor_candles")),
                ]
            )
            if value is not None and value >= 0
        }
    )
    # Most V3 overlays store candle anchors as scalar indices rather than
    # mappings. Preserve both shapes without exposing detector internals.
    if not anchor_indices:
        anchor_indices = sorted(
            {
                int(value)
                for value in (
                    _number(item)
                    for item in (
                        cast(Sequence[object], overlay.get("anchor_candles"))
                        if isinstance(overlay.get("anchor_candles"), Sequence)
                        and not isinstance(
                            overlay.get("anchor_candles"),
                            (str, bytes, bytearray),
                        )
                        else ()
                    )
                )
                if value is not None and value >= 0
            }
        )
    semantic_id = _stable_public_digest(
        {
            "source": stable_source,
            "type": public_overlay.get("type"),
            "family": public_overlay.get("family"),
        },
        prefix="sem",
    )
    anchor_id = _stable_public_digest(
        {
            "semantic_id": semantic_id,
            "anchor_indices": anchor_indices,
            "anchor_type": _text(overlay.get("anchor_type"), "GEOMETRY").upper(),
        },
        prefix="anchor",
    )
    semantic_revision = _stable_public_digest(
        {
            "semantic_id": semantic_id,
            "anchor_id": anchor_id,
            "side": public_overlay.get("side"),
            "group": public_overlay.get("group"),
            "family": public_overlay.get("family"),
            "layer": public_overlay.get("layer"),
            "label": public_overlay.get("label"),
        },
        prefix="osem",
    )
    geometry_revision = _stable_public_digest(
        {
            "bounds": public_overlay.get("bounds"),
            "points": public_overlay.get("points"),
            "line_points": public_overlay.get("line_points"),
            "forecast_band_points": public_overlay.get("forecast_band_points"),
            "forecast_scenarios": public_overlay.get("forecast_scenarios"),
            "forecast_anchor": public_overlay.get("forecast_anchor"),
            "coordinate_space": public_overlay.get("coordinate_space"),
            "coordinate_units": public_overlay.get("coordinate_units"),
        },
        prefix="ogeo",
    )
    return {
        "semantic_id": semantic_id,
        "anchor_id": anchor_id,
        "overlay_semantic_revision": semantic_revision,
        "overlay_geometry_revision": geometry_revision,
    }


def _surface_overlay_revision_contract(
    source: Mapping[str, object],
    tracking_summary: Mapping[str, Any],
    market: Mapping[str, object],
    viewport: Mapping[str, object],
    overlays: Sequence[Mapping[str, object]],
) -> dict[str, str]:
    broker_lock = _mapping(tracking_summary.get("broker_source_lock"))
    selected_target = _mapping(broker_lock.get("selected_target"))
    selector_fingerprint = _text(
        tracking_summary.get("market_selector_visual_fingerprint"),
        "",
        limit=80,
    )
    # Only the V2 selector-only text mask is stable enough to own semantic
    # history. Legacy whole-header hashes include moving sparklines/candles and
    # remain diagnostics. This identity also distinguishes pair switches when
    # broker OCR cannot yet resolve the symbol text.
    selector_identity = (
        selector_fingerprint
        if selector_fingerprint.startswith("selector_v2_")
        else ""
    )
    semantic_identity = _stable_public_digest(
        {
            "session": _safe_session_id(source.get("session_id")),
            "symbol": _text(market.get("symbol"), "UNKNOWN").upper(),
            "timeframe": _text(market.get("timeframe"), "UNKNOWN").upper(),
            # The selector's raw visual fingerprint is deliberately excluded.
            # It is a hash of a live image crop and changes as broker chrome or
            # candles animate even when the selected pair has not changed.
            # Confirmed symbol/timeframe plus the stable broker lock own the
            # semantic history namespace; viewport motion is geometry only.
            "selector_identity": selector_identity,
            "broker_lock": _text(
                source.get("broker_source_lock_id")
                or broker_lock.get("lock_id")
                or selected_target.get("window_id")
                or selected_target.get("title"),
                "SURFACE",
                limit=160,
            ),
        },
        prefix="surface",
    )
    semantic_revision = _stable_public_digest(
        {
            "surface": semantic_identity,
            # Existing studied objects reconcile by their per-row semantic
            # ids. A new live frame may append context, but it must not flush
            # every already-seen history node. Only pair/surface identity owns
            # the history semantic namespace.
            "namespace": "STUDIED_HISTORY_V1",
        },
        prefix="history_sem",
    )
    geometry_revision = _stable_public_digest(
        {
            "surface": semantic_identity,
            "viewport": viewport,
            # Per-row geometry revisions invalidate only the object that
            # moved. Appending one studied swing/history row must not flush
            # every already-projected static overlay.
        },
        prefix="history_geo",
    )
    return {
        "semantic_identity": semantic_identity,
        "overlay_semantic_revision": semantic_revision,
        "overlay_geometry_revision": geometry_revision,
    }


def _overlay_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidates = (
        _mapping(payload.get("overlays")),
        _mapping(_mapping(payload.get("live_visual_state")).get("overlays")),
        _mapping(_mapping(payload.get("live_state_v3")).get("overlays")),
    )
    for container in candidates:
        objects = _rows(container.get("objects"))
        if objects:
            return objects
        all_objects = _rows(container.get("all_objects"))
        if all_objects:
            return all_objects
    return _rows(payload.get("overlay_objects"))


def _sanitize_overlays(payload: Mapping[str, Any], display_frame: object) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    display_frame_id = _frame_id(display_frame)
    chart_frame = _mapping(payload.get("chart_frame"))
    chart = _mapping(payload.get("chart"))
    surface = _mapping(payload.get("surface"))
    artifacts = _mapping(payload.get("artifacts"))
    tracking_summary = _mapping(payload.get("tracking_summary"))
    artifact_integrity = _mapping(tracking_summary.get("artifact_integrity"))
    scene_graph = _mapping(
        payload.get("scene_graph")
        or chart.get("scene_graph")
        or payload.get("broker_scene_graph_v3")
    )
    chart_dimensions = _image_dimensions(
        chart_frame.get("artifact"),
        chart.get("frame"),
        artifacts.get("chart"),
        chart_frame,
        artifact_integrity.get("chart"),
        artifact_integrity.get("selected_plane"),
        tracking_summary.get("chart_region"),
    )
    window_dimensions = _image_dimensions(
        surface.get("frame"),
        artifacts.get("window"),
        _mapping(payload.get("broker_surface")).get("frame"),
    )
    chart_plane_bounds = _coordinate_plane_bounds(
        scene_graph.get("chart_region_chart_bounds"),
        chart_dimensions,
    )
    window_plane_bounds = _coordinate_plane_bounds(
        scene_graph.get("broker_surface_bounds"),
        window_dimensions,
    )
    visual_observation = _mapping(payload.get("visual_observation_v3"))
    waiting_for_new_frame = bool(
        _text(visual_observation.get("status")).upper() == "WAITING_FOR_NEW_FRAME"
        and _explicit_bool(visual_observation.get("new_visual_evidence")) is not True
    )
    for index, overlay in enumerate(_overlay_rows(payload)[:256]):
        raw_type = _text(overlay.get("type") or overlay.get("overlay_type") or overlay.get("kind"), "").upper()
        layer = _text(overlay.get("layer"), "").lower()
        if (
            layer in {"broker_controls", "diagnostics"}
            or "BROKER" in layer.upper()
            or "DIAGNOSTIC" in layer.upper()
            or raw_type in {"BROKER_CONTROL", "DEBUG_RAW_DETECTION"}
            or raw_type.startswith("DEBUG_")
            or _explicit_bool(overlay.get("precision_rejected")) is True
        ):
            continue
        presentation = _OVERLAY_PRESENTATION.get(raw_type)
        if presentation is None:
            continue
        public_type, label, default_group = presentation
        group = _LAYER_GROUPS.get(layer, default_group)
        if group not in {"structure", "zones", "movement", "plan", "outlook", "history"}:
            continue
        family = _TYPE_FAMILIES.get(
            raw_type,
            _LAYER_FAMILIES.get(layer, _GROUP_FALLBACK_FAMILIES[group]),
        )
        public_layer = layer if layer in _LAYER_FAMILIES else _FAMILY_FALLBACK_LAYERS[family]
        role = _text(overlay.get("role"), "").lower()
        scene_forecast_overlay = bool(
            raw_type == "SCENE_FORECAST_STUDY"
            or _is_scene_forecast_source(overlay)
        )
        if scene_forecast_overlay and raw_type in {
            "LSTM_STUDY",
            "SCENE_FORECAST_STUDY",
        }:
            label = "Scene forecaster"
        lstm_forecast_role = ""
        lstm_forecast_status = ""
        if raw_type in {"LSTM_STUDY", "SCENE_FORECAST_STUDY"} and (
            "path" in role
            or "lstm_forecast_90_" in role
            or "lstm_forecast_composite" in role
            or "scene_forecast_composite" in role
        ):
            if "forecast_composite" in role:
                lstm_forecast_role = "composite"
            elif "90_band" in role:
                lstm_forecast_role = "band_90"
            elif "90_upper_boundary" in role:
                lstm_forecast_role = "upper_90"
            elif "90_lower_boundary" in role:
                lstm_forecast_role = "lower_90"
            elif "candle_event_path" in role:
                lstm_forecast_role = "center"
            forecast_noun = "events" if lstm_forecast_role == "composite" else "path"
            forecast_prefix = "Scene forecaster" if scene_forecast_overlay else "LSTM V3"
            if "stale_diagnostic" in role:
                lstm_forecast_status = "STALE"
                label = f"{forecast_prefix} last valid {forecast_noun} - diagnostic"
            elif "no_edge" in role:
                lstm_forecast_status = "NO_EDGE"
                label = f"{forecast_prefix} {forecast_noun} - NO EDGE - diagnostic"
            elif "low_confidence" in role:
                lstm_forecast_status = "LOW_CONFIDENCE"
                label = f"{forecast_prefix} {forecast_noun} - LOW CONFIDENCE - diagnostic"
            elif role.endswith("_diagnostic"):
                lstm_forecast_status = "DIAGNOSTIC"
                label = f"{forecast_prefix} {forecast_noun} - diagnostic"
            elif "authorized" in role:
                lstm_forecast_status = "AUTHORIZED"
                label = f"{forecast_prefix} authorized {forecast_noun}"
            else:
                # Legacy V3 path objects remain explicitly diagnostic.  An
                # absent risk status must never be promoted by presentation.
                lstm_forecast_status = "NO_EDGE"
                label = f"{forecast_prefix} {forecast_noun} - NO EDGE - diagnostic"
            belief_contract = _forecast_belief_contract(overlay)
            belief_state = _text(belief_contract.get("belief_state"), "").upper()
            committed_side = _side(belief_contract.get("committed_side"))
            if scene_forecast_overlay and (
                belief_state == "REVERSAL_PENDING"
                or (
                    lstm_forecast_status == "AUTHORIZED"
                    and (
                        belief_state != "STABLE"
                        or committed_side not in _DIRECTIONAL_SIDES
                    )
                )
            ):
                # A pending/reacquiring scene belief is informative but cannot
                # surface as an authorized forecast revision.
                lstm_forecast_status = "NO_EDGE"
                label = f"{forecast_prefix} {forecast_noun} - revision under review"
        raw_coordinate_mode = _text(
            overlay.get("coordinate_space") or overlay.get("coordinate_mode"),
            "CHART_IMAGE_SPACE",
        ).upper()
        # Plot-normalized coordinates need a private plot transform.  The
        # operator contract deliberately does not expose that transform, so an
        # unprojected plot-space object must fail closed instead of drifting on
        # the full chart image.
        if "PLOT" in raw_coordinate_mode and "NORMALIZED" in raw_coordinate_mode:
            continue
        raw_lifecycle = _text(
            overlay.get("lifecycle_state") or overlay.get("lifecycle") or overlay.get("state"),
            "CURRENT",
        ).upper()
        historical = group == "history" or raw_type in _HISTORICAL_TYPES or raw_lifecycle in {
            "ARCHIVED",
            "HISTORICAL",
            "PAST",
            "REPLAY",
        }
        overlay_frame = _frame_id(overlay.get("frame_id"), overlay.get("display_frame_id"))
        if overlay_frame is None:
            continue
        frame_mismatch = (
            display_frame_id is not None
            and not _frame_matches(overlay_frame, display_frame_id)
        )
        stale = raw_lifecycle in _PAST_STATES or _explicit_bool(overlay.get("stale")) is True
        explicit_mismatch = _explicit_bool(
            overlay.get("frame_mismatch") or overlay.get("artifact_frame_mismatch")
        ) is True
        if display_frame_id is None or frame_mismatch or explicit_mismatch:
            continue
        if not historical and stale:
            continue
        latest_candle = bool(
            raw_type == "CURRENT_CANDLE"
            and (
                role == "current_candle"
                or _explicit_bool(overlay.get("is_latest_candle")) is True
            )
        )
        if raw_type == "CURRENT_CANDLE":
            label = "Current price" if latest_candle or not role else "Recent candle"
        label_hidden = bool(
            _explicit_bool(overlay.get("label_hidden")) is True
            or historical
            or (raw_type == "CURRENT_CANDLE" and role and not latest_candle)
        )
        object_id = _safe_identifier(
            overlay.get("overlay_id")
            or overlay.get("id")
            or overlay.get("object_id")
            or overlay.get("track_id"),
            f"overlay-{index + 1}",
        )
        dedup_key = (object_id, str(overlay_frame or ""))
        if dedup_key in seen:
            continue
        public_bounds = _bounds(overlay.get("bounds") or overlay.get("bbox"))
        public_points = _point_pairs(overlay.get("points"))
        public_line_points = _point_pairs(overlay.get("line_points"))
        coordinate_units = _coordinate_units(overlay)
        coordinate_space = _coordinate_space(overlay)
        if latest_candle:
            # A one-point close anchor is intentionally published only for the
            # latest candle and only when it is corroborated by this frame's
            # chart-pixel tracker bundle.
            public_points = _current_tracked_close_point(
                payload,
                overlay,
                display_frame_id=display_frame_id,
                chart_plane_bounds=chart_plane_bounds,
            )
        if not _geometry_is_on_declared_plane(
            bounds=public_bounds,
            points=public_points,
            line_points=public_line_points,
            coordinate_units=coordinate_units,
        ):
            # A label with no verified chart geometry is exactly the floating
            # overlay failure this contract is designed to prevent.
            continue
        seen.add(dedup_key)
        public_overlay: dict[str, object] = {
            "id": object_id,
            "type": public_type,
            "side": _side(overlay.get("side"), overlay.get("direction"), overlay.get("action")),
            "group": group,
            "family": family,
            "layer": public_layer,
            "label": label,
            "label_hidden": label_hidden,
            "bounds": public_bounds,
            "points": public_points,
            "line_points": public_line_points,
            "confidence": _confidence(overlay.get("confidence"), overlay.get("truth_score")),
            "lifecycle": (
                "historical"
                if historical
                else "stale_diagnostic"
                if waiting_for_new_frame
                else "current"
            ),
            "frame_id": overlay_frame,
            "coordinate_space": _coordinate_space(overlay),
            "coordinate_units": coordinate_units,
        }
        if scene_forecast_overlay:
            public_overlay.update(_scene_forecast_boundary_contract(overlay))
        if lstm_forecast_role:
            interval = _forecast_interval(overlay.get("interval"))
            forecast_band_points = _point_pairs(
                overlay.get("forecast_band_points")
            )
            forecast_candles = _forecast_candle_rows(overlay.get("forecast_candles"))
            forecast_scenarios = _forecast_scenario_rows(
                overlay.get("forecast_scenarios")
            )
            forecast_anchor = _forecast_anchor(overlay.get("forecast_anchor"))
            forecast_coordinate_space = (
                "window"
                if _text(
                    overlay.get("forecast_coordinate_space"),
                    "chart",
                ).lower()
                == "window"
                else "chart"
            )
            forecast_coordinate_units = (
                "pixels"
                if _text(
                    overlay.get("forecast_coordinate_units"),
                    "normalized",
                ).lower()
                == "pixels"
                else "normalized"
            )
            coordinate_plane_bounds = (
                window_plane_bounds
                if coordinate_space == "window"
                else chart_plane_bounds
            )
            forecast_plane_bounds = (
                window_plane_bounds
                if forecast_coordinate_space == "window"
                else chart_plane_bounds
            )
            normalized_forecast_scenarios: list[dict[str, object]] = []
            for scenario in forecast_scenarios:
                scenario_points = _points_as_normalized(
                    cast(
                        Sequence[Sequence[object]],
                        scenario.get("line_points", []),
                    ),
                    units=forecast_coordinate_units,
                    plane_bounds=forecast_plane_bounds,
                )
                if len(scenario_points) != 13:
                    normalized_forecast_scenarios = []
                    break
                normalized_forecast_scenarios.append(
                    {**scenario, "line_points": scenario_points}
                )
            selected_scenarios = [
                scenario
                for scenario in normalized_forecast_scenarios
                if bool(scenario.get("selected"))
            ]
            overlay_belief = _forecast_belief_contract(overlay)
            committed_scenario_side = _side(
                overlay_belief.get("committed_side")
            )
            if lstm_forecast_role == "composite" and (
                len(public_line_points) != 13
                or len(forecast_candles) != 12
                or len(normalized_forecast_scenarios) != 3
                or len(selected_scenarios) != 1
                or (
                    committed_scenario_side in _DIRECTIONAL_SIDES
                    and _side(selected_scenarios[0].get("side"))
                    != committed_scenario_side
                )
                or coordinate_space != forecast_coordinate_space
                or not _forecast_paths_match(
                    cast(Sequence[Sequence[object]], public_line_points),
                    cast(
                        Sequence[Sequence[object]],
                        next(
                            (
                                scenario.get("line_points", [])
                                for scenario in forecast_scenarios
                                if bool(scenario.get("selected"))
                            ),
                            (),
                        ),
                    ),
                    left_units=coordinate_units,
                    right_units=forecast_coordinate_units,
                    plane_bounds=coordinate_plane_bounds,
                )
                or not _forecast_anchor_matches_path(
                    forecast_anchor,
                    cast(
                        Sequence[Sequence[object]],
                        selected_scenarios[0].get("line_points", []),
                    ),
                    path_units="normalized",
                    plane_bounds=forecast_plane_bounds,
                )
            ):
                continue
            if lstm_forecast_role == "composite":
                normalized_bounds = _bounds_as_normalized(
                    cast(Sequence[object], public_bounds),
                    units=coordinate_units,
                    plane_bounds=coordinate_plane_bounds,
                )
                normalized_points = _points_as_normalized(
                    cast(Sequence[Sequence[object]], public_points),
                    units=coordinate_units,
                    plane_bounds=coordinate_plane_bounds,
                )
                normalized_band_points = _points_as_normalized(
                    cast(Sequence[Sequence[object]], forecast_band_points),
                    units=forecast_coordinate_units,
                    plane_bounds=forecast_plane_bounds,
                )
                if (
                    len(normalized_bounds) != 4
                    or (public_points and len(normalized_points) != len(public_points))
                    or (
                        forecast_band_points
                        and len(normalized_band_points) != len(forecast_band_points)
                    )
                ):
                    continue
                # The public DTO uses one canonical normalized chart plane.
                # This also lets the API's final atomic-bundle guard compare
                # the selected scenario to the centerline without repeating a
                # private pixel transform.
                public_overlay.update(
                    {
                        "bounds": normalized_bounds,
                        "points": normalized_points,
                        "line_points": [
                            list(point)
                            for point in cast(
                                Sequence[Sequence[float]],
                                selected_scenarios[0]["line_points"],
                            )
                        ],
                        "coordinate_space": forecast_coordinate_space,
                        "coordinate_units": "normalized",
                    }
                )
                forecast_band_points = normalized_band_points
                forecast_scenarios = normalized_forecast_scenarios
                forecast_coordinate_units = "normalized"
            public_overlay.update(
                {
                    "forecast_role": lstm_forecast_role,
                    "forecast_status": lstm_forecast_status,
                    "forecast_authorized": lstm_forecast_status == "AUTHORIZED",
                    "trade_authorization_status": (
                        "AUTHORIZED" if lstm_forecast_status == "AUTHORIZED" else "NO_EDGE"
                    ),
                    "forecast_quality_status": _text(
                        overlay.get("forecast_quality_status"),
                        lstm_forecast_status,
                        limit=32,
                    ).upper(),
                    "forecast_direction": _side(overlay.get("forecast_direction")),
                    "body_bias": _side(overlay.get("body_bias")),
                    "direction_conflict": _explicit_bool(overlay.get("direction_conflict")) is True,
                    "path_confidence_status": _text(
                        overlay.get("path_confidence_status"),
                        "UNAVAILABLE",
                        limit=32,
                    ).upper(),
                    "forecast_band_points": forecast_band_points,
                    "forecast_candles": forecast_candles,
                    "forecast_scenarios": forecast_scenarios,
                    "forecast_anchor": forecast_anchor,
                    "trajectory_mode": _side(overlay.get("trajectory_mode")),
                    "trajectory_mode_probability_calibrated": (
                        _explicit_bool(
                            overlay.get("trajectory_mode_probability_calibrated")
                        )
                        is True
                    ),
                    "forecast_coordinate_space": (
                        forecast_coordinate_space
                    ),
                    "forecast_coordinate_units": (
                        forecast_coordinate_units
                    ),
                    "interval": interval,
                    "horizon_unit": "CANDLE_EVENTS",
                    "clock_time_assumption": "NONE",
                }
            )
            if overlay_belief:
                public_overlay.update(overlay_belief)
            if bool(interval.get("calibrated")) and interval.get("level") is not None:
                public_overlay["uncertainty_level"] = interval["level"]
        public_overlay.update(
            _overlay_identity_and_revisions(overlay, public_overlay)
        )
        output.append(public_overlay)
    return output


def _history_summary(direction: str, state: str) -> str:
    movement_word = "Upward" if direction == "BUY" else "Downward" if direction == "SELL" else "Neutral"
    if state == "CURRENT":
        return f"{movement_word} movement is current."
    if state == "ENDED":
        return f"{movement_word} movement ended."
    if state == "STALE":
        return f"An older {movement_word.lower()} reading is retained for context."
    return f"{movement_word} movement was observed."


def _history_contract(
    payload: Mapping[str, Any],
    current_move: Mapping[str, object],
    pressure_event: Mapping[str, object],
) -> list[dict[str, object]]:
    history: list[dict[str, object]] = []
    raw_history = _rows(payload.get("recent_studies")) + _rows(payload.get("history"))
    for row in raw_history:
        command = _mapping(row.get("decision_command_center"))
        event = _mapping(command.get("current_movement")) or _mapping(row.get("current_movement"))
        pressure = _mapping(command.get("pressure_event")) or _mapping(row.get("pressure_event"))
        source = event or pressure or row
        direction = _side(
            source.get("direction"),
            source.get("side"),
            row.get("side"),
            _mapping(row.get("latest_signal")).get("side"),
        )
        observed_at = _epoch(
            source.get("observed_at"),
            source.get("observed_epoch"),
            source.get("ended_at"),
            row.get("published_epoch"),
            row.get("created_epoch"),
            row.get("last_capture_epoch"),
        )
        state = _event_state(source, None)
        history_state = "ENDED" if state == "ENDED" else "STALE" if state == "STALE" else "HISTORICAL"
        history.append(
            {
                "observed_at": observed_at,
                "direction": direction,
                "state": history_state,
                "summary": _history_summary(direction, history_state),
                "frame_id": _frame_id(source.get("frame_id"), row.get("frame_id")),
            }
        )

    pressure_state = _text(pressure_event.get("state"), "UNKNOWN").upper()
    pressure_direction = _text(pressure_event.get("direction"), "NEUTRAL").upper()
    if pressure_state in {"ENDED", "STALE"}:
        history.append(
            {
                "observed_at": pressure_event.get("ended_at") or pressure_event.get("observed_at"),
                "direction": pressure_direction,
                "state": pressure_state,
                "summary": _history_summary(pressure_direction, pressure_state),
                "frame_id": pressure_event.get("frame_id"),
            }
        )

    current_state = _text(current_move.get("state"), "UNKNOWN").upper()
    current_direction = _text(current_move.get("direction"), "NEUTRAL").upper()
    if current_state == "ACTIVE" and current_direction in _DIRECTIONAL_SIDES:
        history.append(
            {
                "observed_at": current_move.get("observed_at"),
                "direction": current_direction,
                "state": "CURRENT",
                "summary": _history_summary(current_direction, "CURRENT"),
                "frame_id": current_move.get("frame_id"),
            }
        )

    deduplicated: dict[tuple[object, object, object, object], dict[str, object]] = {}
    for item in history:
        key = (item.get("observed_at"), item.get("direction"), item.get("state"), item.get("frame_id"))
        deduplicated[key] = item
    ordered = sorted(
        deduplicated.values(),
        key=lambda item: (_number(item.get("observed_at")) or 0.0, str(item.get("frame_id") or "")),
    )
    return ordered[-24:]


def build_operator_workspace_v1(
    payload: Mapping[str, object],
    *,
    now_epoch: float | None = None,
) -> dict[str, object]:
    """Build the narrow, user-facing operator workspace contract.

    The projection is intentionally fail-closed. It never derives current movement
    from forecast, score, provider, or model fields and never returns raw diagnostic
    or filesystem data from the live-state payload.
    """

    source: Mapping[str, object] = payload
    current_epoch = float(now_epoch if now_epoch is not None else time.time())
    session_id = _safe_session_id(source.get("session_id"))
    encoded_session_id = quote(session_id, safe="")
    command = _mapping(source.get("decision_command_center"))
    display_frame = _frame_id(
        source.get("display_frame_id"),
        source.get("chart_frame_id"),
        source.get("frame_id"),
    )
    canonical_current, canonical_pressure = _canonical_candle_movement_fallback(source, display_frame)
    explicit_current = _mapping(command.get("current_movement")) or _mapping(
        source.get("current_movement")
    )
    current_event = _reconcile_current_event(
        explicit_current,
        canonical_current,
        display_frame,
    )
    explicit_pressure = _mapping(command.get("pressure_event")) or _mapping(
        source.get("pressure_event")
    )
    pressure_source = _reconcile_pressure_event(
        explicit_pressure,
        canonical_pressure,
        current_event,
        display_frame,
    )
    current_move = _sanitize_event(current_event, display_frame, pressure=False)
    pressure_event = _sanitize_event(pressure_source, display_frame, pressure=True)
    freshness = _freshness_contract(
        source,
        command,
        current_move,
        pressure_event,
        now_epoch=current_epoch,
    )
    overlays = _sanitize_overlays(source, display_frame)
    forecast = _forecast_contract(
        source,
        command,
        freshness,
        display_frame,
        overlays,
    )
    permission = _permission_contract(
        source,
        command,
        freshness,
        current_move,
        pressure_event,
        now_epoch=current_epoch,
    )
    history = _history_contract(source, current_move, pressure_event)
    tracking_summary = _mapping(source.get("tracking_summary"))
    tracking_flag = _explicit_bool(source.get("tracking_enabled"))
    if tracking_flag is True:
        tracking_state = (
            "LIVE"
            if freshness["state"] == "FRESH"
            else "DELAYED"
            if freshness["state"] == "STALE"
            else "UPDATING"
        )
    elif tracking_flag is False:
        tracking_state = "PAUSED"
    else:
        tracking_state = "WAITING"
    revision = max(
        _integer(source.get("state_version")),
        _integer(source.get("decision_version")),
        _integer(source.get("sequence_id")),
        _integer(display_frame),
        _integer(source.get("capture_count")),
    )
    market = {
        "symbol": _safe_public_text(
            tracking_summary.get("detected_market")
            or _mapping(source.get("latest_signal")).get("symbol")
            or _mapping(source.get("latest_signal")).get("pair")
            or source.get("market")
        ),
        "timeframe": _safe_public_text(
            tracking_summary.get("detected_timeframe")
            or _mapping(source.get("latest_signal")).get("timeframe"),
            "Unknown",
            limit=32,
        ),
    }
    observed_at = current_move.get("observed_at") or pressure_event.get("observed_at") or _epoch(
        tracking_summary.get("last_capture_epoch"), source.get("last_capture_epoch")
    )
    surface_base = (
        f"/v1/mobile/window-tracker/sessions/{encoded_session_id}/artifacts" if encoded_session_id else ""
    )
    surface_frame_query = (
        f"?frame_id={display_frame}"
        if surface_base and isinstance(display_frame, int) and display_frame > 0
        else ""
    )
    surface_available = bool(surface_base and surface_frame_query)
    full_window_url = (
        f"{surface_base}/latest-window{surface_frame_query}" if surface_available else ""
    )
    chart_focus_url = (
        f"{surface_base}/latest-chart{surface_frame_query}" if surface_available else ""
    )
    overlay_viewport = _overlay_viewport_contract(source, tracking_summary)
    surface_revisions = _surface_overlay_revision_contract(
        source,
        tracking_summary,
        market,
        overlay_viewport,
        overlays,
    )
    result: dict[str, object] = {
        "schema_version": OPERATOR_WORKSPACE_SCHEMA_VERSION,
        "session_id": session_id,
        "revision": revision,
        "market": market,
        "tracking": {
            "active": tracking_flag is True,
            "state": tracking_state,
            "updated_at": observed_at,
            "history_count": len(history),
        },
        "freshness": freshness,
        "current_move": current_move,
        "forecast": forecast,
        "permission": permission,
        "pressure_event": pressure_event,
        "surface": {
            "primary_url": full_window_url,
            "primary_space": "window",
            "fallback_url": chart_focus_url,
            "fallback_space": "chart",
            "focus_url": chart_focus_url,
            "overlay_viewport": overlay_viewport,
            "frame_id": display_frame,
            "updated_at": observed_at,
            **surface_revisions,
        },
        "overlays": overlays,
        "history": history,
    }
    assert tuple(result) == _TOP_LEVEL_KEYS
    return result


__all__ = ["OPERATOR_WORKSPACE_SCHEMA_VERSION", "build_operator_workspace_v1"]
