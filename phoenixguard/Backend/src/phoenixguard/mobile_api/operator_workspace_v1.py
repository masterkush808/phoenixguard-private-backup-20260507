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
from phoenixguard.decision.order_positioning_evidence_v3 import (
    build_current_order_positioning_candidate_v3,
    build_current_order_reference_map_v3,
)


OPERATOR_WORKSPACE_SCHEMA_VERSION = "PG_OPERATOR_WORKSPACE_V1"

_DIRECTIONAL_SIDES = frozenset({"BUY", "SELL"})
_STUDY_HISTORY_LIMIT = 128
_RETRACEMENT_LEVEL_CATALOG: dict[str, dict[str, object]] = {
    "OTE_70_5": {
        "level_id": "OTE_70_5",
        "level_ratio": 0.705,
        "classification": "ICT_STYLE_OTE_REFERENCE",
        "label": "70.5% ICT-style OTE reference",
        "experimental": False,
        "user_defined": False,
        "standard_fibonacci": False,
    },
    "CUSTOM_71_8": {
        "level_id": "CUSTOM_71_8",
        "level_ratio": 0.718,
        "classification": "USER_DEFINED_EXPERIMENTAL_NONSTANDARD",
        "label": "71.8% custom experimental nonstandard retracement",
        "experimental": True,
        "user_defined": True,
        "standard_fibonacci": False,
    },
}
_RETRACEMENT_LEVEL_ALIASES = {
    "ICT_OTE_MIDPOINT_0_705": "OTE_70_5",
    "USER_DEFINED_EXPERIMENTAL_0_718": "CUSTOM_71_8",
}
_RETRACEMENT_GRAPH_OBSERVATION_LIMIT = 128
_RETRACEMENT_PARTITION_INPUT_LIMIT = 64
_RETRACEMENT_PARTITION_OUTPUT_LIMIT = 16
_RETRACEMENT_REGIME_BASIS = "CURRENT_STUDY_FRAME_AT_CONFLUENCE_OBSERVATION"
_TOP_LEVEL_KEYS = (
    "schema_version",
    "session_id",
    "revision",
    "market",
    "tracking",
    "freshness",
    "current_move",
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
    "REPLAY_ENTRY": ("entry", "Past entry", "history"),
    "REPLAY_EXIT": ("exit", "Past exit", "history"),
    "MODEL_COUNCIL_MARKER": ("plan", "Combined analysis", "plan"),
    "REGIME_MARKER": ("context", "Market phase", "structure"),
    "MARKET_PLAY_MARKER": ("setup", "Active setup", "plan"),
    "PRICE_LOCATION_MARKER": ("context", "Price location", "structure"),
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
    "MODEL_COUNCIL_MARKER": "council",
    "REGIME_MARKER": "council",
    "MARKET_PLAY_MARKER": "council",
    "PRICE_LOCATION_MARKER": "council",
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
    "PROGRESSION_PATH": ("observed_path", "Observed path"),
    "REPLAY_ENTRY": ("past_entry", "Past entry"),
    "REPLAY_EXIT": ("past_exit", "Past exit"),
    "MODEL_COUNCIL_MARKER": ("combined_analysis", "Combined analysis"),
    "REGIME_MARKER": ("market_phase", "Market phase"),
    "MARKET_PLAY_MARKER": ("active_setup", "Active setup"),
    "PRICE_LOCATION_MARKER": ("price_location", "Price location"),
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
    directional_command = selected_side in _DIRECTIONAL_SIDES
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
        directional_command
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
    elif not directional_command:
        message = "Wait. The current decision has not authorized a buy or sell direction."
        next_condition = "Wait for the current decision, live movement, and safety controls to agree on one direction."
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
            if opportunity_open and directional_command
            else None
        ),
        "window_open": bool(opportunity_open and directional_command),
        "valid_for_seconds": (
            valid_for_seconds if directional_command else None
        ),
        "window_label": _window_label(
            is_open=bool(opportunity_open and directional_command),
            valid_for_seconds=(
                valid_for_seconds if directional_command else None
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
        # Older plans can predate the current-evidence contract. Keep their
        # reprojected area, but do not expose an origin that cannot be
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


def _current_order_positioning_rows(
    payload: Mapping[str, Any],
    *,
    display_frame_id: int | str | None,
) -> list[Mapping[str, Any]]:
    """Publish current validated candidates as mutable, study-only evidence."""

    if display_frame_id is None:
        return []
    try:
        candidate = build_current_order_positioning_candidate_v3(payload)
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
            mode="CURRENT",
        )
        for index, zone in enumerate(_rows(candidate.get("candidate_zones"))[:24])
    )
    return _bounded_positioning_rows([row for row in rows if row is not None])


def _current_order_reference_rows(
    payload: Mapping[str, Any],
    *,
    display_frame_id: int | str | None,
) -> list[Mapping[str, Any]]:
    """Publish current observational locations without frozen-plan memory."""

    if display_frame_id is None:
        return []
    try:
        reference_map = build_current_order_reference_map_v3(payload)
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
    """Expose one row per kind, preferring current candidates over references."""

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


