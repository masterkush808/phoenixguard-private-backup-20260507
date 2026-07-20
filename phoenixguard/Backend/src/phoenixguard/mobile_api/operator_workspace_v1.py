from __future__ import annotations

import hashlib
import json
import math
import re
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, cast
from urllib.parse import quote

from phoenixguard.decision.entry_window_policy_v3 import entry_location_guidance_v3
from phoenixguard.decision.order_positioning_v3 import (
    fit_order_positioning_reprojection_v3,
    order_positioning_plan_anchors_valid_v3,
    order_positioning_plan_geometry_valid_v3,
    reproject_order_positioning_bounds_v3,
)
from phoenixguard.tracking.tracking_episode_v3 import (
    TRACKING_EPISODE_HISTORY_LIMIT,
    TRACKING_EPISODE_HORIZON,
    build_tracking_order_positioning_candidate_v3,
    build_tracking_order_reference_map_v3,
    order_positioning_source_rows_v3,
    tracking_episode_readiness_v1,
    tracking_reprojection_anchors_v3,
)


OPERATOR_WORKSPACE_SCHEMA_VERSION = "PG_OPERATOR_WORKSPACE_V1"

_DIRECTIONAL_SIDES = frozenset({"BUY", "SELL"})
_NON_EPISODE_HISTORY_LIMIT = 24
_EPISODE_HISTORY_ROW_LIMIT = (
    TRACKING_EPISODE_HISTORY_LIMIT * (TRACKING_EPISODE_HORIZON + 1)
    + TRACKING_EPISODE_HORIZON
)
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

# Forecast provenance is required inside the runtime to validate and reproject
# one atomic study, but it is not part of the operator-facing explanation.
# Keep the geometry and human-readable belief/status fields while preventing
# provider, cache, detector, frame-lineage, and event-identity telemetry from
# crossing the public workspace boundary.
_PRIVATE_OPERATOR_FORECAST_FIELDS = frozenset(
    {
        "forecast_engine",
        "forecast_provider",
        "forecast_provider_status",
        "forecast_id",
        "forecast_revision",
        "belief_revision",
        "closed_candle_key",
        "closed_candle_sequence",
        "forecast_computed_frame_id",
        "source_forecast_frame_id",
        "geometry_projected_frame_id",
        "geometry_frame_match_verified",
        "geometry_reprojected_from_cache",
        "geometry_projection_provenance",
        "detector_coverage_rebase_applied",
        "cache_replaced_for_detector_coverage_rebase",
        "scene_feature_audit",
    }
)


def _strip_operator_forecast_telemetry(
    value: Mapping[str, object],
) -> dict[str, object]:
    return {
        key: nested
        for key, nested in value.items()
        if key not in _PRIVATE_OPERATOR_FORECAST_FIELDS
    }


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
    "BUY_LIMIT_ZONE": ("entry", "Lower-price buy area", "plan"),
    "SELL_LIMIT_ZONE": ("entry", "Higher-price sell area", "plan"),
    "BUY_STOP_ENTRY_ZONE": ("entry", "Upside break area", "plan"),
    "SELL_STOP_ENTRY_ZONE": ("entry", "Downside break area", "plan"),
    "PROTECTIVE_STOP_ZONE": ("risk", "Plan failure area", "plan"),
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
    "MODEL_COUNCIL_MARKER": ("plan", "Combined analysis", "plan"),
    "REGIME_MARKER": ("context", "Market phase", "structure"),
    "MARKET_PLAY_MARKER": ("setup", "Active setup", "plan"),
    "PRICE_LOCATION_MARKER": ("context", "Price location", "structure"),
    "TWO_CANDLE_STUDY": ("outlook", "Near-term candle read", "outlook"),
    "LSTM_STUDY": ("outlook", "12-step future blocks", "outlook"),
    "SCENE_FORECAST_STUDY": ("outlook", "Visual outlook", "outlook"),
    "ORDER_BLOCK": ("zone", "Reaction zone", "zones"),
    "FAIR_VALUE_GAP": ("zone", "Price imbalance", "zones"),
    "LIQUIDITY_POOL": ("zone", "Crowded price area", "zones"),
    "LIQUIDITY_SWEEP": ("movement", "Level sweep", "movement"),
    "MARKET_STRUCTURE_SHIFT": ("movement", "Structure change", "structure"),
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
    "order_positioning": "plan",
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
    "order_positioning": "order_positioning",
    "trendlines": "trendlines",
    "trigger_zones": "triggers",
    "target_zones": "targets",
    "invalidation": "invalidation",
    "active_council_decision": "council",
    "prediction_path": "prediction",
    "historical_replay": "history",
    "smart_money": "market_context",
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
    "BUY_LIMIT_ZONE": "order_positioning",
    "SELL_LIMIT_ZONE": "order_positioning",
    "BUY_STOP_ENTRY_ZONE": "order_positioning",
    "SELL_STOP_ENTRY_ZONE": "order_positioning",
    "PROTECTIVE_STOP_ZONE": "order_positioning",
    "SNIPER_ENTRY_BOX": "triggers",
    "RETEST_BOX": "triggers",
    "TRIGGER_BOX": "triggers",
    "TRIGGER_ZONE": "triggers",
    "TARGET_ZONE_BOX": "targets",
    "INVALIDATION_BOX": "invalidation",
    "INVALIDATION_ZONE": "invalidation",
    "TWO_CANDLE_STUDY": "two_candle",
    "LSTM_STUDY": "lstm",
    "SCENE_FORECAST_STUDY": "scene_forecaster",
    "MODEL_COUNCIL_MARKER": "council",
    "REGIME_MARKER": "council",
    "MARKET_PLAY_MARKER": "council",
    "PRICE_LOCATION_MARKER": "council",
    "PREDICTION_PATH": "prediction",
    "ANGLE_VECTOR": "prediction",
    "PROGRESSION_PATH": "history",
    "REPLAY_ENTRY": "history",
    "REPLAY_EXIT": "history",
    "ORDER_BLOCK": "market_context",
    "FAIR_VALUE_GAP": "market_context",
    "LIQUIDITY_POOL": "market_context",
    "LIQUIDITY_SWEEP": "market_context",
    "MARKET_STRUCTURE_SHIFT": "market_context",
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
    "order_positioning": "order_positioning",
    "trendlines": "trendlines",
    "triggers": "trigger_zones",
    "targets": "target_zones",
    "invalidation": "invalidation",
    "council": "active_council_decision",
    "two_candle": "active_council_decision",
    "lstm": "active_council_decision",
    "scene_forecaster": "prediction_path",
    "prediction": "prediction_path",
    "history": "historical_replay",
    "market_context": "market_context",
}

_PUBLIC_LAYER_ALIASES = {
    # The engine keeps its canonical layer vocabulary internally.  Public
    # operator payloads use neutral product language so a browser response does
    # not disclose the strategy taxonomy.
    "smart_money": "market_context",
}