def _sanitize_overlays(payload: Mapping[str, Any], display_frame: object) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    display_frame_id = _frame_id(display_frame)
    chart_frame = _mapping(payload.get("chart_frame"))
    chart = _mapping(payload.get("chart"))
    artifacts = _mapping(payload.get("artifacts"))
    tracking_summary = _mapping(payload.get("tracking_summary"))
    latest_signal = _mapping(payload.get("latest_signal"))
    current_symbol = _text(
        payload.get("symbol")
        or latest_signal.get("market")
        or latest_signal.get("symbol")
        or tracking_summary.get("detected_market")
    ).upper()
    current_timeframe = _text(
        payload.get("timeframe")
        or latest_signal.get("focus_timeframe")
        or latest_signal.get("timeframe")
        or tracking_summary.get("detected_timeframe")
    ).upper()
    current_selector_fingerprint = _text(
        payload.get("market_selector_visual_fingerprint")
        or latest_signal.get("market_selector_visual_fingerprint")
        or tracking_summary.get("market_selector_visual_fingerprint")
    )
    current_identity_locked = bool(
        _text(payload.get("instrument_identity_status")).upper() == "LOCKED"
        and _explicit_bool(payload.get("market_identity_confirmed")) is True
        and _explicit_bool(payload.get("timeframe_identity_confirmed")) is True
        and bool(current_symbol)
        and bool(current_timeframe)
        and current_selector_fingerprint.startswith("selector_v2_")
    )
    enforce_instrument_identity_contract = any(
        key in payload
        for key in (
            "instrument_identity_status",
            "market_identity_confirmed",
            "timeframe_identity_confirmed",
            "market_selector_visual_fingerprint",
        )
    )

    def canonical_instrument_token(value: object) -> str:
        return "".join(character for character in _text(value).upper() if character.isalnum())

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
    chart_plane_bounds = _coordinate_plane_bounds(
        scene_graph.get("chart_region_chart_bounds"),
        chart_dimensions,
    )
    visual_observation = _mapping(payload.get("visual_observation_v3"))
    waiting_for_new_frame = bool(
        _text(visual_observation.get("status")).upper() == "WAITING_FOR_NEW_FRAME"
        and _explicit_bool(visual_observation.get("new_visual_evidence")) is not True
    )
    current_positioning_rows = _current_order_positioning_rows(
        payload,
        display_frame_id=display_frame_id,
    )
    reference_positioning_rows = _current_order_reference_rows(
        payload,
        display_frame_id=display_frame_id,
    )
    positioning_rows = _merge_order_positioning_rows(
        current_positioning_rows,
        reference_positioning_rows,
    )
    trusted_positioning_object_ids = {id(row) for row in positioning_rows}
    approved_reference_object_ids = {
        id(row) for row in reference_positioning_rows
    }
    source_rows = [
        *positioning_rows,
        *_overlay_rows(payload),
    ]
    for index, overlay in enumerate(source_rows[:256]):
        source_object_id = id(overlay)
        raw_type = _text(overlay.get("type") or overlay.get("overlay_type") or overlay.get("kind"), "").upper()
        layer = _text(overlay.get("layer"), "").lower()
        trusted_positioning_row = bool(
            source_object_id in trusted_positioning_object_ids
            and raw_type in _ORDER_POSITIONING_TYPES
        )
        overlay_symbol = _text(
            overlay.get("symbol") or overlay.get("pair") or overlay.get("market")
        ).upper()
        overlay_timeframe = _text(overlay.get("timeframe") or overlay.get("tf")).upper()
        overlay_selector_fingerprint = _text(
            overlay.get("market_selector_visual_fingerprint")
        )
        overlay_identity_locked = _text(
            overlay.get("instrument_identity_status")
        ).upper() == "LOCKED"
        positioning_identity_mismatch = bool(
            (overlay_symbol and canonical_instrument_token(overlay_symbol) != canonical_instrument_token(current_symbol))
            or (overlay_timeframe and overlay_timeframe != current_timeframe)
            or (
                overlay_selector_fingerprint
                and current_selector_fingerprint
                and overlay_selector_fingerprint != current_selector_fingerprint
            )
        )
        if (
            trusted_positioning_row
            and current_identity_locked
            and not positioning_identity_mismatch
        ):
            overlay = dict(overlay)
            overlay.update(
                {
                    "symbol": current_symbol,
                    "timeframe": current_timeframe,
                    "market_selector_visual_fingerprint": current_selector_fingerprint,
                    "instrument_identity_status": "LOCKED",
                }
            )
            overlay_symbol = current_symbol
            overlay_timeframe = current_timeframe
            overlay_selector_fingerprint = current_selector_fingerprint
            overlay_identity_locked = True
        if enforce_instrument_identity_contract and not (
            current_identity_locked
            and overlay_identity_locked
            and canonical_instrument_token(overlay_symbol)
            == canonical_instrument_token(current_symbol)
            and overlay_timeframe == current_timeframe
            and (
                not current_selector_fingerprint
                or overlay_selector_fingerprint == current_selector_fingerprint
            )
        ):
            continue
        source_positioning_mode = _text(
            overlay.get("positioning_mode"),
            "",
            limit=16,
        ).upper()
        if raw_type in _ORDER_POSITIONING_TYPES:
            allowed_modes: set[str] = {"CURRENT", "REFERENCE"}
            if (
                source_positioning_mode not in allowed_modes
                or (
                    source_positioning_mode == "REFERENCE"
                    and source_object_id not in approved_reference_object_ids
                )
            ):
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
        # Forward-route studies are retired from the public V3 workspace.
        if raw_type in {
            "PREDICTION_PATH",
            "ANGLE_VECTOR",
            "OUTLOOK",
            "TWO_CANDLE_STUDY",
            "LSTM_STUDY",
            "SCENE_FORECAST_STUDY",
        }:
            continue
        presentation = _OVERLAY_PRESENTATION.get(raw_type)
        if presentation is None:
            continue
        public_type, label, default_group = presentation
        public_kind, public_kind_label = _PUBLIC_OVERLAY_KINDS.get(
            raw_type,
            (public_type, label),
        )
        group = _LAYER_GROUPS.get(layer, default_group)
        if group not in {"structure", "zones", "movement", "plan", "history"}:
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
            "symbol": overlay_symbol,
            "timeframe": overlay_timeframe,
            "market_selector_visual_fingerprint": overlay_selector_fingerprint,
            "instrument_identity_status": (
                "LOCKED" if overlay_identity_locked else "UNPROVEN"
            ),
            "anchor_quality": _mapping(overlay.get("anchor_quality")),
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
                required=True,
            )
            source_bounds_contract = (
                _positioning_source_bounds_contract(overlay, geometry_contract)
                if geometry_contract is not None
                else None
            )
            if (
                positioning_mode not in {"CURRENT", "REFERENCE"}
                or immutable_geometry is not False
                or geometry_contract is None
                or source_bounds_contract is None
                or overlay.get("evidence_only") is not True
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
                        "Current chart structure",
                        limit=96,
                    ),
                    "positioning_mode": positioning_mode,
                    "immutable_geometry": immutable_geometry,
                    "evidence_only": True,
                    **geometry_contract,
                    **source_bounds_contract,
                }
            )
        public_overlay.update(
            _overlay_identity_and_revisions(overlay, public_overlay)
        )
        output.append(public_overlay)
    return output


def _continuous_study_history_summary(study: Mapping[str, object]) -> str:
    """Describe one automatic closed-candle study without decision jargon."""

    regression = _mapping(study.get("regression"))
    behavior = _mapping(study.get("behavior"))
    current_state = _mapping(behavior.get("current_state"))
    directional = _mapping(study.get("directional_read"))

    def direction_word(value: object) -> str:
        side = _side(value)
        return "up" if side == "BUY" else "down" if side == "SELL" else "sideways"

    major = _mapping(regression.get("major_trend"))
    inner = _mapping(regression.get("inner_trend"))
    state = _safe_public_text(current_state.get("state"), "unknown", limit=32)
    candles = _integer(current_state.get("candle_count"))
    duration = _integer(current_state.get("duration_seconds"))
    duration_copy = f", {duration}s" if duration > 0 else ""
    return (
        f"Major trend {direction_word(major.get('side'))}; "
        f"inner trend {direction_word(inner.get('side'))}; "
        f"{state.replace('_', ' ').lower()} for {candles} "
        f"candle{'' if candles == 1 else 's'}{duration_copy}; "
        f"regression read {direction_word(directional.get('side'))}."
    )


def _history_contract(
    payload: Mapping[str, Any],
) -> list[dict[str, object]]:
    """Return bounded automatic closed-candle studies in chronological order."""

    tracking_summary = _mapping(payload.get("tracking_summary"))
    latest_signal = _mapping(payload.get("latest_signal"))
    current_study = _market_study_contract(
        tracking_summary.get("market_study_v3")
        or latest_signal.get("market_study_v3")
    )
    current_symbol = _text(current_study.get("symbol"), "").upper()
    current_timeframe = _text(current_study.get("timeframe"), "").upper()
    if not current_symbol or not current_timeframe:
        return []

    history: list[dict[str, object]] = []
    raw_history = _rows(payload.get("recent_studies")) + _rows(payload.get("history"))
    for row in raw_history:
        command = _mapping(row.get("decision_command_center"))
        event = _mapping(command.get("current_movement")) or _mapping(row.get("current_movement"))
        pressure = _mapping(command.get("pressure_event")) or _mapping(row.get("pressure_event"))
        source = event or pressure or row
        raw_study = (
            row.get("market_study_v3")
            or _mapping(row.get("tracking_summary")).get("market_study_v3")
            or _mapping(row.get("latest_signal")).get("market_study_v3")
        )
        study = _market_study_contract(raw_study)
        if not study:
            continue
        if (
            _text(study.get("status"), "").upper() != "STUDIED"
            or not _text(study.get("closed_candle_key"), "")
        ):
            continue
        if (
            _text(study.get("symbol"), "").upper() != current_symbol
            or _text(study.get("timeframe"), "").upper()
            != current_timeframe
        ):
            continue
        directional = _mapping(study.get("directional_read"))
        direction = _side(
            directional.get("side"),
            source.get("direction"),
            source.get("side"),
            row.get("side"),
            row.get("action"),
            row.get("execution_action"),
        )
        observed_at = _epoch(
            source.get("observed_at"),
            source.get("observed_epoch"),
            source.get("ended_at"),
            row.get("observed_at"),
            row.get("captured_at"),
            row.get("timestamp"),
            row.get("published_epoch"),
            row.get("last_capture_epoch"),
        )
        frame_id = _frame_id(source.get("frame_id"), row.get("frame_id"))
        closed_candle_key = _safe_identifier(study.get("closed_candle_key"), "")
        history_item: dict[str, object] = {
            "id": closed_candle_key or _safe_identifier(
                row.get("id"),
                f"study-{frame_id or 'frame'}-{int(observed_at or 0.0)}",
            ),
            "observed_at": observed_at,
            "direction": direction,
            "state": "HISTORICAL",
            "summary": _continuous_study_history_summary(study),
            "frame_id": frame_id,
        }
        regression = _mapping(study.get("regression"))
        history_item.update(
            {
                "closed_candle_sequence": _integer(study.get("closed_candle_sequence")),
                "closed_candle_key": closed_candle_key,
                "market_study_v3": study,
                "major_trend": _mapping(regression.get("major_trend")),
                "inner_trend": _mapping(regression.get("inner_trend")),
                "regression_read": directional,
                "behavior": _mapping(study.get("behavior")),
            }
        )
        history.append(history_item)

    deduplicated: dict[tuple[object, object, object], dict[str, object]] = {}
    for item in history:
        key = (item.get("id"), item.get("observed_at"), item.get("frame_id"))
        deduplicated[key] = item
    ordered = sorted(
        deduplicated.values(),
        key=lambda item: (
            _number(item.get("observed_at")) or 0.0,
            _integer(item.get("closed_candle_sequence")),
            str(item.get("frame_id") or ""),
        ),
    )
    return ordered[-_STUDY_HISTORY_LIMIT:]


def _study_trend_contract(value: object) -> dict[str, object]:
    row = _mapping(value)
    return {
        "side": _side(row.get("side"), row.get("direction"), row.get("label")),
        "label": _safe_public_text(row.get("label"), "Unknown", limit=32),
        "slope": round(_number(row.get("slope", row.get("normalized_slope"))) or 0.0, 8),
        "confidence": _confidence(row.get("confidence"), row.get("strength")) or 0.0,
        "window_candles": _integer(row.get("window_candles"), row.get("candle_count")),
    }


def _public_count_map(value: object, *, limit: int = 10) -> dict[str, int]:
    source = _mapping(value)
    rows = sorted(
        (
            (_safe_public_text(key, "Unknown", limit=64), _integer(count))
            for key, count in source.items()
        ),
        key=lambda item: (-item[1], item[0]),
    )
    return dict(rows[:limit])


def _canonical_retracement_level(value: object) -> dict[str, object]:
    """Return one fixed public definition for the two studied level ids."""

    source = _mapping(value)
    raw_level_id = str(source.get("level_id") or "").strip().upper()
    level_id = _RETRACEMENT_LEVEL_ALIASES.get(raw_level_id, raw_level_id)
    if level_id not in _RETRACEMENT_LEVEL_CATALOG:
        ratio = _number(source.get("level_ratio"))
        if ratio is not None:
            for candidate_id, candidate in _RETRACEMENT_LEVEL_CATALOG.items():
                candidate_ratio = _number(candidate["level_ratio"])
                if candidate_ratio is not None and abs(ratio - candidate_ratio) <= 1e-9:
                    level_id = candidate_id
                    break
    catalog = _RETRACEMENT_LEVEL_CATALOG.get(level_id)
    return dict(catalog) if catalog else {}


def _safe_retracement_count_map(
    value: object,
    *,
    limit: int = 8,
    allowed_keys: frozenset[str] | None = None,
) -> dict[str, int]:
    source = _mapping(value)
    rows: list[tuple[str, int]] = []
    for raw_key, raw_count in source.items():
        if isinstance(raw_count, bool):
            continue
        key = _safe_public_text(raw_key, "", limit=64).upper()
        if not key or (allowed_keys is not None and key not in allowed_keys):
            continue
        rows.append((key, _integer(raw_count)))
    rows.sort(key=lambda item: (-item[1], item[0]))
    return dict(rows[:limit])


def retracement_graph_contract_v3(value: object) -> dict[str, object]:
    """Strip raw geometry/identities and retain bounded per-level support."""

    source = _mapping(value)
    if (
        source.get("study_only") is not True
        or source.get("observation_only") is not True
        or source.get("execution_authority") is not False
    ):
        return {}

    catalog_ids: set[str] = set()
    raw_catalog = source.get("level_catalog")
    catalog_rows = (
        _rows(raw_catalog)
        if not isinstance(raw_catalog, Mapping)
        else [
            {"level_id": key, **dict(_mapping(row))}
            for key, row in cast(Mapping[object, object], raw_catalog).items()
        ]
    )
    for raw_level in catalog_rows[:8]:
        canonical = _canonical_retracement_level(raw_level)
        if canonical:
            catalog_ids.add(str(canonical["level_id"]))

    support_by_level: dict[str, int] = {}
    compact_support = _rows(source.get("level_support"))[:2]
    if compact_support:
        for row in compact_support:
            canonical = _canonical_retracement_level(row)
            if not canonical:
                continue
            level_id = str(canonical["level_id"])
            catalog_ids.add(level_id)
            support_by_level[level_id] = _integer(
                row.get("completed_observation_count"),
                row.get("graph_support"),
                row.get("support"),
            )
    else:
        for row in _rows(source.get("observations"))[
            :_RETRACEMENT_GRAPH_OBSERVATION_LIMIT
        ]:
            if (
                str(row.get("status") or "").strip().upper() != "COMPLETED"
                or row.get("identity_stable") is not True
                or row.get("observational_confluence") is not True
                or row.get("causal") is not False
            ):
                continue
            canonical = _canonical_retracement_level(row)
            if not canonical:
                continue
            level_id = str(canonical["level_id"])
            catalog_ids.add(level_id)
            support_by_level[level_id] = support_by_level.get(level_id, 0) + 1

    level_support: list[dict[str, object]] = []
    for level_id in _RETRACEMENT_LEVEL_CATALOG:
        if level_id not in catalog_ids and level_id not in support_by_level:
            continue
        level = dict(_RETRACEMENT_LEVEL_CATALOG[level_id])
        level["completed_observation_count"] = support_by_level.get(level_id, 0)
        level_support.append(level)

    return {
        "schema_version": "PG_RETRACEMENT_CONFLUENCE_STUDY_V3",
        "status": _safe_public_text(source.get("status"), "PENDING", limit=40).upper(),
        "study_only": True,
        "observation_only": True,
        "execution_authority": False,
        "truncated": source.get("truncated") is True,
        "completed_observation_count": sum(support_by_level.values()),
        "level_support": level_support,
    }