# Stable public identifiers let the browser expose individual overlay toggles
# without publishing the proprietary canonical vocabulary.  The canonical
# type remains inside the engine and never crosses this operator boundary.
_PUBLIC_OVERLAY_KINDS: dict[str, tuple[str, str]] = {
    "CHART_BOUNDS": ("chart_area", "Chart area"),
    "CURRENT_CANDLE": ("current_price", "Current price"),
    "IMPULSE_BOX": ("strong_move", "Strong move"),
    "PULLBACK_BOX": ("pullback", "Pullback"),
    "RETEST_BOX": ("retest_area", "Retest area"),
    "CONTINUATION_BOX": ("continuation", "Continuation"),
    "SNIPER_ENTRY_BOX": ("precision_entry", "Precision entry area"),
    "TARGET_ZONE_BOX": ("target_area", "Target area"),
    "INVALIDATION_BOX": ("risk_limit", "Risk limit"),
    "SUPPLY_ZONE": ("higher_reaction", "Higher-price reaction area"),
    "DEMAND_ZONE": ("lower_reaction", "Lower-price reaction area"),
    "OPPOSING_FORCE": ("opposing_area", "Opposing area"),
    "BUY_LIMIT_ZONE": ("lower_price_buy_area", "Lower-price buy area"),
    "SELL_LIMIT_ZONE": ("higher_price_sell_area", "Higher-price sell area"),
    "BUY_STOP_ENTRY_ZONE": ("upside_break_area", "Upside break area"),
    "SELL_STOP_ENTRY_ZONE": ("downside_break_area", "Downside break area"),
    "PROTECTIVE_STOP_ZONE": ("plan_failure_area", "Plan failure area"),
    "SUPPORT_TRENDLINE": ("rising_support", "Rising support line"),
    "RESISTANCE_TRENDLINE": ("falling_resistance", "Falling resistance line"),
    "INNER_TRENDLINE": ("local_structure_line", "Local structure line"),
    "ANGLE_VECTOR": ("movement_angle", "Movement angle"),
    "PROGRESSION_PATH": ("observed_path", "Observed path"),
    "PREDICTION_PATH": ("possible_path", "Possible path"),
    "REPLAY_ENTRY": ("past_entry", "Past entry"),
    "REPLAY_EXIT": ("past_exit", "Past exit"),
    "MODEL_COUNCIL_MARKER": ("combined_analysis", "Combined analysis"),
    "REGIME_MARKER": ("market_phase", "Market phase"),
    "MARKET_PLAY_MARKER": ("active_setup", "Active setup"),
    "PRICE_LOCATION_MARKER": ("price_location", "Price location"),
    "TWO_CANDLE_STUDY": ("near_term_read", "Near-term candle read"),
    "LSTM_STUDY": ("future_blocks", "12-step future blocks"),
    "SCENE_FORECAST_STUDY": ("visual_outlook", "Visual outlook"),
    "ORDER_BLOCK": ("reaction_zone", "Reaction zone"),
    "FAIR_VALUE_GAP": ("price_imbalance", "Price imbalance"),
    "LIQUIDITY_POOL": ("crowded_price_area", "Crowded price area"),
    "LIQUIDITY_SWEEP": ("level_sweep", "Level sweep"),
    "MARKET_STRUCTURE_SHIFT": ("structure_change", "Structure change"),
    "ZONE": ("price_area", "Price area"),
    "TREND": ("trend", "Trend"),
    "ENTRY": ("entry_area", "Entry area"),
    "TARGET": ("target_area", "Target area"),
    "RISK": ("risk_limit", "Risk limit"),
    "MOVEMENT": ("price_movement", "Price movement"),
    "OUTLOOK": ("possible_path", "Possible path"),
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
        text = _text(value, "", limit=80)
        if not text:
            continue
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        parsed_epoch = parsed.timestamp()
        if parsed_epoch > 0.0 and math.isfinite(parsed_epoch):
            return round(parsed_epoch, 6)
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
    return _strip_operator_forecast_telemetry(result)


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
        block_rows = _forecast_candle_rows(overlay.get("forecast_candles"))
        if len(points) >= 2:
            delta_y = points[-1][1] - points[0][1]
        elif len(block_rows) == 12:
            first_close = _number(block_rows[0].get("close_y_norm"))
            last_close = _number(block_rows[-1].get("close_y_norm"))
            if first_close is None or last_close is None:
                continue
            delta_y = last_close - first_close
        else:
            continue
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
            f"The current 12-step outlook leans {movement_word}, but input quality is low "
            "and the quality checks found no reliable edge. It remains visible for context "
            "and never grants entry permission."
            if center_path_status == "LOW_CONFIDENCE"
            else f"The current 12-step outlook leans {movement_word}, but the quality checks "
            "found no reliable edge. It is observation only and never grants entry permission."
        )
    elif state == "STALE":
        movement_word = "upward" if direction == "BUY" else "downward"
        summary = (
            f"The last valid outlook pointed {movement_word}. "
            "It remains observation only while waiting for a new broker frame."
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
    return _strip_operator_forecast_telemetry(result)


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
    tracking_episode = _mapping(payload.get("tracking_episode"))
    episode_contract_present = bool(
        _text(tracking_episode.get("schema_version"), "").upper()
        == "PG_TRACKING_EPISODE_V1"
    )
    episode_state = _text(tracking_episode.get("state"), "IDLE").upper()
    # Entry permission is deliberately episode-gated.  Missing episode state is
    # not a legacy opt-in: it is an incomplete contract and therefore WAIT.
    episode_active = bool(
        episode_contract_present and episode_state in {"ARMING", "ACTIVE"}
    )
    episode_side = _episode_direction(tracking_episode)
    episode_has_direction = episode_side in _DIRECTIONAL_SIDES
    selected_side = _side(command.get("selected_side"))
    episode_direction_matches = bool(
        episode_active
        and episode_has_direction
        and selected_side == episode_side
    )
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
    # Once an episode has frozen a directional plan, operator guidance must stay
    # bound to that plan even if a later live proposal fluctuates to the other
    # side.  Permission can fail closed without silently rewriting where the
    # operator should wait for the saved plan.
    guidance_side = episode_side if episode_has_direction else selected_side
    entry_location, entry_guidance = _entry_location_contract(guidance_side)
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
        episode_direction_matches
        and command_fresh
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
    elif not episode_active:
        message = (
            "Wait. Start Tracking to anchor a plan before entry can be permitted."
            if not episode_contract_present or episode_state == "IDLE"
            else "Wait. This tracking episode is closed; start a new episode for another entry study."
        )
        next_condition = "Start Tracking after the chart is ready and the next plan is deliberately anchored."
    elif not episode_has_direction:
        message = "Wait. The saved tracking plan does not permit a directional entry."
        next_condition = "Keep observing this episode; start a new one only when a directional plan is deliberately anchored."
    elif not episode_direction_matches:
        message = "Wait. The current proposal differs from the saved tracking plan."
        next_condition = "Keep the saved plan unchanged and wait; start a new episode before studying the opposite direction."
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
        "expires_at": (
            expires_at
            if opportunity_open and episode_direction_matches
            else None
        ),
        "window_open": bool(opportunity_open and episode_direction_matches),
        "valid_for_seconds": (
            valid_for_seconds if episode_direction_matches else None
        ),
        "window_label": _window_label(
            is_open=bool(opportunity_open and episode_direction_matches),
            valid_for_seconds=(
                valid_for_seconds if episode_direction_matches else None
            ),
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


def _strict_normalized_rectangle(value: object) -> list[float]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return []
    if len(cast(Sequence[object], value)) != 4:
        return []
    bounds = _bounds(cast(Sequence[object], value))
    if (
        len(bounds) != 4
        or any(number < 0.0 or number > 1.0 for number in bounds)
        or bounds[2] <= bounds[0]
        or bounds[3] <= bounds[1]
    ):
        return []
    return bounds


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


_ORDER_POSITIONING_TYPES = frozenset(
    {
        "BUY_LIMIT_ZONE",
        "SELL_LIMIT_ZONE",
        "BUY_STOP_ENTRY_ZONE",
        "SELL_STOP_ENTRY_ZONE",
        "PROTECTIVE_STOP_ZONE",
    }
)

_ORDER_POSITIONING_SOURCE_TYPES = frozenset(
    {
        "DEMAND_ZONE",
        "SUPPLY_ZONE",
        "ORDER_BLOCK",
        "RETEST_BOX",
        "SUPPORT_TRENDLINE",
        "RESISTANCE_TRENDLINE",
    }
)

_ADAPTIVE_PLAN_OVERLAY_TYPES = frozenset(
    {
        "SNIPER_ENTRY_BOX",
        "RETEST_BOX",
        "TRIGGER_BOX",
        "TRIGGER_ZONE",
        "TARGET_ZONE_BOX",
        "INVALIDATION_BOX",
        "INVALIDATION_ZONE",
    }
)

_PUBLIC_POSITIONING_STATES = frozenset(
    {
        "WAITING",
        "STANDBY",
        "ARMED",
        "APPROACHING",
        "TOUCHED",
        "ACTIVATED",
        "RESPECTED",
        "FAVORED",
        "FAILED",
        "MISSED",
        "EXPIRED",
        "AMBIGUOUS",
        "INVALIDATED",
    }
)

_PUBLIC_POSITIONING_KINDS = (
    "lower_price_buy_area",
    "higher_price_sell_area",
    "upside_break_area",
    "downside_break_area",
    "plan_failure_area",
)
_POSITIONING_GEOMETRY_ROLE = "FORWARD_REACTION_WINDOW"
_POSITIONING_REACTION_WINDOW_ANCHOR = "LATEST_COMPLETED_CANDLE"


def _positioning_overlay_type(zone: Mapping[str, Any]) -> str:
    overlay_type = _text(
        zone.get("overlay_type") or zone.get("type"),
        "",
        limit=48,
    ).upper()
    if overlay_type in _ORDER_POSITIONING_TYPES:
        return overlay_type
    intent = _text(zone.get("intent"), "", limit=32).upper()
    order_kind = _text(zone.get("order_kind"), "", limit=32).upper()
    if intent in {"PROTECTIVE_STOP", "PROTECTIVE_INVALIDATION"}:
        return "PROTECTIVE_STOP_ZONE"
    return {
        ("ENTRY_LIMIT", "BUY_LIMIT"): "BUY_LIMIT_ZONE",
        ("ENTRY_LIMIT", "SELL_LIMIT"): "SELL_LIMIT_ZONE",
        ("ENTRY_STOP", "BUY_STOP"): "BUY_STOP_ENTRY_ZONE",
        ("ENTRY_STOP", "SELL_STOP"): "SELL_STOP_ENTRY_ZONE",
    }.get((intent, order_kind), "")


def _positioning_public_basis(
    zone: Mapping[str, Any],
    overlay_type: str,
    *,
    mode: str = "",
) -> str:
    _ = zone
    if mode == "REFERENCE":
        return {
            "BUY_LIMIT_ZONE": "Possible lower-price reaction area",
            "SELL_LIMIT_ZONE": "Possible higher-price reaction area",
            "BUY_STOP_ENTRY_ZONE": "Possible completed-candle upside confirmation",
            "SELL_STOP_ENTRY_ZONE": "Possible completed-candle downside confirmation",
        }.get(overlay_type, "Current chart reference")
    return {
        "BUY_LIMIT_ZONE": "Lower-price reaction area",
        "SELL_LIMIT_ZONE": "Higher-price reaction area",
        "BUY_STOP_ENTRY_ZONE": "Completed-candle upside confirmation",
        "SELL_STOP_ENTRY_ZONE": "Completed-candle downside confirmation",
        "PROTECTIVE_STOP_ZONE": "Original plan boundary",
    }.get(overlay_type, "Verified chart structure")


def _positioning_geometry_contract(
    row: Mapping[str, Any],
    *,
    required: bool,
) -> dict[str, str] | None:
    """Validate the only public current-reaction geometry provenance fields."""

    geometry_role = _text(row.get("geometry_role"), "", limit=40).upper()
    reaction_window_anchor = _text(
        row.get("reaction_window_anchor"),
        "",
        limit=40,
    ).upper()
    if not geometry_role and not reaction_window_anchor:
        return None if required else {}
    if (
        geometry_role != _POSITIONING_GEOMETRY_ROLE
        or reaction_window_anchor != _POSITIONING_REACTION_WINDOW_ANCHOR
    ):
        return None
    return {
        "geometry_role": geometry_role,
        "reaction_window_anchor": reaction_window_anchor,
    }


def _positioning_source_bounds_contract(
    row: Mapping[str, Any],
    geometry_contract: Mapping[str, str],
) -> dict[str, list[float]] | None:
    """Expose an origin only beside a proven forward-reaction rectangle."""

    if "source_bounds" not in row or row.get("source_bounds") is None:
        return {}
    if not geometry_contract:
        # Legacy frozen plans predate the forward-window contract. Keep their
        # live reprojected area, but do not expose an origin that cannot be
        # distinguished safely from the current reaction rectangle.
        return {}
    source_bounds = _strict_normalized_rectangle(row.get("source_bounds"))
    if (
        not source_bounds
        or geometry_contract.get("geometry_role")
        != _POSITIONING_GEOMETRY_ROLE
        or geometry_contract.get("reaction_window_anchor")
        != _POSITIONING_REACTION_WINDOW_ANCHOR
    ):
        return None
    return {"source_bounds": source_bounds}


def _bounded_positioning_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Keep one primary row per public kind and a paired protective boundary."""

    selected: list[Mapping[str, Any]] = []
    seen_types: set[str] = set()
    for row in rows:
        overlay_type = _text(row.get("type"), "", limit=48).upper()
        if overlay_type not in _ORDER_POSITIONING_TYPES or overlay_type in seen_types:
            continue
        seen_types.add(overlay_type)
        selected.append(row)

    entry_sides = {
        _side(row.get("side"))
        for row in selected
        if _text(row.get("type"), "", limit=48).upper()
        != "PROTECTIVE_STOP_ZONE"
    }
    output: list[Mapping[str, Any]] = []
    for row in selected:
        if _text(row.get("type"), "", limit=48).upper() == "PROTECTIVE_STOP_ZONE":
            protective_order_side = _side(row.get("side"))
            protected_thesis_side = {
                "BUY": "SELL",
                "SELL": "BUY",
            }.get(protective_order_side, "")
            if not protected_thesis_side or protected_thesis_side not in entry_sides:
                continue
        output.append(row)
    return output


def _current_positioning_overlay_row(
    row: Mapping[str, Any],
    *,
    index: int,
    display_frame_id: int | str,
    mode: str,
) -> Mapping[str, Any] | None:
    overlay_type = _positioning_overlay_type(row)
    # The producer's `bounds` is the current forward reaction window. Its
    # immutable `source_bounds` is separate optional history evidence: never
    # substitute it for the live rectangle, and publish it only after strict
    # normalized validation beside the named forward-window contract.
    bounds = _normalized_rectangle(
        row.get("bounds") or row.get("normalized_bounds")
    )
    geometry_contract = _positioning_geometry_contract(row, required=True)
    source_bounds_contract = (
        _positioning_source_bounds_contract(row, geometry_contract)
        if geometry_contract is not None
        else None
    )
    if (
        overlay_type not in _ORDER_POSITIONING_TYPES
        or len(bounds) != 4
        or geometry_contract is None
        or source_bounds_contract is None
        or (mode == "REFERENCE" and overlay_type == "PROTECTIVE_STOP_ZONE")
    ):
        return None
    reference = mode == "REFERENCE"
    source_id = _safe_identifier(
        (
            row.get("reference_id") or row.get("source_reference_id")
            if reference
            else row.get("zone_id") or row.get("order_zone_id")
        ),
        f"{mode.lower()}-order-area-{index + 1}",
    )
    digest_source: dict[str, object] = {
        "source": source_id,
        "kind": _PUBLIC_OVERLAY_KINDS[overlay_type][0],
        "frame": display_frame_id,
    }
    if reference:
        digest_source["mode"] = mode
    public_id = _stable_public_digest(digest_source, prefix="order_area")
    return {
        "overlay_id": public_id,
        "object_id": public_id,
        "track_id": public_id,
        "type": overlay_type,
        "layer": "order_positioning",
        "role": f"current_order_area_{mode.lower()}",
        "side": _side(row.get("side")),
        "label": _OVERLAY_PRESENTATION[overlay_type][1],
        "label_hidden": False,
        "bounds": bounds,
        "coordinate_space": "CHART_NORMALIZED",
        "coordinate_units": "normalized",
        "frame_id": display_frame_id,
        "confidence": _confidence(
            row.get("confidence"),
            row.get("source_confidence"),
            row.get("source_truth_score"),
        ),
        "lifecycle_state": "ACTIVE",
        "positioning_status": (
            "STANDBY"
            if not reference and overlay_type == "PROTECTIVE_STOP_ZONE"
            else "WAITING"
        ),
        "positioning_basis": _positioning_public_basis(
            row,
            overlay_type,
            mode=mode,
        ),
        "positioning_mode": mode,
        "immutable_geometry": False,
        "evidence_only": True,
        **geometry_contract,
        **source_bounds_contract,
    }


def _idle_order_positioning_rows(
    payload: Mapping[str, Any],
    *,
    display_frame_id: int | str | None,
) -> list[Mapping[str, Any]]:
    """Publish current validated candidates as mutable, evidence-only previews."""

    episode = _mapping(payload.get("tracking_episode"))
    if (
        _text(episode.get("state"), "IDLE", limit=24).upper() != "IDLE"
        or display_frame_id is None
    ):
        return []
    try:
        candidate = build_tracking_order_positioning_candidate_v3(payload)
    except Exception:
        # Preview generation is advisory. Any registry/model/runtime failure
        # must fail closed without breaking operator polling or exposing the
        # private rejection reason.
        return []
    if (
        _text(candidate.get("status"), "", limit=24).upper() != "READY"
        or not _frame_matches(candidate.get("frame_id"), display_frame_id)
        or _text(candidate.get("coordinate_mode"), "", limit=32).upper()
        != "CHART_NORMALIZED"
        or _normalized_rectangle(candidate.get("chart_bounds"))
        != [0.0, 0.0, 1.0, 1.0]
    ):
        return []

    rows = (
        _current_positioning_overlay_row(
            zone,
            index=index,
            display_frame_id=display_frame_id,
            mode="PREVIEW",
        )
        for index, zone in enumerate(_rows(candidate.get("candidate_zones"))[:24])
    )
    return _bounded_positioning_rows([row for row in rows if row is not None])


def _current_order_reference_rows(
    payload: Mapping[str, Any],
    *,
    display_frame_id: int | str | None,
) -> list[Mapping[str, Any]]:
    """Publish current observational locations without changing episode memory."""

    episode = _mapping(payload.get("tracking_episode"))
    if (
        _text(episode.get("state"), "IDLE", limit=24).upper()
        not in {"IDLE", "ACTIVE"}
        or display_frame_id is None
    ):
        return []
    try:
        reference_map = build_tracking_order_reference_map_v3(payload)
    except Exception:
        # References are optional operator evidence. Never let a registry or
        # geometry failure interrupt polling or expose a private failure.
        return []
    if (
        _text(reference_map.get("status"), "", limit=24).upper() != "READY"
        or not _frame_matches(reference_map.get("frame_id"), display_frame_id)
        or _text(
            reference_map.get("coordinate_mode"),
            "",
            limit=32,
        ).upper()
        != "CHART_NORMALIZED"
        or _normalized_rectangle(reference_map.get("chart_bounds"))
        != [0.0, 0.0, 1.0, 1.0]
    ):
        return []

    raw_rows = reference_map.get("rows")
    output: list[Mapping[str, Any]] = []
    for index, row in enumerate(_rows(raw_rows)[:24]):
        if (
            row.get("observational_only") is not True
            or _text(
                row.get("execution_authority"),
                "",
                limit=16,
            ).upper()
            != "NONE"
        ):
            continue
        public_row = _current_positioning_overlay_row(
            row,
            index=index,
            display_frame_id=display_frame_id,
            mode="REFERENCE",
        )
        if public_row is not None:
            output.append(public_row)
    return _bounded_positioning_rows(output)


def _merge_order_positioning_rows(
    primary_rows: Sequence[Mapping[str, Any]],
    references: Sequence[Mapping[str, Any]],
) -> list[Mapping[str, Any]]:
    """Expose one row per kind, preferring frozen/preview over references."""

    output = _bounded_positioning_rows(primary_rows)
    selected_types = {
        _text(row.get("type"), "", limit=48).upper() for row in output
    }
    for row in references:
        overlay_type = _text(row.get("type"), "", limit=48).upper()
        if overlay_type not in _ORDER_POSITIONING_TYPES or overlay_type in selected_types:
            continue
        selected_types.add(overlay_type)
        output.append(row)
    return _bounded_positioning_rows(output)


def _episode_order_positioning_rows(
    payload: Mapping[str, Any],
    *,
    display_frame_id: int | str | None,
) -> list[Mapping[str, Any]]:
    """Reproject one immutable plan through a globally proven candle transform."""

    episode = _mapping(payload.get("tracking_episode"))
    state = _text(episode.get("state"), "IDLE", limit=24).upper()
    episode_id = _safe_identifier(episode.get("episode_id"), "")
    if (
        not episode_id
        or state != "ACTIVE"
        or display_frame_id is None
    ):
        return []
    plan = _mapping(episode.get("positioning_plan"))
    zones = _rows(plan.get("zones") or plan.get("order_zones"))[:24]
    if (
        not zones
        or plan.get("frozen") is not True
        or not order_positioning_plan_geometry_valid_v3(plan)
        or not order_positioning_plan_anchors_valid_v3(plan)
    ):
        return []
    anchor = _mapping(episode.get("anchor"))
    plan_source_lock_id = _text(plan.get("broker_source_lock_id"))
    def normalize_market(value: Any) -> str:
        return re.sub(r"[^A-Z0-9]", "", _text(value).upper())
    if (
        not normalize_market(plan.get("market"))
        or normalize_market(plan.get("market"))
        != normalize_market(episode.get("pair"))
        or not _text(plan.get("timeframe"))
        or _text(plan.get("timeframe")).upper()
        != _text(episode.get("timeframe")).upper()
    ):
        return []
    current_lineages: set[tuple[str, str, str]] = set()
    for row in order_positioning_source_rows_v3(payload):
        row_frame = _frame_id(row.get("frame_id"), row.get("display_frame_id"))
        if (
            row_frame is None
            or not _frame_matches(row_frame, display_frame_id)
            or _text(row.get("schema_version")) != "PG_V3_OVERLAY_OBJECT_V1"
            or _coordinate_space(row) != "chart"
            or _text(row.get("type"), "", limit=48).upper()
            not in _ORDER_POSITIONING_SOURCE_TYPES
        ):
            continue
        current_lineages.add(
            (
                _text(row.get("sequence_id")),
                _text(row.get("chart_transform_id")),
                _text(row.get("broker_source_lock_id")),
            )
        )
    if len(current_lineages) != 1:
        return []
    current_sequence_id, current_transform_id, current_source_lock_id = next(
        iter(current_lineages)
    )
    if (
        not current_sequence_id
        or not current_transform_id
        or not current_source_lock_id
        or current_source_lock_id != plan_source_lock_id
    ):
        return []
    reprojection = fit_order_positioning_reprojection_v3(
        plan.get("reprojection_anchors"),
        tracking_reprojection_anchors_v3(payload),
    )
    if reprojection.get("status") != "PROVEN":
        return []

    output: list[Mapping[str, Any]] = []
    for index, zone in enumerate(zones):
        overlay_type = _positioning_overlay_type(zone)
        if overlay_type not in _ORDER_POSITIONING_TYPES:
            return []
        raw_bounds = zone.get("normalized_bounds") or zone.get("bounds")
        if not isinstance(raw_bounds, Sequence) or isinstance(
            raw_bounds,
            (str, bytes, bytearray),
        ):
            return []
        bounds = [_number(value) for value in cast(Sequence[object], raw_bounds)[:4]]
        if (
            len(bounds) != 4
            or any(value is None or not 0.0 <= value <= 1.0 for value in bounds)
        ):
            return []
        projected_bounds = reproject_order_positioning_bounds_v3(
            cast(list[float], bounds),
            reprojection,
        )
        if len(projected_bounds) != 4:
            return []
        left, top, right, bottom = projected_bounds
        if right <= left or bottom <= top:
            continue
        status = _text(zone.get("status"), "WAITING", limit=24).upper()
        if status not in _PUBLIC_POSITIONING_STATES:
            status = "AMBIGUOUS"
        source_id = _safe_identifier(
            zone.get("zone_id") or zone.get("order_zone_id"),
            f"{episode_id}-order-area-{index + 1}",
        )
        public_id = _stable_public_digest(
            {
                "source": source_id,
                "kind": _PUBLIC_OVERLAY_KINDS[overlay_type][0],
                "episode": episode_id,
            },
            prefix="order_area",
        )
        label = _OVERLAY_PRESENTATION[overlay_type][1]
        geometry_contract = _positioning_geometry_contract(zone, required=False)
        if geometry_contract is None:
            return []
        source_bounds_contract = _positioning_source_bounds_contract(
            zone,
            geometry_contract,
        )
        if source_bounds_contract is None:
            return []
        output.append(
            {
                "overlay_id": public_id,
                "object_id": public_id,
                "track_id": public_id,
                "type": overlay_type,
                "layer": "order_positioning",
                "role": "episode_frozen_order_area",
                "side": _side(zone.get("side")),
                "label": label,
                "label_hidden": False,
                "bounds": [left, top, right, bottom],
                "coordinate_space": "CHART_NORMALIZED",
                "coordinate_units": "normalized",
                "frame_id": display_frame_id,
                "confidence": _confidence(
                    zone.get("confidence"),
                    zone.get("source_confidence"),
                    zone.get("source_truth_score"),
                ),
                "lifecycle_state": "ACTIVE",
                "positioning_status": status,
                "positioning_basis": _positioning_public_basis(
                    zone,
                    overlay_type,
                ),
                "positioning_mode": "FROZEN",
                "immutable_geometry": True,
                "geometry_reprojected": True,
                "reprojection_method": "GLOBAL_CLOSED_CANDLE_AFFINE_V1",
                "evidence_only": True,
                "episode_id": episode_id,
                "origin_frame_id": _frame_id(
                    zone.get("origin_frame_id"),
                    anchor.get("frame_id"),
                ),
                **geometry_contract,
                **source_bounds_contract,
            }
        )
    return _bounded_positioning_rows(output)


def _forecast_lane_authorization_evidence(
    payload: Mapping[str, Any],
    overlay: Mapping[str, Any],
    *,
    scene_forecaster: bool,
) -> Mapping[str, Any]:
    tracking = _mapping(payload.get("tracking_summary"))
    signal = _mapping(payload.get("latest_signal"))
    result = _mapping(payload.get("model_council_result"))
    snapshot = _mapping(payload.get("forecast_snapshot_v3"))
    high_frequency = _mapping(snapshot.get("high_frequency_forecast"))
    key = "scene_forecast_contribution" if scene_forecaster else "lstm_contribution"
    candidate_sources = [
        (_mapping(payload.get(key)), payload),
        (_mapping(signal.get(key)), signal),
        (_mapping(tracking.get(key)), tracking),
        (_mapping(result.get(key)), result),
        (_mapping(snapshot.get(key)), snapshot),
        (_mapping(high_frequency.get(key)), high_frequency),
    ]
    matching = [
        (candidate, parent)
        for candidate, parent in candidate_sources
        if candidate
        and _is_scene_forecast_source(candidate) is scene_forecaster
    ]
    if not matching:
        return {}
    current_pair = _text(
        signal.get("market")
        or tracking.get("detected_market")
        or payload.get("market")
        or payload.get("symbol")
    ).upper()
    chart_timeframe = _text(
        signal.get("focus_timeframe")
        or tracking.get("detected_timeframe")
        or payload.get("timeframe")
    ).upper()
    high_frequency_timeframe = _text(
        signal.get("high_frequency_study_timeframe")
        or tracking.get("high_frequency_study_timeframe")
        or signal.get("configured_high_frequency_timeframe")
        or tracking.get("configured_high_frequency_timeframe")
    ).upper()
    current_timeframe = (
        chart_timeframe if scene_forecaster else high_frequency_timeframe
    )
    current_frame = _frame_id(
        overlay.get("frame_id"),
        payload.get("display_frame_id"),
        payload.get("frame_id"),
    )

    def canonical_identity(value: object) -> str:
        return "".join(
            character
            for character in _text(value).upper()
            if character.isalnum()
        )

    def candidate_identity(candidate: Mapping[str, Any]) -> tuple[str, str]:
        state = _mapping(candidate.get("closed_candle_identity_state"))
        pair = _text(candidate.get("pair") or state.get("pair")).upper()
        timeframe = _text(
            candidate.get("timeframe") or state.get("timeframe")
        ).upper()
        forecast_id = _text(candidate.get("forecast_id"))
        if forecast_id and (not pair or not timeframe):
            parts = forecast_id.split("|", 2)
            if len(parts) >= 2:
                pair = pair or parts[0].upper()
                timeframe = timeframe or parts[1].upper()
        return pair, timeframe

    lineage_matching: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []
    for candidate, parent in matching:
        explicit_candidate_frame = _frame_id(
            candidate.get("frame_id"),
            candidate.get("model_vote_frame_id"),
            candidate.get("source_frame_id"),
            candidate.get("forecast_computed_frame_id"),
        )
        parent_frame = _frame_id(
            parent.get("source_frame_id"),
            parent.get("frame_id"),
            parent.get("model_vote_frame_id"),
            parent.get("display_frame_id"),
        )
        candidate_frame = explicit_candidate_frame or parent_frame
        candidate_pair, candidate_timeframe = candidate_identity(candidate)
        if (
            current_frame is None
            or candidate_frame is None
            or not _frame_matches(candidate_frame, current_frame)
            or (
                parent_frame is not None
                and (
                    not _frame_matches(parent_frame, current_frame)
                    or (
                        explicit_candidate_frame is not None
                        and not _frame_matches(
                            parent_frame,
                            explicit_candidate_frame,
                        )
                    )
                )
            )
            or not current_pair
            or not candidate_pair
            or canonical_identity(candidate_pair)
            != canonical_identity(current_pair)
            or not current_timeframe
            or not candidate_timeframe
            or candidate_timeframe != current_timeframe
        ):
            continue
        lineage_matching.append((candidate, parent))
    if not lineage_matching:
        return {}
    evidence_keys = {
        "forecast_available",
        "artifact_production_gate_passed",
        "production_authorized",
        "selective_authorized",
        "trade_authorization_status",
        "geometry_frame_match_verified",
        "belief_state",
        "committed_side",
        "forecast_belief",
        "belief_update",
    }
    selected, parent = max(
        lineage_matching,
        key=lambda item: sum(key in item[0] for key in evidence_keys),
    )
    evidence = dict(selected)
    # A nested forecast cannot override a stale/diagnostic parent snapshot.
    for key in ("stale", "expired", "diagnostic_only", "forecast_suppressed"):
        if _explicit_bool(parent.get(key)) is True:
            evidence[key] = True
    if _explicit_bool(parent.get("fresh")) is False:
        evidence["fresh"] = False
    for key in ("market_identity_confirmed", "timeframe_identity_confirmed"):
        if _explicit_bool(parent.get(key)) is False:
            evidence[key] = False
    for key in ("freshness_status", "stale_status"):
        parent_status = _text(parent.get(key))
        if parent_status:
            evidence[key] = parent_status
    return evidence


def _forecast_overlay_authorized(
    overlay: Mapping[str, Any],
    evidence: Mapping[str, Any],
    *,
    scene_forecaster: bool,
) -> bool:
    if _text(overlay.get("trade_authorization_status")).upper() != "AUTHORIZED":
        return False
    if not evidence:
        return False
    if (
        _explicit_bool(evidence.get("fresh")) is not True
        or _explicit_bool(evidence.get("forecast_available")) is not True
    ):
        return False
    if (
        "fresh" in overlay
        and _explicit_bool(overlay.get("fresh")) is not True
    ) or (
        "forecast_available" in overlay
        and _explicit_bool(overlay.get("forecast_available")) is not True
    ):
        return False
    for key in ("market_identity_confirmed", "timeframe_identity_confirmed"):
        if _explicit_bool(evidence.get(key)) is not True:
            return False
        if key in overlay and _explicit_bool(overlay.get(key)) is not True:
            return False
    if any(
        _explicit_bool(evidence.get(key)) is True
        for key in ("stale", "expired", "diagnostic_only", "forecast_suppressed")
    ):
        return False
    if any(
        _explicit_bool(overlay.get(key)) is True
        for key in ("stale", "expired", "diagnostic_only", "forecast_suppressed")
    ):
        return False
    stale_statuses = {"STALE", "EXPIRED", "OUTDATED", "FAIL", "FAILED"}
    if any(
        _text(evidence.get(key)).upper() in stale_statuses
        for key in ("freshness_status", "stale_status")
    ):
        return False
    if any(
        _text(overlay.get(key)).upper() in stale_statuses
        for key in ("freshness_status", "stale_status")
    ):
        return False
    evidence_trade_status = _text(
        evidence.get("trade_authorization_status")
    ).upper()
    if evidence_trade_status and evidence_trade_status != "AUTHORIZED":
        return False
    for key in (
        "artifact_production_gate_passed",
        "production_authorized",
        "selective_authorized",
    ):
        if _explicit_bool(evidence.get(key)) is not True:
            return False
        # Explicit contradictory overlay evidence always fails closed. The
        # generated overlay need not duplicate these private gate details.
        if key in overlay and _explicit_bool(overlay.get(key)) is not True:
            return False
    if scene_forecaster:
        belief = _forecast_belief_contract(evidence, overlay)
        return bool(
            _explicit_bool(evidence.get("geometry_frame_match_verified")) is True
            and _text(belief.get("belief_state")).upper() == "STABLE"
            and _side(belief.get("committed_side")) in _DIRECTIONAL_SIDES
        )
    return bool(
        evidence_trade_status == "AUTHORIZED"
    )


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
    episode_state = _text(
        _mapping(payload.get("tracking_episode")).get("state"),
        "IDLE",
        limit=24,
    ).upper()
    waiting_for_new_frame = bool(
        _text(visual_observation.get("status")).upper() == "WAITING_FOR_NEW_FRAME"
        and _explicit_bool(visual_observation.get("new_visual_evidence")) is not True
    )
    frozen_positioning_rows = _episode_order_positioning_rows(
        payload,
        display_frame_id=display_frame_id,
    )
    preview_positioning_rows = _idle_order_positioning_rows(
        payload,
        display_frame_id=display_frame_id,
    )
    reference_positioning_rows = _current_order_reference_rows(
        payload,
        display_frame_id=display_frame_id,
    )
    primary_positioning_rows = (
        frozen_positioning_rows
        if episode_state == "ACTIVE"
        else preview_positioning_rows
        if episode_state == "IDLE"
        else []
    )
    positioning_rows = _merge_order_positioning_rows(
        primary_positioning_rows,
        reference_positioning_rows,
    )
    approved_reference_object_ids = {
        id(row) for row in reference_positioning_rows
    }
    source_rows = [
        *positioning_rows,
        *_overlay_rows(payload),
    ]
    for index, overlay in enumerate(source_rows[:256]):
        raw_type = _text(overlay.get("type") or overlay.get("overlay_type") or overlay.get("kind"), "").upper()
        layer = _text(overlay.get("layer"), "").lower()
        source_positioning_mode = _text(
            overlay.get("positioning_mode"),
            "",
            limit=16,
        ).upper()
        if raw_type in _ORDER_POSITIONING_TYPES:
            allowed_modes: set[str] = (
                {"FROZEN", "REFERENCE"}
                if episode_state == "ACTIVE"
                else {"PREVIEW", "REFERENCE"}
                if episode_state == "IDLE"
                else set()
            )
            if (
                source_positioning_mode not in allowed_modes
                or (
                    source_positioning_mode == "REFERENCE"
                    and id(overlay) not in approved_reference_object_ids
                )
            ):
                continue
        if frozen_positioning_rows and raw_type in _ADAPTIVE_PLAN_OVERLAY_TYPES:
            # Once tracking owns a frozen order-positioning plan, current-frame
            # trigger/target copies must not slide over it.  Their source
            # evidence remains available through structure, zones and replay.
            continue
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
        # A prediction-path token remains known to the internal contract for
        # replay/diagnostics, but it is never a public live drawing.  The
        # separately validated twelve future candle blocks are the only
        # forward visual published to the operator.
        if raw_type == "PREDICTION_PATH":
            continue
        public_type, label, default_group = presentation
        public_kind, public_kind_label = _PUBLIC_OVERLAY_KINDS.get(
            raw_type,
            (public_type, label),
        )
        group = _LAYER_GROUPS.get(layer, default_group)
        if group not in {"structure", "zones", "movement", "plan", "outlook", "history"}:
            continue
        family = _TYPE_FAMILIES.get(
            raw_type,
            _LAYER_FAMILIES.get(layer, _GROUP_FALLBACK_FAMILIES[group]),
        )
        public_layer = _PUBLIC_LAYER_ALIASES.get(
            layer,
            layer if layer in _LAYER_FAMILIES else _FAMILY_FALLBACK_LAYERS[family],
        )
        role = _text(overlay.get("role"), "").lower()
        scene_forecast_overlay = bool(
            raw_type == "SCENE_FORECAST_STUDY"
            or _is_scene_forecast_source(overlay)
        )
        if scene_forecast_overlay:
            # The scene forecaster and the production-gated LSTM are separate
            # public studies even though old wire payloads can share the
            # LSTM_STUDY compatibility type.
            family = "scene_forecaster"
        if scene_forecast_overlay and raw_type in {
            "LSTM_STUDY",
            "SCENE_FORECAST_STUDY",
        }:
            label = "Visual outlook"
            public_kind = "visual_outlook"
            public_kind_label = "Visual outlook"
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
            forecast_prefix = "Visual outlook" if scene_forecast_overlay else "Future blocks"
            if "stale_diagnostic" in role:
                lstm_forecast_status = "STALE"
                label = f"{forecast_prefix} · last saved {forecast_noun}"
            elif "no_edge" in role:
                lstm_forecast_status = "NO_EDGE"
                label = f"{forecast_prefix} · no reliable edge"
            elif "low_confidence" in role:
                lstm_forecast_status = "LOW_CONFIDENCE"
                label = f"{forecast_prefix} · low confidence"
            elif role.endswith("_diagnostic"):
                lstm_forecast_status = "DIAGNOSTIC"
                label = f"{forecast_prefix} · observation only"
            elif "authorized" in role and _forecast_overlay_authorized(
                overlay,
                _forecast_lane_authorization_evidence(
                    payload,
                    overlay,
                    scene_forecaster=scene_forecast_overlay,
                ),
                scene_forecaster=scene_forecast_overlay,
            ):
                lstm_forecast_status = "AUTHORIZED"
                label = f"{forecast_prefix} · current {forecast_noun}"
            else:
                # Legacy V3 path objects remain explicitly diagnostic.  An
                # absent risk status must never be promoted by presentation.
                lstm_forecast_status = "NO_EDGE"
                label = f"{forecast_prefix} · no reliable edge"
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
                label = f"{forecast_prefix} · change under review"
        if (
            raw_type == "LSTM_STUDY"
            and not scene_forecast_overlay
            and lstm_forecast_role
            and lstm_forecast_role != "composite"
        ):
            # LSTM is an object forecast in the operator product.  Boundary,
            # centre, and band paths remain available to internal validation,
            # but only the validated twelve-candle composite is public.
            continue
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
        source_object_id = _safe_identifier(
            overlay.get("overlay_id")
            or overlay.get("id")
            or overlay.get("object_id")
            or overlay.get("track_id"),
            f"overlay-{index + 1}",
        )
        dedup_key = (source_object_id, str(overlay_frame or ""))
        if dedup_key in seen:
            continue
        object_id = (
            _stable_public_digest(
                {
                    "source": source_object_id,
                    "kind": public_kind,
                },
                prefix="context",
            )
            if family == "market_context"
            else source_object_id
        )
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
            "kind": public_kind,
            "kind_label": public_kind_label,
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
        if raw_type in _ORDER_POSITIONING_TYPES:
            positioning_status = _text(
                overlay.get("positioning_status"),
                "WAITING",
                limit=24,
            ).upper()
            positioning_mode = _text(
                overlay.get("positioning_mode"),
                "",
                limit=16,
            ).upper()
            immutable_geometry = _explicit_bool(
                overlay.get("immutable_geometry")
            )
            geometry_contract = _positioning_geometry_contract(
                overlay,
                required=positioning_mode != "FROZEN",
            )
            source_bounds_contract = (
                _positioning_source_bounds_contract(overlay, geometry_contract)
                if geometry_contract is not None
                else None
            )
            if (
                positioning_mode not in {"PREVIEW", "FROZEN", "REFERENCE"}
                or immutable_geometry is None
                or (positioning_mode == "FROZEN") != immutable_geometry
                or geometry_contract is None
                or source_bounds_contract is None
                or (
                    positioning_mode == "REFERENCE"
                    and overlay.get("evidence_only") is not True
                )
            ):
                continue
            public_overlay.update(
                {
                    "positioning_status": (
                        positioning_status
                        if positioning_status in _PUBLIC_POSITIONING_STATES
                        else "AMBIGUOUS"
                    ),
                    "positioning_basis": _safe_public_text(
                        overlay.get("positioning_basis"),
                        "Saved chart structure",
                        limit=96,
                    ),
                    "positioning_mode": positioning_mode,
                    "immutable_geometry": immutable_geometry,
                    "evidence_only": True,
                    **geometry_contract,
                    **source_bounds_contract,
                }
            )
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
            if (
                family == "lstm"
                and not scene_forecast_overlay
                and lstm_forecast_role == "composite"
            ):
                public_overlay["line_points"] = []
                public_overlay["forecast_band_points"] = []
                public_overlay["forecast_scenarios"] = [
                    {
                        key: value
                        for key, value in scenario.items()
                        if key not in {"line_points", "forecast_path"}
                    }
                    for scenario in forecast_scenarios
                ]
                public_overlay["geometry_kind"] = "future_blocks"
        public_overlay.update(
            _overlay_identity_and_revisions(overlay, public_overlay)
        )
        output.append(_strip_operator_forecast_telemetry(public_overlay))
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


def _episode_direction(episode: Mapping[str, Any]) -> str:
    committed = _mapping(episode.get("committed_plan"))
    decision = _mapping(committed.get("decision"))
    council = _mapping(committed.get("model_council"))
    thesis = _mapping(committed.get("signal_thesis"))
    return _side(
        decision.get("execution_action"),
        decision.get("action"),
        decision.get("side"),
        council.get("final_side"),
        thesis.get("committed_side"),
        thesis.get("side"),
    )


def _episode_future_blocks(episode: Mapping[str, Any]) -> list[dict[str, object]]:
    """Project the frozen twelve-event sequence without exposing model data."""

    forecasts = _mapping(episode.get("baseline_forecasts"))
    sequence = _mapping(forecasts.get("lstm"))
    visual = _mapping(forecasts.get("scene"))
    sequence_path = sorted(
        _rows(sequence.get("forecast_path")),
        key=lambda row: _integer(row.get("step")),
    )
    visual_blocks = sorted(
        _rows(visual.get("forecast_candles")),
        key=lambda row: _integer(row.get("step")),
    )
    if len(visual_blocks) != 12:
        if len(sequence_path) != 12:
            return []
        explicit_x = [_number(row.get("x_norm")) for row in sequence_path]
        use_explicit_x = bool(
            all(value is not None and 0.0 <= value <= 1.0 for value in explicit_x)
            and all(
                cast(float, right) > cast(float, left)
                for left, right in zip(explicit_x, explicit_x[1:])
            )
        )
        # LSTM event rows are normalized prices, not chart Y coordinates.  If
        # the producer has no frozen X lane, place the 12 blocks in one stable
        # normalized forward lane.  This is block geometry only; the operator
        # frontend intentionally draws no LSTM trajectory line.
        fallback_start_x = 0.64
        fallback_step_x = 0.34 / 12.0
        output: list[dict[str, object]] = []
        for index, row in enumerate(sequence_path, start=1):
            open_price = _number(row.get("expected_open_norm"))
            high_price = _number(row.get("expected_high_norm"))
            low_price = _number(row.get("expected_low_norm"))
            close_price = _number(row.get("expected_close_norm"))
            if (
                open_price is None
                or high_price is None
                or low_price is None
                or close_price is None
                or any(
                    value < 0.0 or value > 1.0
                    for value in (open_price, high_price, low_price, close_price)
                )
            ):
                return []
            x_norm = (
                cast(float, explicit_x[index - 1])
                if use_explicit_x
                else fallback_start_x + fallback_step_x * index
            )
            movement_side = _side(
                row.get("movement_direction"),
                row.get("direction"),
            )
            body_bias = _side(
                row.get("candle_body_direction"),
                row.get("body_bias"),
            )
            output.append(
                {
                    "step": index,
                    "label": f"E{index}",
                    "x_norm": round(max(0.0, min(1.0, x_norm)), 6),
                    "open_y_norm": round(1.0 - open_price, 6),
                    "high_y_norm": round(1.0 - high_price, 6),
                    "low_y_norm": round(1.0 - low_price, 6),
                    "close_y_norm": round(1.0 - close_price, 6),
                    "movement_side": movement_side,
                    "body_bias": body_bias,
                    "direction_conflict": bool(
                        movement_side in _DIRECTIONAL_SIDES
                        and body_bias in _DIRECTIONAL_SIDES
                        and movement_side != body_bias
                    ),
                }
            )
        return output
    if len(sequence_path) != 12:
        output: list[dict[str, object]] = []
        for index, block in enumerate(visual_blocks, start=1):
            values = {
                key: _number(block.get(key))
                for key in (
                    "x_norm",
                    "open_y_norm",
                    "high_y_norm",
                    "low_y_norm",
                    "close_y_norm",
                )
            }
            if any(value is None for value in values.values()):
                return []
            output.append(
                {
                    "step": index,
                    "label": f"E{index}",
                    **{key: round(max(0.0, min(1.0, cast(float, value))), 6) for key, value in values.items()},
                    "movement_side": _side(block.get("movement_side"), block.get("side")),
                    "body_bias": _side(block.get("body_bias"), block.get("direction")),
                    "direction_conflict": _explicit_bool(block.get("direction_conflict")) is True,
                }
            )
        return output

    first_open = _number(sequence_path[0].get("expected_open_norm"))
    first_visual_open = _number(visual_blocks[0].get("open_y_norm"))
    if first_open is None or first_visual_open is None:
        return []
    vertical_offset = first_visual_open - (1.0 - first_open)

    def price_y(value: object) -> float | None:
        number = _number(value)
        if number is None:
            return None
        return round(max(0.0, min(1.0, 1.0 - number + vertical_offset)), 6)

    output = []
    for index, (row, visual_block) in enumerate(
        zip(sequence_path, visual_blocks, strict=True),
        start=1,
    ):
        x_norm = _number(visual_block.get("x_norm"))
        open_y = price_y(row.get("expected_open_norm"))
        high_y = price_y(row.get("expected_high_norm"))
        low_y = price_y(row.get("expected_low_norm"))
        close_y = price_y(row.get("expected_close_norm"))
        if x_norm is None or None in {open_y, high_y, low_y, close_y}:
            return []
        output.append(
            {
                "step": index,
                "label": f"E{index}",
                "x_norm": round(max(0.0, min(1.0, x_norm)), 6),
                "open_y_norm": open_y,
                "high_y_norm": high_y,
                "low_y_norm": low_y,
                "close_y_norm": close_y,
                "movement_side": _side(
                    row.get("movement_direction"),
                    row.get("direction"),
                ),
                "body_bias": _side(
                    row.get("candle_body_direction"),
                    row.get("body_bias"),
                ),
                "direction_conflict": bool(
                    _side(row.get("movement_direction")) in _DIRECTIONAL_SIDES
                    and _side(row.get("candle_body_direction")) in _DIRECTIONAL_SIDES
                    and _side(row.get("movement_direction"))
                    != _side(row.get("candle_body_direction"))
                ),
            }
        )
    return output


def _episode_owned_lstm_composite(
    episode: Mapping[str, object],
    *,
    display_frame_id: int | str | None,
) -> dict[str, object]:
    """Build the safe, block-only outlook owned by a frozen episode.

    A live contributor can legitimately disappear for a frame, but that must
    not erase the baseline the operator deliberately froze.  This public
    object contains only normalized render geometry and the twelve already
    sanitized candle blocks; it carries no provider, model, or source fields.
    """

    frame_id = _frame_id(display_frame_id)
    episode_id = _safe_identifier(episode.get("episode_id"), "")
    state = _text(episode.get("state"), "IDLE", limit=24).upper()
    raw_blocks = episode.get("future_blocks", [])
    blocks = (
        [
            dict(cast(Mapping[str, object], row))
            for row in cast(Sequence[object], raw_blocks)
            if isinstance(row, Mapping)
        ]
        if isinstance(raw_blocks, Sequence)
        and not isinstance(raw_blocks, (str, bytes, bytearray))
        else []
    )
    if not episode_id or state == "IDLE" or frame_id is None or len(blocks) != 12:
        return {}
    if [_integer(block.get("step")) for block in blocks] != list(range(1, 13)):
        return {}

    block_points: list[list[float]] = []
    all_x: list[float] = []
    all_y: list[float] = []
    for block in blocks:
        x_norm = _number(block.get("x_norm"))
        open_y = _number(block.get("open_y_norm"))
        high_y = _number(block.get("high_y_norm"))
        low_y = _number(block.get("low_y_norm"))
        close_y = _number(block.get("close_y_norm"))
        if (
            x_norm is None
            or open_y is None
            or high_y is None
            or low_y is None
            or close_y is None
            or any(
                value < 0.0 or value > 1.0
                for value in (x_norm, open_y, high_y, low_y, close_y)
            )
        ):
            return {}
        block_points.append([round(x_norm, 6), round(close_y, 6)])
        all_x.append(x_norm)
        all_y.extend((open_y, high_y, low_y, close_y))

    first_x = all_x[0]
    first_open_y = cast(float, _number(blocks[0].get("open_y_norm")))
    x_spacing = (
        max(0.001, all_x[1] - all_x[0])
        if len(all_x) > 1 and all_x[1] > all_x[0]
        else 0.01
    )
    anchor = [round(max(0.0, first_x - x_spacing), 6), round(first_open_y, 6)]
    line_points = [anchor, *block_points]
    bounds = [
        round(min(point[0] for point in line_points), 6),
        round(min(all_y), 6),
        round(max(point[0] for point in line_points), 6),
        round(max(all_y), 6),
    ]
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        return {}

    baseline = _mapping(episode.get("baseline"))
    direction = _side(baseline.get("direction"))
    object_id = _stable_public_digest(
        {"episode_id": episode_id, "role": "saved_future_blocks"},
        prefix="episode-outlook",
    )
    return {
        "id": object_id,
        "type": "outlook",
        "kind": "future_blocks",
        "kind_label": "12-step future blocks",
        "side": direction,
        "group": "outlook",
        "family": "lstm",
        "layer": "future_blocks",
        "label": "Saved future blocks",
        "label_hidden": True,
        "bounds": bounds,
        "points": [],
        # Bounds and anchor are derived from the immutable block geometry;
        # no public LSTM trajectory line is published.
        "line_points": [],
        "confidence": 0.0,
        "lifecycle": "current",
        "frame_id": frame_id,
        "coordinate_space": "chart",
        "coordinate_units": "normalized",
        "forecast_role": "composite",
        "forecast_status": "NO_EDGE",
        "forecast_authorized": False,
        "trade_authorization_status": "NO_EDGE",
        "forecast_quality_status": "NO_EDGE",
        "forecast_direction": direction,
        "body_bias": "NEUTRAL",
        "direction_conflict": False,
        "path_confidence_status": "UNAVAILABLE",
        "forecast_band_points": [],
        "forecast_candles": blocks,
        "forecast_scenarios": [],
        "forecast_anchor": {
            "x_norm": anchor[0],
            "y_norm": anchor[1],
        },
        "trajectory_mode": direction,
        "trajectory_mode_probability_calibrated": False,
        "forecast_coordinate_space": "chart",
        "forecast_coordinate_units": "normalized",
        "geometry_kind": "future_blocks",
        "interval": {"calibrated": False},
        "horizon_unit": "CANDLE_EVENTS",
        "clock_time_assumption": "NONE",
        "baseline_locked": True,
    }


def _episode_event_contract(
    event: Mapping[str, Any],
    *,
    episode_id: str,
) -> dict[str, object]:
    step = max(1, min(12, _integer(event.get("step") or event.get("event_index"))))
    predicted = _mapping(event.get("predicted_block"))
    actual = _mapping(event.get("actual_block"))
    observation_kind = _text(
        event.get("observation_kind"),
        "LIVE_CLOSE",
        limit=32,
    ).upper()
    predicted_side = _side(predicted.get("side"))
    actual_side = _side(actual.get("side"))
    agreement = event.get("direction_agreement")
    if not isinstance(agreement, bool):
        agreement = None
    result_available = event.get("result_available")
    if not isinstance(result_available, bool):
        result_available = observation_kind != "UNKNOWN_GAP"
    movement_word = "up" if actual_side == "BUY" else "down" if actual_side == "SELL" else "without a confirmed direction"
    if result_available is False:
        agreement = None
        summary = (
            f"E{step}: the chart observation was unavailable; the saved "
            "future block remains recorded but this result is unscored."
        )
    elif agreement is True:
        summary = f"E{step}: price moved {movement_word} and matched the saved future block."
    elif agreement is False:
        summary = f"E{step}: price moved {movement_word} and differed from the saved future block."
    else:
        summary = f"E{step}: the completed candle was recorded for the before-and-after study."
    raw_path_fit = _mapping(event.get("path_fit_by_id"))
    path_fit_by_id: dict[str, object] = {}
    for path_id in ("PATH_A", "PATH_B"):
        raw_fit = _mapping(raw_path_fit.get(path_id))
        status = _text(raw_fit.get("status"), "UNKNOWN", limit=16).upper()
        direction_match = raw_fit.get("direction_agreement")
        measured = status == "MEASURED"
        path_fit_by_id[path_id] = {
            "status": "MEASURED" if measured else "UNKNOWN",
            "direction_agreement": (
                direction_match if isinstance(direction_match, bool) and measured else None
            ),
        }
    raw_favored_path_id = _text(event.get("favored_path_id"), "", limit=12).upper()
    favored_path_id = (
        raw_favored_path_id
        if raw_favored_path_id in {"PATH_A", "PATH_B"}
        else ""
    )
    observed_close = _number(event.get("observed_close_level"))
    observed_close_level = (
        round(observed_close, 6)
        if observed_close is not None and 0.0 <= observed_close <= 1.0
        else None
    )
    raw_entry_progress = _mapping(event.get("entry_location_progress"))
    entry_progress_status = _text(
        raw_entry_progress.get("status"),
        "UNKNOWN",
        limit=16,
    ).upper()
    if entry_progress_status not in {
        "INSIDE",
        "APPROACHING",
        "MOVED_AWAY",
        "OUTSIDE",
        "CONFIRMED",
        "INVALIDATED",
        "UNKNOWN",
    }:
        entry_progress_status = "UNKNOWN"
    return {
        "id": _safe_identifier(
            event.get("event_id"),
            f"{episode_id or 'episode'}-e{step}",
        ),
        "episode_id": episode_id,
        "event_index": step,
        "observed_at": _epoch(event.get("observed_at")),
        "direction": actual_side,
        "predicted_direction": predicted_side,
        "agreement": agreement,
        "result_available": result_available,
        "path_fit_by_id": path_fit_by_id,
        "favored_path_id": favored_path_id,
        "observed_close_level": observed_close_level,
        "entry_location_progress": {
            "status": entry_progress_status,
        },
        "state": "HISTORICAL",
        "summary": summary,
    }


def _episode_observation_contract(episode: Mapping[str, Any]) -> dict[str, object]:
    observation = _mapping(episode.get("observation_state"))
    status = _text(observation.get("status"), "WAITING_FOR_BASELINE", limit=32).upper()
    if status not in {
        "WAITING_FOR_BASELINE",
        "WAITING_FOR_CLOSE",
        "LIVE",
        "REACQUIRING",
        "STOPPED",
    }:
        status = "REACQUIRING"
    unresolved_gap = _explicit_bool(observation.get("unresolved_gap")) is True
    if status == "REACQUIRING":
        message = (
            "Live capture is continuing, but candle continuity is being "
            "re-established before another result is counted."
        )
    elif status == "WAITING_FOR_CLOSE":
        message = "Live tracking is current and waiting for the next completed candle."
    elif status == "LIVE":
        message = "Live tracking is current; the latest completed candle was recorded."
    elif status == "STOPPED":
        message = "This tracking episode is no longer collecting new candles."
    else:
        message = "Start tracking after the chart baseline is ready."
    return {
        "schema_version": "PG_TRACKING_EPISODE_OBSERVATION_PUBLIC_V1",
        "status": status,
        "message": message,
        "unresolved_gap": unresolved_gap,
    }


def _episode_order_area_contract(episode: Mapping[str, Any]) -> dict[str, object]:
    plan = _mapping(episode.get("positioning_plan"))
    zones = [
        row
        for row in _rows(plan.get("zones"))[:12]
        if (
            _text(row.get("intent"), "", limit=32).upper()
            in {"ENTRY_LIMIT", "ENTRY_STOP", "PROTECTIVE_STOP"}
            or _text(
                row.get("overlay_type") or row.get("type"),
                "",
                limit=48,
            ).upper()
            in _ORDER_POSITIONING_TYPES
        )
    ]
    frozen = plan.get("frozen") is True and bool(zones)
    if not frozen:
        return {
            "status": "UNAVAILABLE",
            "count": 0,
            "message": (
                "No chart-verified order area was available at the starting candle. "
                "Tracking continues without inventing one."
            ),
        }
    statuses: dict[str, int] = {}
    for zone in zones:
        status = _text(zone.get("status"), "WAITING", limit=24).upper()
        if status not in _PUBLIC_POSITIONING_STATES:
            status = "AMBIGUOUS"
        statuses[status] = statuses.get(status, 0) + 1
    plan_status = _text(plan.get("status"), "TRACKING", limit=24).upper()
    return {
        "status": "COMPLETE" if plan_status == "COMPLETE" else "TRACKING",
        "count": len(zones),
        "message": (
            f"{len(zones)} order area{'s' if len(zones) != 1 else ''} were saved "
            "at the starting candle; their geometry remains fixed."
        ),
        "status_counts": statuses,
    }


def _visible_order_area_counts(
    overlays: Sequence[Mapping[str, object]],
) -> tuple[dict[str, int], dict[str, int]]:
    kind_counts: dict[str, int] = dict.fromkeys(_PUBLIC_POSITIONING_KINDS, 0)
    mode_counts = {"PREVIEW": 0, "FROZEN": 0, "REFERENCE": 0}
    for overlay in overlays:
        mode = _text(overlay.get("positioning_mode"), "", limit=16).upper()
        immutable = overlay.get("immutable_geometry")
        if overlay.get("family") != "order_positioning" or (
            mode not in mode_counts
            or immutable is not (mode == "FROZEN")
            or overlay.get("evidence_only") is not True
        ):
            continue
        kind = _text(overlay.get("kind"), "", limit=48)
        if kind in kind_counts:
            kind_counts[kind] += 1
            mode_counts[mode] += 1
    return kind_counts, mode_counts


def _order_area_contract(
    overlays: Sequence[Mapping[str, object]],
    *,
    episode_state: str,
    saved_contract: Mapping[str, object] | None = None,
) -> dict[str, object]:
    kind_counts, mode_counts = _visible_order_area_counts(overlays)
    active = episode_state == "ACTIVE"
    primary_mode = "FROZEN" if active else "PREVIEW"
    primary_count = mode_counts[primary_mode]
    reference_count = mode_counts["REFERENCE"]
    count = primary_count + reference_count
    if not count:
        if active:
            return {**(saved_contract or {}), "kind_counts": kind_counts}
        return {
            "status": "UNAVAILABLE",
            "count": 0,
            "message": "No chart-verified order area is available on this current frame.",
            "kind_counts": kind_counts,
        }

    if active and primary_count:
        status = _text(
            (saved_contract or {}).get("status"),
            "TRACKING",
            limit=24,
        ).upper()
        if status not in {"TRACKING", "COMPLETE"}:
            status = "TRACKING"
        message = (
            f"{primary_count} saved fixed order area"
            f"{'s' if primary_count != 1 else ''}"
        )
        message += (
            f" and {reference_count} current chart location reference"
            f"{'s are' if reference_count != 1 else ' is'} visible"
            if reference_count
            else " remain visible"
        )
        message += (
            ". The original tracking plan remains unchanged; entry permission "
            "remains separate."
        )
    elif primary_count:
        status = "PREVIEW"
        message = (
            f"{primary_count} current order area preview"
            f"{'s are' if primary_count != 1 else ' is'} aligned to this chart."
        )
        message += (
            f" {reference_count} distinct chart location reference"
            f"{'s are' if reference_count != 1 else ' is'} also visible; entry "
            "permission remains separate. Start Tracking to freeze only the "
            "validated plan."
            if reference_count
            else " Start Tracking to freeze the geometry."
        )
    else:
        status = "REFERENCE"
        message = (
            f"{reference_count} current chart location reference"
            f"{'s are' if reference_count != 1 else ' is'} visible for study. "
        )
        if active:
            message += "The original tracking plan remains unchanged; "
        message += "Entry permission remains separate."
    return {
        "status": status,
        "count": count,
        "message": message,
        "kind_counts": kind_counts,
    }


def _episode_path_comparison_contract(
    episode: Mapping[str, Any],
    observation: Mapping[str, object],
) -> dict[str, object]:
    raw_comparison = _mapping(episode.get("path_comparison"))
    public_paths: list[dict[str, object]] = []
    for expected_id, raw_path in zip(
        ("PATH_A", "PATH_B"),
        _rows(raw_comparison.get("paths"))[:2],
        strict=False,
    ):
        path_id = _text(raw_path.get("id"), "", limit=12).upper()
        if path_id != expected_id:
            return {}
        steps: list[dict[str, object]] = []
        for index, raw_step in enumerate(_rows(raw_path.get("steps"))[:12], start=1):
            step = _integer(raw_step.get("step"))
            open_level = _number(raw_step.get("open_level"))
            close_level = _number(raw_step.get("close_level"))
            if (
                step != index
                or open_level is None
                or close_level is None
                or not 0.0 <= open_level <= 1.0
                or not 0.0 <= close_level <= 1.0
            ):
                return {}
            steps.append(
                {
                    "step": step,
                    "open_level": round(open_level, 6),
                    "close_level": round(close_level, 6),
                    "direction": _side(raw_step.get("direction")),
                }
            )
        if len(steps) != 12:
            return {}
        raw_points = _point_pairs(raw_path.get("points"), limit=13)
        points = (
            raw_points
            if len(raw_points) == 13
            and all(0.0 <= point[0] <= 1.0 and 0.0 <= point[1] <= 1.0 for point in raw_points)
            else []
        )
        public_paths.append(
            {
                "id": path_id,
                "label": _safe_public_text(
                    raw_path.get("label"),
                    "Main forecast" if path_id == "PATH_A" else "Alternative forecast",
                    limit=40,
                ),
                "direction": _side(raw_path.get("direction")),
                "summary": _safe_public_text(
                    raw_path.get("summary"),
                    "Saved progression from the starting candle.",
                    limit=160,
                ),
                "points": points,
                "steps": steps,
            }
        )
    if len(public_paths) != 2:
        return {}
    verdict = _text(raw_comparison.get("verdict"), "WAITING", limit=32).upper()
    if verdict not in {
        "PATH_A",
        "PATH_B",
        "TOO_CLOSE",
        "WAITING",
        "NEITHER_PATH_FITS",
        "GEOMETRY_UNAVAILABLE",
        "PATHS_OVERLAP",
    }:
        verdict = "WAITING"
    raw_favored = _text(raw_comparison.get("favored_path_id"), "", limit=12).upper()
    favored_path_id = raw_favored if raw_favored == verdict and verdict in {"PATH_A", "PATH_B"} else ""
    thesis = _mapping(raw_comparison.get("entry_thesis"))
    thesis_status = _text(thesis.get("status"), "UNAVAILABLE", limit=16).upper()
    if thesis_status not in {"DIRECTIONAL", "NEUTRAL", "UNAVAILABLE"}:
        thesis_status = "UNAVAILABLE"
    observation_status = _text(observation.get("status"), "WAITING", limit=32).upper()
    continuity_state = (
        "LIVE"
        if observation_status == "LIVE"
        else "REACQUIRING"
        if observation_status == "REACQUIRING"
        else "STOPPED"
        if observation_status == "STOPPED"
        else "WAITING"
    )
    anchor = _mapping(raw_comparison.get("anchor"))
    anchor_close = _number(anchor.get("close_level"))
    forming = _mapping(raw_comparison.get("forming_at_start"))
    forming_open = _number(forming.get("open_level"))
    forming_current = _number(forming.get("current_level"))
    bias = _mapping(raw_comparison.get("forecast_bias"))
    bias_status = _text(bias.get("status"), "UNAVAILABLE", limit=16).upper()
    if bias_status not in {"DIRECTIONAL", "NEUTRAL", "UNAVAILABLE"}:
        bias_status = "UNAVAILABLE"
    permission = _mapping(raw_comparison.get("trade_permission"))
    permission_status = _text(permission.get("status"), "WAIT", limit=16).upper()
    if permission_status not in {"PERMITTED", "WAIT"}:
        permission_status = "WAIT"
    entry_location = _mapping(raw_comparison.get("entry_location"))
    entry_status = _text(
        entry_location.get("status"),
        "UNAVAILABLE",
        limit=20,
    ).upper()
    if entry_status not in {"TRACKING", "GUIDANCE_ONLY", "UNAVAILABLE"}:
        entry_status = "UNAVAILABLE"
    top_level = _number(entry_location.get("top_level"))
    bottom_level = _number(entry_location.get("bottom_level"))
    raw_progress = _mapping(entry_location.get("progress"))
    progress_status = _text(
        raw_progress.get("status"),
        "UNKNOWN",
        limit=16,
    ).upper()
    if progress_status not in {
        "INSIDE",
        "APPROACHING",
        "MOVED_AWAY",
        "OUTSIDE",
        "CONFIRMED",
        "INVALIDATED",
        "UNKNOWN",
    }:
        progress_status = "UNKNOWN"
    transform = _mapping(raw_comparison.get("transform_contract"))
    geometry_status = (
        "STABLE"
        if transform.get("status") == "LOCKED"
        and verdict != "GEOMETRY_UNAVAILABLE"
        else "UNAVAILABLE"
    )
    return {
        "schema_version": "PG_TRACKING_PATH_COMPARISON_PUBLIC_V1",
        "paths": public_paths,
        "verdict": verdict,
        "favored_path_id": favored_path_id,
        "verdict_summary": _safe_public_text(
            raw_comparison.get("verdict_summary"),
            "Waiting for confirmed candles to compare the two saved forecasts.",
            limit=200,
        ),
        "anchor": {
            "status": "CONFIRMED" if anchor.get("status") == "CONFIRMED" else "UNAVAILABLE",
            "label": "Latest completed candle",
            "direction": _side(anchor.get("direction")),
            "close_level": (
                round(anchor_close, 6)
                if anchor_close is not None and 0.0 <= anchor_close <= 1.0
                else None
            ),
        },
        "forming_at_start": {
            "status": "OBSERVED" if forming.get("status") == "OBSERVED" else "UNAVAILABLE",
            "label": "Candle forming when tracking started",
            "direction": _side(forming.get("direction")),
            "open_level": (
                round(forming_open, 6)
                if forming_open is not None and 0.0 <= forming_open <= 1.0
                else None
            ),
            "current_level": (
                round(forming_current, 6)
                if forming_current is not None and 0.0 <= forming_current <= 1.0
                else None
            ),
        },
        "forecast_bias": {
            "status": bias_status,
            "label": "Forecast bias at start",
            "summary": _safe_public_text(
                bias.get("summary"),
                "No forecast bias was available when tracking started.",
                limit=200,
            ),
            "direction": _side(bias.get("direction")),
        },
        "entry_thesis": {
            "status": thesis_status,
            "label": "Entry idea at start",
            "summary": _safe_public_text(
                thesis.get("summary"),
                "No entry idea was available when tracking started.",
                limit=200,
            ),
            "direction": _side(thesis.get("direction")),
        },
        "trade_permission": {
            "status": permission_status,
            "label": "Entry permission at start",
            "summary": _safe_public_text(
                permission.get("summary"),
                "Entry was not permitted when tracking started.",
                limit=200,
            ),
        },
        "entry_location": {
            "status": entry_status,
            "label": "Saved entry area",
            "summary": _safe_public_text(
                entry_location.get("summary"),
                "No verified entry area was available when tracking started.",
                limit=220,
            ),
            "direction": _side(entry_location.get("direction")),
            "preferred_location": _safe_public_text(
                entry_location.get("preferred_location"),
                "",
                limit=48,
            ),
            "top_level": (
                round(top_level, 6)
                if top_level is not None and 0.0 <= top_level <= 1.0
                else None
            ),
            "bottom_level": (
                round(bottom_level, 6)
                if bottom_level is not None and 0.0 <= bottom_level <= 1.0
                else None
            ),
            "progress": {
                "status": progress_status,
            },
        },
        "geometry": {
            "status": geometry_status,
            "summary": (
                "Normalized chart geometry is stable for path comparison."
                if geometry_status == "STABLE"
                else "Path comparison is waiting for stable normalized chart geometry."
            ),
        },
        "continuity": {
            "state": continuity_state,
            "summary": _safe_public_text(
                observation.get("message"),
                "Waiting for the next confirmed candle.",
                limit=200,
            ),
        },
    }


def _neutral_tracking_readiness_reason(value: object) -> str:
    """Translate engine readiness evidence into plain operator language."""

    reason = _safe_public_text(value, "", limit=180)
    normalized = reason.lower()
    if not normalized:
        return ""
    if "focus" in normalized:
        return "Lock the chart area before starting tracking."
    if "market identity" in normalized:
        return "Wait until the selected market is confirmed."
    if "timeframe identity" in normalized:
        return "Wait until the chart timeframe is confirmed."
    if "closed-candle" in normalized or "closed candle" in normalized:
        return "Wait for one completed candle before starting tracking."
    if "12-event" in normalized or "forecast baseline" in normalized:
        return "Wait until all 12 future blocks are ready."
    if "scene path" in normalized or "selected scene" in normalized:
        return "Wait until both forecast paths are ready."
    if "frame" in normalized or "bundle" in normalized or "publishing" in normalized:
        return "Wait for the current chart update to finish."
    return "The chart is still preparing the tracking baseline."


def _tracking_episode_contract(payload: Mapping[str, Any]) -> dict[str, object]:
    episode = _mapping(payload.get("tracking_episode"))
    state = _text(episode.get("state"), "IDLE", limit=24).upper()
    if state not in {"IDLE", "ARMING", "ACTIVE", "COMPLETED", "INVALIDATED", "STOPPED", "FAILED"}:
        state = "FAILED"
    episode_id = _safe_identifier(episode.get("episode_id"), "")
    horizon = 12
    cursor = max(0, min(horizon, _integer(episode.get("event_cursor"))))
    events = [
        _episode_event_contract(row, episode_id=episode_id)
        for row in _rows(episode.get("events"))[:horizon]
    ]
    if events:
        cursor = max(cursor, len(events))
    plan_side = _episode_direction(episode)
    pair = _safe_public_text(episode.get("pair"), "Market", limit=32)
    timeframe = _safe_public_text(episode.get("timeframe"), "", limit=16)
    market_label = " · ".join(part for part in (pair, timeframe) if part)
    plan_word = "up" if plan_side == "BUY" else "down" if plan_side == "SELL" else "wait"
    latest_event = events[-1] if events else {}
    observation = _episode_observation_contract(episode)
    if latest_event:
        current_title = f"Event {latest_event['event_index']} recorded"
        current_summary = str(latest_event["summary"])
    else:
        current_title = (
            "Re-establishing candle continuity"
            if observation["status"] == "REACQUIRING"
            else "Waiting for E1"
        )
        current_summary = str(observation["message"])
    terminal_reason = _text(episode.get("terminal_reason"), "", limit=80).upper()
    if state == "ACTIVE":
        summary = f"Tracking E{cursor} of {horizon}; the original plan remains anchored."
    elif state == "COMPLETED":
        summary = "All 12 events are complete and the before-and-after study is saved."
    elif state == "STOPPED":
        summary = "Tracking stopped and the recorded episode remains saved."
    elif state == "INVALIDATED":
        summary = "The episode closed because its original market context changed."
    elif state == "FAILED":
        summary = "The episode needs attention before another study can begin."
    else:
        summary = "Start Tracking to freeze one plan and compare the next 12 completed candles."
    readiness = _mapping(payload.get("tracking_episode_readiness"))
    if not readiness:
        try:
            readiness = tracking_episode_readiness_v1(payload)
        except Exception:
            readiness = {}
    readiness_reasons = [
        _neutral_tracking_readiness_reason(reason)
        for reason in cast(Sequence[object], readiness.get("reasons", []))
        if _neutral_tracking_readiness_reason(reason)
    ] if isinstance(readiness.get("reasons"), Sequence) and not isinstance(
        readiness.get("reasons"), (str, bytes, bytearray)
    ) else []
    has_episode = bool(episode_id and state != "IDLE")
    path_comparison = (
        _episode_path_comparison_contract(episode, observation)
        if has_episode
        else {}
    )
    return {
        "schema_version": "PG_TRACKING_EPISODE_PUBLIC_V1",
        "episode_id": episode_id,
        "state": state,
        "revision": max(0, _integer(episode.get("revision"))),
        "event_horizon": horizon,
        "event_cursor": cursor,
        "progress": {"completed": cursor, "total": horizon},
        "observation": observation,
        "path_comparison": path_comparison,
        "order_areas": _episode_order_area_contract(episode) if has_episode else {},
        "started_at": _safe_public_text(episode.get("started_at"), "", limit=48),
        "updated_at": _safe_public_text(episode.get("updated_at"), "", limit=48),
        "completed_at": _safe_public_text(
            episode.get("completed_at") or episode.get("stopped_at"),
            "",
            limit=48,
        ),
        "summary": summary,
        "ready": _explicit_bool(readiness.get("ready")) is True,
        "readiness_message": readiness_reasons[0] if readiness_reasons else "",
        "baseline": (
            {
                "title": f"{market_label or 'Market'} baseline",
                "summary": f"The original {plan_word} plan was frozen when tracking started.",
                "direction": plan_side,
            }
            if has_episode
            else {}
        ),
        "current": (
            {
                "title": current_title,
                "summary": current_summary,
                "direction": latest_event.get("direction", "NEUTRAL"),
            }
            if has_episode
            else {}
        ),
        "plan": (
            {
                "title": "Plan held steady",
                "summary": "The saved plan is comparison evidence; current Entry status remains the only permission to act.",
                "direction": plan_side,
            }
            if has_episode
            else {}
        ),
        "change_summary": current_summary if has_episode else "",
        "future_blocks": _episode_future_blocks(episode) if has_episode else [],
        "events": events,
        "terminal_reason": (
            "CONTEXT_CHANGED"
            if terminal_reason == "PAIR_OR_TIMEFRAME_CHANGED"
            else "COMPLETE"
            if terminal_reason == "EVENT_HORIZON_COMPLETE"
            else "STOPPED"
            if terminal_reason == "MANUAL_STOP" or state == "STOPPED"
            else ""
        ),
    }


def project_public_tracking_episode_v1(
    payload: Mapping[str, object],
) -> dict[str, object]:
    """Return the safe episode DTO used by both operator and control routes."""

    return _tracking_episode_contract(cast(Mapping[str, Any], payload))


def public_tracking_readiness_message_v1(value: object) -> str:
    """Return one neutral readiness message without engine terminology."""

    return _neutral_tracking_readiness_reason(value)


def _archived_episode_history_contract(value: Mapping[str, Any]) -> dict[str, object]:
    episode_id = _safe_identifier(value.get("episode_id"), "")
    if not episode_id:
        return {}
    cursor = max(0, min(12, _integer(value.get("event_cursor"))))
    observations = max(
        0,
        min(cursor, _integer(value.get("direction_observation_count"))),
    )
    agreements = max(
        0,
        min(observations, _integer(value.get("direction_agreement_count"))),
    )
    state = _text(value.get("state"), "COMPLETED", limit=24).upper()
    if state not in {"COMPLETED", "INVALIDATED", "STOPPED", "FAILED"}:
        state = "COMPLETED"
    if observations:
        result_summary = (
            f"Saved tracking study: {cursor} of 12 events recorded; "
            f"{agreements} of {observations} directional blocks matched."
        )
    else:
        result_summary = f"Saved tracking study: {cursor} of 12 events recorded."
    return {
        "id": f"{episode_id}-summary",
        "episode_id": episode_id,
        "event_index": cursor,
        "observed_at": _epoch(value.get("ended_at"), value.get("updated_at")),
        "direction": "NEUTRAL",
        "state": "ENDED" if state != "FAILED" else "STALE",
        "summary": result_summary,
        "frame_id": _frame_id(value.get("anchor_frame_id")),
    }


def _archived_episode_event_contracts(
    value: Mapping[str, Any],
) -> list[dict[str, object]]:
    """Project persisted episode events without reopening raw model blocks."""

    episode_id = _safe_identifier(value.get("episode_id"), "")
    if not episode_id:
        return []
    output: list[dict[str, object]] = []
    for index, event in enumerate(_rows(value.get("events"))[:12], start=1):
        step = max(1, min(12, _integer(event.get("step")) or index))
        predicted_side = _side(event.get("predicted_side"))
        actual_side = _side(event.get("actual_side"))
        agreement = event.get("direction_agreement")
        if not isinstance(agreement, bool):
            agreement = (
                predicted_side == actual_side
                if predicted_side in _DIRECTIONAL_SIDES
                and actual_side in _DIRECTIONAL_SIDES
                else None
            )
        movement_word = (
            "up"
            if actual_side == "BUY"
            else "down"
            if actual_side == "SELL"
            else "without a confirmed direction"
        )
        if agreement is True:
            summary = (
                f"E{step}: price moved {movement_word} and matched the saved future block."
            )
        elif agreement is False:
            summary = (
                f"E{step}: price moved {movement_word} and differed from the saved future block."
            )
        else:
            summary = (
                f"E{step}: the completed candle was recorded for the before-and-after study."
            )
        output.append(
            {
                "id": _safe_identifier(
                    event.get("event_id"),
                    f"{episode_id}-e{step}",
                ),
                "episode_id": episode_id,
                "event_index": step,
                "observed_at": _epoch(event.get("observed_at")),
                "direction": actual_side,
                "predicted_direction": predicted_side,
                "agreement": agreement,
                "state": "HISTORICAL",
                "summary": summary,
                "frame_id": _frame_id(event.get("frame_id")),
            }
        )
    return output


def _history_contract(
    payload: Mapping[str, Any],
    current_move: Mapping[str, object],
    pressure_event: Mapping[str, object],
) -> list[dict[str, object]]:
    history: list[dict[str, object]] = []
    active_episode = _tracking_episode_contract(payload)
    history.extend(
        cast(list[dict[str, object]], active_episode.get("events", []))
    )
    for row in _rows(payload.get("tracking_episode_history")):
        history.extend(_archived_episode_event_contracts(row))
        archive = _archived_episode_history_contract(row)
        if archive:
            history.append(archive)
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
            row.get("action"),
            row.get("execution_action"),
            _mapping(row.get("latest_signal")).get("side"),
        )
        observed_at = _epoch(
            source.get("observed_at"),
            source.get("observed_epoch"),
            source.get("ended_at"),
            row.get("observed_at"),
            row.get("captured_at"),
            row.get("timestamp"),
            row.get("published_epoch"),
            row.get("created_epoch"),
            row.get("last_capture_epoch"),
        )
        state = _event_state(source, None)
        history_state = "ENDED" if state == "ENDED" else "STALE" if state == "STALE" else "HISTORICAL"
        frame_id = _frame_id(source.get("frame_id"), row.get("frame_id"))
        episode_id = _safe_identifier(row.get("episode_id"), "")
        event_index = _integer(row.get("event_index") or row.get("event_cursor"))
        history_item: dict[str, object] = {
            "observed_at": observed_at,
            "direction": direction,
            "state": history_state,
            "summary": (
                _safe_public_text(
                    row.get("summary"),
                    _history_summary(direction, history_state),
                    limit=240,
                )
                if episode_id
                else _history_summary(direction, history_state)
            ),
            "frame_id": frame_id,
        }
        if episode_id:
            history_item.update(
                {
                    "id": _safe_identifier(
                        row.get("event_id") or row.get("id"),
                        "-".join(
                            part
                            for part in (
                                episode_id,
                                str(frame_id or "frame"),
                                str(event_index or 0),
                                str(int(observed_at or 0.0)),
                            )
                            if part
                        ),
                    ),
                    "episode_id": episode_id,
                    "event_index": event_index,
                }
            )
        history.append(history_item)

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

    deduplicated: dict[tuple[object, object, object, object, object], dict[str, object]] = {}
    for item in history:
        key = (
            item.get("id"),
            item.get("observed_at"),
            item.get("direction"),
            item.get("state"),
            item.get("frame_id"),
        )
        deduplicated[key] = item
    ordered = sorted(
        deduplicated.values(),
        key=lambda item: (_number(item.get("observed_at")) or 0.0, str(item.get("frame_id") or "")),
    )
    # The durable archive retains 24 episodes.  Each terminal episode can
    # contain twelve event rows plus one summary, and the currently active
    # episode can contribute twelve more events.  Preserve that complete,
    # already-bounded before/after record instead of silently flattening it to
    # only 24 rows.  Ordinary rolling context remains capped independently so
    # unrelated study rows cannot grow the public response without bound.
    episode_rows = [item for item in ordered if item.get("episode_id")]
    context_rows = [item for item in ordered if not item.get("episode_id")]
    retained = [
        *episode_rows[-_EPISODE_HISTORY_ROW_LIMIT:],
        *context_rows[-_NON_EPISODE_HISTORY_LIMIT:],
    ]
    return sorted(
        retained,
        key=lambda item: (
            _number(item.get("observed_at")) or 0.0,
            str(item.get("frame_id") or ""),
        ),
    )


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
    tracking_episode = _tracking_episode_contract(source)
    episode_state = tracking_episode.get("state")
    if episode_state in {"IDLE", "ACTIVE"}:
        tracking_episode = {
            **tracking_episode,
            "order_areas": _order_area_contract(
                overlays,
                episode_state=str(episode_state),
                saved_contract=_mapping(tracking_episode.get("order_areas")),
            ),
        }
    frozen_future_blocks = cast(
        list[dict[str, object]],
        tracking_episode.get("future_blocks", []),
    )
    if len(frozen_future_blocks) == 12 and tracking_episode.get("state") != "IDLE":
        overlays = [
            {
                **overlay,
                "forecast_candles": [dict(block) for block in frozen_future_blocks],
                "baseline_locked": True,
            }
            if overlay.get("family") == "lstm"
            and overlay.get("forecast_role") == "composite"
            else overlay
            for overlay in overlays
        ]
        has_current_lstm_composite = any(
            overlay.get("family") == "lstm"
            and overlay.get("forecast_role") == "composite"
            and overlay.get("lifecycle") == "current"
            and _frame_matches(overlay.get("frame_id"), display_frame)
            for overlay in overlays
        )
        if not has_current_lstm_composite:
            frozen_composite = _episode_owned_lstm_composite(
                tracking_episode,
                display_frame_id=display_frame,
            )
            if frozen_composite:
                overlays.append(frozen_composite)
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
        _integer(tracking_episode.get("revision")),
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
            "episode": tracking_episode,
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


__all__ = [
    "OPERATOR_WORKSPACE_SCHEMA_VERSION",
    "build_operator_workspace_v1",
    "project_public_tracking_episode_v1",
    "public_tracking_readiness_message_v1",
]