def _retracement_partition_contract(value: object) -> dict[str, object]:
    source = _mapping(value)
    partition = _mapping(source.get("partition")) or source
    canonical = _canonical_retracement_level(partition)
    if not canonical:
        return {}

    support = _mapping(source.get("support"))
    counts = _mapping(source.get("counts"))
    rates = _mapping(source.get("empirical_rates"))
    completed = _integer(
        support.get("completed_studies"),
        source.get("completed_study_count"),
    )
    directional_alignment_labels = min(
        completed,
        _integer(
            support.get("directional_alignment_label_count"),
            source.get("directional_alignment_label_count"),
        ),
    )
    side_adjusted_returns = min(
        completed,
        _integer(
            support.get("side_adjusted_return_count"),
            source.get("side_adjusted_return_count"),
        ),
    )
    directional_alignment_count = min(
        directional_alignment_labels,
        _integer(
            counts.get("directional_alignment_count"),
            source.get("directional_alignment_count"),
        ),
    )
    outcome_directions = _safe_retracement_count_map(
        counts.get("outcome_directions")
        or source.get("outcome_direction_counts"),
        limit=3,
        allowed_keys=frozenset({"UP", "DOWN", "REST"}),
    )
    if sum(outcome_directions.values()) > directional_alignment_labels:
        outcome_directions = {}
    relation_counts = _safe_retracement_count_map(
        counts.get("relations") or source.get("relation_counts"),
        limit=8,
    )

    def partition_token(key: str, default: str, *, limit: int) -> str:
        return _safe_public_text(partition.get(key), default, limit=limit).upper()

    symbol = partition_token("symbol", "", limit=64)
    timeframe = partition_token("timeframe", "", limit=32)
    observation_regime = partition_token("regime", "UNKNOWN", limit=48)
    supplied_regime_basis = partition_token(
        "regime_basis",
        _RETRACEMENT_REGIME_BASIS,
        limit=96,
    )
    if supplied_regime_basis != _RETRACEMENT_REGIME_BASIS:
        return {}
    side = partition_token("side", "UNKNOWN", limit=16)
    coordinate_space = partition_token("coordinate_space", "UNKNOWN", limit=40)
    object_type = partition_token("object_type", "UNKNOWN", limit=64)
    label_parts = [
        token
        for token in (
            symbol,
            timeframe,
            f"OBSERVATION REGIME {observation_regime}",
            side,
            coordinate_space,
            str(canonical["label"]),
            object_type,
        )
        if token
    ]
    result: dict[str, object] = {
        "partition_label": " | ".join(label_parts)[:240],
        **canonical,
        "observation_regime": observation_regime,
        "regime_basis": _RETRACEMENT_REGIME_BASIS,
        "side": side,
        "coordinate_space": coordinate_space,
        "object_type": object_type,
        "completed_study_count": completed,
        "directional_alignment_label_count": directional_alignment_labels,
        "directional_alignment_count": directional_alignment_count,
        "side_adjusted_return_count": side_adjusted_returns,
        "outcome_direction_counts": outcome_directions,
        "relation_counts": relation_counts,
    }
    directional_alignment_rate = _number(
        rates.get(
            "directional_alignment_rate",
            source.get("directional_alignment_rate"),
        )
    )
    if (
        directional_alignment_labels > 0
        and directional_alignment_rate is not None
        and 0.0 <= directional_alignment_rate <= 1.0
    ):
        result["directional_alignment_rate"] = round(
            directional_alignment_rate, 6
        )
    average_side_adjusted_return = _number(
        rates.get(
            "average_side_adjusted_return",
            source.get("average_side_adjusted_return"),
        )
    )
    if side_adjusted_returns > 0 and average_side_adjusted_return is not None:
        result["average_side_adjusted_return"] = round(
            average_side_adjusted_return, 8
        )
    return result


def retracement_pair_contract_v3(value: object) -> dict[str, object]:
    """Bound the Pair DNA aggregate without persistence or dedupe metadata."""

    source = _mapping(value)
    if (
        source.get("study_only") is not True
        or source.get("observation_only") is not True
        or source.get("execution_authority") is not False
    ):
        return {}

    catalog_ids: set[str] = set()
    raw_catalog = source.get("level_catalog")
    catalog_rows = (
        _rows(raw_catalog)
        if not isinstance(raw_catalog, Mapping)
        else [
            {"level_id": key, **dict(_mapping(row))}
            for key, row in cast(Mapping[object, object], raw_catalog).items()
        ]
    )
    for raw_level in catalog_rows[:8]:
        canonical = _canonical_retracement_level(raw_level)
        if canonical:
            catalog_ids.add(str(canonical["level_id"]))

    full_support_by_level: dict[str, int] = {}
    for raw_support in _rows(source.get("level_support"))[:2]:
        canonical = _canonical_retracement_level(raw_support)
        if not canonical:
            continue
        level_id = str(canonical["level_id"])
        catalog_ids.add(level_id)
        full_support_by_level[level_id] = _integer(
            raw_support.get("completed_study_count")
        )

    deduplicated: dict[tuple[str, str, str, str, str], dict[str, object]] = {}
    for raw_partition in _rows(source.get("empirical_partitions"))[
        :_RETRACEMENT_PARTITION_INPUT_LIMIT
    ]:
        compact = _retracement_partition_contract(raw_partition)
        if not compact:
            continue
        level_id = str(compact["level_id"])
        catalog_ids.add(level_id)
        key = (
            level_id,
            str(compact["observation_regime"]),
            str(compact["side"]),
            str(compact["coordinate_space"]),
            str(compact["object_type"]),
        )
        previous = deduplicated.get(key)
        if previous is None or _integer(compact.get("completed_study_count")) > _integer(
            previous.get("completed_study_count")
        ):
            deduplicated[key] = compact
    partitions = sorted(
        deduplicated.values(),
        key=lambda row: (
            -_integer(row.get("completed_study_count")),
            str(row.get("partition_label") or ""),
        ),
    )[:_RETRACEMENT_PARTITION_OUTPUT_LIMIT]

    return {
        "schema_version": "PG_PAIR_DNA_RETRACEMENT_AGGREGATES_V3",
        "study_only": True,
        "observation_only": True,
        "execution_authority": False,
        "completed_study_count": _integer(source.get("completed_study_count")),
        "partitions_truncated_count": _integer(
            source.get("partitions_truncated_count")
        ),
        "level_catalog": {
            level_id: dict(_RETRACEMENT_LEVEL_CATALOG[level_id])
            for level_id in _RETRACEMENT_LEVEL_CATALOG
            if level_id in catalog_ids
        },
        "level_support": [
            {
                "level_id": level_id,
                "completed_study_count": full_support_by_level[level_id],
            }
            for level_id in _RETRACEMENT_LEVEL_CATALOG
            if level_id in full_support_by_level
        ],
        "empirical_partitions": partitions,
    }


def retracement_study_contract_v3(
    pair_dna: object,
    object_relationship_graph: object,
) -> dict[str, object]:
    """Merge current graph support with Pair DNA history, without authority."""

    pair_contract = retracement_pair_contract_v3(
        _mapping(pair_dna).get("retracement_confluence")
    )
    graph_contract = retracement_graph_contract_v3(
        _mapping(object_relationship_graph).get("retracement_study")
    )
    if not pair_contract and not graph_contract:
        return {}

    graph_support = {
        str(row["level_id"]): _integer(row.get("completed_observation_count"))
        for row in _rows(graph_contract.get("level_support"))
        if row.get("level_id") in _RETRACEMENT_LEVEL_CATALOG
    }
    partitions = _rows(pair_contract.get("empirical_partitions"))[
        :_RETRACEMENT_PARTITION_OUTPUT_LIMIT
    ]
    visible_partition_support: dict[str, int] = {}
    for row in partitions:
        level_id = str(row.get("level_id") or "")
        if level_id not in _RETRACEMENT_LEVEL_CATALOG:
            continue
        visible_partition_support[level_id] = visible_partition_support.get(
            level_id, 0
        ) + _integer(row.get("completed_study_count"))
    full_pair_support = {
        str(row["level_id"]): _integer(row.get("completed_study_count"))
        for row in _rows(pair_contract.get("level_support"))
        if row.get("level_id") in _RETRACEMENT_LEVEL_CATALOG
    }

    pair_catalog = _mapping(pair_contract.get("level_catalog"))
    level_ids = {
        *graph_support,
        *visible_partition_support,
        *full_pair_support,
        *(str(level_id) for level_id in pair_catalog),
        *(
            str(row.get("level_id"))
            for row in _rows(graph_contract.get("level_support"))
        ),
    }
    levels: list[dict[str, object]] = []
    for level_id in _RETRACEMENT_LEVEL_CATALOG:
        if level_id not in level_ids:
            continue
        level = {
            **_RETRACEMENT_LEVEL_CATALOG[level_id],
            "graph_support": graph_support.get(level_id, 0),
        }
        if level_id in full_pair_support:
            level["pair_dna_support"] = full_pair_support[level_id]
        else:
            level["visible_partition_support"] = visible_partition_support.get(
                level_id, 0
            )
        levels.append(level)

    status = (
        _safe_public_text(graph_contract.get("status"), "PENDING", limit=40).upper()
        if graph_contract
        else "PAIR_DNA_ONLY"
    )
    return {
        "schema_version": "PG_MARKET_RETRACEMENT_STUDY_V3",
        "status": status,
        "study_only": True,
        "observation_only": True,
        "execution_authority": False,
        "can_grant_entry_permission": False,
        "graph_completed_observation_count": _integer(
            graph_contract.get("completed_observation_count")
        ),
        "pair_dna_completed_study_count": _integer(
            pair_contract.get("completed_study_count")
        ),
        "empirical_partitions_truncated_count": _integer(
            pair_contract.get("partitions_truncated_count")
        ),
        "levels": levels[:2],
        "empirical_partitions": partitions,
    }


def _market_study_contract(value: object) -> dict[str, object]:
    """Project the bounded observation-only study into the operator DTO."""

    source = _mapping(value)
    if not source or source.get("study_only") is not True:
        return {}
    regression = _mapping(source.get("regression"))
    candle_study = _mapping(source.get("candle_intelligence"))
    latest_candle = _mapping(candle_study.get("latest"))
    ratios = _mapping(latest_candle.get("ratios"))
    interaction = _mapping(latest_candle.get("interaction"))
    behavior = _mapping(source.get("behavior"))
    current_state = _mapping(behavior.get("current_state"))
    current_segment = _mapping(behavior.get("current_segment"))
    similarity = _mapping(source.get("historical_similarity"))
    continuation = _mapping(similarity.get("historical_continuation"))
    similarity_graph = _mapping(similarity.get("similarity_graph"))
    directional = _mapping(source.get("directional_read"))
    pair_dna = _mapping(source.get("pair_dna"))
    pair_candle = _mapping(pair_dna.get("candle"))
    pair_behavior = _mapping(pair_dna.get("behavior"))
    pair_associations = _rows(pair_dna.get("outcome_associations"))[:12]
    candle_ledger = _mapping(source.get("candle_ledger"))
    object_graph = _mapping(source.get("object_relationship_graph"))
    retracement_study = retracement_study_contract_v3(pair_dna, object_graph)
    maturation = _mapping(source.get("outcome_maturation"))
    motif_lattice = _mapping(source.get("motif_lattice"))
    survival_network = _mapping(source.get("survival_network"))
    path_reconstruction = _mapping(source.get("path_reconstruction"))
    feature_ontology = _mapping(source.get("adaptive_feature_ontology"))
    concept_drift = _mapping(source.get("concept_drift"))
    regime_partition = _mapping(source.get("regime_partition"))
    cross_pair = _mapping(source.get("cross_pair_association"))
    claim_proofs = _mapping(source.get("claim_proofs"))
    matches: list[dict[str, object]] = []
    for row in _rows(similarity.get("matches"))[:5]:
        outcome = _mapping(row.get("outcome"))
        matches.append(
            {
                "sequence_id": _safe_identifier(row.get("sequence_id"), "historical-sequence"),
                "similarity": _confidence(row.get("similarity")) or 0.0,
                "regime": _safe_public_text(row.get("regime"), "Unknown", limit=48),
                "outcome_direction": _safe_public_text(
                    outcome.get("direction"), "Unknown", limit=16
                ),
            }
        )
    result: dict[str, object] = {
        "schema_version": "PG_MARKET_STUDY_V3",
        "status": _safe_public_text(source.get("status"), "PENDING", limit=40).upper(),
        "reason": _safe_public_text(source.get("reason"), "", limit=240),
        "study_only": True,
        "execution_authority": False,
        "can_grant_entry_permission": False,
        "symbol": _safe_public_text(source.get("symbol"), "Unknown", limit=64),
        "timeframe": _safe_public_text(source.get("timeframe"), "Unknown", limit=32),
        "closed_candle_sequence": _integer(source.get("closed_candle_sequence")),
        "closed_candle_key": _safe_identifier(
            source.get("closed_candle_key"), ""
        ),
        "regression": {
            "schema_version": "PG_REGRESSION_STUDY_V3",
            "regime": _safe_public_text(regression.get("regime"), "Unknown", limit=40),
            "major_trend": _study_trend_contract(
                regression.get("major_trend") or behavior.get("major_trend")
            ),
            "inner_trend": _study_trend_contract(
                regression.get("inner_trend") or behavior.get("inner_trend")
            ),
            "current_pressure": _study_trend_contract(
                regression.get("current_pressure")
            ),
            "study_only": True,
            "execution_authority": False,
        },
        "candle_intelligence": {
            "status": _safe_public_text(candle_study.get("status"), "PENDING", limit=40),
            "studied_count": _integer(candle_study.get("studied_count")),
            "summary": {
                "direction_counts": _public_count_map(
                    _mapping(candle_study.get("summary")).get("direction_counts")
                ),
                "type_counts": _public_count_map(
                    _mapping(candle_study.get("summary")).get("type_counts")
                ),
                "personality_counts": _public_count_map(
                    _mapping(candle_study.get("summary")).get("personality_counts")
                ),
                "rejection_rate": _confidence(
                    _mapping(candle_study.get("summary")).get("rejection_rate")
                )
                or 0.0,
                "acceptance_rate": _confidence(
                    _mapping(candle_study.get("summary")).get("acceptance_rate")
                )
                or 0.0,
            },
            "latest": {
                "direction": _safe_public_text(latest_candle.get("direction"), "Unknown", limit=20),
                "type": _safe_public_text(latest_candle.get("type"), "Unknown", limit=64),
                "personality": _safe_public_text(
                    latest_candle.get("personality"), "Unknown", limit=64
                ),
                "regime": _safe_public_text(latest_candle.get("regime"), "Unknown", limit=40),
                "relationship": _safe_public_text(
                    latest_candle.get("relation_to_previous"), "Unknown", limit=64
                ),
                "ratios": {
                    key: round(_number(ratios.get(key)) or 0.0, 6)
                    for key in (
                        "body_to_range",
                        "upper_wick_to_range",
                        "lower_wick_to_range",
                        "close_location_in_range",
                        "range_vs_sequence_median",
                    )
                },
                "rejection": {
                    "detected": _mapping(interaction.get("rejection")).get("detected") is True,
                    "side": _safe_public_text(
                        _mapping(interaction.get("rejection")).get("side"),
                        "None",
                        limit=16,
                    ),
                },
                "acceptance": {
                    "detected": _mapping(interaction.get("acceptance")).get("detected") is True,
                    "side": _safe_public_text(
                        _mapping(interaction.get("acceptance")).get("side"),
                        "None",
                        limit=16,
                    ),
                },
            },
        },
        "behavior": {
            "status": _safe_public_text(behavior.get("status"), "PENDING", limit=40),
            "major_trend": _study_trend_contract(behavior.get("major_trend")),
            "inner_trend": _study_trend_contract(behavior.get("inner_trend")),
            "current_state": {
                "state": _safe_public_text(current_state.get("state"), "Unknown", limit=32),
                "direction": _safe_public_text(current_state.get("direction"), "Unknown", limit=20),
                "candle_count": _integer(current_state.get("candle_count")),
                "duration_seconds": _integer(current_state.get("duration_seconds")),
            },
            "current_segment": {
                "state": _safe_public_text(current_segment.get("state"), "Unknown", limit=32),
                "candle_count": _integer(current_segment.get("candle_count")),
                "duration_seconds": _integer(current_segment.get("duration_seconds")),
                "next_state": _safe_public_text(current_segment.get("next_state"), "Unknown", limit=32),
            },
            "swing_summary": {
                key: {
                    "segment_count": _integer(_mapping(row).get("segment_count")),
                    "average_candles": round(_number(_mapping(row).get("average_candles")) or 0.0, 3),
                    "maximum_candles": _integer(_mapping(row).get("maximum_candles")),
                    "average_duration_seconds": round(
                        _number(_mapping(row).get("average_duration_seconds")) or 0.0,
                        2,
                    ),
                }
                for key, row in _mapping(behavior.get("swing_summary")).items()
            },
            "rest_summary": {
                key: value
                for key, value in _mapping(behavior.get("rest_summary")).items()
                if key
                in {
                    "segment_count",
                    "average_candles",
                    "maximum_candles",
                    "average_duration_seconds",
                    "breakout_up_count",
                    "breakout_down_count",
                    "unresolved_count",
                }
                and isinstance(value, (int, float))
            },
            "market_story": _safe_public_text(behavior.get("market_story"), "", limit=280),
        },
        "historical_similarity": {
            "status": _safe_public_text(similarity.get("status"), "NO_MATCHES", limit=40),
            "match_count": _integer(similarity.get("match_count")),
            "historical_continuation": {
                "status": _safe_public_text(
                    continuation.get("status"), "INSUFFICIENT_OUTCOME_SUPPORT", limit=48
                ),
                "side": _side(continuation.get("direction")),
                "direction": _safe_public_text(
                    continuation.get("direction"), "Unknown", limit=16
                ),
                "confidence": _confidence(continuation.get("confidence")) or 0.0,
                "support": _integer(continuation.get("support")),
                "minimum_support": _integer(continuation.get("minimum_support")),
            },
            "matches": matches,
            "similarity_graph": {
                "status": _safe_public_text(
                    similarity_graph.get("status"), "EMPTY", limit=32
                ),
                "graph_kind": _safe_public_text(
                    similarity_graph.get("graph_kind"),
                    "BOUNDED_HISTORICAL_SEQUENCE_SIMILARITY",
                    limit=64,
                ),
                "directed": False,
                "node_count": _integer(similarity_graph.get("node_count")),
                "edge_count": _integer(similarity_graph.get("edge_count")),
                "edges": [
                    {
                        "source": _safe_identifier(row.get("source"), "node"),
                        "target": _safe_identifier(row.get("target"), "node"),
                        "similarity": _confidence(row.get("similarity")) or 0.0,
                    }
                    for row in _rows(similarity_graph.get("edges"))[:24]
                ],
                "study_only": True,
                "execution_authority": False,
            },
        },
        "pair_dna": {
            "observation_count": _integer(pair_dna.get("observation_count")),
            "candle_count": _integer(pair_dna.get("candle_count")),
            "candle": {
                "direction_counts": _public_count_map(pair_candle.get("direction_counts")),
                "type_counts": _public_count_map(pair_candle.get("type_counts")),
                "personality_counts": _public_count_map(pair_candle.get("personality_counts")),
                "averages": {
                    key: round(_number(value) or 0.0, 6)
                    for key, value in _mapping(pair_candle.get("averages")).items()
                },
            },
            "behavior": {
                "state_candle_counts": _public_count_map(
                    pair_behavior.get("state_candle_counts")
                ),
                "major_trend_counts": _public_count_map(
                    pair_behavior.get("major_trend_counts")
                ),
                "inner_trend_counts": _public_count_map(
                    pair_behavior.get("inner_trend_counts")
                ),
            },
            "regime_counts": _public_count_map(pair_dna.get("regime_counts")),
            "object_type_counts": _public_count_map(pair_dna.get("object_type_counts")),
            "outcome_association_contract": {
                "causal": False,
                "note": "Counts describe historical association and do not prove causation.",
            },
            "outcome_associations": [
                {
                    "feature": _safe_public_text(
                        row.get("feature"), "Unknown", limit=160
                    ),
                    "support": _integer(row.get("support")),
                    "direction_probabilities": {
                        key: _confidence(value) or 0.0
                        for key, value in _mapping(
                            row.get("direction_probabilities")
                        ).items()
                        if str(key).upper() in {"UP", "DOWN", "REST"}
                    },
                }
                for row in pair_associations
            ],
        },
        "candle_ledger": {
            "status": _safe_public_text(candle_ledger.get("status"), "UNKNOWN", limit=40),
            "pair_id": _safe_identifier(candle_ledger.get("pair_id"), ""),
            "unique_candle_count": _integer(candle_ledger.get("unique_candle_count")),
            "total_observation_count": _integer(
                candle_ledger.get("total_observation_count")
            ),
            "study_only": True,
            "execution_authority": False,
        },
        "object_relationship_graph": {
            "status": _safe_public_text(object_graph.get("status"), "EMPTY", limit=40),
            "latest_candle_id": _safe_identifier(
                object_graph.get("latest_candle_id"), ""
            ),
            "selected_counts": _public_count_map(
                object_graph.get("selected_counts"), limit=8
            ),
            "relation_counts": _public_count_map(
                object_graph.get("relation_counts"), limit=8
            ),
            "truncated": object_graph.get("truncated") is True,
            "observation_only": True,
            "study_only": True,
            "execution_authority": False,
        },
        "outcome_maturation": {
            "status": _safe_public_text(maturation.get("status"), "UNKNOWN", limit=64),
            "matched_candle_id": _safe_identifier(
                maturation.get("matched_candle_id"), ""
            ),
            "coordinate_space": _safe_public_text(
                maturation.get("coordinate_space"), "Unknown", limit=40
            ),
            "study_only": True,
            "execution_authority": False,
        },
        "directional_read": {
            "side": _side(directional.get("side")),
            "confidence": _confidence(directional.get("confidence")) or 0.0,
            "status": _safe_public_text(
                directional.get("status"), "INSUFFICIENT_EVIDENCE", limit=48
            ),
            "reasons": [
                _safe_public_text(reason, "", limit=120)
                for reason in cast(Sequence[object], directional.get("reasons", []))[:6]
                if isinstance(reason, str) and reason.strip()
            ]
            if isinstance(directional.get("reasons"), Sequence)
            and not isinstance(directional.get("reasons"), (str, bytes, bytearray))
            else [],
            "study_only": True,
            "execution_authority": False,
        },
    }
    if retracement_study:
        result["retracement_study"] = retracement_study

    motif_levels: list[dict[str, object]] = []
    for level in _rows(motif_lattice.get("levels"))[:4]:
        recent_nodes: list[dict[str, object]] = []
        for node in _rows(level.get("nodes"))[-8:]:
            span = _mapping(node.get("span"))
            composition = _mapping(node.get("composition"))
            features = _mapping(node.get("features"))
            ratios = _mapping(features.get("geometry_ratios"))
            penetration = _mapping(features.get("wick_penetration"))
            recent_nodes.append(
                {
                    "node_id": _safe_identifier(node.get("node_id"), ""),
                    "motif_token": _safe_identifier(node.get("motif_token"), ""),
                    "kind": _safe_public_text(node.get("kind"), "Unknown", limit=64),
                    "span": {
                        "start_index": _integer(span.get("start_index")),
                        "end_index": _integer(span.get("end_index")),
                        "candle_count": _integer(span.get("candle_count")),
                    },
                    "composition": {
                        "child_level": _integer(composition.get("child_level")),
                        "published_child_count": _integer(
                            composition.get("published_child_count")
                        ),
                        "omitted_child_count": _integer(
                            composition.get("omitted_child_count")
                        ),
                    },
                    "features": {
                        key: _safe_public_text(features.get(key), "Unknown", limit=64)
                        for key in (
                            "state",
                            "direction",
                            "candle_type",
                            "personality",
                            "relation_to_previous",
                            "dominant_state",
                            "regime_state",
                        )
                        if features.get(key) not in (None, "")
                    }
                    | {
                        key: round(_number(features.get(key)) or 0.0, 6)
                        for key in (
                            "state_transition_count",
                            "net_change_in_window_median_ranges",
                            "path_efficiency",
                            "duration_seconds",
                        )
                        if _number(features.get(key)) is not None
                    }
                    | {
                        "geometry_ratios": {
                            key: round(_number(raw_value) or 0.0, 6)
                            for key, raw_value in ratios.items()
                            if _number(raw_value) is not None
                        },
                        "wick_penetration": {
                            key: round(_number(raw_value) or 0.0, 6)
                            for key, raw_value in penetration.items()
                            if _number(raw_value) is not None
                        },
                    },
                }
            )
        motif_levels.append(
            {
                "level": _integer(level.get("level")),
                "kind": _safe_public_text(level.get("kind"), "Unknown", limit=64),
                "candidate_count": _integer(level.get("candidate_count")),
                "published_count": _integer(level.get("published_count")),
                "truncated_count": _integer(level.get("truncated_count")),
                "recent_nodes": recent_nodes,
            }
        )
    result["motif_lattice"] = {
        "status": _safe_public_text(
            motif_lattice.get("status"), "INSUFFICIENT_PROVEN_HISTORY", limit=64
        ),
        "reason": _safe_public_text(motif_lattice.get("reason"), "", limit=240),
        "depth": _integer(motif_lattice.get("depth")),
        "closed_candle_count": _integer(motif_lattice.get("closed_candle_count")),
        "max_nodes_per_level": _integer(motif_lattice.get("max_nodes_per_level")),
        "levels": motif_levels,
        "summary": {
            "published_node_count": _integer(
                _mapping(motif_lattice.get("summary")).get("published_node_count")
            ),
            "published_by_level": _public_count_map(
                _mapping(motif_lattice.get("summary")).get("published_by_level"),
                limit=4,
            ),
            "truncated_by_level": _public_count_map(
                _mapping(motif_lattice.get("summary")).get("truncated_by_level"),
                limit=4,
            ),
        },
        "claim_proof_id": _safe_identifier(motif_lattice.get("claim_proof_id"), ""),
        "fixed_sequence_horizon": False,
        "study_only": True,
        "causal": False,
        "execution_authority": False,
    }

    result["survival_network"] = {
        "status": _safe_public_text(
            survival_network.get("status"), "INSUFFICIENT_PROVEN_HISTORY", limit=64
        ),
        "reason": _safe_public_text(survival_network.get("reason"), "", limit=240),
        "history_count": _integer(survival_network.get("history_count")),
        "derived_observation_count": _integer(
            survival_network.get("derived_observation_count")
        ),
        "maximum_observed_horizon_closed_candles": _integer(
            survival_network.get("max_horizon_closed_candles")
        ),
        "network": {
            "node_count": _integer(
                _mapping(survival_network.get("network")).get("node_count")
            ),
            "edge_count": _integer(
                _mapping(survival_network.get("network")).get("edge_count")
            ),
            "edge_semantics": "NON_CAUSAL_HISTORICAL_TIME_TO_EVENT_ASSOCIATION",
        },
        "curves": [
            {
                "event_type": _safe_public_text(row.get("event_type"), "Unknown", limit=32),
                "origin_state": _safe_public_text(row.get("origin_state"), "Unknown", limit=32),
                "status": _safe_public_text(row.get("status"), "UNKNOWN", limit=32),
                "support": _integer(row.get("support")),
                "event_count": _integer(row.get("event_count")),
                "right_censored_count": _integer(row.get("right_censored_count")),
                "median_event_time_closed_candles": (
                    _integer(row.get("median_event_time_closed_candles"))
                    if row.get("median_event_time_closed_candles") is not None
                    else None
                ),
                "median_event_time_seconds": (
                    _integer(row.get("median_event_time_seconds"))
                    if row.get("median_event_time_seconds") is not None
                    else None
                ),
                "restricted_mean_event_free_closed_candles": round(
                    _number(row.get("restricted_mean_event_free_closed_candles")) or 0.0,
                    6,
                ),
            }
            for row in _rows(survival_network.get("curves"))[:16]
        ],
        "claim_proof_id": _safe_identifier(
            survival_network.get("claim_proof_id"), ""
        ),
        "fixed_sequence_horizon": False,
        "study_only": True,
        "causal": False,
        "execution_authority": False,
    }

    path_summary = _mapping(path_reconstruction.get("path_summary"))
    result["path_reconstruction"] = {
        "status": _safe_public_text(
            path_reconstruction.get("status"), "INSUFFICIENT_PROVEN_HISTORY", limit=64
        ),
        "reason": _safe_public_text(path_reconstruction.get("reason"), "", limit=240),
        "path_id": _safe_identifier(path_reconstruction.get("path_id"), ""),
        "point_count": _integer(path_reconstruction.get("point_count")),
        "reference_direction": _safe_public_text(
            path_reconstruction.get("reference_direction"), "Unknown", limit=16
        ),
        "reference_direction_is_trade_instruction": False,
        "summary": {
            key: round(_number(path_summary.get(key)) or 0.0, 6)
            for key in (
                "maximum_favorable_excursion_in_median_ranges",
                "maximum_adverse_excursion_in_median_ranges",
                "final_displacement_in_median_ranges",
                "final_path_efficiency",
                "state_transition_count",
            )
        }
        | {
            "time_in_states": {
                state: {
                    "closed_candles": _integer(_mapping(row).get("closed_candles")),
                    "seconds": _integer(_mapping(row).get("seconds")),
                    "fraction": round(_number(_mapping(row).get("fraction")) or 0.0, 6),
                }
                for state, row in _mapping(path_summary.get("time_in_states")).items()
            }
        },
        "points": [
            {
                "offset_closed_candles": _integer(row.get("offset_closed_candles")),
                "state": _safe_public_text(row.get("state"), "Unknown", limit=32),
                "normalized_ohlc_from_anchor_close": {
                    key: round(_number(raw_value) or 0.0, 6)
                    for key, raw_value in _mapping(
                        row.get("normalized_ohlc_from_anchor_close")
                    ).items()
                    if key in {"open", "high", "low", "close"}
                    and _number(raw_value) is not None
                },
                "close_delta_from_previous_in_median_ranges": round(
                    _number(row.get("close_delta_from_previous_in_median_ranges"))
                    or 0.0,
                    6,
                ),
                "path_efficiency": round(_number(row.get("path_efficiency")) or 0.0, 6),
                "favorable_excursion_in_median_ranges": round(
                    _number(
                        row.get("cumulative_favorable_excursion_in_median_ranges")
                    )
                    or 0.0,
                    6,
                ),
                "adverse_excursion_in_median_ranges": round(
                    _number(row.get("cumulative_adverse_excursion_in_median_ranges"))
                    or 0.0,
                    6,
                ),
            }
            for row in _rows(path_reconstruction.get("points"))[:128]
        ],
        "claim_proof_id": _safe_identifier(
            path_reconstruction.get("claim_proof_id"), ""
        ),
        "historical_only": True,
        "study_only": True,
        "causal": False,
        "execution_authority": False,
    }

    ontology_audit = _mapping(feature_ontology.get("shadow_audit"))
    result["adaptive_feature_ontology"] = {
        "status": _safe_public_text(
            feature_ontology.get("status"), "INSUFFICIENT_PROVEN_HISTORY", limit=64
        ),
        "reason": _safe_public_text(feature_ontology.get("reason"), "", limit=240),
        "ontology_version": _integer(feature_ontology.get("ontology_version")),
        "promoted_feature_count": _integer(
            feature_ontology.get("promoted_feature_count")
        ),
        "public_features": [
            {
                "feature_id": _safe_identifier(row.get("feature_id"), ""),
                "status": _safe_public_text(row.get("status"), "UNKNOWN", limit=32),
                "revision": _integer(row.get("revision")),
                "ontology_version": _integer(row.get("ontology_version")),
            }
            for row in _rows(feature_ontology.get("public_features"))[:32]
        ],
        "shadow_audit": {
            "shadow_feature_count": _integer(
                ontology_audit.get("shadow_feature_count")
            ),
            "evaluated_shadow_feature_count": _integer(
                ontology_audit.get("evaluated_shadow_feature_count")
            ),
            "evidence_closed_candle_count": _integer(
                ontology_audit.get("evidence_closed_candle_count")
            ),
            "definitions_published": False,
            "promotion_requires_real_holdout_gate": True,
        },
        "study_only": True,
        "causal": False,
        "execution_authority": False,
    }

    drift_metrics = _mapping(concept_drift.get("metrics"))
    result["concept_drift"] = {
        "status": _safe_public_text(
            concept_drift.get("status"), "INSUFFICIENT_PROVEN_HISTORY", limit=64
        ),
        "reason": _safe_public_text(concept_drift.get("reason"), "", limit=240),
        "partition_count": _integer(concept_drift.get("partition_count")),
        "current_regime_partition_id": _safe_identifier(
            concept_drift.get("current_regime_partition_id"), ""
        ),
        "window_size": _integer(
            _mapping(concept_drift.get("window_policy")).get("window_size")
        ),
        "statistically_significant_drift": (
            drift_metrics.get("statistically_significant_drift") is True
        ),
        "trigger_features": [
            _safe_public_text(item, "", limit=64)
            for item in cast(Sequence[object], drift_metrics.get("trigger_features", []))[:16]
            if isinstance(item, str) and item.strip()
        ]
        if isinstance(drift_metrics.get("trigger_features"), Sequence)
        and not isinstance(drift_metrics.get("trigger_features"), (str, bytes, bytearray))
        else [],
        "partitions": [
            {
                "regime_partition_id": _safe_identifier(
                    row.get("regime_partition_id"), ""
                ),
                "ordinal": _integer(row.get("ordinal")),
                "status": _safe_public_text(row.get("status"), "UNKNOWN", limit=32),
                "created_by": _safe_public_text(row.get("created_by"), "UNKNOWN", limit=64),
            }
            for row in _rows(concept_drift.get("partitions"))[:64]
        ],
        "claim_proof_id": _safe_identifier(concept_drift.get("claim_proof_id"), ""),
        "fixed_sequence_horizon": False,
        "predicts_direction": False,
        "study_only": True,
        "causal": False,
        "execution_authority": False,
    }
    current_partition = _mapping(regime_partition.get("current_partition"))
    result["regime_partition"] = {
        "status": _safe_public_text(regime_partition.get("status"), "UNPROVEN", limit=64),
        "partition_count": _integer(regime_partition.get("partition_count")),
        "current_partition": {
            "regime_partition_id": _safe_identifier(
                current_partition.get("regime_partition_id"), ""
            ),
            "ordinal": _integer(current_partition.get("ordinal")),
            "status": _safe_public_text(current_partition.get("status"), "UNKNOWN", limit=32),
            "created_by": _safe_public_text(
                current_partition.get("created_by"), "UNKNOWN", limit=64
            ),
        },
        "claim_proof_id": _safe_identifier(regime_partition.get("claim_proof_id"), ""),
        "predicts_direction": False,
        "study_only": True,
        "causal": False,
        "execution_authority": False,
    }

    result["cross_pair_association"] = {
        "status": _safe_public_text(
            cross_pair.get("status"), "INSUFFICIENT_SYNCHRONIZED_PAIR", limit=64
        ),
        "reason": _safe_public_text(cross_pair.get("reason"), "", limit=240),
        "stored_pair_count": _integer(cross_pair.get("stored_pair_count")),
        "compatible_pair_count": _integer(cross_pair.get("compatible_pair_count")),
        "tested_pair_count": _integer(cross_pair.get("tested_pair_count")),
        "published_edge_count": _integer(cross_pair.get("published_edge_count")),
        "edges": [
            {
                "source_pair": _safe_public_text(
                    row.get("source_pair_id"), "Unknown", limit=96
                ),
                "target_pair": _safe_public_text(
                    row.get("target_pair_id"), "Unknown", limit=96
                ),
                "lag_completed_candles": _integer(row.get("lag_completed_candles")),
                "support": _integer(row.get("support")),
                "variance_reduction": round(
                    _number(row.get("granger_style_variance_reduction")) or 0.0,
                    6,
                ),
                "mutual_information_nats": round(
                    _number(row.get("mutual_information_nats")) or 0.0,
                    6,
                ),
                "adjusted_p_value": round(
                    _number(row.get("bonferroni_adjusted_p_value")) or 1.0,
                    8,
                ),
                "association_score": round(
                    _number(row.get("association_score")) or 0.0,
                    6,
                ),
                "causal": False,
            }
            for row in _rows(cross_pair.get("edges"))[:32]
        ],
        "claim_proof_id": _safe_identifier(cross_pair.get("claim_proof_id"), ""),
        "study_only": True,
        "causal": False,
        "execution_authority": False,
    }

    result["claim_proofs"] = {
        "status": _safe_public_text(claim_proofs.get("status"), "PENDING", limit=32),
        "certificate_count": _integer(claim_proofs.get("certificate_count")),
        "required_claim_count": _integer(claim_proofs.get("required_claim_count")),
        "covered_claim_count": _integer(claim_proofs.get("covered_claim_count")),
        "coverage": [
            {
                "claim_key": _safe_public_text(row.get("claim_key"), "Unknown", limit=96),
                "status": _safe_public_text(row.get("status"), "UNKNOWN", limit=96),
                "certificate_id": _safe_identifier(row.get("certificate_id"), ""),
            }
            for row in _rows(claim_proofs.get("coverage"))[:64]
        ],
        "certificates": [
            {
                "certificate_id": _safe_identifier(row.get("certificate_id"), ""),
                "status": _safe_public_text(row.get("status"), "UNKNOWN", limit=32),
                "claim_type": _safe_public_text(row.get("claim_type"), "UNKNOWN", limit=64),
                "certificate_hash": _safe_identifier(row.get("certificate_hash"), ""),
                "derivation": {
                    "algorithm_id": _safe_public_text(
                        _mapping(row.get("derivation_identity")).get("algorithm_id"),
                        "UNKNOWN",
                        limit=128,
                    ),
                    "algorithm_version": _safe_public_text(
                        _mapping(row.get("derivation_identity")).get("algorithm_version"),
                        "UNKNOWN",
                        limit=64,
                    ),
                },
                "closed_candle_count": _integer(
                    _mapping(row.get("evidence")).get("closed_candle_count")
                ),
                "binds_ordered_closed_candles": (
                    _mapping(row.get("binding")).get(
                        "binds_ordered_closed_candle_evidence"
                    )
                    is True
                ),
                "causal": False,
                "execution_authority": False,
            }
            for row in _rows(claim_proofs.get("certificates"))[:64]
        ],
        "proves_integrity_not_causation": True,
        "study_only": True,
        "causal": False,
        "execution_authority": False,
    }
    return result


def build_operator_workspace_v1(
    payload: Mapping[str, object],
    *,
    now_epoch: float | None = None,
) -> dict[str, object]:
    """Build the narrow, user-facing operator workspace contract.

    The projection is intentionally fail-closed. It derives current movement only
    from current closed-candle evidence and never returns raw diagnostic or filesystem
    data from the live-state payload.
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
    permission = _permission_contract(
        source,
        command,
        freshness,
        current_move,
        pressure_event,
        now_epoch=current_epoch,
    )
    history = _history_contract(source)
    tracking_summary = _mapping(source.get("tracking_summary"))
    market_study_v3 = _market_study_contract(
        tracking_summary.get("market_study_v3")
        or _mapping(source.get("latest_signal")).get("market_study_v3")
    )
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
            "market_study_v3": market_study_v3,
        },
        "freshness": freshness,
        "current_move": current_move,
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
]
