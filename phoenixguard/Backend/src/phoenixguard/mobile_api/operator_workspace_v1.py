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

from phoenixguard.core.timing_policy_v3 import (
    MAXIMUM_STUDIED_TRADE_DURATION_SECONDS,
    MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS,
)
from phoenixguard.decision.entry_window_policy_v3 import entry_location_guidance_v3
from phoenixguard.decision.countertrend_sniper_v3 import (
    COUNTERTREND_SNIPER_LINEAGE_KEYS,
    COUNTERTREND_SNIPER_PRELIMINARY_PHASE,
    COUNTERTREND_SNIPER_SCHEMA_VERSION,
    COUNTERTREND_SNIPER_VALIDATED_PHASE,
)
from phoenixguard.decision.order_positioning_evidence_v3 import (
    build_current_order_positioning_candidate_v3,
    build_current_order_reference_map_v3,
)


OPERATOR_WORKSPACE_SCHEMA_VERSION = "PG_OPERATOR_WORKSPACE_V1"

_DIRECTIONAL_SIDES = frozenset({"BUY", "SELL"})
_NEXT_IMPULSE_AFTER_ACTIVE_TARGET_EVENT = (
    "NEXT_TARGET_SWING_START_AFTER_ACTIVE_TARGET_AND_REST"
)
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
    "three_questions",
    "tracking",
    "freshness",
    "current_move",
    "permission",
    "pressure_event",
    "surface",
    "overlays",
    "history",
)

_CPU_STREAM_COUNTER_LIMIT = 1_000_000_000_000
_CPU_STREAM_FRESHNESS_MIN_BUDGET_SEC = 8.0
_CPU_STREAM_FRESHNESS_MAX_BUDGET_SEC = 45.0
_CPU_STREAM_OBSERVED_PERIOD_MULTIPLIER = 3.0
_CPU_STREAM_TARGET_PERIOD_MULTIPLIER = 4.0
_CPU_STREAM_MARKET_READ_SCHEMA_VERSION = "PG_CPU_STREAM_MARKET_READ_V3"
_CPU_STREAM_STATE_ALIASES = {
    "ACTIVE": "RUNNING",
    "RUNNING": "RUNNING",
    "LIVE": "RUNNING",
    "STREAMING": "RUNNING",
    "STARTING": "STARTING",
    "CONNECTING": "STARTING",
    "WARMING": "STARTING",
    "WAITING": "STARTING",
    "FALLBACK_SNAPSHOT": "DEGRADED",
    "SNAPSHOT_FALLBACK": "DEGRADED",
    "DEGRADED_SNAPSHOT_FALLBACK": "DEGRADED",
    "DEGRADED": "DEGRADED",
    "DELAYED": "DEGRADED",
    "SLOW": "DEGRADED",
    "STARVED": "DEGRADED",
    "PAUSED": "PAUSED",
    "DISABLED": "DISABLED",
    "STOPPED": "STOPPED",
    "IDLE": "STOPPED",
    "INACTIVE": "STOPPED",
    "OFFLINE": "OFFLINE",
    "ERROR": "OFFLINE",
    "FAILED": "OFFLINE",
    "UNAVAILABLE": "OFFLINE",
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
    "BOOK_RULE_LINE": ("book_rule", "Book rule line", "plan"),
    "BOOK_RULE_ZONE": ("book_rule", "Book rule reaction area", "plan"),
    "BOOK_RULE_CANDLE": ("book_rule", "Book rule candle", "plan"),
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
    "book_rules": "plan",
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
    "book_rules": "book_rules",
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
    "BOOK_RULE_LINE": "book_rules",
    "BOOK_RULE_ZONE": "book_rules",
    "BOOK_RULE_CANDLE": "book_rules",
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
    "book_rules": "book_rules",
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
    "BOOK_RULE_LINE": ("book_rule_line", "Book rule line"),
    "BOOK_RULE_ZONE": ("book_rule_zone", "Book rule reaction area"),
    "BOOK_RULE_CANDLE": ("book_rule_candle", "Book rule candle"),
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


def _aligned_current_chart_identity_v3(
    source: Mapping[str, object],
    display_frame: object,
) -> Mapping[str, Any]:
    """Return the current frame's identity row, including a pending row.

    A same-frame pending row is an explicit namespace veto: it proves that the
    producer has started classifying this new chart frame, so older selector or
    study identity must not be used as a fallback while confirmation is in
    progress.
    """

    identity = _mapping(source.get("current_chart_identity_v3"))
    if not identity:
        return {}
    identity_frame = _frame_id(
        identity.get("display_frame_id"),
        identity.get("frame_id"),
    )
    current_frame = _frame_id(display_frame)
    if (
        _text(identity.get("schema_version"), "").upper()
        != "PG_CURRENT_CHART_IDENTITY_V3"
        or _explicit_bool(identity.get("decision_authority")) is not False
        or current_frame is None
        or identity_frame is None
        or not _frame_matches(identity_frame, current_frame)
    ):
        return {}
    return identity


def _current_chart_identity_v3(
    source: Mapping[str, object],
    display_frame: object,
) -> Mapping[str, Any]:
    """Return only a same-frame, explicitly confirmed fast chart identity.

    The extension/fast selector lane can identify the selected pair before the
    heavier candle study completes.  That identity may name the current
    surface, but it never supplies direction, timing, or entry permission.
    """

    identity = _aligned_current_chart_identity_v3(source, display_frame)
    if not identity:
        return {}
    symbol = _text(identity.get("symbol") or identity.get("market"), "")
    timeframe = _text(identity.get("timeframe"), "").upper()
    if (
        not symbol
        or not timeframe
        or _explicit_bool(identity.get("market_identity_confirmed")) is not True
        or _explicit_bool(identity.get("timeframe_identity_confirmed")) is not True
    ):
        return {}
    return identity


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
    live_frame_unchanged = bool(
        visual_status == "LIVE_FRAME_UNCHANGED"
        and _explicit_bool(visual_observation.get("transport_fresh")) is True
    )
    if waiting_for_new_frame or live_frame_unchanged:
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
    elif live_frame_unchanged:
        # A fresh transport heartbeat proves the selected source is still
        # connected. Identical pixels do not renew the study or an entry
        # window, so expose a third state instead of calling the feed stale.
        state = "UNCHANGED"
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
        if waiting_for_new_frame or live_frame_unchanged
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


def _broker_expiry_contract_v3(
    payload: Mapping[str, Any],
    command: Mapping[str, Any],
    *,
    market: Mapping[str, object],
    market_study: Mapping[str, object],
    now_epoch: float,
) -> dict[str, object]:
    """Admit only current, lineage-bound broker duration proof."""

    current_symbol = _safe_public_text(market.get("symbol"), "", limit=64)
    current_timeframe = _safe_public_text(
        market.get("timeframe"), "", limit=32
    ).upper()
    closed_candle_key = _safe_identifier(
        market_study.get("closed_candle_key"), ""
    )
    display_frame = _integer(
        payload.get("display_frame_id"),
        payload.get("chart_frame_id"),
        payload.get("frame_id"),
    )
    current_input_hash = _first_identity_text(
        payload.get("input_frame_hash"),
        payload.get("frame_hash"),
        _mapping(payload.get("tracking_summary")).get("input_frame_hash"),
    )

    def _result(
        *,
        expiry_seconds: int | None,
        proven: bool,
        source: str,
        valid_until_epoch: float | None,
    ) -> dict[str, object]:
        eligible = bool(
            proven
            and expiry_seconds is not None
            and expiry_seconds >= MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS
        )
        status = (
            "VERIFIED_ELIGIBLE"
            if eligible
            else "VERIFIED_INELIGIBLE"
            if proven
            else "UNVERIFIED"
        )
        if eligible:
            instruction = (
                f"Broker expiry verified at {math.ceil(cast(int, expiry_seconds) / 60)} "
                "minutes."
            )
        elif proven and expiry_seconds is not None:
            instruction = (
                f"AVOID — broker expiry is {math.ceil(expiry_seconds / 60)} minutes; "
                "this system requires at least 15 minutes."
            )
        else:
            instruction = (
                "SET/VERIFY EXPIRY ≥15 MIN — Broker expiry unverified; the model "
                "horizon is not the broker contract duration."
            )
        return {
            "schema_version": "PG_BROKER_EXPIRY_PROOF_V3",
            "status": status,
            "proven": proven,
            "eligible": eligible,
            "expiry_seconds": expiry_seconds if proven else None,
            "minimum_required_seconds": (
                MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS
            ),
            "source": source,
            "valid_until_epoch": valid_until_epoch if proven else None,
            "instruction": instruction,
            "model_horizon_is_broker_expiry": False,
        }

    primary = _mapping(command.get("broker_expiry_contract_v3"))
    if primary:
        expiry = _number(primary.get("expiry_seconds"))
        valid_until = _epoch(primary.get("valid_until_epoch"))
        primary_proven = bool(
            _explicit_bool(
                primary.get("proven")
                if "proven" in primary
                else primary.get("broker_expiry_proven")
            )
            is True
            and expiry is not None
            and expiry > 0.0
            and valid_until is not None
            and valid_until > now_epoch
            and current_symbol
            and current_timeframe
            and closed_candle_key
            and _instrument_token(primary.get("symbol"))
            == _instrument_token(current_symbol)
            and _safe_public_text(
                primary.get("timeframe"), "", limit=32
            ).upper()
            == current_timeframe
            and _safe_identifier(primary.get("closed_candle_key"), "")
            == closed_candle_key
            and display_frame > 0
            and _integer(primary.get("frame_id")) == display_frame
            and current_input_hash
            and _safe_identifier(primary.get("input_frame_hash"), "")
            == _safe_identifier(current_input_hash, "")
        )
        if primary_proven:
            return _result(
                expiry_seconds=int(cast(float, expiry)),
                proven=True,
                source="LINEAGE_BOUND_BROKER_EXPIRY_CONTRACT",
                valid_until_epoch=valid_until,
            )

    packet = _mapping(payload.get("execution_packet")) or _mapping(
        payload.get("model_council_packet")
    )
    execution = _mapping(packet.get("execution"))
    time_sequence = _mapping(packet.get("time_sequence")) or _mapping(
        execution.get("time_sequence")
    )
    lineage = _mapping(command.get("execution_lineage")) or _mapping(
        packet.get("lineage")
    )
    packet_id = _first_identity_text(packet.get("packet_id"), packet.get("id"))
    command_packet_id = _first_identity_text(command.get("execution_packet_id"))
    packet_expiry = _number(execution.get("expiry_seconds"))
    sequence_expiry = _number(
        time_sequence.get("target_expiry_seconds")
        or time_sequence.get("expiry_seconds")
        or time_sequence.get("target_seconds")
    )
    packet_valid_until = _epoch(
        packet.get("valid_until_epoch"),
        packet.get("valid_until_epoch_sec"),
        lineage.get("valid_until_epoch"),
    )
    status_packet_id = _first_identity_text(
        _mapping(payload.get("execution_packet_status")).get("packet_id")
    )
    packet_proven = bool(
        packet
        and packet_id
        and command_packet_id
        and packet_id == command_packet_id
        and (not status_packet_id or status_packet_id == packet_id)
        and _explicit_bool(command.get("execution_packet_present")) is True
        and packet_valid_until is not None
        and packet_valid_until > now_epoch
        and packet_expiry is not None
        and packet_expiry > 0.0
        and sequence_expiry is not None
        and abs(packet_expiry - sequence_expiry) <= 0.001
        and _instrument_token(lineage.get("symbol"))
        == _instrument_token(current_symbol)
        and _safe_public_text(lineage.get("timeframe"), "", limit=32).upper()
        == current_timeframe
        and _safe_identifier(
            lineage.get("trigger_closed_candle_key")
            or lineage.get("closed_candle_key"),
            "",
        )
        == closed_candle_key
    )
    if packet_proven:
        return _result(
            expiry_seconds=int(cast(float, packet_expiry)),
            proven=True,
            source="CURRENT_PERMISSION_EXECUTION_PACKET",
            valid_until_epoch=packet_valid_until,
        )
    return _result(
        expiry_seconds=None,
        proven=False,
        source="UNVERIFIED",
        valid_until_epoch=None,
    )


def _countertrend_sniper_promotion_source(
    payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    return _first_mapping(
        payload,
        ("decision_command_center", "countertrend_sniper_promotion_v3"),
        ("model_council_result", "book_strategy", "countertrend_sniper_promotion_v3"),
        ("model_council_result", "countertrend_sniper_promotion_v3"),
        ("model_council_result", "model_council", "countertrend_sniper_promotion_v3"),
        ("model_council_study_packet", "countertrend_sniper_promotion_v3"),
        ("study_packet", "countertrend_sniper_promotion_v3"),
        ("countertrend_sniper_promotion_v3",),
    )


def _instrument_token(value: object) -> str:
    return "".join(
        character
        for character in _text(value, "").upper()
        if character.isalnum()
    )


def _first_identity_text(*values: object) -> str:
    for value in values:
        resolved = _text(value, "")
        if resolved:
            return resolved
    return ""


def _countertrend_bypass_validation_v3(
    payload: Mapping[str, Any],
    command: Mapping[str, Any],
    *,
    selected_side: str,
    now_epoch: float,
) -> tuple[bool, str]:
    """Validate an opposing-force movement substitute against live identity.

    A projected promotion is evidence, not authority.  The bypass becomes
    usable only when its complete lineage exactly equals the command's bounded
    lineage and that lineage still belongs to the chart currently displayed.
    """

    promotion = _mapping(command.get("countertrend_sniper_promotion_v3"))
    if not promotion:
        return False, "ABSENT"
    classification = _text(promotion.get("classification"), "").upper()
    phase = _text(promotion.get("phase"), "").upper()
    if phase == COUNTERTREND_SNIPER_PRELIMINARY_PHASE or classification == "FORMING":
        return False, "PRELIMINARY"
    command_lineage = _mapping(command.get("execution_lineage"))
    promotion_lineage = _mapping(promotion.get("lineage"))
    if not command_lineage or not promotion_lineage:
        return False, "INVALIDATED"
    if any(
        promotion_lineage.get(field) != command_lineage.get(field)
        for field in COUNTERTREND_SNIPER_LINEAGE_KEYS
    ):
        return False, "INVALIDATED"
    if not (
        _text(promotion.get("schema_version"), "")
        == COUNTERTREND_SNIPER_SCHEMA_VERSION
        and phase == COUNTERTREND_SNIPER_VALIDATED_PHASE
        and _explicit_bool(promotion.get("active")) is True
        and classification == "ENTER_NOW"
        and _side(promotion.get("side")) == selected_side
        and selected_side in _DIRECTIONAL_SIDES
        and _explicit_bool(promotion.get("entry_permission_authorized")) is True
        and _explicit_bool(
            promotion.get("movement_confirmation_bypass_allowed")
        )
        is True
        and _explicit_bool(promotion.get("execution_packet_present")) is True
        and _text(promotion.get("validated_entry_mode"), "").upper()
        == "COUNTERTREND_SNIPER"
        and _explicit_bool(promotion.get("broker_click_authority")) is False
        and _explicit_bool(command.get("execution_packet_present")) is True
    ):
        return False, "INVALIDATED"

    required_text = (
        "packet_id",
        "opportunity_id",
        "session_id",
        "symbol",
        "timeframe",
        "input_frame_hash",
        "instrument_identity_hash",
        "trigger_closed_candle_key",
        "opportunity_key",
    )
    required_positive = (
        "frame_id",
        "capture_count",
        "state_version",
        "trigger_frame_id",
    )
    if (
        any(not _text(command_lineage.get(field), "") for field in required_text)
        or any(_integer(command_lineage.get(field)) <= 0 for field in required_positive)
        or _explicit_bool(command_lineage.get("integrity_valid")) is not True
        or _explicit_bool(command_lineage.get("lineage_rejected")) is not False
        or _integer(command_lineage.get("trigger_frame_id"))
        != _integer(command_lineage.get("frame_id"))
    ):
        return False, "INVALIDATED"
    valid_until_epoch = _epoch(command_lineage.get("valid_until_epoch"))
    if valid_until_epoch is None or valid_until_epoch <= now_epoch:
        return False, "STALE"

    packet_id = _text(command_lineage.get("packet_id"), "")
    if _text(command.get("execution_packet_id"), "") != packet_id:
        return False, "INVALIDATED"
    opportunity = _first_mapping(
        command,
        ("execution_opportunity_window_v3",),
        ("opportunity",),
    )
    if (
        _text(opportunity.get("opportunity_id"), "")
        != _text(command_lineage.get("opportunity_id"), "")
        or _text(opportunity.get("opportunity_key"), "")
        != _text(command_lineage.get("opportunity_key"), "")
    ):
        return False, "INVALIDATED"

    tracking = _mapping(payload.get("tracking_summary"))
    latest_signal = _mapping(payload.get("latest_signal"))
    current_session = _text(payload.get("session_id"), "")
    current_symbol = _first_identity_text(
        tracking.get("detected_market"),
        latest_signal.get("market"),
        latest_signal.get("symbol"),
        payload.get("symbol"),
        payload.get("market"),
    )
    current_timeframe = _first_identity_text(
        tracking.get("detected_timeframe"),
        latest_signal.get("focus_timeframe"),
        latest_signal.get("timeframe"),
        payload.get("timeframe"),
    ).upper()
    if (
        not current_session
        or _text(command_lineage.get("session_id"), "") != current_session
        or not current_symbol
        or _instrument_token(command_lineage.get("symbol"))
        != _instrument_token(current_symbol)
        or not current_timeframe
        or _text(command_lineage.get("timeframe"), "").upper()
        != current_timeframe
    ):
        return False, "INVALIDATED"

    display_frame = _integer(
        payload.get("display_frame_id"),
        payload.get("chart_frame_id"),
        payload.get("frame_id"),
        tracking.get("display_frame_id"),
        tracking.get("frame_id"),
        tracking.get("frame_index"),
    )
    current_capture = _integer(
        payload.get("capture_count"),
        tracking.get("capture_count"),
    )
    current_state_version = _integer(
        payload.get("state_version"),
        payload.get("decision_version"),
        tracking.get("state_version"),
    )
    if not (display_frame > 0 and current_capture > 0 and current_state_version > 0):
        return False, "INVALIDATED"
    if (
        _integer(command_lineage.get("frame_id")) != display_frame
        or _integer(command_lineage.get("trigger_frame_id")) != display_frame
        or _integer(command_lineage.get("capture_count")) != current_capture
        or _integer(command_lineage.get("state_version"))
        != current_state_version
    ):
        return False, "INVALIDATED"

    current_input_hash = _first_identity_text(
        payload.get("input_frame_hash"),
        payload.get("frame_hash"),
        tracking.get("input_frame_hash"),
        tracking.get("frame_hash"),
        latest_signal.get("input_frame_hash"),
        latest_signal.get("frame_hash"),
    )
    current_instrument_hash = _first_identity_text(
        payload.get("instrument_identity_hash"),
        tracking.get("instrument_identity_hash"),
        latest_signal.get("instrument_identity_hash"),
    )
    if not current_input_hash:
        return False, "INVALIDATED"
    if current_input_hash != _text(command_lineage.get("input_frame_hash"), ""):
        return False, "INVALIDATED"
    if (
        current_instrument_hash
        and current_instrument_hash
        != _text(command_lineage.get("instrument_identity_hash"), "")
    ):
        return False, "INVALIDATED"
    current_study = _mapping(tracking.get("market_study_v3")) or _mapping(
        latest_signal.get("market_study_v3")
    )
    current_trigger_key = _first_identity_text(
        payload.get("trigger_closed_candle_key"),
        tracking.get("trigger_closed_candle_key"),
        latest_signal.get("trigger_closed_candle_key"),
        current_study.get("closed_candle_key"),
    )
    if not current_trigger_key:
        return False, "INVALIDATED"
    if current_trigger_key != _text(
        command_lineage.get("trigger_closed_candle_key"), ""
    ):
        return False, "INVALIDATED"
    return True, "VALIDATED"


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
    countertrend_sniper_bypass, _countertrend_validation_state = (
        _countertrend_bypass_validation_v3(
            payload,
            command,
            selected_side=selected_side,
            now_epoch=now_epoch,
        )
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
        and (movement_matches or countertrend_sniper_bypass)
        and (not contradictory_pressure or countertrend_sniper_bypass)
    )
    action = f"{selected_side}_NOW" if allowed else "WAIT"
    if allowed:
        movement_word = "buy" if selected_side == "BUY" else "sell"
        if countertrend_sniper_bypass:
            message = (
                f"A verified countertrend sniper {movement_word} entry window is open. "
                f"Closed-candle rejection is the validated trigger. {entry_guidance}"
            )
        else:
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


def _ordered_pixel_rectangle(value: object) -> list[float]:
    bounds = _bounds(value)
    if len(bounds) != 4:
        return []
    left, right = sorted((bounds[0], bounds[2]))
    top, bottom = sorted((bounds[1], bounds[3]))
    if right <= left or bottom <= top:
        return []
    return [left, top, right, bottom]


def _rectangle_matches_dimensions(
    rectangle: Sequence[float],
    dimensions: tuple[float, float] | None,
    *,
    tolerance: float = 1.5,
) -> bool:
    if len(rectangle) != 4 or dimensions is None:
        return False
    width, height = dimensions
    return bool(
        width > 0.0
        and height > 0.0
        and abs((float(rectangle[2]) - float(rectangle[0])) - width) <= tolerance
        and abs((float(rectangle[3]) - float(rectangle[1])) - height) <= tolerance
    )


def _rectangle_relative_to_plane(
    rectangle: Sequence[float],
    plane: Sequence[float],
    *,
    tolerance: float = 1.5,
) -> list[float]:
    if len(rectangle) != 4 or len(plane) != 4:
        return []
    plane_left, plane_top, plane_right, plane_bottom = map(float, plane)
    left, top, right, bottom = map(float, rectangle)
    plane_width = plane_right - plane_left
    plane_height = plane_bottom - plane_top
    if plane_width <= 0.0 or plane_height <= 0.0:
        return []
    if (
        left < plane_left - tolerance
        or top < plane_top - tolerance
        or right > plane_right + tolerance
        or bottom > plane_bottom + tolerance
    ):
        return []
    return _strict_normalized_rectangle(
        [
            (left - plane_left) / plane_width,
            (top - plane_top) / plane_height,
            (right - plane_left) / plane_width,
            (bottom - plane_top) / plane_height,
        ]
    )


def _overlay_viewports_contract(
    payload: Mapping[str, Any],
    tracking_summary: Mapping[str, Any],
) -> dict[str, dict[str, object]]:
    """Publish exact chart-plane transforms for both operator surfaces.

    Detector geometry is expressed on the inner chart-region plane.  That
    plane is intentionally smaller than the study artifact (which retains
    broker chrome) and the full broker capture.  Each target therefore needs
    its own affine rectangle; treating either image as the detector plane
    shifts and scales every mark.
    """

    artifact_integrity = _mapping(tracking_summary.get("artifact_integrity"))
    broker_source_lock = _mapping(tracking_summary.get("broker_source_lock"))
    selected_target = _mapping(broker_source_lock.get("selected_target"))
    full_dimensions = _image_dimensions(
        artifact_integrity.get("full_window"),
        payload.get("locked_window"),
        selected_target.get("viewport"),
    )
    study_dimensions = _image_dimensions(
        artifact_integrity.get("chart"),
        artifact_integrity.get("study_plane"),
    )
    chart = _mapping(payload.get("chart"))
    scene_graph = _mapping(
        payload.get("scene_graph")
        or chart.get("scene_graph")
        or payload.get("broker_scene_graph_v3")
    )
    broker_plane = _ordered_pixel_rectangle(scene_graph.get("broker_surface_bounds"))
    chart_target = _ordered_pixel_rectangle(scene_graph.get("chart_region_bounds"))
    chart_source = _ordered_pixel_rectangle(scene_graph.get("chart_region_chart_bounds"))
    focus_region = _mapping(tracking_summary.get("focus_region"))
    focus_bounds = _ordered_pixel_rectangle(focus_region.get("pixel_bbox"))
    chart_region = _mapping(
        tracking_summary.get("chart_region")
        or tracking_summary.get("display_region")
    )
    chart_region_bounds = _ordered_pixel_rectangle(
        chart_region.get("pixel_bbox") or chart_region.get("bbox")
    )

    display_frame = _integer(
        payload.get("display_frame_id"),
        payload.get("frame_id"),
        payload.get("frame_index"),
    )
    scene_frame = _integer(scene_graph.get("frame_id"))
    scene_frame_aligned = not (display_frame > 0 and scene_frame > 0) or display_frame == scene_frame

    exact_scene = bool(
        _explicit_bool(scene_graph.get("valid")) is True
        and scene_frame_aligned
        and broker_plane
        and chart_target
        and chart_source
        and _rectangle_matches_dimensions(broker_plane, full_dimensions)
        and abs((chart_target[2] - chart_target[0]) - (chart_source[2] - chart_source[0])) <= 1.5
        and abs((chart_target[3] - chart_target[1]) - (chart_source[3] - chart_source[1])) <= 1.5
    )

    # Prove that the scene transform and tracker crop describe the same plane.
    # This prevents a future detector/study-artifact mix-up from crossing the
    # public boundary merely because both rectangles happen to look valid.
    if exact_scene and focus_bounds and chart_region_bounds:
        composed_target = [
            focus_bounds[0] + chart_region_bounds[0],
            focus_bounds[1] + chart_region_bounds[1],
            focus_bounds[0] + chart_region_bounds[2],
            focus_bounds[1] + chart_region_bounds[3],
        ]
        exact_scene = all(
            abs(composed_target[index] - chart_target[index]) <= 1.5
            for index in range(4)
        )
    if exact_scene and chart_region_bounds:
        exact_scene = bool(
            abs((chart_region_bounds[2] - chart_region_bounds[0]) - (chart_source[2] - chart_source[0])) <= 1.5
            and abs((chart_region_bounds[3] - chart_region_bounds[1]) - (chart_source[3] - chart_source[1])) <= 1.5
        )

    window_bounds: list[float] = []
    if exact_scene:
        window_bounds = _rectangle_relative_to_plane(chart_target, broker_plane)

    # If the scene graph is temporarily absent, compose the tracker-owned
    # focus and inner-chart crops.  A plain focus rectangle is retained only
    # as a compatibility fallback for older payloads that never declared a
    # distinct inner chart plane.
    if not window_bounds and focus_bounds and chart_region_bounds and full_dimensions:
        composed_target = [
            focus_bounds[0] + chart_region_bounds[0],
            focus_bounds[1] + chart_region_bounds[1],
            focus_bounds[0] + chart_region_bounds[2],
            focus_bounds[1] + chart_region_bounds[3],
        ]
        window_bounds = _pixel_rectangle_as_normalized(composed_target, full_dimensions)
    if not window_bounds:
        normalized_focus = _strict_normalized_rectangle(
            focus_region.get("normalized_bbox")
        )
        if not normalized_focus:
            normalized_focus = _pixel_rectangle_as_normalized(
                focus_region.get("pixel_bbox"),
                full_dimensions,
            )
        window_bounds = normalized_focus

    if not window_bounds:
        manual_focus = _mapping(payload.get("manual_focus_region"))
        if _explicit_bool(manual_focus.get("enabled")) is not False:
            window_bounds = _strict_normalized_rectangle(
                manual_focus.get("normalized_bbox")
            )

    focus_artifact_bounds: list[float] = []
    if chart_region_bounds and study_dimensions:
        source_dimensions = (
            chart_source[2] - chart_source[0],
            chart_source[3] - chart_source[1],
        ) if exact_scene and chart_source else (
            chart_region_bounds[2] - chart_region_bounds[0],
            chart_region_bounds[3] - chart_region_bounds[1],
        )
        if _rectangle_matches_dimensions(chart_region_bounds, source_dimensions):
            focus_artifact_bounds = _pixel_rectangle_as_normalized(
                chart_region_bounds,
                study_dimensions,
            )

    declared_source_bounds = (chart_source if exact_scene else []) or (
        [
            0.0,
            0.0,
            chart_region_bounds[2] - chart_region_bounds[0],
            chart_region_bounds[3] - chart_region_bounds[1],
        ]
        if chart_region_bounds
        else []
    )
    source_contract = (
        {"source_bounds": declared_source_bounds}
        if declared_source_bounds
        else {}
    )
    return {
        "window": {
            "source_space": "chart",
            "target_space": "window",
            "coordinate_units": "normalized",
            "bounds": window_bounds,
            **source_contract,
        },
        "chart": {
            "source_space": "chart",
            "target_space": "chart_artifact",
            "coordinate_units": "normalized",
            "bounds": focus_artifact_bounds,
            **source_contract,
        },
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
            # Detector/track identifiers are only stable inside one market
            # namespace.  A broker may reuse the same row id immediately
            # after a pair switch, so the public reconciliation id must own
            # the proven pair/timeframe as well as the source row.
            "symbol": public_overlay.get("symbol"),
            "timeframe": public_overlay.get("timeframe"),
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
            # Unknown is a transition state, not a reusable market.  Give an
            # unclassified display frame its own namespace so an old pair's
            # DOM nodes and geometry can never survive a second pair switch
            # merely because both responses read ``Unknown · M5``.
            "unclassified_frame": (
                _frame_id(
                    source.get("display_frame_id"),
                    source.get("chart_frame_id"),
                    source.get("frame_id"),
                )
                if _text(market.get("symbol"), "UNKNOWN").upper()
                in {"", "UNKNOWN"}
                else None
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
    # The compact public live-state contract publishes already-bounded overlay
    # rows directly as a top-level list.  The full tracker snapshot uses an
    # ``{"objects": [...]}`` container.  Accept both representations: treating
    # the public list as a mapping silently dropped every frame-matched mark on
    # the operator endpoint even while its surface version advertised 59 rows.
    direct_rows = _rows(payload.get("overlays"))
    if direct_rows:
        return direct_rows
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


def _wgc_study_source_proves_identity_without_selector_v3(
    payload: Mapping[str, Any],
    tracking_summary: Mapping[str, Any],
    *,
    symbol: str,
    timeframe: str,
    selector_fingerprint: str,
) -> bool:
    """Accept an explicit identity lock from the exact leased WGC study source.

    The normal public overlay contract requires a stable ``selector_v2``
    fingerprint.  A Windows Graphics Capture ROI can deliberately exclude the
    broker selector after the full selected surface has already proved the
    pair and timeframe.  Permit the missing fingerprint only when the compact
    frame carries explicit identity confirmations and the private source lock
    is the exact fail-closed, study-only WGC contract.  This never makes the
    source broker-click-safe.
    """

    if selector_fingerprint or not symbol or not timeframe:
        return False
    if (
        _text(payload.get("instrument_identity_status")).upper() != "LOCKED"
        or _explicit_bool(payload.get("market_identity_confirmed")) is not True
        or _explicit_bool(payload.get("timeframe_identity_confirmed")) is not True
    ):
        return False

    latest_signal = _mapping(payload.get("latest_signal"))
    instrument = _mapping(payload.get("instrument"))
    identity_sources = (payload, tracking_summary, latest_signal, instrument)
    for source in identity_sources:
        if any(
            _explicit_bool(source.get(key)) is True
            for key in (
                "market_selector_rebind_required",
                "market_selector_studying_new_pair",
                "identity_transition_pending",
                "identity_disagreement",
                "market_identity_disagreement",
                "timeframe_identity_disagreement",
                "selector_fingerprint_disagreement",
            )
        ):
            return False
        if _text(source.get("identity_confirmation_source")).upper() == (
            "REJECTED_TRANSITION_OR_DISAGREEMENT"
        ):
            return False

    source_lock = _mapping(tracking_summary.get("broker_source_lock"))
    lock_evidence = _mapping(source_lock.get("evidence"))
    surface_guard = _mapping(source_lock.get("surface_guard"))
    guard_evidence = _mapping(surface_guard.get("evidence"))
    selected_target = _mapping(source_lock.get("selected_target"))
    reason_codes: set[str] = (
        {
            _text(value).upper()
            for value in cast(Sequence[Any], source_lock.get("reason_codes", []))
            if _text(value)
        }
        if isinstance(source_lock.get("reason_codes"), Sequence)
        and not isinstance(
            source_lock.get("reason_codes"),
            (str, bytes, bytearray),
        )
        else set()
    )
    expected_source_id = "windows-region-capture-v3"
    expected_source_type = "windows_graphics_capture_roi"
    expected_coordinate_space = "wgc_hwnd_roi_v1"
    sequence_id = _text(lock_evidence.get("sequence_id"))
    source_proven = bool(
        source_lock.get("schema_version") == "BROKER_SOURCE_LOCK_V3"
        and _explicit_bool(source_lock.get("valid")) is True
        and _text(source_lock.get("status")).upper() == "VALID"
        and _explicit_bool(source_lock.get("broker_source_locked")) is True
        and {
            "EXTERNAL_FRAME_FEED_LOCKED",
            "CHART_STUDY_SOURCE_LOCKED",
        }.issubset(reason_codes)
        and _text(lock_evidence.get("source_id")) == expected_source_id
        and _text(lock_evidence.get("source_type")).lower()
        == expected_source_type
        and _text(lock_evidence.get("coordinate_space")).lower()
        == expected_coordinate_space
        and sequence_id
        and _explicit_bool(lock_evidence.get("study_source_expected")) is True
        and _explicit_bool(lock_evidence.get("chart_source_like")) is True
        and _explicit_bool(lock_evidence.get("study_source_only")) is True
        and _explicit_bool(lock_evidence.get("broker_click_safe")) is False
        and _text(selected_target.get("title")) == expected_source_id
        and bool(_text(selected_target.get("target_id")))
        and _text(surface_guard.get("surface_class")).upper()
        == "BROKER_SURFACE"
        and _explicit_bool(surface_guard.get("capture_safe")) is True
        and _explicit_bool(surface_guard.get("wrong_surface")) is False
        and _explicit_bool(surface_guard.get("broker_like_pixels")) is True
        and _text(guard_evidence.get("source_id")) == expected_source_id
        and _text(guard_evidence.get("source_type")).lower()
        == expected_source_type
        and _text(guard_evidence.get("coordinate_space")).lower()
        == expected_coordinate_space
        and _text(guard_evidence.get("sequence_id")) == sequence_id
        and bool(_text(source_lock.get("broker_pixel_fingerprint")))
    )
    if not source_proven:
        return False

    # These public summaries are optional in older compact payloads.  When
    # present, an explicit stale/invalid/wrong-surface claim must veto the
    # otherwise valid lock rather than being ignored.
    source_claim = _mapping(tracking_summary.get("broker_source"))
    if source_claim:
        if (
            _explicit_bool(source_claim.get("valid")) is not True
            or _text(source_claim.get("status")).upper() != "VALID"
            or _explicit_bool(source_claim.get("wrong_surface")) is not False
            or _explicit_bool(source_claim.get("study_source_only")) is not True
            or _explicit_bool(source_claim.get("broker_click_safe")) is not False
            or _explicit_bool(source_claim.get("pixel_fingerprint_valid"))
            is not True
        ):
            return False
    return True


def _sanitize_overlays(payload: Mapping[str, Any], display_frame: object) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    display_frame_id = _frame_id(display_frame)
    chart_frame = _mapping(payload.get("chart_frame"))
    chart = _mapping(payload.get("chart"))
    artifacts = _mapping(payload.get("artifacts"))
    tracking_summary = _mapping(payload.get("tracking_summary"))
    latest_signal = _mapping(payload.get("latest_signal"))
    aligned_chart_identity = _aligned_current_chart_identity_v3(
        payload, display_frame_id
    )
    current_chart_identity = _current_chart_identity_v3(payload, display_frame_id)
    if aligned_chart_identity and not current_chart_identity:
        # The producer has explicitly opened a new same-frame identity row but
        # has not confirmed it yet.  This is a hard pair-switch boundary: no
        # overlay from an older selector, study, or detector namespace may be
        # projected onto the new bitmap while the pair is being identified.
        return []
    current_symbol = _text(
        current_chart_identity.get("symbol")
        or current_chart_identity.get("market")
        or payload.get("symbol")
        or latest_signal.get("market")
        or latest_signal.get("symbol")
        or tracking_summary.get("detected_market")
    ).upper()
    current_timeframe = _text(
        current_chart_identity.get("timeframe")
        or payload.get("timeframe")
        or latest_signal.get("focus_timeframe")
        or latest_signal.get("timeframe")
        or tracking_summary.get("detected_timeframe")
    ).upper()
    current_selector_fingerprint = _text(
        current_chart_identity.get("market_selector_visual_fingerprint")
        or payload.get("market_selector_visual_fingerprint")
        or latest_signal.get("market_selector_visual_fingerprint")
        or tracking_summary.get("market_selector_visual_fingerprint")
    )

    def canonical_instrument_token(value: object) -> str:
        return "".join(
            character for character in _text(value).upper() if character.isalnum()
        )

    selector_identity_locked = bool(
        _text(payload.get("instrument_identity_status")).upper() == "LOCKED"
        and _explicit_bool(payload.get("market_identity_confirmed")) is True
        and _explicit_bool(payload.get("timeframe_identity_confirmed")) is True
        and bool(current_symbol)
        and bool(current_timeframe)
        and current_selector_fingerprint.startswith(("selector_v2_", "selector_v3_"))
    )
    fast_chart_identity_locked = bool(current_chart_identity)
    wgc_identity_locked_without_selector = (
        _wgc_study_source_proves_identity_without_selector_v3(
            payload,
            tracking_summary,
            symbol=current_symbol,
            timeframe=current_timeframe,
            selector_fingerprint=current_selector_fingerprint,
        )
    )
    compact_overlay_contract = _mapping(payload.get("overlays"))
    compact_overlay_identity_locked = bool(
        compact_overlay_contract
        and _text(payload.get("instrument_identity_status")).upper() == "LOCKED"
        and _explicit_bool(payload.get("market_identity_confirmed")) is True
        and _explicit_bool(payload.get("timeframe_identity_confirmed")) is True
        and _text(
            compact_overlay_contract.get("instrument_identity_status")
        ).upper()
        == "LOCKED"
        and canonical_instrument_token(
            compact_overlay_contract.get("symbol")
        )
        == canonical_instrument_token(current_symbol)
        and _text(compact_overlay_contract.get("timeframe")).upper()
        == current_timeframe
        and _explicit_bool(
            compact_overlay_contract.get("artifact_frame_aligned")
        )
        is True
        and _frame_id(
            compact_overlay_contract.get("overlay_object_frame_id")
        )
        == display_frame_id
        and _frame_id(compact_overlay_contract.get("artifact_frame_id"))
        == display_frame_id
        and _frame_id(payload.get("overlay_frame_id")) == display_frame_id
        and _frame_id(payload.get("overlay_object_frame_id"))
        == display_frame_id
    )
    current_identity_locked = bool(
        selector_identity_locked
        or fast_chart_identity_locked
        or wgc_identity_locked_without_selector
        or compact_overlay_identity_locked
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
    # The merged live projection can contain a bounded public book contract and
    # a geometry-bearing direct contract at the same time.  Do not stop at the
    # first truthy copy: the public copy intentionally retains the rule report
    # while it may omit private source geometry.  Every row still passes the
    # strict frame, instrument, selector, coordinate-plane, and anchor checks
    # below before it can cross the operator boundary.
    book_rule_overlay_rows = [
        row
        for candidate in (
            tracking_summary.get("book_rule_action_signal_v3"),
            latest_signal.get("book_rule_action_signal_v3"),
            payload.get("book_rule_action_signal_v3"),
        )
        for row in _rows(_mapping(candidate).get("overlays"))
    ]
    book_rule_overlay_rows.extend(
        _rows(payload.get("_book_rule_overlay_rows_v3"))
    )
    book_rule_overlay_rows.extend(
        _rows(tracking_summary.get("book_rule_overlay_rows_v3"))
    )

    def book_geometry_is_inside_declared_chart(
        *,
        accepted_bounds: Sequence[float],
        bounds: Sequence[float],
        points: Sequence[Sequence[float]],
        line_points: Sequence[Sequence[float]],
    ) -> bool:
        if len(accepted_bounds) != 4 or len(bounds) != 4:
            return False
        left, top, right, bottom = (float(value) for value in accepted_bounds)
        if right <= left or bottom <= top:
            return False
        tolerance = 0.75
        geometry_points = [
            [float(bounds[0]), float(bounds[1])],
            [float(bounds[2]), float(bounds[3])],
            *points,
            *line_points,
        ]
        return all(
            len(point) >= 2
            and left - tolerance <= float(point[0]) <= right + tolerance
            and top - tolerance <= float(point[1]) <= bottom + tolerance
            for point in geometry_points
        )

    source_rows = [
        *book_rule_overlay_rows,
        *positioning_rows,
        *_overlay_rows(payload),
    ]
    for index, overlay in enumerate(source_rows[:1_000_000]):
        source_object_id = id(overlay)
        raw_type = _text(overlay.get("type") or overlay.get("overlay_type") or overlay.get("kind"), "").upper()
        is_book_rule_overlay = raw_type in {
            "BOOK_RULE_LINE",
            "BOOK_RULE_ZONE",
            "BOOK_RULE_CANDLE",
        }
        is_trendline_overlay = raw_type in {
            "SUPPORT_TRENDLINE",
            "RESISTANCE_TRENDLINE",
            "INNER_TRENDLINE",
        }
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
        selector_identity_matches = bool(
            (
                selector_identity_locked
                and overlay_selector_fingerprint == current_selector_fingerprint
            )
            or (
                fast_chart_identity_locked
                and (
                    not overlay_selector_fingerprint
                    or overlay_selector_fingerprint
                    == current_selector_fingerprint
                )
            )
            or (
                wgc_identity_locked_without_selector
                and not overlay_selector_fingerprint
            )
            or (
                compact_overlay_identity_locked
                and (
                    not overlay_selector_fingerprint
                    or overlay_selector_fingerprint
                    == current_selector_fingerprint
                )
            )
        )
        # Book-rule rows are emitted by the in-process V3 rule engine with an
        # exact selector, symbol, timeframe, and frame lock.  The compact live
        # payload does not repeat the legacy top-level identity booleans, so
        # requiring those redundant aliases drops otherwise stricter rows at
        # the operator bridge.  Accept only the complete row-to-surface match;
        # frame and geometry acceptance are still enforced below.
        book_identity_matches_surface = bool(
            is_book_rule_overlay
            and overlay_identity_locked
            and bool(current_symbol)
            and bool(current_timeframe)
            and bool(current_selector_fingerprint)
            and canonical_instrument_token(overlay_symbol)
            == canonical_instrument_token(current_symbol)
            and overlay_timeframe == current_timeframe
            and overlay_selector_fingerprint == current_selector_fingerprint
        )
        if enforce_instrument_identity_contract and not (
            (
                current_identity_locked
                and overlay_identity_locked
                and canonical_instrument_token(overlay_symbol)
                == canonical_instrument_token(current_symbol)
                and overlay_timeframe == current_timeframe
                and selector_identity_matches
            )
            or book_identity_matches_surface
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
        if is_book_rule_overlay and (historical or stale):
            # Book geometry is a current-scenario surface only. Historical or
            # stale rows must never be redrawn on a live bitmap. Waiting for a
            # changed/closed candle is not staleness when this row still owns
            # the exact displayed frame, selector, identity, and chart plane;
            # rejecting that state makes valid book overlays disappear while
            # an unchanged forming candle is being observed.
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
        book_chart_bounds: list[float] = []
        book_anchor_wick_points: list[list[float]] = []
        book_touch_count = 0
        book_geometry_role = ""
        trendline_chart_bounds: list[float] = []
        trendline_anchor_wick_points: list[list[float]] = []
        trendline_touch_count = 0
        trendline_contract_accepted = False
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
        if is_trendline_overlay:
            anchor_evidence = _mapping(overlay.get("anchor_evidence"))
            anchor_quality = _mapping(overlay.get("anchor_quality"))
            trendline_touch_points = _point_pairs(
                overlay.get("touch_points") or anchor_evidence.get("touch_points")
            )
            explicit_anchor_contract = bool(
                _explicit_bool(anchor_evidence.get("valid")) is True
                and _text(overlay.get("anchor_evidence_status")).upper()
                == "VALID"
                and anchor_quality
            )
            if explicit_anchor_contract:
                trendline_anchor_wick_points = trendline_touch_points[:2]
                trendline_touch_count = max(
                    _integer(overlay.get("touch_count")),
                    len(trendline_touch_points),
                )
                declared_chart_bounds = _bounds(chart_plane_bounds)
                trendline_chart_bounds = (
                    declared_chart_bounds
                    if declared_chart_bounds
                    and book_geometry_is_inside_declared_chart(
                        accepted_bounds=declared_chart_bounds,
                        bounds=public_bounds,
                        points=public_points,
                        line_points=public_line_points,
                    )
                    else list(public_bounds)
                )
                tolerance = 0.75
                anchors_bind_line = bool(
                    len(public_line_points) >= 2
                    and len(trendline_anchor_wick_points) == 2
                    and all(
                        abs(
                            public_line_points[index][axis]
                            - trendline_anchor_wick_points[index][axis]
                        )
                        <= tolerance
                        for index in range(2)
                        for axis in range(2)
                    )
                )
                trendline_contract_accepted = bool(
                    coordinate_space == "chart"
                    and coordinate_units == "pixels"
                    and len(trendline_chart_bounds) == 4
                    and _explicit_bool(anchor_evidence.get("valid")) is True
                    and _text(overlay.get("anchor_evidence_status")).upper()
                    == "VALID"
                    and _explicit_bool(anchor_quality.get("has_wick_anchor"))
                    is True
                    and _explicit_bool(anchor_quality.get("inside_plot_area"))
                    is True
                    and _explicit_bool(
                        anchor_quality.get("matches_symbol_timeframe")
                    )
                    is True
                    and _explicit_bool(
                        anchor_quality.get("matches_selector_fingerprint")
                    )
                    is True
                    and _explicit_bool(
                        anchor_quality.get("chart_transform_valid")
                    )
                    is True
                    and anchors_bind_line
                    and trendline_touch_count >= 2
                    and book_geometry_is_inside_declared_chart(
                        accepted_bounds=trendline_chart_bounds,
                        bounds=public_bounds,
                        points=public_points,
                        line_points=public_line_points,
                    )
                )
                if not trendline_contract_accepted:
                    continue
        if is_book_rule_overlay:
            # Book-rule marks are live execution-facing evidence, so they use a
            # stricter contract than generic context geometry.  No selector
            # fallback, normalized coordinate inference, or cross-frame reuse
            # is permitted here.
            if not (
                _explicit_bool(overlay.get("geometry_contract_accepted")) is True
                and
                book_identity_matches_surface
                and coordinate_space == "chart"
                and coordinate_units == "pixels"
            ):
                continue
            book_chart_bounds = _bounds(overlay.get("chart_bounds"))
            if not book_geometry_is_inside_declared_chart(
                accepted_bounds=book_chart_bounds,
                bounds=public_bounds,
                points=public_points,
                line_points=public_line_points,
            ):
                continue
            if chart_plane_bounds and not book_geometry_is_inside_declared_chart(
                accepted_bounds=chart_plane_bounds,
                bounds=book_chart_bounds,
                points=(),
                line_points=(),
            ):
                continue

            role_evidence = " ".join(
                _text(value, "", limit=120).upper()
                for value in (
                    overlay.get("book_geometry_role"),
                    overlay.get("geometry_role"),
                    overlay.get("role"),
                    overlay.get("label"),
                    overlay.get("book_playbook"),
                    " ".join(str(value) for value in list(overlay.get("book_rule_ids") or [])[:16]),
                )
            )
            if "HLZ" in role_evidence:
                book_geometry_role = "HLZ"
            elif "REJECT" in role_evidence:
                book_geometry_role = "REJECTION"
            elif any(token in role_evidence for token in ("ACTION", "ENTRY", "TRIGGER")):
                book_geometry_role = "ACTION_BOX"
            elif "SUPPLY" in role_evidence:
                book_geometry_role = "SUPPLY"
            elif "DEMAND" in role_evidence:
                book_geometry_role = "DEMAND"
            elif "RESIST" in role_evidence:
                book_geometry_role = "RESISTANCE"
            elif "SUPPORT" in role_evidence:
                book_geometry_role = "SUPPORT"
            elif any(
                token in role_evidence
                for token in (
                    "SMC",
                    "ORDER BLOCK",
                    "ORDER_BLOCK",
                    "BREAKER",
                    "MITIGATION",
                    "FAIR VALUE GAP",
                    "FAIR_VALUE_GAP",
                    "FVG",
                    "LIQUIDITY",
                )
            ):
                book_geometry_role = "SMC"
            elif "WICK" in role_evidence or raw_type == "BOOK_RULE_CANDLE":
                book_geometry_role = "WICK"
            elif raw_type == "BOOK_RULE_LINE":
                book_geometry_role = "WICK_TRENDLINE"
            else:
                book_geometry_role = "REACTION_ZONE"

            if raw_type == "BOOK_RULE_LINE":
                book_anchor_wick_points = _point_pairs(
                    overlay.get("anchor_wick_points"),
                    limit=2,
                )
                book_touch_count = _integer(overlay.get("touch_count"))
                tolerance = 0.75
                distinct_anchors = bool(
                    len(book_anchor_wick_points) == 2
                    and (
                        abs(book_anchor_wick_points[0][0] - book_anchor_wick_points[1][0])
                        > tolerance
                        or abs(book_anchor_wick_points[0][1] - book_anchor_wick_points[1][1])
                        > tolerance
                    )
                )
                anchors_bind_line = bool(
                    len(public_line_points) >= 2
                    and len(book_anchor_wick_points) == 2
                    and all(
                        abs(public_line_points[index][axis] - book_anchor_wick_points[index][axis])
                        <= tolerance
                        for index in range(2)
                        for axis in range(2)
                    )
                )
                if not (
                    _explicit_bool(overlay.get("geometry_contract_accepted")) is True
                    and distinct_anchors
                    and anchors_bind_line
                    and book_touch_count >= 3
                ):
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
                if waiting_for_new_frame and not is_book_rule_overlay
                else "current"
            ),
            "frame_id": overlay_frame,
            "coordinate_space": coordinate_space,
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
        if raw_type in {"BOOK_RULE_LINE", "BOOK_RULE_ZONE", "BOOK_RULE_CANDLE"}:
            public_overlay.update(
                {
                    "label": _safe_public_text(
                        overlay.get("label"),
                        public_kind_label,
                        limit=72,
                    ),
                    "book_rule_ids": [
                        _safe_identifier(value, "book-rule")
                        for value in list(overlay.get("book_rule_ids") or [])[:16]
                    ],
                    "book_playbook": _safe_public_text(
                        overlay.get("book_playbook"),
                        "Book rule",
                        limit=64,
                    ),
                    "book_action_side": _side(
                        overlay.get("book_action_side"),
                        overlay.get("side"),
                    ),
                    "closed_candle_key": _safe_identifier(
                        overlay.get("closed_candle_key"),
                        "closed-candle",
                    ),
                    "book_geometry_role": book_geometry_role,
                    "geometry_contract_accepted": True,
                    "geometry_status": "BOUNDS_ACCEPTED_CURRENT_CHART",
                    "chart_bounds": book_chart_bounds,
                }
            )
        if raw_type == "BOOK_RULE_LINE":
            public_overlay.update(
                {
                    "geometry_status": "ANCHORS_VALID_STRICT_THIRD_TOUCH",
                    "anchor_wick_points": book_anchor_wick_points,
                    "touch_count": book_touch_count,
                    "strict_third_touch_confirmed": True,
                }
            )
        if is_trendline_overlay and trendline_contract_accepted:
            public_overlay.update(
                {
                    "geometry_contract_accepted": True,
                    "geometry_status": "VISIBLE_TO_LATEST_X",
                    "chart_bounds": trendline_chart_bounds,
                    "anchor_wick_points": trendline_anchor_wick_points,
                    "touch_count": trendline_touch_count,
                    "trendline_validation": _safe_public_text(
                        overlay.get("trendline_validation"),
                        "Wick-anchor validation",
                        limit=96,
                    ),
                    "validation_reason": _safe_public_text(
                        overlay.get("validation_reason"),
                        "Two wick anchors bind the visible line",
                        limit=160,
                    ),
                }
            )
        public_overlay.update(
            _overlay_identity_and_revisions(overlay, public_overlay)
        )
        output.append(public_overlay)
    return output


def _book_rule_action_contract_v3(value: object) -> dict[str, object]:
    source = _mapping(value)
    if _text(source.get("schema_version")).upper() != "PG_BOOK_RULE_ACTION_SIGNAL_V3":
        return {}
    scalar_keys = (
        "schema_version",
        "provider_role",
        "priority",
        "status",
        "action",
        "watch_side",
        "actionable",
        "confidence",
        "confidence_percent",
        "score_margin",
        "playbook",
        "scenario",
        "trigger",
        "invalidation",
        "strategy_family_count",
        "active_strategy_count",
        "watching_strategy_count",
        "overlay_count",
        "overlay_contract",
        "technical_indicators_used",
        "execution_authority",
        "frame_id",
        "closed_candle_key",
        "closed_candle_sequence",
        "pair",
        "timeframe",
        "display_frame_id",
        "market_selector_visual_fingerprint",
        "instrument_identity_status",
        "surface_semantic_identity",
    )
    result: dict[str, object] = {
        key: source[key] for key in scalar_keys if key in source
    }
    for key in (
        "opposing_force",
        "structure",
        "strict_trendlines",
        "hlz",
        "candlestick",
        "strategy_report",
        "active_strategy_ids",
        "rule_traceability",
    ):
        bounded = _bounded_hidden_state_value(source.get(key))
        if bounded not in (None, {}, []):
            result[key] = bounded
    return result


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
    *,
    current_symbol: str = "",
    current_timeframe: str = "",
) -> list[dict[str, object]]:
    """Return bounded automatic closed-candle studies in chronological order."""

    resolved_symbol = _text(current_symbol, "").upper()
    resolved_timeframe = _text(current_timeframe, "").upper()
    if not resolved_symbol or not resolved_timeframe:
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
            _instrument_token(study.get("symbol"))
            != _instrument_token(resolved_symbol)
            or _text(study.get("timeframe"), "").upper()
            != resolved_timeframe
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


def _plain_direction(side: str) -> str:
    if side == "BUY":
        return "upward"
    if side == "SELL":
        return "downward"
    return "sideways or unresolved"


def _dominant_side(counts: Mapping[str, int]) -> str:
    directional = {
        side: max(0, int(counts.get(side, 0)))
        for side in _DIRECTIONAL_SIDES
    }
    maximum = max(directional.values(), default=0)
    winners = [side for side, count in directional.items() if count == maximum and count > 0]
    return winners[0] if len(winners) == 1 else "NEUTRAL"


def _path_clock_direction_side_v3(*values: object) -> str:
    for value in values:
        token = _text(value, "", limit=16).upper()
        if token in _DIRECTIONAL_SIDES:
            return token
        if token in {"UP", "UPWARD", "BULLISH"}:
            return "BUY"
        if token in {"DOWN", "DOWNWARD", "BEARISH"}:
            return "SELL"
    return "NEUTRAL"


def _forward_timing_forecast_contract_v3(value: object) -> dict[str, object]:
    """Bound the public forward-timing forecast without importing authority."""

    source = _mapping(value)
    if not source:
        return {}
    status = _safe_public_text(source.get("status"), "", limit=64).upper()
    candidate = _safe_public_text(
        source.get("candidate_direction"), "", limit=16
    ).upper()
    result: dict[str, object] = {
        "schema_version": _safe_public_text(
            source.get("schema_version"),
            "PG_JPCLF_FORWARD_TIMING_FORECAST_V3",
            limit=96,
        ),
        "probability_semantics_version": _safe_public_text(
            source.get("probability_semantics_version"),
            "PG_JPCLF_FORWARD_PROBABILITY_SEMANTICS_V3",
            limit=96,
        ),
        "status": status or "DIRECTION_UNRESOLVED",
        "candidate_direction": candidate,
        "current_regime": _safe_public_text(
            source.get("current_regime"), "UNKNOWN", limit=64
        ).upper(),
        "study_only": True,
        "execution_authority": False,
        "broker_click_authority": False,
        "can_grant_entry_permission": False,
    }
    forecast_horizon_seconds = _number(source.get("forecast_horizon_seconds"))
    if forecast_horizon_seconds is not None and forecast_horizon_seconds > 0.0:
        result["forecast_horizon_seconds"] = int(forecast_horizon_seconds)
        result["forecast_horizon_source"] = "MODEL_STUDY_HORIZON"
    recommended_duration = _number(
        source.get("recommended_trade_duration_seconds")
    )
    result["recommended_trade_duration_seconds"] = (
        int(recommended_duration)
        if recommended_duration is not None and recommended_duration > 0.0
        else None
    )
    broker_expiry = _number(source.get("broker_expiry_seconds"))
    broker_expiry_proven = bool(
        _explicit_bool(source.get("broker_expiry_proven")) is True
        and broker_expiry is not None
        and broker_expiry > 0.0
    )
    # A recommendation counts as proven when it is anchored to the studied
    # model horizon rather than arriving as a bare declared number.
    recommended_trade_duration_proven = bool(
        result["recommended_trade_duration_seconds"] is not None
        and result.get("forecast_horizon_seconds") is not None
    )
    result["recommended_trade_duration_proven"] = (
        recommended_trade_duration_proven
    )
    result["broker_expiry_seconds"] = (
        int(cast(float, broker_expiry)) if broker_expiry_proven else None
    )
    result["duration_provenance"] = {
        "forecast_horizon": (
            "MODEL_STUDY_HORIZON"
            if result.get("forecast_horizon_seconds") is not None
            else "UNAVAILABLE"
        ),
        "recommended_trade_duration": (
            _safe_public_text(
                source.get("recommended_trade_duration_source"),
                "DECLARED_RECOMMENDATION",
                limit=64,
            ).upper()
            if result["recommended_trade_duration_seconds"] is not None
            else "UNAVAILABLE"
        ),
        "recommended_trade_duration_proven": (
            recommended_trade_duration_proven
        ),
        "broker_expiry": (
            _safe_public_text(
                source.get("broker_expiry_source"),
                "BROKER_PROVEN",
                limit=64,
            ).upper()
            if broker_expiry_proven
            else "UNPROVEN"
        ),
        "broker_expiry_proven": broker_expiry_proven,
    }
    lineage = _mapping(source.get("lineage"))
    if lineage:
        result["lineage"] = {
            "symbol": _safe_public_text(lineage.get("symbol"), "", limit=64),
            "timeframe": _safe_public_text(
                lineage.get("timeframe"), "", limit=32
            ).upper(),
            "closed_candle_key": _safe_identifier(
                lineage.get("closed_candle_key"), ""
            ),
            "closed_candle_sequence": _integer(
                lineage.get("closed_candle_sequence")
            ),
            "source_cadence_seconds": _integer(
                lineage.get("source_cadence_seconds")
            ),
            "lineage_bound": _explicit_bool(lineage.get("lineage_bound")) is True,
            "freshness_state": _safe_public_text(
                lineage.get("freshness_state"), "UNBOUND", limit=40
            ).upper(),
            "lineage_digest": _safe_identifier(
                lineage.get("lineage_digest"), ""
            ),
        }
    forecast_digest = _safe_identifier(source.get("forecast_digest"), "")
    if forecast_digest:
        result["forecast_digest"] = forecast_digest

    move_window = _mapping(source.get("move_window"))
    if move_window:
        bounded_window: dict[str, object] = {
            "basis": _safe_public_text(move_window.get("basis"), "", limit=160),
            "relative_to": _safe_public_text(
                move_window.get("relative_to"),
                "CLOSED_CANDLE_ANCHOR",
                limit=64,
            ).upper(),
            # A published forecast is never a rolling wall-clock window.  It is
            # frozen to one completed-candle anchor until a new lineage is
            # published.
            "rolling_wall_clock": False,
            "anchor_time_proven": (
                _explicit_bool(move_window.get("anchor_time_proven")) is True
            ),
            "estimate_calibrated": (
                _explicit_bool(move_window.get("estimate_calibrated")) is True
            ),
            "event_definition": _safe_public_text(
                move_window.get("event_definition"), "UNAVAILABLE", limit=96
            ).upper(),
        }
        for key in ("earliest", "central", "latest"):
            point = _mapping(move_window.get(key))
            bounded_point: dict[str, object] = {}
            for unit in ("seconds", "minutes", "candles"):
                numeric = _number(point.get(unit))
                if numeric is not None and numeric >= 0.0:
                    bounded_point[unit] = round(numeric, 3)
            if bounded_point:
                bounded_window[key] = bounded_point
        anchor_epoch = _epoch(move_window.get("anchor_close_epoch_seconds"))
        start_epoch = _epoch(
            move_window.get("target_window_start_epoch_seconds")
        )
        central_epoch = _epoch(
            move_window.get("target_window_central_epoch_seconds")
        )
        end_epoch = _epoch(
            move_window.get("target_window_end_epoch_seconds")
        )
        lineage_anchor_epoch = _epoch(
            lineage.get("anchor_close_epoch_seconds")
        )
        earliest_seconds = _number(
            _mapping(bounded_window.get("earliest")).get("seconds")
        )
        central_seconds = _number(
            _mapping(bounded_window.get("central")).get("seconds")
        )
        latest_seconds = _number(
            _mapping(bounded_window.get("latest")).get("seconds")
        )
        exact_epoch_contract_valid = bool(
            _explicit_bool(move_window.get("exact_wall_clock_proven")) is True
            and _explicit_bool(move_window.get("anchor_time_proven")) is True
            and _explicit_bool(move_window.get("rolling_wall_clock")) is False
            and anchor_epoch is not None
            and start_epoch is not None
            and central_epoch is not None
            and end_epoch is not None
            and lineage_anchor_epoch is not None
            and abs(anchor_epoch - lineage_anchor_epoch) <= 0.001
            and anchor_epoch < start_epoch <= central_epoch <= end_epoch
            and earliest_seconds is not None
            and central_seconds is not None
            and latest_seconds is not None
            and abs((start_epoch - anchor_epoch) - earliest_seconds) <= 1.0
            and abs((central_epoch - anchor_epoch) - central_seconds) <= 1.0
            and abs((end_epoch - anchor_epoch) - latest_seconds) <= 1.0
        )
        bounded_window["exact_wall_clock_proven"] = exact_epoch_contract_valid
        if exact_epoch_contract_valid:
            bounded_window.update(
                {
                    "anchor_close_epoch_seconds": anchor_epoch,
                    "target_window_start_epoch_seconds": start_epoch,
                    "target_window_central_epoch_seconds": central_epoch,
                    "target_window_end_epoch_seconds": end_epoch,
                }
            )
            if isinstance(result.get("lineage"), dict):
                cast(dict[str, object], result["lineage"])[
                    "anchor_close_epoch_seconds"
                ] = anchor_epoch
        result["move_window"] = bounded_window

    probability = _mapping(source.get("probability"))
    if probability:
        bounded_probability: dict[str, object] = {
            "metric": _safe_public_text(
                probability.get("metric"), "UNAVAILABLE", limit=96
            ).upper(),
            "source_tier": _safe_public_text(
                probability.get("source_tier"), "", limit=64
            ).upper(),
            "calibration_grade": _safe_public_text(
                probability.get("calibration_grade"), "UNRATED", limit=24
            ).upper(),
            "calibrated": _explicit_bool(probability.get("calibrated")) is True,
            "support_count": _integer(probability.get("support_count")),
            "compatibility_alias_for": _safe_public_text(
                probability.get("compatibility_alias_for"),
                "event_likelihood",
                limit=48,
            ),
        }
        for key in ("value", "confidence", "shrinkage_weight"):
            numeric = _number(probability.get(key))
            if numeric is not None:
                bounded_probability[key] = round(
                    max(0.0, min(1.0, numeric)), 6
                )
        result["probability"] = bounded_probability

    directional_model = _mapping(source.get("directional_model"))
    if directional_model:
        directional_score = _number(directional_model.get("score"))
        result["directional_model"] = {
            "candidate_direction": _safe_public_text(
                directional_model.get("candidate_direction"), candidate, limit=16
            ).upper(),
            "score": (
                round(max(0.0, min(1.0, directional_score)), 6)
                if directional_score is not None
                else None
            ),
            "source": _safe_public_text(
                directional_model.get("source"),
                "CURRENT_DIRECTIONAL_ENSEMBLE",
                limit=64,
            ).upper(),
            "is_event_likelihood": False,
        }

    timing_estimate = _mapping(source.get("timing_estimate"))
    if timing_estimate:
        window_blend_weight = _number(
            timing_estimate.get("window_blend_weight")
        )
        result["timing_estimate"] = {
            "source_tier": _safe_public_text(
                timing_estimate.get("source_tier"), "NONE", limit=64
            ).upper(),
            "basis": _safe_public_text(
                timing_estimate.get("basis"), "UNAVAILABLE", limit=128
            ).upper(),
            "event_definition": _safe_public_text(
                timing_estimate.get("event_definition"),
                "UNAVAILABLE",
                limit=96,
            ).upper(),
            "current_target_state": _safe_public_text(
                timing_estimate.get("current_target_state"),
                "UNKNOWN",
                limit=64,
            ).upper(),
            "empirical_timing_evidence": (
                _explicit_bool(timing_estimate.get("empirical_timing_evidence"))
                is True
            ),
            "support_count": _integer(timing_estimate.get("support_count")),
            "current_sequence_candle_count": _integer(
                timing_estimate.get("current_sequence_candle_count")
            ),
            "window_blend_weight": (
                round(max(0.0, min(1.0, window_blend_weight)), 6)
                if window_blend_weight is not None
                else None
            ),
        }

    event_likelihood = _mapping(source.get("event_likelihood"))
    if event_likelihood:
        event_support = _integer(event_likelihood.get("support_count"))
        event_value = _number(event_likelihood.get("value"))
        result["event_likelihood"] = {
            "value": (
                round(max(0.0, min(1.0, event_value)), 6)
                if event_value is not None and event_support > 0
                else None
            ),
            "event": _safe_public_text(
                event_likelihood.get("event"), "UNAVAILABLE", limit=96
            ).upper(),
            "source_tier": _safe_public_text(
                event_likelihood.get("source_tier"), "NONE", limit=64
            ).upper(),
            "support_count": event_support,
            "calibrated": (
                _explicit_bool(event_likelihood.get("calibrated")) is True
            ),
        }

    evidence_confidence = _mapping(source.get("evidence_confidence"))
    if evidence_confidence:
        confidence_support = _integer(evidence_confidence.get("support_count"))
        confidence_value = _number(evidence_confidence.get("value"))
        result["evidence_confidence"] = {
            "value": (
                round(max(0.0, min(1.0, confidence_value)), 6)
                if confidence_value is not None and confidence_support > 0
                else None
            ),
            "basis": _safe_public_text(
                evidence_confidence.get("basis"), "UNAVAILABLE", limit=96
            ).upper(),
            "support_count": confidence_support,
        }

    transition = _mapping(source.get("state_transition_estimate"))
    if transition:
        transition_support = _integer(transition.get("support_count"))
        transition_value = _number(transition.get("value"))
        result["state_transition_estimate"] = {
            "value": (
                round(max(0.0, min(1.0, transition_value)), 6)
                if transition_value is not None and transition_support > 0
                else None
            ),
            "transition": _safe_public_text(
                transition.get("transition"), "UNAVAILABLE", limit=96
            ).upper(),
            "target_count": _integer(transition.get("target_count")),
            "support_count": transition_support,
            "source_tier": _safe_public_text(
                transition.get("source_tier"), "NONE", limit=64
            ).upper(),
            "is_directional_likelihood": False,
        }

    stop_survival = _mapping(source.get("stop_survival"))
    if stop_survival:
        survival_support = _integer(stop_survival.get("support_count"))
        survival_value = _number(stop_survival.get("value"))
        bounded_survival: dict[str, object] = {
            "value": (
                round(max(0.0, min(1.0, survival_value)), 6)
                if survival_value is not None and survival_support > 0
                else None
            ),
            "source_tier": _safe_public_text(
                stop_survival.get("source_tier"), "NONE", limit=64
            ).upper(),
            "support_count": survival_support,
            "exact_wall_clock_proven": (
                _explicit_bool(stop_survival.get("exact_wall_clock_proven"))
                is True
            ),
            "calibrated": _explicit_bool(stop_survival.get("calibrated")) is True,
        }
        for key in ("stop_distance_mru", "move_size_mru"):
            numeric = _number(stop_survival.get(key))
            if numeric is not None and numeric >= 0.0:
                bounded_survival[key] = round(numeric, 6)
        result["stop_survival"] = bounded_survival

    adverse_risk = _mapping(source.get("adverse_excursion_risk"))
    if adverse_risk:
        adverse_support = _integer(adverse_risk.get("support_count"))
        adverse_value = _number(
            adverse_risk.get("worst_drawdown_still_ahead_probability")
        )
        result["adverse_excursion_risk"] = {
            "worst_drawdown_still_ahead_probability": (
                round(max(0.0, min(1.0, adverse_value)), 6)
                if adverse_value is not None and adverse_support > 0
                else None
            ),
            "source_tier": _safe_public_text(
                adverse_risk.get("source_tier"), "NONE", limit=64
            ).upper(),
            "support_count": adverse_support,
        }

    expected_pre_move = _mapping(source.get("expected_pre_move"))
    if expected_pre_move:
        bounded_pre_move: dict[str, object] = {
            "state": _safe_public_text(
                expected_pre_move.get("state"), "UNKNOWN", limit=40
            ).upper(),
            "sweep_risk": _safe_public_text(
                expected_pre_move.get("sweep_risk"), "UNRATED", limit=40
            ).upper(),
            "sweep_source_tier": _safe_public_text(
                expected_pre_move.get("sweep_source_tier"), "NONE", limit=64
            ).upper(),
            "sweep_support_count": _integer(
                expected_pre_move.get("sweep_support_count")
            ),
        }
        for key in ("rest_window_candles", "rest_window_minutes"):
            raw_window = expected_pre_move.get(key)
            interval = _mapping(raw_window)
            if interval:
                bounded_interval = {
                    point: round(numeric, 3)
                    for point in ("earliest", "central", "latest")
                    if (numeric := _number(interval.get(point))) is not None
                    and numeric >= 0.0
                }
                if bounded_interval:
                    bounded_pre_move[key] = bounded_interval
            else:
                numeric = _number(raw_window)
                if numeric is not None and numeric >= 0.0:
                    bounded_pre_move[key] = round(numeric, 6)
        sweep_probability = _number(
            expected_pre_move.get("sweep_probability")
        )
        if (
            sweep_probability is not None
            and sweep_probability >= 0.0
            and _integer(expected_pre_move.get("sweep_support_count")) > 0
        ):
            bounded_pre_move["sweep_probability"] = round(
                max(0.0, min(1.0, sweep_probability)), 6
            )
        result["expected_pre_move"] = bounded_pre_move

    invalidation = _mapping(source.get("invalidation"))
    if invalidation:
        bounded_invalidation: dict[str, object] = {
            "direction": _safe_public_text(
                invalidation.get("direction"), "", limit=16
            ).upper(),
            "condition": _safe_public_text(
                invalidation.get("condition"), "", limit=240
            ),
            "closed_candles_only": (
                _explicit_bool(invalidation.get("closed_candles_only")) is True
            ),
        }
        for key in (
            "adverse_distance_mru",
            "expires_after_seconds",
            "expires_after_candles",
        ):
            numeric = _number(invalidation.get(key))
            if numeric is not None and numeric >= 0.0:
                bounded_invalidation[key] = round(numeric, 6)
        result["invalidation"] = bounded_invalidation

    enter_now = _mapping(source.get("enter_now"))
    if enter_now:
        result["enter_now"] = {
            "permission": _explicit_bool(enter_now.get("permission")) is True,
            "duration_eligible": (
                _explicit_bool(enter_now.get("duration_eligible")) is True
            ),
            "timing_advisory": _safe_public_text(
                enter_now.get("timing_advisory"), "", limit=80
            ).upper(),
            "reason": _safe_public_text(enter_now.get("reason"), "", limit=240),
            "permission_source": _safe_public_text(
                enter_now.get("permission_source"), "", limit=80
            ).upper(),
        }

    hierarchy = _mapping(source.get("evidence_hierarchy"))
    if hierarchy:
        result["evidence_hierarchy"] = {
            "selected_tier": _safe_public_text(
                hierarchy.get("selected_tier"), "", limit=64
            ).upper(),
            "support_count": _integer(hierarchy.get("support_count")),
        }
    return result


def _path_clock_replay_score_contract_v3(value: object) -> dict[str, object]:
    """Keep only bounded four-axis replay evidence for the operator surface."""

    source = _mapping(value)
    if not source:
        return {}
    result: dict[str, object] = {}
    for key in (
        "audited_replay_count",
        "eligible_replay_count",
        "excluded_early_move_count",
        "sweep_outcome_count",
    ):
        if _number(source.get(key)) is not None:
            result[key] = _integer(source.get(key))
    metrics = _mapping(source.get("metrics")) or source
    safe_metrics: dict[str, object] = {}
    for key in (
        "directional_accuracy",
        "timing_accuracy",
        "sweep_survival_rate",
        "calibration_score",
        "expected_calibration_error",
        "brier_score",
    ):
        numeric = _number(metrics.get(key))
        if numeric is not None:
            safe_metrics[key] = round(max(0.0, min(1.0, numeric)), 6)
    if safe_metrics:
        result["metrics"] = safe_metrics
    return result


def _passive_prediction_audit_contract_v3(value: object) -> dict[str, object]:
    """Expose prediction-vs-outcome evidence while retaining no trade authority."""

    source = _mapping(value)
    if (
        not source
        or _safe_public_text(source.get("schema_version"), "", limit=96).upper()
        != "PG_PASSIVE_PREDICTION_OUTCOME_AUDIT_V3"
        or source.get("study_only") is not True
        or source.get("execution_authority") is not False
        or source.get("places_trades") is not False
    ):
        return {}

    result: dict[str, object] = {
        "schema_version": "PG_PASSIVE_PREDICTION_OUTCOME_AUDIT_V3",
        "status": _safe_public_text(
            source.get("status"), "BUILDING_FORECAST_SUPPORT", limit=64
        ).upper(),
        "symbol": _safe_public_text(source.get("symbol"), "", limit=64),
        "timeframe": _safe_public_text(
            source.get("timeframe"), "", limit=32
        ).upper(),
        "frozen_forecast_count": _integer(source.get("frozen_forecast_count")),
        "pending_outcome_count": _integer(source.get("pending_outcome_count")),
        "matured_outcome_count": _integer(source.get("matured_outcome_count")),
        "minimum_promotion_replays": _integer(
            source.get("minimum_promotion_replays")
        ),
        "tracks_market_outcomes_only": True,
        "places_trades": False,
        "study_only": True,
        "execution_authority": False,
        "can_grant_entry_permission": False,
    }

    frozen = _mapping(source.get("latest_frozen_forecast"))
    if frozen:
        frozen_window = _mapping(frozen.get("timing_window_seconds"))
        public_frozen: dict[str, object] = {
            "closed_candle_key": _safe_identifier(
                frozen.get("closed_candle_key"), ""
            ),
            "predicted_direction": _path_clock_direction_side_v3(
                frozen.get("predicted_direction")
            ),
            "horizon_seconds": _integer(frozen.get("horizon_seconds")),
            "sweep_scenario_count": _integer(
                frozen.get("sweep_scenario_count")
            ),
            "frozen_on_closed_candle": (
                _explicit_bool(frozen.get("frozen_on_closed_candle")) is True
            ),
            "future_leakage_detected": (
                _explicit_bool(frozen.get("future_leakage_detected")) is not False
            ),
        }
        closed_at = _number(frozen.get("closed_at_seconds"))
        if closed_at is not None:
            public_frozen["closed_at_seconds"] = round(closed_at, 6)
        for key in ("stop_distance_mru", "move_size_mru"):
            numeric = _number(frozen.get(key))
            if numeric is not None:
                public_frozen[key] = round(numeric, 6)
        if frozen_window:
            public_frozen["timing_window_seconds"] = {
                "start": _integer(frozen_window.get("start")),
                "end": _integer(frozen_window.get("end")),
            }
        result["latest_frozen_forecast"] = public_frozen

    outcome = _mapping(source.get("latest_matured_outcome"))
    if outcome:
        outcome_window = _mapping(outcome.get("timing_window_seconds"))
        public_outcome: dict[str, object] = {
            "closed_candle_key": _safe_identifier(
                outcome.get("closed_candle_key"), ""
            ),
            "horizon_seconds": _integer(outcome.get("horizon_seconds")),
            "predicted_direction": _path_clock_direction_side_v3(
                outcome.get("predicted_direction")
            ),
            "observed_direction": _safe_public_text(
                outcome.get("observed_direction"), "", limit=8
            ).upper(),
            "direction_correct": (
                _explicit_bool(outcome.get("direction_correct")) is True
            ),
            "observed_move_occurred": (
                _explicit_bool(outcome.get("observed_move_occurred")) is True
            ),
            "observed_move_time_seconds": _integer(
                outcome.get("observed_move_time_seconds")
            ),
            "timing_correct": (
                _explicit_bool(outcome.get("timing_correct")) is True
            ),
            "sweep_scenario_count": _integer(
                outcome.get("sweep_scenario_count")
            ),
            "sweep_survived_count": _integer(
                outcome.get("sweep_survived_count")
            ),
            "frozen_on_closed_candle": (
                _explicit_bool(outcome.get("frozen_on_closed_candle")) is True
            ),
            "future_leakage_detected": (
                _explicit_bool(outcome.get("future_leakage_detected")) is not False
            ),
        }
        survival_rate = _number(outcome.get("sweep_survival_rate"))
        if survival_rate is not None:
            public_outcome["sweep_survival_rate"] = round(
                max(0.0, min(1.0, survival_rate)), 6
            )
        if outcome_window:
            public_outcome["timing_window_seconds"] = {
                "start": _integer(outcome_window.get("start")),
                "end": _integer(outcome_window.get("end")),
            }
        result["latest_matured_outcome"] = public_outcome

    for source_key, public_key in (
        ("candidate_metrics", "candidate_metrics"),
        ("baseline_metrics", "baseline_metrics"),
        ("axis_deltas", "axis_deltas"),
    ):
        metrics = _mapping(source.get(source_key))
        safe_metrics: dict[str, object] = {}
        for axis in (
            "directional_accuracy",
            "timing_accuracy",
            "sweep_survival_rate",
            "calibration_score",
        ):
            numeric = _number(metrics.get(axis))
            if numeric is not None:
                safe_metrics[axis] = round(
                    max(-1.0, min(1.0, numeric)), 6
                )
        if safe_metrics:
            result[public_key] = safe_metrics
    return result


def path_clock_liquidity_contract_v3(value: object) -> dict[str, object]:
    """Return the compact public JPCLF contract and discard trajectory internals.

    The operator projection deliberately retains only one current timing read,
    its promotion proof summary, and bounded replay-calibration metrics.  Raw
    trajectories, neighbours, liquidity vectors, freezes, and persistence data
    never cross this boundary.
    """

    source = _mapping(value)
    if (
        not source
        or source.get("study_only") is not True
        or source.get("execution_authority") is not False
        or source.get("can_grant_entry_permission") is True
    ):
        return {}

    scope = _mapping(source.get("scope"))
    lineage = _mapping(source.get("lineage"))
    duration_policy = _mapping(source.get("duration_policy"))
    timing = (
        _mapping(source.get("timing_read"))
        or _mapping(source.get("current_estimate"))
        or _mapping(source.get("live_estimate"))
        or _mapping(source.get("recommended_scenario"))
        or _mapping(source.get("recommendation"))
    )
    if not timing and any(
        key in source
        for key in (
            "contract_duration_seconds",
            "remaining_seconds",
            "survival_probability",
            "timing_supports_entry",
            "timing_veto",
        )
    ):
        timing = source

    minimum_duration = _number(
        source.get("minimum_eligible_duration_seconds")
        if source.get("minimum_eligible_duration_seconds") is not None
        else source.get("minimum_duration_seconds")
        if source.get("minimum_duration_seconds") is not None
        else duration_policy.get("minimum_eligible_duration_seconds")
    )
    maximum_duration = _number(
        source.get("maximum_studied_duration_seconds")
        if source.get("maximum_studied_duration_seconds") is not None
        else duration_policy.get("maximum_studied_duration_seconds")
    )
    symbol = _safe_public_text(
        source.get("symbol") or scope.get("symbol") or lineage.get("symbol"),
        "",
        limit=64,
    )
    timeframe = _safe_public_text(
        source.get("timeframe") or scope.get("timeframe") or lineage.get("timeframe"),
        "",
        limit=32,
    ).upper()
    closed_candle_key = _safe_identifier(
        source.get("closed_candle_key")
        or lineage.get("closed_candle_key")
        or timing.get("closed_candle_key"),
        "",
    )
    result: dict[str, object] = {
        "schema_version": _safe_public_text(
            source.get("schema_version"),
            "PG_PATH_CLOCK_LIQUIDITY_FIELD_V3",
            limit=96,
        ),
        "status": _safe_public_text(source.get("status"), "PENDING", limit=64).upper(),
        "reason": _safe_public_text(source.get("reason"), "", limit=320),
        "study_only": True,
        "execution_authority": False,
        "can_grant_entry_permission": False,
        "symbol": symbol,
        "timeframe": timeframe,
        "closed_candle_key": closed_candle_key,
        "closed_candle_sequence": _integer(
            source.get("closed_candle_sequence")
            if source.get("closed_candle_sequence") is not None
            else lineage.get("closed_candle_sequence")
        ),
        "minimum_eligible_duration_seconds": (
            int(minimum_duration)
            if minimum_duration is not None and minimum_duration.is_integer()
            else round(minimum_duration, 6)
            if minimum_duration is not None
            else None
        ),
        "maximum_studied_duration_seconds": (
            int(maximum_duration)
            if maximum_duration is not None and maximum_duration.is_integer()
            else round(maximum_duration, 6)
            if maximum_duration is not None
            else None
        ),
        "mature": _explicit_bool(source.get("mature")) is True,
        "promoted": _explicit_bool(source.get("promoted")) is True,
        "freshness_state": _safe_public_text(
            source.get("freshness_state") or source.get("freshness"),
            "",
            limit=32,
        ).upper(),
    }
    if duration_policy:
        duration_policy_status = _safe_public_text(
            duration_policy.get("status"), "", limit=64
        ).upper()
    else:
        duration_policy_status = _safe_public_text(
            source.get("duration_policy_status"), "", limit=64
        ).upper()
    if duration_policy_status:
        result["duration_policy_status"] = duration_policy_status
    duration_policy_eligible = _explicit_bool(
        duration_policy.get("new_entry_eligible")
        if duration_policy.get("new_entry_eligible") is not None
        else source.get("duration_policy_eligible")
    )
    if duration_policy_eligible is not None:
        result["duration_policy_eligible"] = duration_policy_eligible

    if timing:
        timing_side = _path_clock_direction_side_v3(
            timing.get("side"),
            timing.get("studied_direction"),
            timing.get("direction"),
        )
        timing_read: dict[str, object] = {
            "status": _safe_public_text(timing.get("status"), "PENDING", limit=64).upper(),
            "state": _safe_public_text(timing.get("state"), "", limit=64).upper(),
            "reason": _safe_public_text(timing.get("reason"), "", limit=320),
            "side": timing_side,
        }
        scalar_integer_fields = (
            "contract_duration_seconds",
            "candidate_horizon_seconds",
            "elapsed_seconds",
            "remaining_seconds",
            "support_count",
            "minimum_support",
            "audited_neighbor_count",
            "excluded_early_target_count",
        )
        for key in scalar_integer_fields:
            raw = timing.get(key)
            if _number(raw) is not None:
                timing_read[key] = _integer(raw)
        for key in (
            "current_path_mru",
            "stop_distance_mru",
            "move_size_mru",
            "survival_probability",
            "probability_worst_drawdown_still_ahead",
        ):
            numeric = _number(timing.get(key))
            if numeric is not None:
                timing_read[key] = round(numeric, 6)
        for key in ("observed_at", "valid_until"):
            numeric = _number(timing.get(key))
            if numeric is not None:
                timing_read[key] = round(numeric, 6)
        for key in (
            "eligible",
            "contract_admitted",
            "new_entry_eligible",
            "timing_supports_entry",
            "timing_veto",
        ):
            boolean = _explicit_bool(timing.get(key))
            if boolean is not None:
                timing_read[key] = boolean
        target_time = _mapping(timing.get("target_time_seconds"))
        if target_time:
            timing_read["target_time_seconds"] = {
                key: round(numeric, 3)
                for key in ("p10", "median", "p90")
                if (numeric := _number(target_time.get(key))) is not None
            }
        result["timing_read"] = timing_read

    promotion_source = (
        _mapping(source.get("promotion_gate"))
        or _mapping(source.get("promotion"))
        or _mapping(source.get("maturation_gate"))
    )
    if promotion_source:
        promotion: dict[str, object] = {
            "status": _safe_public_text(
                promotion_source.get("status"), "RETAIN_BASELINE", limit=64
            ).upper(),
        }
        for key in ("passed", "all_axes_improved"):
            boolean = _explicit_bool(promotion_source.get(key))
            if boolean is not None:
                promotion[key] = boolean
        for key in ("minimum_replays", "eligible_replay_count"):
            if _number(promotion_source.get(key)) is not None:
                promotion[key] = _integer(promotion_source.get(key))
        support = _mapping(promotion_source.get("support"))
        if support:
            promotion["support"] = {
                key: _integer(raw)
                for key in ("baseline", "candidate")
                if (raw := support.get(key)) is not None and _number(raw) is not None
            } | ({"passed": True} if support.get("passed") is True else {})
        result["promotion_gate"] = promotion

    replay_source = (
        _mapping(source.get("replay_score"))
        or _mapping(source.get("candidate_replay_score"))
        or _mapping(source.get("calibration"))
        or _mapping(source.get("replay_calibration"))
    )
    if replay_source:
        replay = _path_clock_replay_score_contract_v3(replay_source)
        if replay:
            result["replay_calibration"] = replay
    baseline_replay = _path_clock_replay_score_contract_v3(
        source.get("baseline_replay_score")
    )
    candidate_replay = _path_clock_replay_score_contract_v3(
        source.get("candidate_replay_score")
    )
    if baseline_replay:
        result["baseline_replay_calibration"] = baseline_replay
    if candidate_replay:
        result["candidate_replay_calibration"] = candidate_replay
    passive_audit = _passive_prediction_audit_contract_v3(
        source.get("passive_prediction_audit_v3")
    )
    if passive_audit:
        result["passive_prediction_audit_v3"] = passive_audit
    forward_forecast = _forward_timing_forecast_contract_v3(
        source.get("forward_timing_forecast")
        or timing.get("forward_timing_forecast")
    )
    if forward_forecast:
        result["forward_timing_forecast"] = forward_forecast
        if isinstance(result.get("timing_read"), dict):
            cast(dict[str, object], result["timing_read"])[
                "forward_timing_forecast"
            ] = forward_forecast
    return result


def _path_clock_timing_effect_v3(
    timing_contract: Mapping[str, object],
    *,
    current_symbol: str,
    current_timeframe: str,
    study_closed_candle_key: str,
    studied_side: str,
    freshness_state: str,
    now_epoch: float,
) -> dict[str, object]:
    """Evaluate JPCLF as an asymmetric timing brake, never an authority source."""

    if not timing_contract:
        return {
            "present": False,
            "mature": False,
            "timing_supports_entry": False,
            "timing_veto": False,
            "state": "UNAVAILABLE",
            "reason": "No mature timing study is published yet.",
        }

    timing = _mapping(timing_contract.get("timing_read"))
    promotion = _mapping(timing_contract.get("promotion_gate"))
    contract_status = _text(timing_contract.get("status"), "PENDING").upper()
    timing_status = _text(timing.get("status"), "PENDING").upper()
    timing_side = _path_clock_direction_side_v3(timing.get("side"))
    source_symbol = _safe_public_text(timing_contract.get("symbol"), "", limit=64)
    source_timeframe = _safe_public_text(
        timing_contract.get("timeframe"), "", limit=32
    ).upper()
    source_key = _safe_identifier(timing_contract.get("closed_candle_key"), "")
    lineage_matches = bool(
        source_symbol
        and source_timeframe
        and source_key
        and _instrument_token(source_symbol) == _instrument_token(current_symbol)
        and source_timeframe == current_timeframe.upper()
        and source_key == study_closed_candle_key
        and timing_side in _DIRECTIONAL_SIDES
        and timing_side == studied_side
    )
    source_freshness = _text(
        timing_contract.get("freshness_state"), "", limit=32
    ).upper()
    timing_valid_until = _number(timing.get("valid_until"))
    freshness_matches = bool(
        freshness_state == "FRESH"
        and source_freshness not in {"STALE", "EXPIRED", "WAITING", "INVALID"}
        and (timing_valid_until is None or timing_valid_until > now_epoch)
    )
    promotion_passed = bool(
        promotion.get("passed") is True
        and promotion.get("all_axes_improved") is True
        and _text(promotion.get("status"), "", limit=64).upper()
        in {"PROMOTION_ELIGIBLE", "PROMOTED", "PASSED"}
    ) or bool(
        timing_contract.get("promoted") is True
        and timing_contract.get("mature") is True
    )
    maturity_claimed = bool(
        timing_contract.get("mature") is True
        or contract_status in {"MATURE", "PROMOTED", "READY"}
        or (
            timing_status
            in {
                "STUDIED",
                "MATURE",
                "SUPPORTED",
                "READY",
                "TIMING_SUPPORT",
                "TIMING_VETO",
            }
            and promotion_passed
        )
        or (
            contract_status == "STUDIED"
            and timing_status in {"TIMING_SUPPORT", "TIMING_VETO"}
            and promotion_passed
        )
    )
    eligible = timing.get("eligible") is True
    explicit_support = _explicit_bool(timing.get("timing_supports_entry"))
    explicit_veto = _explicit_bool(timing.get("timing_veto"))
    minimum_duration = _number(
        timing_contract.get("minimum_eligible_duration_seconds")
    )
    maximum_duration = _number(
        timing_contract.get("maximum_studied_duration_seconds")
    )
    duration_policy_status = _text(
        timing_contract.get("duration_policy_status"), "", limit=64
    ).upper()
    contract_duration = _number(timing.get("contract_duration_seconds"))
    remaining_seconds = _number(timing.get("remaining_seconds"))
    duration_policy_valid = bool(
        minimum_duration == float(MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS)
        and (
            maximum_duration is None
            or maximum_duration == float(MAXIMUM_STUDIED_TRADE_DURATION_SECONDS)
        )
        and contract_duration is not None
        and MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS
        <= contract_duration
        <= MAXIMUM_STUDIED_TRADE_DURATION_SECONDS
    )
    remaining_window_eligible = bool(
        remaining_seconds is not None
        and remaining_seconds >= MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS
    )
    timing_evidence_unavailable = bool(
        contract_status == "CENSORED_INVALID_TIMING_EVIDENCE"
        or duration_policy_status == "NOT_ALIGNED_TO_CLOSED_CANDLE_GRID"
        or timing.get("contract_admitted") is False
        or timing_status
        in {
            "INSUFFICIENT_PROVEN_CLOSED_CANDLE_EVIDENCE",
            "INVALID_TIMING_EVIDENCE",
            "UNPROVEN_CLOSED_CANDLE_TIME",
        }
    )
    fully_mature = bool(
        maturity_claimed
        and promotion_passed
        and timing_status
        in {
            "STUDIED",
            "MATURE",
            "SUPPORTED",
            "READY",
            "TIMING_SUPPORT",
            "TIMING_VETO",
        }
        and eligible
        and lineage_matches
        and freshness_matches
        and duration_policy_valid
        and remaining_window_eligible
        and not timing_evidence_unavailable
        and (explicit_support is not None or explicit_veto is not None)
    )
    # Timing is an asymmetric brake only when duration is explicitly
    # ineligible or a promoted, lineage-matched study publishes a veto.
    # Missing calibration or an unproven clock downgrades the forecast; it
    # must not silently cancel independently authorized entry permission.
    explicit_mature_veto = bool(fully_mature and explicit_veto is True)
    known_duration_veto = bool(
        timing
        and (
            duration_policy_status
            in {
                "EXCLUDED_UNDER_15_MINUTES",
                "EXCLUDED_ABOVE_BOUNDED_HORIZON",
            }
            or (
                contract_duration is not None
                and contract_duration < MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS
            )
            or (
                contract_duration is not None
                and contract_duration > MAXIMUM_STUDIED_TRADE_DURATION_SECONDS
            )
            or (
                remaining_seconds is not None
                and remaining_seconds < MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS
            )
        )
    )
    timing_veto = bool(explicit_mature_veto or known_duration_veto)
    timing_supports = bool(
        fully_mature and explicit_support is True and explicit_veto is not True
    )

    if (
        minimum_duration != float(MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS)
        or (
            maximum_duration is not None
            and maximum_duration != float(MAXIMUM_STUDIED_TRADE_DURATION_SECONDS)
        )
        or contract_duration is None
        or contract_duration > MAXIMUM_STUDIED_TRADE_DURATION_SECONDS
    ):
        state = "PROVISIONAL"
        reason = (
            "Exact timing calibration is unavailable; use the completed-candle "
            "forecast and keep entry permission separate."
        )
    elif (
        contract_duration < MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS
        or not remaining_window_eligible
        or duration_policy_status == "EXCLUDED_UNDER_15_MINUTES"
    ):
        state = "UNDER_15_MINUTES"
        reason = "Do not start this move with less than 15 minutes of room."
    elif timing_evidence_unavailable:
        state = "PROVISIONAL"
        reason = (
            "Exact contiguous closed-candle timing is not proven for this chart yet; "
            "the forecast remains provisional and no survival probability is invented."
        )
    elif maturity_claimed and not lineage_matches:
        state = "PROVISIONAL"
        reason = (
            "The prior timing study does not match this pair, timeframe, candle, and "
            "direction, so only the current completed-candle forecast is shown."
        )
    elif maturity_claimed and not freshness_matches:
        state = "PROVISIONAL"
        reason = (
            "The prior timing read is stale; the current completed-candle forecast "
            "remains visible without calibrated timing claims."
        )
    elif fully_mature and timing_veto:
        state = "DELAY"
        reason = _safe_public_text(
            timing.get("reason"),
            "Historical path, clock, and liquidity evidence says delay this entry.",
            limit=320,
        )
    elif fully_mature and timing_supports:
        state = "SUPPORTED"
        reason = _safe_public_text(
            timing.get("reason"),
            "Historical path, clock, and liquidity evidence supports this timing.",
            limit=320,
        )
    else:
        state = "BUILDING"
        reason = "Timing history is still building and cannot grant entry permission."

    target_time = _mapping(timing.get("target_time_seconds"))
    replay = _mapping(timing_contract.get("replay_calibration"))
    replay_metrics = _mapping(replay.get("metrics"))
    forward_forecast = _forward_timing_forecast_contract_v3(
        timing_contract.get("forward_timing_forecast")
        or timing.get("forward_timing_forecast")
    )
    return {
        "present": True,
        "mature": fully_mature,
        "maturity_claimed": maturity_claimed,
        "lineage_matches": lineage_matches,
        "fresh": freshness_matches,
        "duration_policy_valid": duration_policy_valid,
        "remaining_window_eligible": remaining_window_eligible,
        "timing_evidence_proven": not timing_evidence_unavailable,
        "source_status": contract_status,
        "source_timing_status": timing_status,
        "minimum_duration_seconds": MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS,
        "contract_duration_seconds": (
            int(contract_duration) if contract_duration is not None else None
        ),
        "remaining_seconds": (
            int(remaining_seconds) if remaining_seconds is not None else None
        ),
        "valid_until": timing_valid_until,
        "side": timing_side,
        "timing_supports_entry": timing_supports,
        "timing_veto": timing_veto,
        "timing_veto_basis": (
            "EXPLICIT_MATURE_VETO"
            if explicit_mature_veto
            else "DURATION_INELIGIBLE"
            if known_duration_veto
            else "NONE"
        ),
        "survival_probability": (
            round(value, 6)
            if (value := _number(timing.get("survival_probability"))) is not None
            else None
        ),
        "probability_worst_drawdown_still_ahead": (
            round(value, 6)
            if (
                value := _number(
                    timing.get("probability_worst_drawdown_still_ahead")
                )
            )
            is not None
            else None
        ),
        "support_count": _integer(timing.get("support_count")),
        "target_time_seconds": {
            key: round(value, 3)
            for key in ("p10", "median", "p90")
            if (value := _number(target_time.get(key))) is not None
        },
        "replay_calibration": {
            "audited_replay_count": _integer(replay.get("audited_replay_count")),
            "eligible_replay_count": _integer(replay.get("eligible_replay_count")),
            "metrics": {
                key: round(value, 6)
                for key in (
                    "directional_accuracy",
                    "timing_accuracy",
                    "sweep_survival_rate",
                    "calibration_score",
                    "expected_calibration_error",
                    "brier_score",
                )
                if (value := _number(replay_metrics.get(key))) is not None
            },
        },
        "forward_timing_forecast": forward_forecast,
        "state": state,
        "reason": reason,
    }


def _timeframe_seconds_v3(value: object) -> int | None:
    token = _safe_public_text(value, "", limit=16).upper().replace(" ", "")
    match = re.fullmatch(r"([MHD])(\d{1,3})", token)
    if not match:
        return None
    amount = int(match.group(2))
    if amount <= 0:
        return None
    multiplier = {"M": 60, "H": 3_600, "D": 86_400}[match.group(1)]
    return amount * multiplier


def _calibration_grade_v3(
    probability: Mapping[str, object],
    _timing_effect: Mapping[str, object],
) -> tuple[str, bool, int]:
    source_grade = _safe_public_text(
        probability.get("calibration_grade"), "", limit=24
    ).upper()
    calibrated = probability.get("calibrated") is True
    support_count = _integer(probability.get("support_count"))
    if source_grade and source_grade not in {"UNRATED", "UNKNOWN"}:
        return source_grade, calibrated, support_count
    # Exact JPCLF replay calibration belongs to stop-survival telemetry.  It
    # must never leak into the distinct event-likelihood grade.
    return "UNRATED", False, support_count


def _calibration_grade_label_v3(grade: str) -> str:
    labels = {
        "A_PROMOTED_PAIRED_REPLAY": "A \u00b7 promoted paired replay",
        "B_SHRUNK_MOTIF_OR_JPCLF": "B \u00b7 shrunk motif and timing history",
        "C_SHRUNK_PAIR_REGIME": "C \u00b7 shrunk pair-regime history",
        "C_SPARSE_PAIR": "C \u00b7 sparse pair history",
        "D_CURRENT_SEQUENCE": "D \u00b7 current sequence",
        "D_UNCALIBRATED_POOLED_PRIOR": "D \u00b7 uncalibrated pooled prior",
    }
    if grade in labels:
        return labels[grade]
    if re.fullmatch(r"[A-E]", grade):
        return grade
    return grade.replace("_", " ").title() if grade else "UNRATED"


def _timing_source_label_v3(source_tier: str, *, empirical: bool) -> str:
    labels = {
        "EXACT_JPCLF": "Exact pair path-clock timing",
        "PAIR_MOTIF_JPCLF": "Matched pair motif and path-clock history",
        "PAIR_JPCLF": "Pair path-clock history",
        "PAIR_REGIME_MOTIF_JPCLF": "Pair regime, motif, and path-clock history",
        "PAIR_REGIME_JPCLF": "Pair regime and path-clock history",
        "PAIR_MOTIF": "Matched pair motif history",
        "PAIR_REGIME_MOTIF": "Matched pair-regime motif history",
        "PAIR_STATE_SURVIVAL": "Pair state-survival timing history",
        "PAIR_REGIME": "Pair regime timing history",
        "PAIR": "Pair behavior timing history",
        "LIVE_M5_SEQUENCE": "Current M5 closed-candle sequence",
        "POLICY_WINDOW": "Timing unrated · model horizon only",
        "NONE": "No timing source",
    }
    label = labels.get(
        source_tier,
        source_tier.replace("_", " ").title() if source_tier else "No timing source",
    )
    if source_tier not in {"", "NONE", "POLICY_WINDOW", "LIVE_M5_SEQUENCE"}:
        label += " · empirical" if empirical else " · not empirical"
    return label


def _event_metric_label_v3(metric: str) -> str:
    labels = {
        "MOTIF_TARGET_FOLLOW_THROUGH_WITHIN_FORECAST_HORIZON": (
            "motif target follow-through within the forecast horizon"
        ),
        "DIRECTION_CHANGE_BY_FORECAST_HORIZON": (
            "direction change by the forecast horizon"
        ),
        "SWING_BY_FORECAST_HORIZON": "swing completion by the forecast horizon",
        "UNAVAILABLE": "the named event",
    }
    return labels.get(
        metric,
        metric.replace("_", " ").lower() if metric else "the named event",
    )


def _forecast_window_point_v3(
    value: object,
    *,
    timeframe_seconds: int | None,
) -> tuple[int | None, int | None, int | None]:
    point = _mapping(value)
    seconds = _number(point.get("seconds"))
    minutes = _number(point.get("minutes"))
    candles = _number(point.get("candles"))
    if seconds is None and minutes is not None:
        seconds = minutes * 60.0
    if seconds is None and candles is not None and timeframe_seconds is not None:
        seconds = candles * timeframe_seconds
    if minutes is None and seconds is not None:
        minutes = seconds / 60.0
    if candles is None and seconds is not None and timeframe_seconds:
        candles = seconds / timeframe_seconds
    return (
        max(0, int(round(seconds))) if seconds is not None else None,
        max(0, int(round(minutes))) if minutes is not None else None,
        max(0, int(round(candles))) if candles is not None else None,
    )


def _fixed_exact_window_read_v3(
    *,
    side: str,
    start_epoch: float,
    end_epoch: float,
    now_epoch: float,
) -> dict[str, object]:
    """Render a countdown from fixed epochs without creating a rolling window."""

    seconds_until_start = max(0, int(math.ceil(start_epoch - now_epoch)))
    seconds_until_end = max(0, int(math.ceil(end_epoch - now_epoch)))
    expired = now_epoch >= end_epoch
    if expired:
        countdown_label = "Exact anchor-bound window expired"
        headline = (
            f"{side} remains the studied path · exact timing expired"
            if side in _DIRECTIONAL_SIDES
            else "Direction unresolved · exact timing expired"
        )
    elif now_epoch < start_epoch:
        open_minutes = max(1, int(math.ceil(seconds_until_start / 60.0)))
        close_minutes = max(1, int(math.ceil(seconds_until_end / 60.0)))
        countdown_label = (
            f"Fixed window opens in {open_minutes} min · closes in "
            f"{close_minutes} min"
        )
        headline = f"{side} leading path · {countdown_label.lower()}"
    else:
        close_minutes = max(1, int(math.ceil(seconds_until_end / 60.0)))
        countdown_label = f"Fixed window active · closes in {close_minutes} min"
        headline = f"{side} leading path · {countdown_label.lower()}"
    return {
        "expired": expired,
        "headline": headline,
        "countdown_label": countdown_label,
        "seconds_until_window_start": seconds_until_start,
        "seconds_until_window_end": seconds_until_end,
    }


def _operator_timing_forecast_v3(
    *,
    studied_side: str,
    current_symbol: str,
    current_timeframe: str,
    closed_candle_key: str,
    identity_proven: bool,
    behavior_state: str,
    behavior_side: str,
    directional_confidence: float,
    timing_effect: Mapping[str, object],
    now_epoch: float,
) -> dict[str, object]:
    """Build one bounded forecast headline without implying entry authority."""

    timeframe_seconds = _timeframe_seconds_v3(current_timeframe)
    forward = _mapping(timing_effect.get("forward_timing_forecast"))
    forward_status = _text(forward.get("status"), "", limit=64).upper()
    forward_side = _path_clock_direction_side_v3(
        forward.get("candidate_direction")
    )
    move_window = _mapping(forward.get("move_window"))
    earliest = _forecast_window_point_v3(
        move_window.get("earliest"), timeframe_seconds=timeframe_seconds
    )
    latest = _forecast_window_point_v3(
        move_window.get("latest"), timeframe_seconds=timeframe_seconds
    )
    anchor_close_epoch = _epoch(
        move_window.get("anchor_close_epoch_seconds")
    )
    target_window_start_epoch = _epoch(
        move_window.get("target_window_start_epoch_seconds")
    )
    target_window_central_epoch = _epoch(
        move_window.get("target_window_central_epoch_seconds")
    )
    target_window_end_epoch = _epoch(
        move_window.get("target_window_end_epoch_seconds")
    )
    exact_wall_clock = bool(
        move_window.get("exact_wall_clock_proven") is True
        and anchor_close_epoch is not None
        and target_window_start_epoch is not None
        and target_window_central_epoch is not None
        and target_window_end_epoch is not None
        and anchor_close_epoch
        < target_window_start_epoch
        <= target_window_central_epoch
        <= target_window_end_epoch
    )
    exact_window_read = (
        _fixed_exact_window_read_v3(
            side=forward_side,
            start_epoch=cast(float, target_window_start_epoch),
            end_epoch=cast(float, target_window_end_epoch),
            now_epoch=now_epoch,
        )
        if exact_wall_clock
        else {}
    )
    exact_window_expired = exact_window_read.get("expired") is True
    forecast_lineage = _mapping(forward.get("lineage"))
    forecast_lineage_freshness = _safe_public_text(
        forecast_lineage.get("freshness_state"), "UNBOUND", limit=40
    ).upper()
    forecast_lineage_matches = bool(
        forecast_lineage.get("lineage_bound") is True
        and _instrument_token(forecast_lineage.get("symbol"))
        == _instrument_token(current_symbol)
        and _safe_public_text(
            forecast_lineage.get("timeframe"), "", limit=32
        ).upper()
        == current_timeframe.upper()
        and _safe_identifier(
            forecast_lineage.get("closed_candle_key"), ""
        )
        == closed_candle_key
        and forecast_lineage_freshness
        in {"CURRENT", "FRESH", "CURRENT_CLOSED_CANDLE"}
    )
    # A JPCLF forward field is current only when its full completed-candle
    # identity still matches.  This prevents a prior pair's probability,
    # timing, risk, or invalidation from surviving an A -> B chart switch.
    forward_window_eligible = bool(
        earliest[0] is not None
        and latest[0] is not None
        and earliest[0] >= MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS
        and latest[0] <= MAXIMUM_STUDIED_TRADE_DURATION_SECONDS
        and latest[0] >= earliest[0]
    )
    forward_identity_valid = bool(
        identity_proven
        and forecast_lineage_matches
        and forward_side in _DIRECTIONAL_SIDES
        and forward_side == studied_side
        and forward_status
        in {
            "FORECAST_AVAILABLE",
            "TIMING_UNRATED",
            "TARGET_MOVE_ALREADY_ACTIVE",
        }
    )
    use_forward = bool(
        forward_identity_valid
        and forward_status == "FORECAST_AVAILABLE"
        and forward_window_eligible
        and not exact_window_expired
    )
    side = (
        forward_side
        if forward_identity_valid
        else studied_side
        if identity_proven and studied_side in _DIRECTIONAL_SIDES
        else "NEUTRAL"
    )
    probability: Mapping[str, object] = (
        _mapping(forward.get("probability")) if forward_identity_valid else {}
    )
    event_likelihood: Mapping[str, object] = (
        _mapping(forward.get("event_likelihood"))
        if forward_identity_valid
        else {}
    )
    evidence_contract: Mapping[str, object] = (
        _mapping(forward.get("evidence_confidence"))
        if forward_identity_valid
        else {}
    )
    directional_model: Mapping[str, object] = (
        _mapping(forward.get("directional_model"))
        if forward_identity_valid
        else {}
    )
    timing_estimate: Mapping[str, object] = (
        _mapping(forward.get("timing_estimate"))
        if forward_identity_valid
        else {}
    )
    timing_event_definition = _safe_public_text(
        timing_estimate.get("event_definition")
        or move_window.get("event_definition"),
        "UNAVAILABLE",
        limit=96,
    ).upper()
    active_target_next_impulse = bool(
        timing_event_definition == _NEXT_IMPULSE_AFTER_ACTIVE_TARGET_EVENT
    )
    target_move_already_active = bool(
        active_target_next_impulse
        or forward_status == "TARGET_MOVE_ALREADY_ACTIVE"
        or _safe_public_text(
            timing_estimate.get("current_target_state"), "", limit=64
        ).upper()
        == "ALREADY_ACTIVE_AT_ANCHOR"
    )
    if exact_window_expired:
        probability = {}
        event_likelihood = {}
        evidence_contract = {}
    calibration_grade, calibrated, support_count = _calibration_grade_v3(
        probability,
        timing_effect if forward_identity_valid else {},
    )
    event_support_count = max(
        _integer(event_likelihood.get("support_count")),
        _integer(probability.get("support_count")),
    )
    # These are intentionally separate quantities.  Event likelihood and
    # evidence confidence require empirical outcome support.  Directional model
    # score is preserved independently and is never promoted into probability.
    estimated_likelihood = (
        _confidence(
            event_likelihood.get("value"), probability.get("value")
        )
        if event_support_count > 0
        else None
    )
    evidence_confidence = (
        _confidence(
            evidence_contract.get("value"), probability.get("confidence")
        )
        if event_support_count > 0
        else None
    )
    directional_model_score = _confidence(
        directional_model.get("score"),
        directional_confidence if identity_proven else None,
    )
    event_metric = _safe_public_text(
        event_likelihood.get("event") or probability.get("metric"),
        "UNAVAILABLE",
        limit=96,
    ).upper()
    source_tier = _safe_public_text(
        timing_estimate.get("source_tier"), "", limit=64
    ).upper()
    timing_support_count = _integer(timing_estimate.get("support_count"))
    current_sequence_candle_count = _integer(
        timing_estimate.get("current_sequence_candle_count")
    )
    timing_empirical = (
        _explicit_bool(timing_estimate.get("empirical_timing_evidence")) is True
    )
    pullback_like = bool(
        behavior_side in _DIRECTIONAL_SIDES
        and side in _DIRECTIONAL_SIDES
        and behavior_side != side
    ) or any(
        token in behavior_state
        for token in ("REST", "PULLBACK", "RANGE", "PAUSE", "COMPRESSION")
    )

    low_seconds: int | None = None
    high_seconds: int | None = None
    low_candles: int | None = None
    high_candles: int | None = None
    source = "NO_VERIFIED_DIRECTION"
    source_label = "No verified forecast source"
    provisional = True

    if use_forward:
        low_seconds, _, low_candles = earliest
        high_seconds, _, high_candles = latest
        # Do not clip or invent a policy-compliant window.  The complete field
        # is accepted only when its own earliest/latest bounds are already
        # inside the declared 15-minute to two-hour study policy.
        low_seconds = cast(int, low_seconds)
        high_seconds = cast(int, high_seconds)
        if timeframe_seconds:
            low_candles = int(math.ceil(low_seconds / timeframe_seconds))
            high_candles = int(math.ceil(high_seconds / timeframe_seconds))
        source = source_tier or "JPCLF_FORWARD_TIMING"
        source_label = _timing_source_label_v3(
            source_tier,
            empirical=timing_empirical,
        )
        provisional = not calibrated or not exact_wall_clock
    elif forward_identity_valid:
        source = source_tier or "TIMING_UNRATED"
        source_label = _timing_source_label_v3(
            source_tier,
            empirical=timing_empirical,
        )
        provisional = True
    elif identity_proven and side in _DIRECTIONAL_SIDES:
        source = "CURRENT_CLOSED_CANDLE_DIRECTION"
        source_label = "Current completed-candle direction · timing unrated"
        calibration_grade = "UNRATED"
        calibrated = False
        support_count = 0
        estimated_likelihood = None
        provisional = True

    candle_anchor_label = (
        f"completed {current_timeframe} candles after the anchor close"
        if current_timeframe != "UNKNOWN"
        else "completed candles after the anchor close"
    )
    if exact_window_expired:
        headline = _safe_public_text(
            exact_window_read.get("headline"),
            f"{side} remains the studied path · exact timing expired",
            limit=180,
        )
    elif (
        side in _DIRECTIONAL_SIDES
        and exact_wall_clock
        and use_forward
        and active_target_next_impulse
    ):
        countdown = _safe_public_text(
            exact_window_read.get("countdown_label"),
            "fixed anchor-bound window",
            limit=120,
        ).lower()
        headline = f"{side} is active · next {side} impulse {countdown}"
    elif side in _DIRECTIONAL_SIDES and exact_wall_clock and use_forward:
        headline = _safe_public_text(
            exact_window_read.get("headline"),
            f"{side} has a fixed anchor-bound timing window",
            limit=180,
        )
    elif (
        side in _DIRECTIONAL_SIDES
        and low_candles is not None
        and high_candles is not None
        and active_target_next_impulse
    ):
        headline = (
            f"{side} is active · next {side} impulse estimated "
            f"{low_candles}\u2013{high_candles} {candle_anchor_label}"
        )
    elif side in _DIRECTIONAL_SIDES and low_candles is not None and high_candles is not None:
        headline = (
            f"{side} leading {low_candles}\u2013{high_candles} "
            f"{candle_anchor_label}"
        )
    elif side in _DIRECTIONAL_SIDES and target_move_already_active:
        headline = (
            f"{side} is active · wait for a completed rest before estimating "
            f"the next {side} impulse"
        )
    elif side in _DIRECTIONAL_SIDES:
        headline = f"{side} remains the studied path \u00b7 timing is unrated"
    else:
        next_close = current_timeframe if current_timeframe != "UNKNOWN" else "chart"
        headline = f"Direction unresolved \u00b7 reassess after the next {next_close} close"

    expected_pre_move: Mapping[str, Any] = (
        _mapping(forward.get("expected_pre_move"))
        if use_forward
        else _mapping(None)
    )
    sweep_probability = _number(expected_pre_move.get("sweep_probability"))
    sweep_risk = _safe_public_text(
        expected_pre_move.get("sweep_risk"), "", limit=40
    ).upper()
    adverse_excursion: Mapping[str, Any] = (
        _mapping(forward.get("adverse_excursion_risk"))
        if use_forward
        else _mapping(None)
    )
    adverse_support_count = _integer(adverse_excursion.get("support_count"))
    worst_drawdown = (
        _number(
            adverse_excursion.get(
                "worst_drawdown_still_ahead_probability"
            )
        )
        if adverse_support_count > 0
        else None
    )
    rest_window_candles = _mapping(
        expected_pre_move.get("rest_window_candles")
    )
    rest_window_minutes = _mapping(
        expected_pre_move.get("rest_window_minutes")
    )

    def _interval_text(interval: Mapping[str, object], unit: str) -> str:
        low = _number(interval.get("earliest"))
        high = _number(interval.get("latest"))
        if low is None and high is None:
            return ""
        low = max(0.0, low if low is not None else cast(float, high))
        high = max(low, high if high is not None else low)
        low_text = str(int(round(low))) if low.is_integer() else f"{low:.1f}"
        high_text = str(int(round(high))) if high.is_integer() else f"{high:.1f}"
        return (
            f"{low_text} {unit}"
            if low_text == high_text
            else f"{low_text}–{high_text} {unit}"
        )

    rest_window_text = _interval_text(rest_window_candles, "candles")
    if not rest_window_text:
        rest_window_text = _interval_text(rest_window_minutes, "minutes")
    rest_prefix = (
        f"Rest may persist {rest_window_text}. " if rest_window_text else ""
    )
    if sweep_probability is not None:
        estimate_word = "Historical" if calibrated else "Estimated"
        rest_sweep_risk = (
            f"{rest_prefix}{estimate_word} sweep risk is "
            f"{sweep_risk.lower() or 'measured'} "
            f"({round(max(0.0, min(1.0, sweep_probability)) * 100)}%) before "
            "the move window."
        )
    elif worst_drawdown is not None:
        rest_sweep_risk = (
            f"{rest_prefix}Similar paths put the chance that the worst adverse sweep is still "
            f"ahead at {round(max(0.0, min(1.0, worst_drawdown)) * 100)}%."
        )
    elif pullback_like and side in _DIRECTIONAL_SIDES:
        rest_sweep_risk = (
            f"A rest or pullback is active; price may sweep against {side} before "
            "the continuation attempt."
        )
    else:
        rest_sweep_risk = (
            "No calibrated sweep probability is available; allow at least 15 minutes "
            "and do not use this forecast as entry permission."
        )
    if active_target_next_impulse and side in _DIRECTIONAL_SIDES:
        rest_sweep_risk = (
            f"The current {side} impulse is already active. Do not chase it; wait "
            "for the move to mature and one completed rest or pullback before "
            f"reassessing the next {side} impulse."
        )

    invalidation_source: Mapping[str, Any] = (
        _mapping(forward.get("invalidation"))
        if use_forward
        else _mapping(None)
    )
    invalidation = _safe_public_text(
        invalidation_source.get("condition"), "", limit=240
    )
    if not invalidation:
        invalidation = (
            f"Invalidate if the completed-candle regression no longer reads {side}, "
            "the pair or timeframe changes, or the verified entry window closes."
            if side in _DIRECTIONAL_SIDES
            else "Reassess when a completed candle publishes a verified direction."
        )

    event_label = _event_metric_label_v3(event_metric)
    estimated_likelihood_label = (
        f"{round(estimated_likelihood * 100)}% estimated chance of {event_label}"
        + (" \u00b7 replay-calibrated" if calibrated else " \u00b7 not replay-calibrated")
        if estimated_likelihood is not None
        else f"Event likelihood unavailable for {event_label}"
    )
    evidence_confidence_label = (
        f"{round(evidence_confidence * 100)}% evidence confidence"
        if evidence_confidence is not None
        else "Evidence confidence unavailable"
    )
    directional_model_score_label = (
        f"{round(directional_model_score * 100)}% directional model score \u00b7 not probability"
        if directional_model_score is not None
        else "Directional model score unavailable"
    )
    public_grade_label = _calibration_grade_label_v3(calibration_grade)
    calibration_label = (
        f"Calibration {public_grade_label} \u00b7 {support_count} audited cases"
        if calibrated and support_count > 0
        else (
            f"Empirical outcomes \u00b7 {event_support_count} cases \u00b7 not replay-calibrated"
        )
        if calibration_grade == "EMPIRICAL_UNCALIBRATED" and event_support_count > 0
        else (
            f"Evidence grade {public_grade_label}"
            + (f" \u00b7 {support_count} cases" if support_count > 0 else "")
            + " \u00b7 not replay-calibrated"
        )
        if calibration_grade not in {"", "UNRATED", "UNKNOWN"}
        else "Calibration UNRATED \u00b7 not replay-calibrated"
    )
    horizon_label = (
        _safe_public_text(
            exact_window_read.get("countdown_label"),
            "fixed anchor-bound exact window",
            limit=160,
        )
        if exact_wall_clock
        else f"{low_candles}\u2013{high_candles} {candle_anchor_label}"
        if low_candles is not None and high_candles is not None
        else "timing unrated"
    )
    likelihood_summary = (
        estimated_likelihood_label
        if estimated_likelihood is not None
        else directional_model_score_label
    )
    base_summary = (
        f"The current {side} move is mature and already active. This forecast "
        f"estimates the next {side} impulse only after the active move completes "
        "and one rest or pullback is observed. It is not permission to chase or enter."
        if active_target_next_impulse and side in _DIRECTIONAL_SIDES
        else
        f"{side} is the leading path inside one fixed window anchored to the "
        f"completed close. {likelihood_summary}. This is a timing forecast, not "
        "entry permission."
        if exact_wall_clock and side in _DIRECTIONAL_SIDES
        else f"{headline}. {likelihood_summary}. This is a timing forecast, not entry permission."
    )
    timing_evidence_label = (
        f"{source_label} \u00b7 {timing_support_count} timing observations"
        if timing_empirical and timing_support_count > 0
        else (
            f"{source_label} \u00b7 {current_sequence_candle_count} current candles"
            if source_tier == "LIVE_M5_SEQUENCE"
            and current_sequence_candle_count > 0
            else source_label
        )
    )
    duration_provenance = _mapping(forward.get("duration_provenance"))
    forecast_horizon_seconds = _number(
        forward.get("forecast_horizon_seconds")
    )
    recommended_duration = _number(
        forward.get("recommended_trade_duration_seconds")
    )
    broker_expiry = _number(forward.get("broker_expiry_seconds"))
    return {
        "schema_version": "PG_OPERATOR_TIMING_FORECAST_V3",
        "status": (
            "FORECAST_AVAILABLE"
            if use_forward
            else "TIMING_UNRATED"
            if side in _DIRECTIONAL_SIDES
            else "DIRECTION_UNRESOLVED"
        ),
        "headline": headline,
        "summary": base_summary,
        "closed_candle_summary": base_summary,
        "side": side,
        "scope": {
            "symbol": current_symbol if identity_proven else "",
            "timeframe": current_timeframe if identity_proven else "",
            "closed_candle_key": closed_candle_key if identity_proven else "",
            "identity_proven": identity_proven,
        },
        "horizon_label": horizon_label,
        "horizon_seconds_low": low_seconds,
        "horizon_seconds_high": high_seconds,
        "horizon_candles_low": low_candles,
        "horizon_candles_high": high_candles,
        "anchor_close_epoch_seconds": (
            anchor_close_epoch if exact_wall_clock and not exact_window_expired else None
        ),
        "target_window_start_epoch_seconds": (
            target_window_start_epoch
            if exact_wall_clock and not exact_window_expired
            else None
        ),
        "target_window_central_epoch_seconds": (
            target_window_central_epoch
            if exact_wall_clock and not exact_window_expired
            else None
        ),
        "target_window_end_epoch_seconds": (
            target_window_end_epoch
            if exact_wall_clock and not exact_window_expired
            else None
        ),
        "countdown_label": _safe_public_text(
            exact_window_read.get("countdown_label"), "", limit=160
        ),
        "seconds_until_window_start": (
            _integer(exact_window_read.get("seconds_until_window_start"))
            if exact_wall_clock and not exact_window_expired
            else None
        ),
        "seconds_until_window_end": (
            _integer(exact_window_read.get("seconds_until_window_end"))
            if exact_wall_clock and not exact_window_expired
            else None
        ),
        "estimated_likelihood": estimated_likelihood,
        "estimated_likelihood_label": estimated_likelihood_label,
        "evidence_confidence": evidence_confidence,
        "evidence_confidence_label": evidence_confidence_label,
        "directional_model_score": directional_model_score,
        "directional_model_score_label": directional_model_score_label,
        "directional_model_source": _safe_public_text(
            directional_model.get("source"),
            "CURRENT_DIRECTIONAL_STUDY",
            limit=64,
        ).upper(),
        "event_likelihood_metric": event_metric,
        "event_likelihood_event_label": event_label,
        "event_likelihood_support_count": event_support_count,
        "event_definition": timing_event_definition,
        "active_target_next_impulse": active_target_next_impulse,
        "target_move_already_active": target_move_already_active,
        # Compatibility alias: this is evidence confidence, never probability.
        "confidence": evidence_confidence,
        "confidence_label": evidence_confidence_label,
        "calibration_grade": calibration_grade,
        "calibration_label": calibration_label,
        "calibrated": calibrated,
        "support_count": support_count,
        "source": source,
        "source_label": source_label,
        "timing_support_count": timing_support_count,
        "timing_empirical": timing_empirical,
        "timing_evidence_label": timing_evidence_label,
        "forecast_horizon_seconds": (
            int(forecast_horizon_seconds)
            if forecast_horizon_seconds is not None
            and forecast_horizon_seconds > 0.0
            else None
        ),
        "forecast_horizon_source": (
            "MODEL_STUDY_HORIZON"
            if forecast_horizon_seconds is not None
            and forecast_horizon_seconds > 0.0
            else "UNAVAILABLE"
        ),
        "recommended_trade_duration_seconds": (
            int(recommended_duration)
            if recommended_duration is not None and recommended_duration > 0.0
            else None
        ),
        "recommended_trade_duration_proven": bool(
            recommended_duration is not None
            and recommended_duration > 0.0
            and forecast_horizon_seconds is not None
        ),
        "broker_expiry_seconds": (
            int(broker_expiry)
            if broker_expiry is not None
            and broker_expiry > 0.0
            and duration_provenance.get("broker_expiry_proven") is True
            else None
        ),
        "duration_provenance": {
            "forecast_horizon": (
                "MODEL_STUDY_HORIZON"
                if forecast_horizon_seconds is not None
                and forecast_horizon_seconds > 0.0
                else "UNAVAILABLE"
            ),
            "recommended_trade_duration": _safe_public_text(
                duration_provenance.get("recommended_trade_duration"),
                "UNAVAILABLE",
                limit=64,
            ).upper(),
            "recommended_trade_duration_proven": bool(
                duration_provenance.get("recommended_trade_duration_proven")
            ),
            "broker_expiry": _safe_public_text(
                duration_provenance.get("broker_expiry"),
                "UNPROVEN",
                limit=64,
            ).upper(),
            "broker_expiry_proven": (
                duration_provenance.get("broker_expiry_proven") is True
            ),
        },
        "technical_estimates": {
            "state_transition": _mapping(
                forward.get("state_transition_estimate")
            ),
            "stop_survival": _mapping(forward.get("stop_survival")),
            "adverse_excursion_risk": _mapping(
                forward.get("adverse_excursion_risk")
            ),
        },
        "provisional": provisional,
        "exact_wall_clock_proven": bool(
            use_forward and exact_wall_clock and not exact_window_expired
        ),
        "forecast_lineage_matches": forecast_lineage_matches,
        "rest_sweep_risk": rest_sweep_risk,
        "base_rest_sweep_risk": rest_sweep_risk,
        "invalidation": invalidation,
        "study_only": True,
        "execution_authority": False,
        "broker_click_authority": False,
        "can_grant_entry_permission": False,
    }


def _operator_action_contract_v3(
    *,
    enter_now: bool,
    timing_state: str,
    timing_effect: Mapping[str, object],
    studied_side: str,
    behavior_state: str,
    behavior_side: str,
    instruction: str,
    active_target_next_impulse: bool = False,
) -> dict[str, object]:
    pullback_like = bool(
        behavior_side in _DIRECTIONAL_SIDES
        and studied_side in _DIRECTIONAL_SIDES
        and behavior_side != studied_side
    ) or any(
        token in behavior_state
        for token in ("REST", "PULLBACK", "RANGE", "PAUSE", "COMPRESSION")
    )
    if enter_now:
        state = "ENTER_NOW"
        label = "ENTER NOW"
    elif timing_effect.get("timing_veto_basis") == "DURATION_INELIGIBLE":
        state = "AVOID"
        label = "AVOID"
    elif timing_state in {"INVALIDATED", "CONFLICT", "STALE"}:
        state = "AVOID"
        label = "AVOID"
    elif active_target_next_impulse and studied_side in _DIRECTIONAL_SIDES:
        state = "WAIT_FOR_PULLBACK"
        label = "WAIT FOR PULLBACK"
    elif (
        timing_effect.get("timing_veto") is True
        or timing_state == "MISSED"
        or pullback_like
    ) and studied_side in _DIRECTIONAL_SIDES:
        state = "WAIT_FOR_PULLBACK"
        label = "WAIT FOR PULLBACK"
    elif studied_side in _DIRECTIONAL_SIDES:
        state = "PREPARE"
        label = "PREPARE"
    else:
        state = "AVOID"
        label = "AVOID"
    return {
        "schema_version": "PG_OPERATOR_ACTION_V3",
        "state": state,
        "label": label,
        "instruction": instruction,
        "enter_now": enter_now,
        "entry_permission_authorized": enter_now,
        "execution_authority": False,
        "broker_click_authority": False,
    }


def _three_question_brief_v3(
    payload: Mapping[str, Any],
    *,
    command: Mapping[str, Any],
    market: Mapping[str, object],
    market_study: Mapping[str, object],
    history: Sequence[Mapping[str, object]],
    freshness: Mapping[str, object],
    current_move: Mapping[str, object],
    pressure_event: Mapping[str, object],
    permission: Mapping[str, object],
    now_epoch: float,
) -> dict[str, object]:
    """Answer the only three questions the public operator surface must resolve.

    Directional study evidence deliberately remains visible when execution is
    unavailable.  Entry permission is projected only by the third answer and is
    never inferred from trend, regression, a council score, or a missed move.
    """

    regression = _mapping(market_study.get("regression"))
    behavior = _mapping(market_study.get("behavior"))
    current_behavior = _mapping(behavior.get("current_state"))
    directional = _mapping(market_study.get("directional_read"))
    pair_dna = _mapping(market_study.get("pair_dna"))
    path_clock_liquidity = _mapping(
        market_study.get("path_clock_liquidity_v3")
    )

    major = _mapping(regression.get("major_trend"))
    inner = _mapping(regression.get("inner_trend"))
    regression_pressure = _mapping(regression.get("current_pressure"))
    major_side = _side(major.get("side"))
    inner_side = _side(inner.get("side"))
    regression_side = _side(directional.get("side"))
    behavior_side = _side(current_behavior.get("direction"))
    behavior_state = _safe_public_text(
        current_behavior.get("state"), "Unknown", limit=32
    ).upper()
    behavior_candles = _integer(current_behavior.get("candle_count"))
    behavior_duration = _integer(current_behavior.get("duration_seconds"))

    current_symbol = _safe_public_text(market.get("symbol"), "Unknown", limit=64)
    current_timeframe = _safe_public_text(
        market.get("timeframe"), "Unknown", limit=32
    ).upper()
    study_symbol = _safe_public_text(
        market_study.get("symbol"), "Unknown", limit=64
    )
    study_timeframe = _safe_public_text(
        market_study.get("timeframe"), "Unknown", limit=32
    ).upper()
    has_completed_study_identity = bool(
        _text(market_study.get("status"), "").upper() == "STUDIED"
        and _safe_identifier(market_study.get("closed_candle_key"), "")
        and study_symbol != "Unknown"
        and study_timeframe != "UNKNOWN"
    )
    study_matches_current_market = bool(
        has_completed_study_identity
        and current_symbol != "Unknown"
        and current_timeframe != "UNKNOWN"
        and _instrument_token(study_symbol) == _instrument_token(current_symbol)
        and study_timeframe == current_timeframe
    )
    identity_proven = study_matches_current_market
    identity_mismatch = bool(
        has_completed_study_identity and not study_matches_current_market
    )
    freshness_state = _text(freshness.get("state"), "UNKNOWN").upper()
    last_completed_only = identity_proven and freshness_state != "FRESH"

    history_major_counts = {"BUY": 0, "SELL": 0}
    history_regression_counts = {"BUY": 0, "SELL": 0}
    for row in history:
        row_major = _side(_mapping(row.get("major_trend")).get("side"))
        row_regression = _side(_mapping(row.get("regression_read")).get("side"))
        if row_major in _DIRECTIONAL_SIDES:
            history_major_counts[row_major] += 1
        if row_regression in _DIRECTIONAL_SIDES:
            history_regression_counts[row_regression] += 1
    history_dominant_side = _dominant_side(history_regression_counts)
    origin_side = (
        major_side if major_side in _DIRECTIONAL_SIDES else history_dominant_side
    ) if identity_proven else "NEUTRAL"

    if identity_proven:
        behavior_phrase = ""
        if behavior_state != "UNKNOWN":
            duration_phrase = (
                f" over {behavior_duration} seconds" if behavior_duration > 0 else ""
            )
            candle_phrase = (
                f" for {behavior_candles} completed candle"
                f"{'' if behavior_candles == 1 else 's'}"
                if behavior_candles > 0
                else ""
            )
            directional_phrase = (
                f" {_plain_direction(behavior_side)}"
                if behavior_side in _DIRECTIONAL_SIDES
                else ""
            )
            behavior_phrase = (
                f" The latest completed behavior is{directional_phrase} "
                f"{behavior_state.replace('_', ' ').lower()}"
                f"{candle_phrase}{duration_phrase}."
            )
        inner_phrase = (
            f" The inner trend is {_plain_direction(inner_side)}."
            if inner_side in _DIRECTIONAL_SIDES
            else ""
        )
        history_phrase = (
            f" {len(history)} identity-matched closed-candle history "
            f"observation{'' if len(history) == 1 else 's'} are available."
            if history
            else ""
        )
        answer = (
            f"The market comes from {_plain_direction(origin_side)} major structure."
            f"{inner_phrase}{behavior_phrase}{history_phrase}"
        )
        headline_prefix = "Last completed study: " if last_completed_only else ""
        behavior_headline = (
            f"; now {behavior_state.replace('_', ' ').lower()}"
            if behavior_state != "UNKNOWN"
            else ""
        )
        history_headline = (
            f"{headline_prefix}{_plain_direction(origin_side).capitalize()} history"
            f"{behavior_headline}"
        )
        history_state = "LAST_COMPLETED" if last_completed_only else "CURRENT"
    elif identity_mismatch:
        history_headline = (
            f"Last evidence was {study_symbol} {study_timeframe}, not "
            f"{current_symbol} {current_timeframe}"
        )
        answer = (
            f"The completed study belongs to {study_symbol} {study_timeframe}. "
            f"The chart now shows {current_symbol} {current_timeframe}, so that old "
            "pair and timeframe cannot describe where this market came from."
        )
        history_state = "MISMATCHED_EVIDENCE"
    else:
        history_headline = "Not enough completed study evidence"
        answer = (
            "The system has not published an identity-proven completed market study, "
            "so it cannot honestly describe where this market came from yet."
        )
        history_state = "INSUFFICIENT_EVIDENCE"

    updated_at = _epoch(
        freshness.get("observed_at"),
        history[-1].get("observed_at") if history else None,
    )
    history_answer: dict[str, object] = {
        "question": "Where is the market from, and how did history behave?",
        "headline": history_headline,
        "answer": answer,
        "state": history_state,
        "side": origin_side,
        "confidence": _confidence(major.get("confidence")) or 0.0,
        "evidence": {
            "identity_proven": identity_proven,
            "identity_mismatch": identity_mismatch,
            "study_scope": (
                "MISMATCHED_STUDY"
                if identity_mismatch
                else "LAST_COMPLETED_STUDY"
                if last_completed_only
                else "CURRENT_STUDY"
            ),
            "symbol": study_symbol,
            "timeframe": study_timeframe,
            "current_symbol": current_symbol,
            "current_timeframe": current_timeframe,
            "closed_candle_key": _safe_identifier(
                market_study.get("closed_candle_key"), ""
            ),
            "major_trend_side": major_side,
            "inner_trend_side": inner_side,
            "regression_side": regression_side,
            "behavior_state": behavior_state,
            "behavior_side": behavior_side,
            "behavior_candle_count": behavior_candles,
            "behavior_duration_seconds": behavior_duration,
            "history_observation_count": len(history),
            "history_dominant_side": history_dominant_side,
            "history_major_counts": history_major_counts,
            "history_regression_counts": history_regression_counts,
            "pair_dna_observation_count": _integer(pair_dna.get("observation_count")),
        },
        "updated_at": updated_at,
    }

    model_result = _mapping(payload.get("model_council_result"))
    model_council = _first_mapping(
        payload,
        ("model_council_result", "model_council"),
        ("model_council_study_packet", "model_council"),
        ("study_packet", "model_council"),
    )
    book_strategy = _first_mapping(
        payload,
        ("model_council_result", "book_strategy"),
        ("model_council_result", "model_council", "book_strategy"),
        ("model_council_study_packet", "book_strategy"),
        ("study_packet", "book_strategy"),
        ("book_strategy",),
    )
    dual_thesis = _first_mapping(
        payload,
        ("model_council_result", "book_strategy", "dual_thesis_report_v3"),
        ("model_council_result", "dual_thesis_report_v3"),
        ("model_council_result", "model_council", "dual_thesis_report_v3"),
        ("model_council_study_packet", "dual_thesis_report_v3"),
        ("study_packet", "dual_thesis_report_v3"),
    )
    countertrend_promotion = _countertrend_sniper_promotion_source(payload)
    countertrend_side = (
        _side(countertrend_promotion.get("side"))
        if _explicit_bool(countertrend_promotion.get("active")) is True
        else "NEUTRAL"
    )
    command_side = _side(command.get("selected_side"))
    dual_side = _side(
        dual_thesis.get("selected_authority_side"),
        dual_thesis.get("playbook_ai_selected_side"),
    )
    council_side = _side(
        book_strategy.get("final_side"),
        book_strategy.get("candidate_side"),
        book_strategy.get("side"),
        model_council.get("final_side"),
        model_council.get("side"),
        model_result.get("final_side"),
        model_result.get("side"),
    )
    selected_side = _side(command_side, countertrend_side, dual_side, council_side)
    direction_source = (
        "DECISION_COMMAND_CENTER"
        if command_side in _DIRECTIONAL_SIDES
        else "COUNTERTREND_SNIPER"
        if countertrend_side in _DIRECTIONAL_SIDES
        else "DUAL_THESIS"
        if dual_side in _DIRECTIONAL_SIDES
        else "MODEL_COUNCIL"
        if council_side in _DIRECTIONAL_SIDES
        else "CLOSED_CANDLE_REGRESSION"
        if regression_side in _DIRECTIONAL_SIDES
        else "NONE"
    )
    studied_side = (
        selected_side
        if selected_side in _DIRECTIONAL_SIDES
        else regression_side
    )
    sides = _mapping(command.get("sides"))
    countertrend_ensemble = _mapping(countertrend_promotion.get("ensemble_basis"))
    council_scores = _mapping(model_result.get("council_scores"))
    selected_score = _confidence(
        _mapping(sides.get(selected_side)).get("score"),
        command.get(f"{selected_side.lower()}_score") if selected_side in _DIRECTIONAL_SIDES else None,
        countertrend_ensemble.get("council_side_score"),
        _mapping(dual_thesis.get(selected_side.lower())).get("score"),
        council_scores.get(f"{selected_side.lower()}_score"),
        council_scores.get(selected_side),
        directional.get("confidence"),
    ) or 0.0
    countertrend = bool(
        studied_side in _DIRECTIONAL_SIDES
        and major_side in _DIRECTIONAL_SIDES
        and studied_side != major_side
    )
    timing_effect = _path_clock_timing_effect_v3(
        path_clock_liquidity,
        current_symbol=current_symbol,
        current_timeframe=current_timeframe,
        study_closed_candle_key=_safe_identifier(
            market_study.get("closed_candle_key"), ""
        ),
        studied_side=studied_side,
        freshness_state=freshness_state,
        now_epoch=now_epoch,
    )
    broker_expiry = _broker_expiry_contract_v3(
        payload,
        command,
        market=market,
        market_study=market_study,
        now_epoch=now_epoch,
    )
    if identity_mismatch:
        directional_state = "MISMATCHED_EVIDENCE"
    elif freshness_state == "STALE":
        directional_state = "STALE"
    elif studied_side not in _DIRECTIONAL_SIDES:
        directional_state = "NO_DIRECTION"
    elif identity_proven and freshness_state == "FRESH":
        directional_state = "CURRENT"
    else:
        directional_state = "FORMING"

    if identity_mismatch:
        directional_headline = (
            f"Last directional study belongs to {study_symbol} {study_timeframe}"
        )
    elif selected_side in _DIRECTIONAL_SIDES and regression_side in _DIRECTIONAL_SIDES:
        if selected_side == regression_side:
            directional_headline = (
                f"{selected_side} was studied and remains the current regression read"
            )
        else:
            directional_headline = (
                f"{selected_side} was studied; current regression now reads {regression_side}"
            )
    elif selected_side in _DIRECTIONAL_SIDES:
        directional_headline = (
            f"{selected_side} is the ensemble study; regression is still forming"
        )
    elif regression_side in _DIRECTIONAL_SIDES:
        directional_headline = f"Current regression is studying {regression_side}"
    else:
        directional_headline = "No directional study is ready"
    if (
        not identity_mismatch
        and freshness_state != "FRESH"
        and studied_side in _DIRECTIONAL_SIDES
    ):
        directional_headline = f"Last completed read: {directional_headline}"

    study_sentences: list[str] = []
    if identity_mismatch:
        study_sentences.append(
            f"The last directional evidence was produced for {study_symbol} "
            f"{study_timeframe}, not the current {current_symbol} {current_timeframe}."
        )
    elif selected_side in _DIRECTIONAL_SIDES:
        study_sentences.append(f"The ensemble was studying {selected_side}.")
    else:
        study_sentences.append("The ensemble has not selected a directional side.")
    if not identity_mismatch and regression_side in _DIRECTIONAL_SIDES:
        qualifier = "last completed" if freshness_state != "FRESH" else "current"
        study_sentences.append(
            f"The {qualifier} closed-candle regression reads {regression_side}."
        )
    else:
        study_sentences.append("The closed-candle regression has no directional read yet.")
    if identity_mismatch:
        study_sentences.append(
            "A new completed study must bind to the current pair and timeframe "
            "before a direction can be called current."
        )
    elif countertrend:
        study_sentences.append(
            f"This is a countertrend {studied_side} study inside a {major_side} major trend."
        )
    elif studied_side in _DIRECTIONAL_SIDES and major_side in _DIRECTIONAL_SIDES:
        study_sentences.append(
            f"The studied direction agrees with the {major_side} major trend."
        )
    if timing_effect.get("mature") is True and (
        timing_effect.get("timing_supports_entry") is True
        or timing_effect.get("timing_veto") is True
    ):
        timing_duration = _integer(timing_effect.get("contract_duration_seconds"))
        timing_minutes = max(
            15,
            int(math.ceil(timing_duration / 60.0)) if timing_duration > 0 else 15,
        )
        if timing_effect.get("timing_supports_entry") is True:
            study_sentences.append(
                f"Mature timing history supports studying this {studied_side} over a "
                f"{timing_minutes}-minute window; anything under 15 minutes is excluded."
            )
        else:
            study_sentences.append(
                f"Mature timing history says delay this {studied_side}; anything under "
                "15 minutes is excluded."
            )
    directional_answer: dict[str, object] = {
        "question": "Which direction was studied, and what is being studied now?",
        "headline": directional_headline,
        "answer": " ".join(study_sentences),
        "state": directional_state,
        "side": studied_side,
        "confidence": selected_score,
        "evidence": {
            "ensemble_studied_side": selected_side,
            "direction_source": direction_source,
            "countertrend_classification": _safe_public_text(
                countertrend_promotion.get("classification"), "UNAVAILABLE", limit=40
            ).upper(),
            "current_regression_side": regression_side,
            "current_move_side": _side(current_move.get("direction")),
            "current_pressure_side": _side(
                pressure_event.get("direction"), regression_pressure.get("side")
            ),
            "major_trend_side": major_side,
            "inner_trend_side": inner_side,
            "countertrend": countertrend,
            "study_status": _safe_public_text(
                market_study.get("status"), "UNAVAILABLE", limit=40
            ).upper(),
            "study_freshness": freshness_state,
            "closed_candle_key": _safe_identifier(
                market_study.get("closed_candle_key"), ""
            ),
            "path_clock_liquidity_v3": timing_effect,
        },
        "updated_at": updated_at,
    }

    opportunity = _first_mapping(
        command,
        ("execution_opportunity_window_v3",),
        ("opportunity",),
    ) or _first_mapping(
        payload,
        ("execution_opportunity_window_v3",),
        ("model_council_result", "execution_opportunity_window_v3"),
        ("model_council_result", "model_council", "execution_opportunity_window_v3"),
        ("model_council_study_packet", "execution_opportunity_window_v3"),
        ("study_packet", "execution_opportunity_window_v3"),
    )
    opportunity_state = _text(
        opportunity.get("state") or opportunity.get("status"), "UNAVAILABLE"
    ).upper()
    opportunity_expiry = _epoch(
        opportunity.get("valid_until_epoch_sec"),
        opportunity.get("valid_until_epoch"),
        opportunity.get("expires_at"),
    )
    promotion = _first_mapping(
        payload,
        ("promotion_trace",),
        ("model_council_study_packet", "promotion_trace"),
        ("study_packet", "promotion_trace"),
        ("model_council_result", "promotion_trace"),
        ("model_council_result", "model_council", "promotion_trace"),
    )
    missed = _mapping(promotion.get("missed_opportunity")) or _mapping(
        payload.get("missed_opportunity")
    )
    blocker_text = " ".join(
        _text(value, "", limit=160).upper()
        for value in (
            command.get("blocker"),
            command.get("next_required"),
            promotion.get("true_blocker"),
            promotion.get("denied_at"),
            book_strategy.get("maturity_state"),
            book_strategy.get("denied_at"),
            book_strategy.get("true_blocker"),
            countertrend_promotion.get("classification"),
            countertrend_promotion.get("book_strategy_state"),
        )
        if value not in (None, "")
    )
    missed_tokens = (
        "EXPIRED",
        "MISSED",
        "TOO_LATE",
        "LATE_ENTRY",
        "LATE_CHASE",
        "OVEREXTENDED",
        "DO_NOT_CHASE",
        "MOVED_WITHOUT_ENTRY",
    )
    explicit_missed = bool(
        opportunity_state in {"EXPIRED", "MISSED", "TOO_LATE"}
        or (
            studied_side in _DIRECTIONAL_SIDES
            and opportunity_state in {"ACTIVE", "AUTHORIZED_NOW", "OPEN", "READY"}
            and opportunity_expiry is not None
            and opportunity_expiry <= now_epoch
        )
        # The council's ``missed_opportunity`` probe is a diagnostic candidate
        # whose future move may still be unconfirmed.  It must not become a
        # definitive human-facing MISSED claim without an explicit outcome.
        or (
            _side(missed.get("side")) in _DIRECTIONAL_SIDES
            and (
                _explicit_bool(missed.get("future_move_confirmed")) is True
                or _text(
                    missed.get("classification")
                    or missed.get("state")
                    or missed.get("status"),
                    "",
                ).upper()
                in {"MISSED", "CONFIRMED_MISSED", "MISSED_OPPORTUNITY"}
            )
        )
        or _text(countertrend_promotion.get("classification"), "").upper()
        == "MISSED_DO_NOT_CHASE"
        or any(token in blocker_text for token in missed_tokens)
    )
    opposite_live_side = any(
        _side(event.get("direction")) in _DIRECTIONAL_SIDES
        and _side(event.get("direction")) != studied_side
        and _text(event.get("state"), "UNKNOWN").upper() in {"ACTIVE", "UNKNOWN"}
        for event in (current_move, pressure_event)
    ) if studied_side in _DIRECTIONAL_SIDES else False
    _countertrend_valid, countertrend_validation_state = (
        _countertrend_bypass_validation_v3(
            payload,
            command,
            selected_side=studied_side,
            now_epoch=now_epoch,
        )
    )
    no_current_entry_study = bool(
        studied_side not in _DIRECTIONAL_SIDES
        and not market_study
        and not countertrend_promotion
        and _explicit_bool(command.get("execution_packet_present")) is not True
        and updated_at is None
    )

    permission_allowed = permission.get("allowed") is True
    permission_side = _side(permission.get("side"))
    permission_action = _text(permission.get("action"), "WAIT", limit=32).upper()
    permission_contract_authorized = bool(
        permission_allowed
        and permission_side in _DIRECTIONAL_SIDES
        and permission_action in {permission_side, f"{permission_side}_NOW"}
    )
    timing_supports_entry = timing_effect.get("timing_supports_entry") is True
    timing_veto = timing_effect.get("timing_veto") is True
    broker_expiry_proven = broker_expiry.get("proven") is True
    broker_expiry_eligible = broker_expiry.get("eligible") is True
    entry_permission_authorized = bool(
        permission_contract_authorized and broker_expiry_eligible
    )
    enter_now = bool(
        entry_permission_authorized
        and not timing_veto
    )
    if enter_now:
        timing_state = "ENTER_NOW"
    elif permission_contract_authorized and broker_expiry_proven:
        timing_state = "DURATION_INELIGIBLE"
    elif permission_contract_authorized and not broker_expiry_proven:
        timing_state = "EXPIRY_UNVERIFIED"
    elif identity_mismatch or countertrend_validation_state == "INVALIDATED":
        timing_state = "INVALIDATED"
    elif timing_veto:
        timing_state = "TIMING_DELAY"
    elif no_current_entry_study:
        timing_state = "FORMING"
    elif (
        countertrend_validation_state == "STALE"
        and freshness_state in {"STALE", "WAITING", "UNKNOWN"}
    ):
        timing_state = "STALE"
    elif explicit_missed:
        timing_state = "MISSED"
    elif countertrend_validation_state == "STALE":
        timing_state = "STALE"
    elif opposite_live_side:
        timing_state = "CONFLICT"
    elif freshness_state in {"STALE", "WAITING", "UNKNOWN"}:
        timing_state = "STALE"
    else:
        timing_state = "FORMING"

    side_label = studied_side if studied_side in _DIRECTIONAL_SIDES else "trade"
    next_trigger = "A new current-frame directional study must publish."
    if timing_state == "ENTER_NOW":
        action = f"{studied_side}_NOW"
        entry_headline = f"YES — enter {studied_side} now"
        entry_answer = (
            f"Yes. A verified {studied_side} entry window is open on the current frame."
        )
        reason = _safe_public_text(
            permission.get("message"),
            "Every current execution check is aligned.",
            limit=240,
        )
        next_trigger = (
            "Act only inside this verified window and stop if the current live truth changes."
        )
    elif timing_state == "TIMING_DELAY":
        action = "DO_NOT_ENTER"
        entry_headline = f"{side_label} timing is delayed by mature evidence"
        entry_answer = (
            f"Timing delay for {side_label}. {_safe_public_text(timing_effect.get('reason'), '', limit=320)}"
        ).strip()
        reason = _safe_public_text(
            timing_effect.get("reason"),
            "Historical path, clock, and liquidity evidence says delay this entry.",
            limit=320,
        )
        next_trigger = (
            "Wait for a fresh matching timing read with at least 15 minutes of room; "
            "timing evidence can only delay permission, never create it."
        )
    elif timing_state == "DURATION_INELIGIBLE":
        action = "DO_NOT_ENTER"
        entry_headline = f"{side_label} path studied · broker duration too short"
        entry_answer = _safe_public_text(
            broker_expiry.get("instruction"),
            "AVOID — the broker duration is under 15 minutes.",
            limit=320,
        )
        reason = entry_answer
        next_trigger = "Set and verify a broker expiry of at least 15 minutes."
    elif timing_state == "EXPIRY_UNVERIFIED":
        action = "DO_NOT_ENTER"
        entry_headline = f"{side_label} path studied · verify broker expiry"
        entry_answer = _safe_public_text(
            broker_expiry.get("instruction"),
            "SET/VERIFY EXPIRY ≥15 MIN — Broker expiry unverified.",
            limit=320,
        )
        reason = entry_answer
        next_trigger = (
            "Bind a current broker or execution-packet expiry of at least 15 minutes "
            "to this exact pair, timeframe, frame, and completed candle."
        )
    elif timing_state == "MISSED":
        action = "DO_NOT_ENTER"
        entry_headline = f"NO — the {side_label} opportunity was missed"
        entry_answer = (
            f"No. The studied {side_label} move progressed beyond its verified entry "
            "window; chasing it now is not authorized."
        )
        reason = (
            "The opportunity expired or was explicitly classified as missed before a "
            "current executable entry remained available."
        )
        next_trigger = (
            f"A newly detected {side_label} setup must open its own fresh entry window."
        )
    elif timing_state == "INVALIDATED":
        action = "DO_NOT_ENTER"
        entry_headline = "NO — this entry belongs to different live evidence"
        entry_answer = (
            "No. The entry study or its validated execution lineage does not match "
            "the pair, timeframe, frame, capture, or opportunity currently on screen."
        )
        reason = (
            "The prior entry evidence was invalidated instead of being carried across "
            "a market or frame change."
        )
        next_trigger = (
            "A new current-frame setup must publish for this exact pair and timeframe."
        )
    elif timing_state == "CONFLICT":
        action = "DO_NOT_ENTER"
        entry_headline = f"NO — live movement conflicts with {side_label}"
        entry_answer = (
            f"No. {side_label} is still the studied direction, but the current live "
            "movement evidence does not agree with it."
        )
        reason = "Current movement or active pressure points opposite the studied direction."
        next_trigger = (
            f"Current movement and pressure must both confirm {side_label} inside a fresh window."
        )
    elif timing_state == "STALE":
        action = "DO_NOT_ENTER"
        entry_headline = "NO — the entry evidence is stale"
        entry_answer = (
            f"No. The last {side_label} study remains useful history, but it is not a "
            "current entry instruction."
        )
        reason = "The latest entry evidence is not fresh enough to support a trade now."
        next_trigger = "A new completed candle must publish a fresh directional and entry read."
    else:
        action = "DO_NOT_ENTER"
        if no_current_entry_study:
            entry_headline = "Direction unresolved · current entry study unavailable"
            entry_answer = (
                "No identity-proven completed study has selected a "
                "directional trade on the current chart."
            )
            reason = "There is no current directional study to authorize or reject."
            next_trigger = (
                "The tracker must publish one identity-proven completed-candle "
                "directional study."
            )
        else:
            entry_headline = f"{side_label} timing forecast is forming"
            entry_answer = (
                f"{side_label} is being studied, but the system has not published "
                "a complete current entry permission."
                if studied_side in _DIRECTIONAL_SIDES
                else "No directional trade has reached entry readiness."
            )
            reason = _safe_public_text(
                permission.get("message"),
                "The entry checks are still forming.",
                limit=240,
            )
            if reason.lower().startswith("wait. "):
                reason = reason[6:]
            elif reason.lower().startswith("wait for "):
                reason = f"Still requires {reason[9:]}"
        if not no_current_entry_study:
            if permission.get("window_open") is True:
                next_trigger = (
                    f"Current-frame execution permission must publish while the {side_label} "
                    "window remains open."
                )
            elif studied_side in _DIRECTIONAL_SIDES:
                next_trigger = (
                    f"A fresh {side_label} opportunity window must open with matching "
                    "live movement."
                )
            else:
                next_trigger = (
                    "One directional ensemble study must become selected and executable."
                )

    current_closed_candle_key = _safe_identifier(
        market_study.get("closed_candle_key"), ""
    )
    forward_timing = _mapping(timing_effect.get("forward_timing_forecast"))
    forward_timing_lineage = _mapping(forward_timing.get("lineage"))
    forward_timing_side = _path_clock_direction_side_v3(
        forward_timing.get("candidate_direction")
    )
    forward_timing_freshness = _safe_public_text(
        forward_timing_lineage.get("freshness_state"), "UNBOUND", limit=40
    ).upper()
    current_regression_owns_forecast = bool(
        not permission_contract_authorized
        and identity_proven
        and regression_side in _DIRECTIONAL_SIDES
        and forward_timing_side == regression_side
        and _safe_public_text(
            forward_timing.get("status"), "", limit=64
        ).upper()
        == "FORECAST_AVAILABLE"
        and forward_timing_lineage.get("lineage_bound") is True
        and _instrument_token(forward_timing_lineage.get("symbol"))
        == _instrument_token(current_symbol)
        and _safe_public_text(
            forward_timing_lineage.get("timeframe"), "", limit=32
        ).upper()
        == current_timeframe.upper()
        and _safe_identifier(
            forward_timing_lineage.get("closed_candle_key"), ""
        )
        == current_closed_candle_key
        and forward_timing_freshness
        in {"CURRENT", "FRESH", "CURRENT_CLOSED_CANDLE"}
    )
    timing_forecast_side = (
        regression_side if current_regression_owns_forecast else studied_side
    )
    timing_directional_confidence = selected_score
    if current_regression_owns_forecast:
        timing_directional_confidence = _confidence(
            _mapping(forward_timing.get("directional_model")).get("score")
        ) or 0.0

    timing_forecast = _operator_timing_forecast_v3(
        studied_side=timing_forecast_side,
        current_symbol=current_symbol,
        current_timeframe=current_timeframe,
        closed_candle_key=current_closed_candle_key,
        identity_proven=identity_proven,
        behavior_state=behavior_state,
        behavior_side=behavior_side,
        directional_confidence=timing_directional_confidence,
        timing_effect=timing_effect,
        now_epoch=now_epoch,
    )
    active_target_next_impulse = (
        timing_forecast.get("active_target_next_impulse") is True
    )
    action_instruction = entry_answer
    if (
        active_target_next_impulse
        and not enter_now
        and timing_forecast_side in _DIRECTIONAL_SIDES
    ):
        action_instruction = (
            f"WAIT FOR PULLBACK — the current {timing_forecast_side} move is already "
            f"mature and active. Do not chase it; wait for one completed rest or "
            f"pullback before reassessing the next {timing_forecast_side} impulse."
        )
    operator_action = _operator_action_contract_v3(
        enter_now=enter_now,
        timing_state=timing_state,
        timing_effect=timing_effect,
        studied_side=timing_forecast_side,
        behavior_state=behavior_state,
        behavior_side=behavior_side,
        instruction=action_instruction,
        active_target_next_impulse=active_target_next_impulse,
    )
    if timing_state == "DURATION_INELIGIBLE":
        operator_action.update(
            {
                "state": "AVOID",
                "label": "AVOID",
                "instruction": _safe_public_text(
                    broker_expiry.get("instruction"), entry_answer, limit=320
                ),
            }
        )
    elif (
        active_target_next_impulse
        and not enter_now
    ):
        operator_action.update(
            {
                "state": "WAIT_FOR_PULLBACK",
                "label": "WAIT FOR PULLBACK",
                "instruction": action_instruction,
            }
        )
    elif timing_state == "EXPIRY_UNVERIFIED":
        operator_action.update(
            {
                "state": "PREPARE",
                "label": "PREPARE",
                "instruction": _safe_public_text(
                    broker_expiry.get("instruction"), entry_answer, limit=320
                ),
            }
        )
    entry_headline = _safe_public_text(
        timing_forecast.get("headline"), entry_headline, limit=180
    )
    entry_answer = _safe_public_text(
        timing_forecast.get("summary"), entry_answer, limit=480
    )
    reason = _safe_public_text(
        timing_forecast.get("rest_sweep_risk"), reason, limit=320
    )
    next_trigger = _safe_public_text(
        timing_forecast.get("invalidation"), next_trigger, limit=320
    )

    entry_answer_contract: dict[str, object] = {
        "question": "What is the best decision to do right now?",
        "headline": entry_headline,
        "answer": entry_answer,
        "state": timing_state,
        "side": timing_forecast_side,
        "confidence": timing_directional_confidence,
        "evidence": {
            "directional_study_present": timing_forecast_side in _DIRECTIONAL_SIDES,
            "direction_source": (
                "CURRENT_CLOSED_CANDLE_FORECAST"
                if current_regression_owns_forecast
                else direction_source
            ),
            "historical_studied_side": studied_side,
            "current_forecast_side": timing_forecast_side,
            "forecast_uses_current_regression": current_regression_owns_forecast,
            "countertrend_classification": _safe_public_text(
                countertrend_promotion.get("classification"), "UNAVAILABLE", limit=40
            ).upper(),
            "countertrend_validation_state": countertrend_validation_state,
            "permission_allowed": permission_allowed,
            "permission_side": permission_side,
            "permission_contract_authorized": permission_contract_authorized,
            "entry_permission_authorized": entry_permission_authorized,
            "timing_supports_entry": timing_supports_entry,
            "timing_veto": timing_veto,
            "broker_expiry_v3": broker_expiry,
            "broker_expiry_proven": broker_expiry_proven,
            "broker_expiry_eligible": broker_expiry_eligible,
            "path_clock_liquidity_v3": timing_effect,
            "freshness": freshness_state,
            "opportunity_state": opportunity_state,
            "opportunity_expires_at": opportunity_expiry,
            "window_open": permission.get("window_open") is True,
            "current_move_side": _side(current_move.get("direction")),
            "current_move_state": _safe_public_text(
                current_move.get("state"), "UNKNOWN", limit=24
            ).upper(),
            "pressure_side": _side(pressure_event.get("direction")),
            "pressure_state": _safe_public_text(
                pressure_event.get("state"), "UNKNOWN", limit=24
            ).upper(),
        },
        "updated_at": updated_at,
        "enter_now": enter_now,
        "action": action,
        "reason": reason,
        "next_trigger": next_trigger,
        "timing_state": timing_state,
        "permission_allowed": permission_allowed,
        "entry_permission_authorized": entry_permission_authorized,
        "timing_supports_entry": timing_supports_entry,
        "timing_veto": timing_veto,
        "broker_expiry_v3": broker_expiry,
        "broker_expiry_proven": broker_expiry_proven,
        "broker_expiry_eligible": broker_expiry_eligible,
        "timing_forecast": timing_forecast,
        "operator_action": operator_action,
    }
    return {
        "schema_version": "PG_THREE_QUESTION_OPERATOR_BRIEF_V3",
        "market_origin_history": history_answer,
        "studied_direction_current": directional_answer,
        "entry_now": entry_answer_contract,
    }


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


def _bounded_hidden_state_value(value: object, *, depth: int = 0) -> object:
    if depth > 10:
        return None
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 8) if math.isfinite(value) else None
    if isinstance(value, str):
        return _safe_public_text(value, "", limit=320)
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for raw_key, item in list(
            cast(Mapping[object, object], value).items()
        )[:1_000_000]:
            key = str(raw_key)
            lowered = key.lower()
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,95}", key):
                continue
            if any(
                token in lowered
                for token in (
                    "private",
                    "secret",
                    "password",
                    "auth_token",
                    "filesystem",
                    "host_path",
                )
            ):
                continue
            result[key] = _bounded_hidden_state_value(item, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return [
            _bounded_hidden_state_value(item, depth=depth + 1)
            for item in list(cast(Sequence[object], value))[:1_000_000]
        ]
    return None


def _hidden_state_discovery_contract(value: object) -> dict[str, object]:
    source = _mapping(value)
    if (
        _text(source.get("schema_version"), "")
        != "PG_LATENT_STATE_DISCOVERY_V3"
        or source.get("study_only") is not True
    ):
        return {}
    selected = (
        "schema_version",
        "status",
        "study_only",
        "observation_only",
        "strategy_authority",
        "blocker_authority",
        "execution_authority",
        "grants_entry_permission",
        "symbol",
        "timeframe",
        "timeframe_seconds",
        "input_authority",
        "publication_policy",
        "hidden_state",
        "control",
        "directional_components",
        "next_state_distribution",
        "state_survival",
        "state_cycle_horizon",
        "directional_outcome_distribution",
        "pair_dna",
        "learning_objectives",
        "causal_hypotheses",
        "causal_limit",
        "operator_interpretation",
    )
    result = {
        key: _bounded_hidden_state_value(source.get(key))
        for key in selected
        if key in source
    }
    result.update(
        {
            "schema_version": "PG_LATENT_STATE_DISCOVERY_V3",
            "study_only": True,
            "observation_only": True,
            "strategy_authority": False,
            "blocker_authority": False,
            "execution_authority": False,
            "grants_entry_permission": False,
        }
    )
    return result


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
    hidden_state_discovery = _hidden_state_discovery_contract(
        source.get("hidden_state_discovery_v3")
        or source.get("latent_state_discovery_v3")
    )
    path_clock_liquidity = path_clock_liquidity_contract_v3(
        source.get("path_clock_liquidity_v3")
        or source.get("path_clock_liquidity")
    )
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
        "hidden_state_discovery_v3": hidden_state_discovery,
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
    if path_clock_liquidity:
        result["path_clock_liquidity_v3"] = path_clock_liquidity

    motif_levels: list[dict[str, object]] = []
    for level in _rows(motif_lattice.get("levels"))[:4]:
        recent_nodes: list[dict[str, object]] = []
        for node in _rows(level.get("nodes"))[-1_000_000:]:
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
            for row in _rows(path_reconstruction.get("points"))[:1_000_000]
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
            for row in _rows(feature_ontology.get("public_features"))[:1_000_000]
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
            for row in _rows(concept_drift.get("partitions"))[:1_000_000]
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
            for row in _rows(cross_pair.get("edges"))[:1_000_000]
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
            for row in _rows(claim_proofs.get("coverage"))[:1_000_000]
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
            for row in _rows(claim_proofs.get("certificates"))[:1_000_000]
        ],
        "proves_integrity_not_causation": True,
        "study_only": True,
        "causal": False,
        "execution_authority": False,
    }
    return result


def _cpu_stream_source(
    source: Mapping[str, object],
    tracking_summary: Mapping[str, Any],
) -> Mapping[str, Any]:
    """Return the first declared CPU stream health payload.

    Raw tracker sessions currently publish the health block at the session root,
    while compact/live adapters can nest it under tracking.  This lookup is
    intentionally narrow so unrelated runtime diagnostics never cross into the
    operator workspace.
    """

    containers = (
        source,
        _mapping(source.get("tracking")),
        tracking_summary,
        _mapping(source.get("session")),
    )
    for container in containers:
        stream = _mapping(container.get("cpu_stream_v3"))
        if stream:
            return stream
    return {}


def _bounded_stream_count(*values: object) -> int:
    return min(_CPU_STREAM_COUNTER_LIMIT, _integer(*values))


def _cpu_stream_freshness_budget_sec(
    stream: Mapping[str, Any],
    temporal: Mapping[str, Any],
    *,
    acquisition_fps: float | None,
) -> float:
    """Return a bounded freshness window matched to the observed CPU cadence.

    The stream captures a native broker window on CPU and can legitimately run
    below its target rate while a frame capture or closed-candle study is in
    flight.  A fixed five-second timeout therefore produced false STALE reads on
    otherwise advancing streams.  This budget follows the slower of the recent
    and aggregate observed periods, while the hard ceiling still makes a stopped
    observer fail closed.  It affects forming-chart observation only and cannot
    refresh completed-candle evidence or entry permission.
    """

    candidate_budgets = [_CPU_STREAM_FRESHNESS_MIN_BUDGET_SEC]

    target_fps = _number(stream.get("target_fps"))
    if target_fps is not None and target_fps > 0.0:
        candidate_budgets.append(
            _CPU_STREAM_TARGET_PERIOD_MULTIPLIER / min(240.0, target_fps)
        )

    if acquisition_fps is not None and acquisition_fps > 0.0:
        candidate_budgets.append(
            _CPU_STREAM_OBSERVED_PERIOD_MULTIPLIER
            / min(240.0, acquisition_fps)
        )

    frame_delta_sec = _number(temporal.get("frame_delta_sec"))
    if frame_delta_sec is not None and frame_delta_sec > 0.0:
        candidate_budgets.append(
            _CPU_STREAM_OBSERVED_PERIOD_MULTIPLIER
            * min(_CPU_STREAM_FRESHNESS_MAX_BUDGET_SEC, frame_delta_sec)
        )

    return round(
        min(
            _CPU_STREAM_FRESHNESS_MAX_BUDGET_SEC,
            max(candidate_budgets),
        ),
        3,
    )


def _external_capture_stream_observation_v3(
    source: Mapping[str, object],
    *,
    now_epoch: float,
) -> dict[str, object]:
    """Project a fresh leased capture heartbeat as observation-only liveness.

    Browser/WGC capture has its own lease-fenced heartbeat and does not require
    the legacy CPU observer to be running.  This adapter only establishes that
    the selected pixels are arriving or being processed.  It deliberately
    supplies no direction, completed-candle truth, or entry authority.
    """

    capture_source = _mapping(source.get("capture_source_v3"))
    visual_observation = _mapping(source.get("visual_observation_v3"))
    capture_stream = _mapping(capture_source.get("stream"))
    source_state = _safe_public_text(
        capture_source.get("state"), "", limit=32
    ).upper()
    reason_code = _safe_public_text(
        capture_source.get("reason_code"), "", limit=48
    ).upper()
    visual_status = _safe_public_text(
        visual_observation.get("status"), "", limit=48
    ).upper()
    study_update_state = _safe_public_text(
        visual_observation.get("study_update_state"), "", limit=48
    ).upper()

    # A public source snapshot intentionally omits the private lease secret.
    # Its immutable ownership tuple is still present and proves that a
    # VALIDATING heartbeat belongs to a selected, lease-fenced transport.
    public_lease_identity = bool(
        _safe_identifier(capture_source.get("source_id"), "")
        and _safe_identifier(capture_source.get("sequence_id"), "")
        and _integer(capture_source.get("source_generation")) > 0
    )
    source_fresh = _explicit_bool(capture_source.get("fresh")) is True
    processing_reason = reason_code in {
        "FRAME_PROCESSING",
        "FRAME_PENDING",
        "WAITING_FOR_ANALYSIS",
        "ANALYSIS_PENDING",
    }
    source_transport_live = bool(
        source_fresh
        and (
            source_state == "LIVE"
            or (
                source_state == "VALIDATING"
                and public_lease_identity
                and (
                    processing_reason
                    or _explicit_bool(capture_stream.get("processing")) is True
                    or _explicit_bool(
                        capture_stream.get("material_change_pending")
                    )
                    is True
                )
            )
        )
    )
    visual_transport_live = bool(
        _safe_public_text(
            visual_observation.get("transport_state"), "", limit=32
        ).upper()
        == "LIVE"
        and _explicit_bool(visual_observation.get("transport_fresh")) is True
    )
    active = bool(source_transport_live or visual_transport_live)

    processing = bool(
        active
        and (
            processing_reason
            or _explicit_bool(capture_stream.get("processing")) is True
            or _explicit_bool(capture_stream.get("material_change_pending"))
            is True
            or visual_status
            in {
                "FRAME_PROCESSING",
                "FRAME_PENDING",
                "PROCESSING_FRAME",
                "WAITING_FOR_ANALYSIS",
                "ANALYSIS_PENDING",
            }
            or study_update_state in {"PROCESSING", "PENDING", "ANALYZING"}
        )
    )
    unchanged = bool(
        active
        and not processing
        and visual_status == "LIVE_FRAME_UNCHANGED"
        and _explicit_bool(visual_observation.get("new_visual_evidence"))
        is not True
    )
    activity_state = (
        "ANALYZING" if processing else "UNCHANGED" if unchanged else "OBSERVING"
    )
    activity_summary = {
        "ANALYZING": (
            "External chart transport is live; Phoenix Guard is analyzing the "
            "latest frame."
        ),
        "UNCHANGED": (
            "External chart transport is live; the latest delivered pixels are "
            "unchanged."
        ),
        "OBSERVING": "External chart transport is live and observing the selected chart.",
    }[activity_state]
    observed_at = _epoch(
        capture_source.get("last_frame_epoch"),
        capture_stream.get("last_capture_epoch"),
        capture_stream.get("last_transport_capture_epoch"),
        visual_observation.get("last_observed_epoch"),
        visual_observation.get("attempted_epoch"),
    )
    heartbeat_at = _epoch(
        capture_source.get("updated_at"),
        capture_stream.get("last_transport_heartbeat_epoch"),
        visual_observation.get("attempted_epoch"),
        observed_at,
    )
    # `fresh` is already computed beside the lease-fenced source.  A minimal
    # public snapshot may omit its private heartbeat time, so the projection
    # time is safe to use as the bounded observation timestamp in that case.
    if active and heartbeat_at is None:
        heartbeat_at = now_epoch
    if active and observed_at is None:
        observed_at = heartbeat_at
    return {
        "active": active,
        "state": activity_state,
        "summary": activity_summary,
        "observed_at": observed_at,
        "heartbeat_at": heartbeat_at,
        "reason": _safe_public_text(
            capture_source.get("message")
            or visual_observation.get("message")
            or activity_summary,
            activity_summary,
            limit=160,
        ),
        "frame_seq": _bounded_stream_count(
            capture_source.get("last_frame_id"),
            capture_stream.get("presented_frames"),
        ),
    }


def _cpu_stream_contract(
    source: Mapping[str, object],
    tracking_summary: Mapping[str, Any],
    *,
    now_epoch: float | None = None,
) -> dict[str, object]:
    """Project bounded stream health and an observation-only intrabar read.

    Pixel motion is useful for saying whether the displayed chart is moving,
    resting, or changing materially.  It is deliberately never converted into
    BUY/SELL truth: only a completed-candle study can supply direction or entry
    permission.
    """

    stream = _cpu_stream_source(source, tracking_summary)
    observer = _mapping(stream.get("observer"))
    observer_counters = _mapping(observer.get("counters"))
    lineage = _mapping(stream.get("last_keyframe_lineage"))
    last_decision = _mapping(observer.get("last_decision"))
    temporal = _mapping(last_decision.get("temporal_evidence"))
    motion = _mapping(temporal.get("motion"))
    rest = _mapping(temporal.get("rest"))
    wick_motion = _mapping(temporal.get("wick_motion"))
    change = _mapping(temporal.get("change"))

    enabled_flag = _explicit_bool(stream.get("enabled"))
    if enabled_flag is None:
        enabled_flag = _explicit_bool(stream.get("requested"))
    enabled = enabled_flag is True

    raw_state = _text(stream.get("state") or stream.get("status"), "", limit=48)
    normalized_state = _CPU_STREAM_STATE_ALIASES.get(
        raw_state.upper().replace("-", "_").replace(" ", "_"),
        "UNKNOWN" if stream else "UNAVAILABLE",
    )
    if stream and not enabled:
        normalized_state = "DISABLED"

    acquisition_fps = None
    for fps_value in (
        stream.get("acquisition_fps"),
        stream.get("actual_fps"),
        observer.get("acquisition_fps"),
        observer.get("observed_fps"),
    ):
        acquisition_fps = _number(fps_value)
        if acquisition_fps is not None:
            break
    if acquisition_fps is not None:
        acquisition_fps = round(max(0.0, min(240.0, acquisition_fps)), 3)

    freshness_budget_sec = _cpu_stream_freshness_budget_sec(
        stream,
        temporal,
        acquisition_fps=acquisition_fps,
    )
    available_flag = _explicit_bool(stream.get("available"))
    stream_available = available_flag is not False

    current_epoch = float(now_epoch if now_epoch is not None else time.time())
    heartbeat_epoch = _epoch(
        stream.get("status_updated_epoch"),
        stream.get("updated_epoch"),
        stream.get("updated_at"),
    )
    last_frame_epoch = _epoch(
        stream.get("last_frame_epoch"),
        stream.get("last_capture_epoch"),
        observer.get("last_captured_epoch"),
    )
    heartbeat_age = (
        round(max(0.0, current_epoch - heartbeat_epoch), 3)
        if heartbeat_epoch is not None
        else None
    )
    frame_age = (
        round(max(0.0, current_epoch - last_frame_epoch), 3)
        if last_frame_epoch is not None
        else None
    )
    stream_fresh = bool(
        enabled
        and stream_available
        and normalized_state == "RUNNING"
        and heartbeat_age is not None
        and frame_age is not None
        and -1.0 <= current_epoch - cast(float, heartbeat_epoch)
        <= freshness_budget_sec
        and -1.0 <= current_epoch - cast(float, last_frame_epoch)
        <= freshness_budget_sec
    )
    cpu_stream_fresh = stream_fresh
    external_observation = _external_capture_stream_observation_v3(
        source,
        now_epoch=current_epoch,
    )
    external_active = external_observation.get("active") is True
    if external_active and not stream_fresh:
        # The Edge/WGC lease is an independent live transport.  It replaces
        # only the health/read portion of this legacy-named contract; all
        # directional fields below remain neutral and completed-candle gated.
        enabled = True
        normalized_state = "RUNNING"
        stream_fresh = True
        heartbeat_epoch = _epoch(external_observation.get("heartbeat_at"))
        last_frame_epoch = _epoch(external_observation.get("observed_at"))
        heartbeat_age = (
            round(max(0.0, current_epoch - heartbeat_epoch), 3)
            if heartbeat_epoch is not None
            else None
        )
        frame_age = (
            round(max(0.0, current_epoch - last_frame_epoch), 3)
            if last_frame_epoch is not None
            else None
        )

    raw_activity = _text(
        temporal.get("state") or motion.get("state") or observer.get("state"),
        "",
        limit=32,
    ).upper().replace("-", "_").replace(" ", "_")
    activity_aliases = {
        "MATERIAL_CHANGE": "MATERIAL_CHANGE",
        "MOTION": "MOVING",
        "MOVING": "MOVING",
        "REST": "RESTING",
        "RESTING": "RESTING",
        "DUPLICATE": "UNCHANGED",
        "UNCHANGED": "UNCHANGED",
        "KEYFRAME": "STARTING",
        "IDLE": "STARTING",
    }
    if external_active and not cpu_stream_fresh:
        activity_state = _safe_public_text(
            external_observation.get("state"), "OBSERVING", limit=32
        ).upper()
    elif not stream or not enabled:
        activity_state = "UNAVAILABLE"
    elif not stream_fresh:
        activity_state = "STARTING" if last_frame_epoch is None else "STALE"
    else:
        activity_state = activity_aliases.get(raw_activity, "OBSERVING")

    motion_score_value = _number(motion.get("motion_score"))
    motion_score = (
        round(max(0.0, min(1.0, motion_score_value)), 6)
        if motion_score_value is not None
        else None
    )
    acceleration_value = _number(motion.get("motion_acceleration"))
    motion_acceleration = (
        round(max(-1.0, min(1.0, acceleration_value)), 6)
        if acceleration_value is not None
        else None
    )
    changed_ratio_value = _number(change.get("changed_pixel_ratio"))
    changed_pixel_ratio = (
        round(max(0.0, min(1.0, changed_ratio_value)), 6)
        if changed_ratio_value is not None
        else None
    )
    wick_pressure = _text(
        wick_motion.get("dominant_extreme"), "NONE", limit=24
    ).upper()
    if wick_pressure not in {"UPPER", "LOWER", "BALANCED", "NONE"}:
        wick_pressure = "NONE"
    activity_summary = {
        "MATERIAL_CHANGE": "Live stream sees a material change on the chart.",
        "MOVING": "Live stream sees the chart moving now.",
        "RESTING": "Live stream sees the chart resting now.",
        "UNCHANGED": "Live stream sees no material visual change right now.",
        "STARTING": "Live stream is establishing a current visual baseline.",
        "ANALYZING": (
            "External chart transport is live; Phoenix Guard is analyzing the "
            "latest frame."
        ),
        "STALE": "The last stream frame is too old to describe the chart now.",
        "UNAVAILABLE": "No current CPU stream observation is available.",
        "OBSERVING": "Live stream is observing the current chart.",
    }[activity_state]
    market_read = {
        "schema_version": _CPU_STREAM_MARKET_READ_SCHEMA_VERSION,
        "state": activity_state,
        "summary": activity_summary,
        "fresh": stream_fresh,
        "observed_at": last_frame_epoch,
        "heartbeat_at": heartbeat_epoch,
        "frame_age_seconds": frame_age,
        "heartbeat_age_seconds": heartbeat_age,
        "freshness_budget_seconds": freshness_budget_sec,
        "frame_seq": _bounded_stream_count(
            temporal.get("frame_seq"),
            last_decision.get("frame_seq"),
            observer.get("frame_seq"),
            external_observation.get("frame_seq") if external_active else None,
        ),
        "stream_generation": _bounded_stream_count(
            temporal.get("stream_generation"),
            last_decision.get("stream_generation"),
            observer.get("stream_generation"),
            lineage.get("stream_generation"),
        ),
        "motion_score": motion_score,
        "motion_acceleration": motion_acceleration,
        "changed_pixel_ratio": changed_pixel_ratio,
        "rest_active": _explicit_bool(rest.get("active")) is True,
        "rest_duration_seconds": round(
            max(0.0, _number(rest.get("duration_sec")) or 0.0), 3
        ),
        "wick_pressure": wick_pressure,
        "direction": "NEUTRAL",
        "direction_basis": "COMPLETED_CANDLE_REQUIRED",
        "direction_available": False,
        "forming_candle": True,
        "closed_candle": False,
        "can_grant_entry_permission": False,
        "study_only": True,
        "execution_authority": False,
        "broker_click_authority": False,
    }

    last_reason = _safe_public_text(
        external_observation.get("reason")
        if external_active
        else stream.get("last_reason")
        or lineage.get("accepted_reason")
        or stream.get("last_error"),
        "",
        limit=160,
    )

    return {
        "enabled": enabled,
        "state": normalized_state,
        "acquisition_fps": acquisition_fps,
        "observed_frames": _bounded_stream_count(stream.get("observed_frames")),
        "accepted_keyframes": _bounded_stream_count(
            stream.get("accepted_keyframes"), stream.get("accepted_events")
        ),
        "dropped_frames": _bounded_stream_count(
            stream.get("dropped_frames"), stream.get("dropped_keyframes")
        ),
        "duplicate_frames": _bounded_stream_count(
            stream.get("duplicate_frames"),
            observer.get("duplicate_frames"),
            observer.get("duplicate_events"),
            observer_counters.get("duplicate_frames"),
        ),
        "last_frame_epoch": last_frame_epoch,
        "last_keyframe_epoch": _epoch(
            stream.get("last_keyframe_epoch"), stream.get("last_event_epoch")
        ),
        "heartbeat_epoch": heartbeat_epoch,
        "fresh": stream_fresh,
        "last_reason": last_reason,
        "stream_generation": _bounded_stream_count(
            stream.get("stream_generation"), lineage.get("stream_generation")
        ),
        "market_read": market_read,
    }


def cpu_stream_tracking_contract_v3(
    payload: Mapping[str, object],
    *,
    now_epoch: float | None = None,
) -> dict[str, object]:
    """Project the safe stream strip independently of the cached workspace."""

    return _cpu_stream_contract(
        payload,
        _mapping(payload.get("tracking_summary")),
        now_epoch=now_epoch,
    )


def _closed_candle_basis_v3(
    contract: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, object]:
    existing = _mapping(evidence.get("closed_candle_basis"))
    if existing:
        return {
            "headline": _text(existing.get("headline"), "", limit=240),
            "answer": _text(existing.get("answer"), "", limit=960),
            "state": _text(existing.get("state"), "UNKNOWN", limit=40).upper(),
            "updated_at": _epoch(existing.get("updated_at")),
            "reason": _text(existing.get("reason"), "", limit=320),
            "next_trigger": _text(existing.get("next_trigger"), "", limit=320),
        }
    return {
        "headline": _text(contract.get("headline"), "", limit=240),
        "answer": _text(contract.get("answer"), "", limit=960),
        "state": _text(contract.get("state"), "UNKNOWN", limit=40).upper(),
        "updated_at": _epoch(contract.get("updated_at")),
        "reason": _text(contract.get("reason"), "", limit=320),
        "next_trigger": _text(contract.get("next_trigger"), "", limit=320),
    }


def _stream_activity_label(state: str) -> str:
    return {
        "MATERIAL_CHANGE": "changing materially",
        "MOVING": "moving",
        "RESTING": "resting",
        "UNCHANGED": "visually unchanged",
        "ANALYZING": "analyzing the latest frame",
        "STARTING": "establishing its baseline",
        "OBSERVING": "being observed",
    }.get(state, "not current")


def _current_order_reference_area_label_v3(
    value: object,
    *,
    side: str,
) -> str:
    """Return one bounded, side-matched public reference label when present."""

    if side not in _DIRECTIONAL_SIDES:
        return ""
    candidates: list[tuple[int, str]] = []
    for row in _rows(value):
        if (
            _text(row.get("layer"), "", limit=40).lower() != "order_positioning"
            or _side(row.get("side")) != side
            or _text(row.get("lifecycle"), "current", limit=24).lower()
            != "current"
        ):
            continue
        mode = _text(row.get("positioning_mode"), "", limit=24).upper()
        if mode not in {"REFERENCE", "CURRENT"}:
            continue
        label = _safe_public_text(row.get("label"), "", limit=80)
        if label:
            candidates.append((0 if mode == "REFERENCE" else 1, label))
    return min(candidates)[1] if candidates else ""


def _streaming_three_question_synthesis_v3(
    questions: Mapping[str, Any],
    *,
    permission: Mapping[str, Any],
    stream: Mapping[str, Any],
    order_reference_rows: object = (),
    identity_matches: bool = True,
    identity_rebind_pending: bool = False,
    completed_study_current: bool = False,
    now_epoch: float,
) -> dict[str, object]:
    """Refresh Q2/Q3 from one bounded stream heartbeat without minting authority."""

    result = dict(questions)
    market_read = dict(_mapping(stream.get("market_read")))
    stream_fresh = market_read.get("fresh") is True
    stream_state = _text(market_read.get("state"), "UNAVAILABLE", limit=32).upper()
    stream_summary = _text(
        market_read.get("summary"),
        "No current stream observation is available.",
        limit=240,
    )
    heartbeat_epoch = _epoch(
        market_read.get("heartbeat_at"),
        stream.get("heartbeat_epoch"),
    )

    studied = dict(_mapping(result.get("studied_direction_current")))
    studied_evidence = dict(_mapping(studied.get("evidence")))
    studied_basis = _closed_candle_basis_v3(studied, studied_evidence)
    studied_evidence["closed_candle_basis"] = studied_basis
    studied_evidence["streaming_market_read"] = market_read
    studied_evidence["stream_read_fresh"] = stream_fresh
    studied_evidence["stream_frame_seq"] = _bounded_stream_count(
        market_read.get("frame_seq")
    )
    studied["evidence"] = studied_evidence
    studied_side = _side(studied.get("side"))
    if stream_fresh:
        activity_label = _stream_activity_label(stream_state)
        studied["headline"] = (
            f"{studied_side} was studied; live stream is {activity_label}"
            if studied_side in _DIRECTIONAL_SIDES
            else f"No closed-candle direction yet; live stream is {activity_label}"
        )
        basis_answer = _text(studied_basis.get("answer"), "", limit=960)
        studied["answer"] = " ".join(
            part
            for part in (
                basis_answer,
                stream_summary,
                "This intrabar observation is current, but it does not replace completed-candle direction.",
            )
            if part
        )
        studied["updated_at"] = heartbeat_epoch
        studied["live_state"] = (
            "ANALYZING" if stream_state == "ANALYZING" else "STREAMING"
        )
    else:
        studied["live_state"] = stream_state
    result["studied_direction_current"] = studied

    entry = dict(_mapping(result.get("entry_now")))
    timing_forecast = dict(_mapping(entry.get("timing_forecast")))
    initial_entry_answer = _safe_public_text(
        entry.get("answer"), "", limit=960
    )
    prior_operator_action = _mapping(entry.get("operator_action"))
    prior_action_instruction = _safe_public_text(
        prior_operator_action.get("instruction"), "", limit=960
    )
    entry_evidence = dict(_mapping(entry.get("evidence")))
    entry_basis = _closed_candle_basis_v3(entry, entry_evidence)
    entry_evidence["closed_candle_basis"] = entry_basis
    entry_evidence["streaming_market_read"] = market_read
    entry_evidence["stream_read_fresh"] = stream_fresh
    entry_evidence["stream_frame_seq"] = _bounded_stream_count(
        market_read.get("frame_seq")
    )
    entry["question"] = "What is the best decision to do right now?"

    permission_allowed = permission.get("allowed") is True
    permission_side = _side(permission.get("side"))
    permission_action = _text(permission.get("action"), "WAIT", limit=32).upper()
    permission_contract_authorized = bool(
        identity_matches
        and permission_allowed
        and permission_side in _DIRECTIONAL_SIDES
        and permission_action in {f"{permission_side}_NOW", permission_side}
    )
    broker_expiry = dict(
        _mapping(
            entry_evidence.get("broker_expiry_v3")
            or entry.get("broker_expiry_v3")
        )
    )
    broker_expiry_valid_until = _epoch(
        broker_expiry.get("valid_until_epoch")
    )
    broker_expiry_proven = bool(
        broker_expiry.get("proven") is True
        and broker_expiry_valid_until is not None
        and broker_expiry_valid_until > now_epoch
    )
    broker_expiry_seconds = _number(broker_expiry.get("expiry_seconds"))
    broker_expiry_eligible = bool(
        broker_expiry_proven
        and broker_expiry.get("eligible") is True
        and broker_expiry_seconds is not None
        and broker_expiry_seconds >= MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS
    )
    if not broker_expiry_proven:
        broker_expiry.update(
            {
                "status": "UNVERIFIED",
                "proven": False,
                "eligible": False,
                "expiry_seconds": None,
                "valid_until_epoch": None,
                "instruction": (
                    "SET/VERIFY EXPIRY ≥15 MIN — Broker expiry unverified; the "
                    "model horizon is not the broker contract duration."
                ),
            }
        )
    entry_permission_authorized = bool(
        permission_contract_authorized and broker_expiry_eligible
    )
    timing_supports_entry = bool(
        entry.get("timing_supports_entry") is True
        or entry_evidence.get("timing_supports_entry") is True
    )
    timing_effect = dict(
        _mapping(entry_evidence.get("path_clock_liquidity_v3"))
    )
    timing_valid_until = _number(timing_effect.get("valid_until"))
    exact_forecast_end_epoch = _epoch(
        timing_forecast.get("target_window_end_epoch_seconds")
    )
    exact_forecast_expired = bool(
        timing_forecast.get("exact_wall_clock_proven") is True
        and exact_forecast_end_epoch is not None
        and now_epoch >= exact_forecast_end_epoch
    )
    timing_expired = bool(
        exact_forecast_expired
        or (
            timing_effect.get("maturity_claimed") is True
            and timing_valid_until is not None
            and timing_valid_until <= now_epoch
        )
    )
    timing_veto_basis = _text(
        timing_effect.get("timing_veto_basis"), "NONE", limit=40
    ).upper()
    timing_veto = bool(
        timing_veto_basis == "DURATION_INELIGIBLE"
        or (
            not timing_expired
            and (
                entry.get("timing_veto") is True
                or entry_evidence.get("timing_veto") is True
            )
        )
    )
    if timing_expired:
        timing_supports_entry = False
        timing_effect.update(
            {
                "fresh": False,
                "state": "PROVISIONAL",
                "timing_supports_entry": False,
                "timing_veto": timing_veto,
                "reason": (
                    "The exact timing read expired. The completed-candle forecast "
                    "remains visible, but its replay calibration is no longer current."
                ),
            }
        )
        entry_evidence["path_clock_liquidity_v3"] = timing_effect
    enter_now = bool(
        entry_permission_authorized and not timing_veto
    )
    timing_state = _text(
        entry.get("timing_state") or entry_basis.get("state"),
        "FORMING",
        limit=40,
    ).upper()
    if not identity_matches:
        timing_state = "INVALIDATED"
    elif permission_contract_authorized and broker_expiry_proven and not broker_expiry_eligible:
        timing_state = "DURATION_INELIGIBLE"
    elif permission_contract_authorized and not broker_expiry_proven:
        timing_state = "EXPIRY_UNVERIFIED"
    elif timing_veto and timing_state not in {"INVALIDATED", "MISSED", "CONFLICT"}:
        timing_state = "TIMING_DELAY"
    elif timing_state == "ENTER_NOW" and not enter_now:
        timing_state = "FORMING" if stream_fresh else "STALE"
    # No issued entry deadline is not the same thing as stale market truth.
    # The closed-candle direction may be identity-proven while the independent
    # entry contract has never opened a window, leaving its legacy freshness
    # field UNKNOWN.  When the exact live stream is fresh, keep that condition
    # visible as a current PREPARE/OBSERVE study instead of collapsing Q3 into
    # the generic STAY OUT/STALE fallback.  Explicit stale, invalidated,
    # conflicting, missed, and countertrend-stale evidence remains fail-closed.
    current_study_without_issued_window = bool(
        stream_fresh
        and identity_matches
        and timing_state == "STALE"
        and _text(entry_evidence.get("freshness"), "UNKNOWN", limit=32).upper()
        == "UNKNOWN"
        and entry_evidence.get("directional_study_present") is True
        and _text(
            entry_evidence.get("countertrend_validation_state"),
            "ABSENT",
            limit=32,
        ).upper()
        not in {"STALE", "INVALIDATED"}
        and studied_side in _DIRECTIONAL_SIDES
    )
    market_origin = _mapping(result.get("market_origin_history"))
    market_origin_evidence = _mapping(market_origin.get("evidence"))
    major_side = _side(
        studied_evidence.get("major_trend_side"),
        market_origin_evidence.get("major_trend_side"),
        market_origin.get("side"),
    )
    prior_studied_side = _side(
        entry_evidence.get("historical_studied_side"),
        entry.get("side"),
        studied_side,
    )
    current_regression_side = _side(
        studied_evidence.get("current_regression_side")
    )
    current_actionable_side = _side(current_regression_side, major_side)
    current_regression_major_aligned = bool(
        current_actionable_side in _DIRECTIONAL_SIDES
        and (
            current_regression_side not in _DIRECTIONAL_SIDES
            or major_side not in _DIRECTIONAL_SIDES
            or current_regression_side == major_side
        )
    )
    prior_thesis_superseded = bool(
        timing_state in {"MISSED", "STALE", "FORMING", "WAITING"}
        and prior_studied_side in _DIRECTIONAL_SIDES
        and current_actionable_side in _DIRECTIONAL_SIDES
        and prior_studied_side != current_actionable_side
        and current_regression_major_aligned
    )
    selected_side = (
        current_actionable_side
        if prior_thesis_superseded
        else _side(prior_studied_side, current_actionable_side)
    )
    closed_move_side = _side(entry_evidence.get("current_move_side"))
    order_reference_label = _current_order_reference_area_label_v3(
        order_reference_rows,
        side=selected_side,
    )
    order_reference_guidance = (
        f" Use the current {order_reference_label} as the reference area; "
        "the stream alone does not prove price is inside it."
        if order_reference_label
        else ""
    )
    study_transition_guidance = (
        f"The prior {prior_studied_side} thesis remains history; the current "
        f"closed-candle study now tracks {selected_side}."
        if prior_thesis_superseded
        else ""
    )

    if enter_now:
        best_action = f"ENTER_{permission_side}"
        decision_state = "ENTER_NOW"
        entry["headline"] = f"ENTER {permission_side} NOW — verified window open"
        entry["answer"] = _text(
            entry_basis.get("answer"),
            f"Enter {permission_side} only inside the current verified window.",
            limit=960,
        )
        entry["reason"] = _safe_public_text(
            permission.get("message"),
            "Closed-candle permission and the current opportunity window are aligned.",
            limit=320,
        )
        entry["next_trigger"] = _safe_public_text(
            permission.get("next_condition"),
            "Stop if the verified window closes or the closed-candle truth changes.",
            limit=320,
        )
    elif timing_state == "TIMING_DELAY":
        best_action = "DELAY_FOR_TIMING"
        decision_state = "TIMING_DELAY"
        timing_reason = _safe_public_text(
            timing_effect.get("reason") or entry.get("reason"),
            "Historical path, clock, and liquidity evidence says delay this entry.",
            limit=320,
        )
        entry["headline"] = (
            f"{selected_side} timing is delayed by mature evidence"
            if selected_side in _DIRECTIONAL_SIDES
            else "Trade timing is delayed by mature evidence"
        )
        entry["answer"] = " ".join(
            part
            for part in (
                timing_reason,
                stream_summary if stream_fresh else "",
                "At least 15 minutes of room is required, and timing cannot create entry permission.",
            )
            if part
        )
        entry["reason"] = timing_reason
        entry["next_trigger"] = (
            "Wait for a fresh matching timing read with at least 15 minutes of room and "
            "an independently verified entry window."
        )
    elif timing_state == "DURATION_INELIGIBLE":
        best_action = "AVOID_SHORT_DURATION"
        decision_state = "DURATION_INELIGIBLE"
        entry["answer"] = _safe_public_text(
            broker_expiry.get("instruction"),
            "AVOID — the broker duration is under 15 minutes.",
            limit=320,
        )
        entry["reason"] = entry["answer"]
        entry["next_trigger"] = (
            "Set and verify a broker expiry of at least 15 minutes."
        )
    elif timing_state == "EXPIRY_UNVERIFIED":
        best_action = "SET_VERIFY_EXPIRY"
        decision_state = "EXPIRY_UNVERIFIED"
        entry["answer"] = _safe_public_text(
            broker_expiry.get("instruction"),
            "SET/VERIFY EXPIRY ≥15 MIN — Broker expiry unverified.",
            limit=320,
        )
        entry["reason"] = entry["answer"]
        entry["next_trigger"] = (
            "Bind a current broker expiry of at least 15 minutes to this exact "
            "pair, timeframe, frame, and completed candle."
        )
    elif timing_state == "MISSED":
        best_action = (
            f"WAIT_FOR_FRESH_{selected_side}_PULLBACK"
            if selected_side in _DIRECTIONAL_SIDES
            else "STAND_ASIDE"
        )
        decision_state = timing_state
        if stream_fresh:
            entry["headline"] = (
                f"WAIT FOR A FRESH {selected_side} PULLBACK — the prior "
                f"{prior_studied_side} opportunity was missed"
                if selected_side in _DIRECTIONAL_SIDES
                else "STAND ASIDE — the prior opportunity was missed"
            )
            entry["answer"] = " ".join(
                part
                for part in (
                    stream_summary,
                    _text(entry_basis.get("answer"), "Do not enter this trade.", limit=960),
                    study_transition_guidance,
                    order_reference_guidance.strip(),
                )
                if part
            )
        if selected_side in _DIRECTIONAL_SIDES:
            entry["next_trigger"] = (
                f"Wait for a fresh {selected_side} pullback to publish its own verified "
                "closed-candle entry window."
            )
    elif timing_state in {"INVALIDATED", "CONFLICT"}:
        best_action = "STAND_ASIDE"
        decision_state = timing_state
        if stream_fresh:
            entry["headline"] = {
                "INVALIDATED": "STAND ASIDE — the prior evidence was invalidated",
                "CONFLICT": "STAND ASIDE — live and studied evidence conflict",
            }[timing_state]
            entry["answer"] = " ".join(
                part
                for part in (
                    stream_summary,
                    _text(entry_basis.get("answer"), "Do not enter this trade.", limit=960),
                )
                if part
            )
    elif stream_fresh and stream_state in {"MATERIAL_CHANGE", "MOVING"}:
        if selected_side in _DIRECTIONAL_SIDES:
            aligned_continuation = closed_move_side == selected_side
            best_action = (
                f"TRACK_{selected_side}_CONTINUATION"
                if aligned_continuation
                else f"TRACK_{selected_side}"
            )
            entry["headline"] = (
                f"TRACK {selected_side} CONTINUATION — live chart is "
                f"{_stream_activity_label(stream_state)}"
                if aligned_continuation
                else f"TRACK {selected_side} — live chart is "
                f"{_stream_activity_label(stream_state)}"
            )
            entry["answer"] = (
                f"Track the existing {selected_side} thesis. {stream_summary} "
                f"{study_transition_guidance} "
                "The stream read is intrabar observation only; closed-candle entry permission "
                "is closed, so remain out until a fresh verified window opens."
            )
        else:
            best_action = "OBSERVE_MOVE"
            entry["headline"] = "OBSERVE — the live chart is moving"
            entry["answer"] = (
                f"{stream_summary} No completed-candle direction currently has entry "
                "permission, so observe the move without entering."
            )
        decision_state = "TRACKING"
    elif stream_fresh and stream_state == "RESTING":
        if selected_side in _DIRECTIONAL_SIDES:
            retrace_name = "PULLBACK" if selected_side == "BUY" else "RALLY"
            best_action = f"WATCH_{selected_side}_{retrace_name}"
            decision_state = "WATCHING_RETRACE"
            entry["headline"] = (
                f"WATCH {selected_side} {retrace_name} — the live chart is resting"
            )
            entry["answer"] = (
                f"The completed-candle {selected_side} thesis remains the directional "
                f"context. {stream_summary} {study_transition_guidance}"
                f"{order_reference_guidance} Do not enter until "
                "a fresh closed-candle permission window verifies the continuation."
            )
        else:
            best_action = "STAND_ASIDE"
            decision_state = "RESTING"
            entry["headline"] = "STAND ASIDE — the live chart is resting"
            entry["answer"] = (
                f"{stream_summary} Keep the completed-candle study as context, but do not "
                "enter without a newly verified permission window."
            )
    elif stream_fresh and stream_state == "UNCHANGED":
        # Byte-identical pixels can be a quiet visible surface or a stale
        # Chromium off-screen cache. They are capture-health evidence, never
        # enough evidence to classify the market itself as resting.
        decision_state = "OBSERVING_CAPTURE"
        if selected_side in _DIRECTIONAL_SIDES:
            best_action = f"TRACK_{selected_side}"
            entry["headline"] = (
                f"WATCH {selected_side} — live pixels are unchanged"
            )
            entry["answer"] = (
                f"The completed-candle {selected_side} thesis remains context. "
                f"{stream_summary} Unchanged pixels do not prove a market rest. "
                f"{study_transition_guidance}{order_reference_guidance} Wait for a fresh "
                "capture and a verified closed-candle permission window before entering."
            )
        else:
            best_action = "OBSERVE_CAPTURE"
            entry["headline"] = "OBSERVE — live pixels are unchanged"
            entry["answer"] = (
                f"{stream_summary} Unchanged pixels alone do not prove that the market "
                "is resting. Wait for a fresh capture and completed-candle evidence; "
                "there is no verified entry permission now."
            )
    elif stream_fresh:
        if (
            stream_state == "ANALYZING"
            and completed_study_current
            and selected_side in _DIRECTIONAL_SIDES
        ):
            best_action = f"TRACK_{selected_side}"
            decision_state = "TRACKING_LATEST_COMPLETED"
            entry["headline"] = (
                f"TRACK {selected_side} — next frame is being analyzed"
            )
            entry["answer"] = (
                f"The exact latest completed-candle study tracks {selected_side}. "
                f"{stream_summary} Its lineage-bound timing forecast remains visible, "
                "but entry permission is closed while the new frame is analyzed."
            )
        else:
            best_action = (
                "ANALYZE_CURRENT_FRAME"
                if stream_state == "ANALYZING"
                else "OBSERVE"
            )
            decision_state = (
                "ANALYZING" if stream_state == "ANALYZING" else "OBSERVING"
            )
            entry["headline"] = (
                "ANALYZING CURRENT FRAME"
                if stream_state == "ANALYZING"
                else "OBSERVE — the stream is building the current read"
            )
            entry["answer"] = (
                f"{stream_summary} Do not enter until completed-candle permission is current."
            )
    else:
        best_action = "STAND_ASIDE" if timing_state in {"STALE", "WAITING"} else "OBSERVE"
        decision_state = timing_state

    # The stream contributes current movement context, but it must not replace
    # the completed-candle timing forecast on every heartbeat.  Keep forecast
    # and action as separate contracts: one answers when/which path, the other
    # says what the operator may do now.
    current_action_instruction = _safe_public_text(
        entry.get("answer"),
        "Keep observing until the next completed-candle update.",
        limit=960,
    )
    stream_instruction = (
        prior_action_instruction
        if prior_action_instruction
        and current_action_instruction == initial_entry_answer
        else current_action_instruction
    )
    if (
        not enter_now
        and selected_side in _DIRECTIONAL_SIDES
        and (
            timing_state == "MISSED"
            or decision_state == "WATCHING_RETRACE"
        )
    ):
        pullback_instruction = (
            f"Wait for a fresh {selected_side} pullback and its own fresh verified "
            "entry window before entry."
        )
        stream_instruction = " ".join(
            part for part in (stream_instruction, pullback_instruction) if part
        )
    forecast_side = _side(timing_forecast.get("side"))
    if not identity_matches or (
        forecast_side in _DIRECTIONAL_SIDES
        and selected_side in _DIRECTIONAL_SIDES
        and forecast_side != selected_side
    ):
        # Never retarget a probability or time window across directions.  The
        # next completed study must publish a new lineage-bound forecast.
        timing_forecast = {
            "schema_version": "PG_OPERATOR_TIMING_FORECAST_V3",
            "status": "DIRECTION_UNRESOLVED",
            "headline": (
                "Direction unresolved on the current pair \u00b7 reassess after the "
                "next completed close"
                if not identity_matches
                else f"{selected_side} timing will publish after the next completed close"
            ),
            "summary": (
                "The completed direction changed, so the prior timing field was "
                "discarded instead of being relabelled."
            ),
            "closed_candle_summary": (
                "The completed direction changed, so the prior timing field was "
                "discarded instead of being relabelled."
            ),
            "side": "NEUTRAL",
            "horizon_label": "awaiting current lineage",
            "estimated_likelihood": None,
            "estimated_likelihood_label": "Estimated likelihood unavailable",
            "evidence_confidence": None,
            "evidence_confidence_label": "Evidence confidence unavailable",
            "confidence": None,
            "confidence_label": "Evidence confidence unavailable",
            "calibration_grade": "UNRATED",
            "calibration_label": (
                "Calibration UNRATED \u00b7 not replay-calibrated"
            ),
            "calibrated": False,
            "support_count": 0,
            "source": "CURRENT_LINEAGE_REQUIRED",
            "source_label": "Current completed-candle lineage required",
            "provisional": True,
            "rest_sweep_risk": (
                "The prior direction's rest and sweep estimate was discarded."
            ),
            "base_rest_sweep_risk": (
                "The prior direction's rest and sweep estimate was discarded."
            ),
            "invalidation": (
                "Reassess after a completed candle binds the new direction."
            ),
            "study_only": True,
            "execution_authority": False,
            "broker_click_authority": False,
            "can_grant_entry_permission": False,
        }
    elif timing_forecast:
        exact_start_epoch = _epoch(
            timing_forecast.get("target_window_start_epoch_seconds")
        )
        if (
            timing_forecast.get("exact_wall_clock_proven") is True
            and exact_start_epoch is not None
            and exact_forecast_end_epoch is not None
            and not exact_forecast_expired
        ):
            fixed_window = _fixed_exact_window_read_v3(
                side=_side(timing_forecast.get("side")),
                start_epoch=exact_start_epoch,
                end_epoch=exact_forecast_end_epoch,
                now_epoch=now_epoch,
            )
            fixed_headline = _safe_public_text(
                fixed_window.get("headline"),
                _safe_public_text(
                    timing_forecast.get("headline"), "", limit=180
                ),
                limit=180,
            )
            if timing_forecast.get("active_target_next_impulse") is True:
                fixed_side = _side(timing_forecast.get("side"))
                fixed_countdown = _safe_public_text(
                    fixed_window.get("countdown_label"),
                    "fixed anchor-bound window",
                    limit=120,
                ).lower()
                fixed_headline = (
                    f"{fixed_side} is active · next {fixed_side} impulse "
                    f"{fixed_countdown}"
                )
            timing_forecast["headline"] = _safe_public_text(
                fixed_headline,
                "Direction unresolved · exact window active",
                limit=180,
            )
            timing_forecast["countdown_label"] = _safe_public_text(
                fixed_window.get("countdown_label"), "", limit=160
            )
            timing_forecast["seconds_until_window_start"] = _integer(
                fixed_window.get("seconds_until_window_start")
            )
            timing_forecast["seconds_until_window_end"] = _integer(
                fixed_window.get("seconds_until_window_end")
            )
            timing_forecast["horizon_label"] = timing_forecast[
                "countdown_label"
            ]
        if exact_forecast_expired:
            expired_side = _side(timing_forecast.get("side"))
            expired_headline = (
                f"{expired_side} remains the studied path \u00b7 exact timing expired"
                if expired_side in _DIRECTIONAL_SIDES
                else "Direction unresolved \u00b7 exact timing expired"
            )
            expired_summary = (
                f"{expired_headline}. The expired replay field was removed; a fresh "
                "lineage-bound timing forecast is required."
            )
            timing_forecast.update(
                {
                    "headline": expired_headline,
                    "summary": expired_summary,
                    "closed_candle_summary": expired_summary,
                    "horizon_label": "exact timing expired",
                    "horizon_seconds_low": None,
                    "horizon_seconds_high": None,
                    "horizon_candles_low": None,
                    "horizon_candles_high": None,
                    "anchor_close_epoch_seconds": None,
                    "target_window_start_epoch_seconds": None,
                    "target_window_central_epoch_seconds": None,
                    "target_window_end_epoch_seconds": None,
                    "countdown_label": "Exact anchor-bound window expired",
                    "seconds_until_window_start": None,
                    "seconds_until_window_end": None,
                    "estimated_likelihood": None,
                    "estimated_likelihood_label": (
                        "Estimated likelihood unavailable \u00b7 exact replay expired"
                    ),
                    "evidence_confidence": None,
                    "evidence_confidence_label": (
                        "Evidence confidence unavailable \u00b7 exact replay expired"
                    ),
                    "confidence": None,
                    "confidence_label": (
                        "Evidence confidence unavailable \u00b7 exact replay expired"
                    ),
                    "calibration_grade": "UNRATED",
                    "calibration_label": (
                        "Calibration UNRATED \u00b7 exact replay expired"
                    ),
                    "calibrated": False,
                    "support_count": 0,
                    "source": "CURRENT_CLOSED_CANDLE_DIRECTION",
                    "source_label": (
                        "Current completed-candle direction \u00b7 exact timing expired"
                    ),
                    "provisional": True,
                    "exact_wall_clock_proven": False,
                    "rest_sweep_risk": (
                        "The exact rest and sweep estimate expired and was removed."
                    ),
                    "base_rest_sweep_risk": (
                        "The exact rest and sweep estimate expired and was removed."
                    ),
                    "invalidation": (
                        "Reassess after a fresh completed candle publishes current timing."
                    ),
                }
            )
            technical_estimates = dict(
                _mapping(timing_forecast.get("technical_estimates"))
            )
            technical_estimates["stop_survival"] = {}
            technical_estimates["adverse_excursion_risk"] = {}
            timing_forecast["technical_estimates"] = technical_estimates
        base_summary = _safe_public_text(
            timing_forecast.get("closed_candle_summary")
            or timing_forecast.get("summary"),
            "The completed-candle timing forecast remains in force.",
            limit=480,
        )
        timing_forecast["closed_candle_summary"] = base_summary
        timing_forecast["summary"] = " ".join(
            part
            for part in (
                base_summary,
                stream_summary if stream_fresh else "",
            )
            if part
        )
        base_risk = _safe_public_text(
            timing_forecast.get("base_rest_sweep_risk")
            or timing_forecast.get("rest_sweep_risk"),
            "No calibrated sweep probability is available.",
            limit=320,
        )
        timing_forecast["base_rest_sweep_risk"] = base_risk
        if stream_fresh and stream_state == "RESTING":
            timing_forecast["rest_sweep_risk"] = (
                f"{base_risk} The live stream currently sees a rest; wait for the "
                "completed-candle continuation or invalidation."
            )
        elif stream_fresh and stream_state in {"MOVING", "MATERIAL_CHANGE"}:
            timing_forecast["rest_sweep_risk"] = (
                f"{base_risk} The live chart is moving, but intrabar motion does not "
                "change the published timing window."
            )
        else:
            timing_forecast["rest_sweep_risk"] = base_risk

    forecast_headline = _safe_public_text(
        timing_forecast.get("headline"),
        "Direction unresolved \u00b7 reassess after the next completed close",
        limit=180,
    )
    forecast_summary = _safe_public_text(
        timing_forecast.get("summary"),
        "No current timing forecast is available.",
        limit=720,
    )
    entry["headline"] = forecast_headline
    entry["answer"] = forecast_summary
    entry["reason"] = _safe_public_text(
        timing_forecast.get("rest_sweep_risk"),
        _safe_public_text(entry.get("reason"), "", limit=480),
        limit=480,
    )
    entry["next_trigger"] = _safe_public_text(
        timing_forecast.get("invalidation"),
        _safe_public_text(entry.get("next_trigger"), "", limit=320),
        limit=320,
    )
    entry["timing_forecast"] = timing_forecast
    active_target_next_impulse = (
        timing_forecast.get("active_target_next_impulse") is True
        or _safe_public_text(
            timing_forecast.get("event_definition"), "", limit=96
        ).upper()
        == _NEXT_IMPULSE_AFTER_ACTIVE_TARGET_EVENT
    )
    if (
        active_target_next_impulse
        and not enter_now
        and selected_side in _DIRECTIONAL_SIDES
    ):
        best_action = f"WAIT_FOR_{selected_side}_PULLBACK"
        decision_state = "WAITING_FOR_NEXT_IMPULSE_REST"
        stream_instruction = (
            f"WAIT FOR PULLBACK — the current {selected_side} move is already "
            "mature and active. Do not chase it; wait for one completed rest or "
            f"pullback before reassessing the next {selected_side} impulse."
        )
    if identity_rebind_pending:
        stream_instruction = (
            "Avoid entry while the chart identity is rebinding. The displayed "
            "frame and forecast remain atomic until a coherent new frame arrives."
        )
    operator_action = _operator_action_contract_v3(
        enter_now=enter_now,
        timing_state=(
            "INVALIDATED"
            if identity_rebind_pending
            else "FORMING"
            if current_study_without_issued_window
            else timing_state
        ),
        timing_effect=timing_effect,
        studied_side=selected_side,
        behavior_state=stream_state,
        behavior_side=closed_move_side,
        instruction=stream_instruction,
        active_target_next_impulse=active_target_next_impulse,
    )
    if timing_state == "DURATION_INELIGIBLE":
        operator_action.update(
            {
                "state": "AVOID",
                "label": "AVOID",
                "instruction": _safe_public_text(
                    broker_expiry.get("instruction"),
                    stream_instruction,
                    limit=320,
                ),
            }
        )
    elif (
        stream_fresh
        and stream_state == "ANALYZING"
        and not identity_rebind_pending
        and timing_state not in {"INVALIDATED", "CONFLICT"}
    ):
        operator_action.update(
            {
                "state": (
                    "TRACKING_LATEST_COMPLETED"
                    if completed_study_current
                    and selected_side in _DIRECTIONAL_SIDES
                    else "ANALYZING"
                ),
                "label": (
                    f"TRACK {selected_side}"
                    if completed_study_current
                    and selected_side in _DIRECTIONAL_SIDES
                    else "ANALYZING"
                ),
                "instruction": stream_instruction,
            }
        )
    elif (
        active_target_next_impulse
        and not enter_now
        and not identity_rebind_pending
    ):
        operator_action.update(
            {
                "state": "WAIT_FOR_PULLBACK",
                "label": "WAIT FOR PULLBACK",
                "instruction": stream_instruction,
            }
        )
    elif (
        not broker_expiry_proven
        and selected_side in _DIRECTIONAL_SIDES
        and not identity_rebind_pending
        and timing_state not in {"INVALIDATED", "CONFLICT", "STALE", "MISSED"}
    ):
        operator_action.update(
            {
                "state": "PREPARE",
                "label": "PREPARE",
                "instruction": _safe_public_text(
                    broker_expiry.get("instruction"),
                    stream_instruction,
                    limit=320,
                ),
            }
        )
    # Q3 is an action contract, not a forecast headline.  Preserve the full
    # direction/time study as a separate, explicitly non-authoritative object
    # so every consumer (not only the browser) leads with what may be done now.
    # This prevents an active/next-impulse BUY or SELL study from reading like
    # immediate entry permission when the actual action is to wait or stay out.
    projection_side = _side(timing_forecast.get("side"))
    projection_support_count = max(
        0,
        _integer(
            timing_forecast.get(
                "event_likelihood_support_count",
                timing_forecast.get("support_count", 0),
            )
        ),
    )
    projection_timing_support_count = max(
        0,
        _integer(timing_forecast.get("timing_support_count")),
    )
    projection_calibration_grade = _safe_public_text(
        timing_forecast.get("calibration_grade"), "UNRATED", limit=48
    ).upper()
    projection_calibrated = timing_forecast.get("calibrated") is True
    projection_basis = _safe_public_text(
        timing_forecast.get("source"), "UNAVAILABLE", limit=64
    ).upper()
    projection_horizon_low = _number(
        timing_forecast.get("horizon_seconds_low")
    )
    projection_horizon_high = _number(
        timing_forecast.get("horizon_seconds_high")
    )
    calibrated_projection_publishable = bool(
        projection_calibrated
        and projection_support_count > 0
        and projection_calibration_grade not in {"", "UNKNOWN", "UNRATED"}
    )
    live_sequence_projection_publishable = bool(
        not projection_calibrated
        and _safe_public_text(
            timing_forecast.get("status"), "", limit=48
        ).upper()
        == "FORECAST_AVAILABLE"
        and projection_basis == "LIVE_M5_SEQUENCE"
        and timing_forecast.get("forecast_lineage_matches") is True
        and projection_side in _DIRECTIONAL_SIDES
        and projection_horizon_low is not None
        and projection_horizon_low
        >= MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS
        and projection_horizon_high is not None
        and projection_horizon_high >= projection_horizon_low
    )
    empirical_pair_projection_publishable = bool(
        not projection_calibrated
        and _safe_public_text(
            timing_forecast.get("status"), "", limit=48
        ).upper()
        == "FORECAST_AVAILABLE"
        and (
            projection_basis == "PAIR"
            or projection_basis.startswith("PAIR_")
        )
        and timing_forecast.get("timing_empirical") is True
        and projection_timing_support_count > 0
        and timing_forecast.get("forecast_lineage_matches") is True
        and projection_side in _DIRECTIONAL_SIDES
        and projection_horizon_low is not None
        and projection_horizon_low
        >= MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS
        and projection_horizon_high is not None
        and projection_horizon_high >= projection_horizon_low
    )
    uncalibrated_projection_publishable = bool(
        live_sequence_projection_publishable
        or empirical_pair_projection_publishable
    )
    projection_publishable = bool(
        calibrated_projection_publishable
        or uncalibrated_projection_publishable
    )
    if calibrated_projection_publishable:
        projection_headline = forecast_headline
        projection_summary = forecast_summary
        projection_horizon = _safe_public_text(
            timing_forecast.get("horizon_label"),
            "Calibrated timing range",
            limit=180,
        )
    elif uncalibrated_projection_publishable:
        projection_kind = (
            "empirical pair"
            if empirical_pair_projection_publishable
            else "live-sequence"
        )
        projection_headline = _safe_public_text(
            f"{forecast_headline} · uncalibrated {projection_kind} estimate",
            f"Uncalibrated {projection_kind} timing estimate",
            limit=240,
        )
        projection_summary = _safe_public_text(
            (
                "This is an uncalibrated empirical pair estimate from current "
                "lineage-matched closed-candle history. Its bounded timing range "
                "is visible, but it is not an event probability and cannot grant "
                f"entry permission. {forecast_summary}"
                if empirical_pair_projection_publishable
                else "This is an uncalibrated live-sequence estimate from the current "
                "lineage-matched closed-candle sequence. Its bounded timing range "
                "is visible, but it supplies no event probability and cannot grant "
                f"entry permission. {forecast_summary}"
            ),
            f"Uncalibrated {projection_kind} estimate; no probability or entry permission.",
            limit=960,
        )
        projection_horizon = _safe_public_text(
            timing_forecast.get("horizon_label"),
            f"Uncalibrated {projection_kind} timing range",
            limit=180,
        )
    else:
        projection_headline = (
            f"{projection_side} direction studied · timing range withheld"
            if projection_side in _DIRECTIONAL_SIDES
            else "Direction study active · timing range withheld"
        )
        projection_summary = (
            "The directional study remains visible, but its candle range is "
            "not replay-calibrated for this pair and event. It is not an entry signal."
        )
        projection_horizon = "Not published until replay calibration passes"
    study_projection = {
        "schema_version": "PG_OPERATOR_STUDY_PROJECTION_V3",
        "headline": projection_headline,
        "summary": projection_summary,
        "side": projection_side,
        "status": _safe_public_text(
            timing_forecast.get("status"), "RESEARCH_ONLY", limit=48
        ).upper()
        if calibrated_projection_publishable
        else "FORECAST_AVAILABLE_UNCALIBRATED"
        if uncalibrated_projection_publishable
        else "RESEARCH_ONLY_UNCALIBRATED",
        "horizon_label": projection_horizon,
        "support_count": (
            projection_timing_support_count
            if empirical_pair_projection_publishable
            else projection_support_count
        ),
        "calibrated": projection_calibrated,
        "basis": projection_basis,
        "calibration_grade": projection_calibration_grade,
        "timing_range_publishable": projection_publishable,
        "study_only": True,
        "can_grant_entry_permission": False,
    }
    action_state = _safe_public_text(
        operator_action.get("state"), "AVOID", limit=48
    ).upper()
    if enter_now and permission_side in _DIRECTIONAL_SIDES:
        action_headline = f"ENTER — {permission_side} NOW"
    elif action_state == "WAIT_FOR_PULLBACK":
        action_headline = "WAIT FOR PULLBACK"
    elif action_state == "PREPARE":
        action_headline = "PREPARE"
    elif action_state == "ANALYZING":
        action_headline = "ANALYZING CURRENT FRAME"
    elif (
        action_state == "TRACKING_LATEST_COMPLETED"
        and selected_side in _DIRECTIONAL_SIDES
    ):
        action_headline = f"TRACK {selected_side} — next frame is being analyzed"
    else:
        action_headline = "STAY OUT"
        operator_action["label"] = "STAY OUT"
    entry["study_projection"] = study_projection
    entry["headline"] = action_headline
    entry["answer"] = _safe_public_text(
        operator_action.get("instruction"),
        "Stay out until a current verified entry window opens.",
        limit=960,
    )
    entry["operator_action"] = operator_action
    entry["identity_rebind_pending"] = identity_rebind_pending

    entry["enter_now"] = enter_now
    entry["action"] = f"{permission_side}_NOW" if enter_now else "DO_NOT_ENTER"
    entry["decision"] = best_action
    entry["decision_state"] = decision_state
    entry["timing_state"] = "ENTER_NOW" if enter_now else timing_state
    # ``timing_state`` describes the last completed-candle entry/timing
    # contract.  During a fresh external-frame study that contract may still
    # correctly be STALE while the operator's *current* state is ANALYZING.
    # Publishing STALE as the card state made a healthy, advancing Edge stream
    # look dead even though the action contract and headline were already
    # processing the newest frame.  Keep both truths separate: retain the
    # closed-candle timing state for audit consumers, and expose the live action
    # state as the primary Q3 state.
    entry["state"] = (
        "ENTER_NOW"
        if enter_now
        else "ANALYZING"
        if action_state == "ANALYZING"
        else "TRACKING_LATEST_COMPLETED"
        if action_state == "TRACKING_LATEST_COMPLETED"
        else "OBSERVING"
        if current_study_without_issued_window
        else entry["timing_state"]
    )
    entry["side"] = permission_side if enter_now else selected_side
    if stream_fresh and heartbeat_epoch is not None:
        entry["updated_at"] = heartbeat_epoch
    entry["permission_allowed"] = permission_allowed
    entry["entry_permission_authorized"] = entry_permission_authorized
    entry["timing_supports_entry"] = timing_supports_entry
    entry["timing_veto"] = timing_veto
    entry["broker_expiry_v3"] = broker_expiry
    entry["broker_expiry_proven"] = broker_expiry_proven
    entry["broker_expiry_eligible"] = broker_expiry_eligible
    entry_evidence["permission_allowed"] = permission_allowed
    entry_evidence["best_action"] = best_action
    entry_evidence["entry_permission_authorized"] = entry_permission_authorized
    entry_evidence["timing_supports_entry"] = timing_supports_entry
    entry_evidence["timing_veto"] = timing_veto
    entry_evidence["permission_contract_authorized"] = (
        permission_contract_authorized
    )
    entry_evidence["broker_expiry_v3"] = broker_expiry
    entry_evidence["broker_expiry_proven"] = broker_expiry_proven
    entry_evidence["broker_expiry_eligible"] = broker_expiry_eligible
    entry_evidence["prior_studied_side"] = prior_studied_side
    entry_evidence["current_regression_side"] = current_regression_side
    entry_evidence["current_actionable_study_side"] = selected_side
    entry_evidence["prior_thesis_superseded"] = prior_thesis_superseded
    entry_evidence["execution_authority"] = False
    entry_evidence["broker_click_authority"] = False
    entry["evidence"] = entry_evidence
    result["entry_now"] = entry
    return result


def _capture_source_transport_read_v3(
    capture_source: Mapping[str, object],
) -> dict[str, object]:
    """Project transport liveness separately from decision freshness."""

    state = _text(capture_source.get("state"), "NO_SOURCE", limit=32).upper()
    reason_code = _text(
        capture_source.get("reason_code"),
        state,
        limit=48,
    ).upper()
    fresh = _explicit_bool(capture_source.get("fresh")) is True
    processing = bool(
        fresh
        and state == "VALIDATING"
        and reason_code in {"FRAME_PENDING", "FRAME_PROCESSING"}
    )
    active = bool(fresh and (state == "LIVE" or processing))
    source_stream = _mapping(capture_source.get("stream"))
    return {
        "active": active,
        "processing": processing,
        "state": state,
        "fresh": fresh,
        "reason_code": reason_code,
        "transport_heartbeat_count": max(
            0,
            _integer(source_stream.get("transport_heartbeat_count")),
        ),
        "last_transport_heartbeat_epoch": _epoch(
            source_stream.get("last_transport_heartbeat_epoch")
        ),
    }


def _heartbeat_revocation_identity_v3(
    capture_source: Mapping[str, object],
    *,
    now_epoch: float,
) -> dict[str, object]:
    """Read a backend lease-fenced identity observation as revocation-only.

    This deliberately reads only the backend-stamped schema. It cannot become
    ``current_chart_identity_v3`` or completed-study/overlay authority.
    """

    source_stream = _mapping(capture_source.get("stream"))
    row = _mapping(source_stream.get("revocation_identity_observation_v3"))
    symbol = _safe_public_text(row.get("symbol"), "", limit=64).upper()
    timeframe = _safe_public_text(row.get("timeframe"), "", limit=32).upper()
    observed_epoch = _epoch(row.get("observed_epoch"))
    received_epoch = _epoch(row.get("received_epoch"))
    source_sequence = _safe_public_text(
        capture_source.get("sequence_id"), "", limit=192
    )
    row_sequence = _safe_public_text(row.get("sequence_id"), "", limit=192)
    source_generation = _integer(capture_source.get("source_generation"))
    row_generation = _integer(row.get("source_generation"))
    stale_after = _number(capture_source.get("stale_after_sec")) or 20.0
    maximum_age = max(5.0, min(60.0, stale_after))
    if (
        _explicit_bool(capture_source.get("fresh")) is not True
        or _safe_public_text(row.get("schema_version"), "", limit=64)
        != "PG_REVOCATION_IDENTITY_OBSERVATION_V3"
        or row.get("revocation_only") is not True
        or row.get("lease_fenced") is not True
        or row.get("study_authority") is not False
        or row.get("overlay_authority") is not False
        or row.get("decision_authority") is not False
        or not re.fullmatch(r"[A-Z]{3}/[A-Z]{3}(?: OTC)?", symbol)
        or not re.fullmatch(r"(?:M1|M3|M5|M15|M30|H1|H4|D1)", timeframe)
        or not source_sequence
        or row_sequence != source_sequence
        or source_generation <= 0
        or row_generation != source_generation
        or observed_epoch is None
        or received_epoch is None
        or received_epoch < observed_epoch - 2.0
        or now_epoch - received_epoch > maximum_age
    ):
        return {}
    return {
        "schema_version": "PG_REVOCATION_IDENTITY_OBSERVATION_V3",
        "symbol": symbol,
        "timeframe": timeframe,
        "observed_epoch": observed_epoch,
        "received_epoch": received_epoch,
        "sequence_id": row_sequence,
        "source_generation": row_generation,
        "revocation_only": True,
        "lease_fenced": True,
        "study_authority": False,
        "overlay_authority": False,
        "decision_authority": False,
    }


def _heartbeat_identity_mismatches_published_namespace_v3(
    observation: Mapping[str, object],
    market_study: Mapping[str, object],
    *,
    published_market: Mapping[str, object] | None = None,
    overlay_rows: object = None,
) -> bool:
    """Fail closed when a heartbeat differs from any published namespace.

    A completed study is the strongest comparison target, but overlays can be
    published before that study row reaches the compact operator payload.  In
    that partial-publish interval an old pair's identity-locked geometry must
    still be revoked immediately.  These fallback namespaces are used only to
    invalidate old output; the heartbeat never gains positive study, overlay,
    direction, or permission authority.
    """

    if not observation:
        return False
    namespaces: list[tuple[str, str]] = []

    def remember_namespace(symbol_value: object, timeframe_value: object) -> None:
        symbol = _safe_public_text(symbol_value, "", limit=64).upper()
        timeframe = _safe_public_text(timeframe_value, "", limit=32).upper()
        if (
            re.fullmatch(r"[A-Z]{3}/[A-Z]{3}(?: OTC)?", symbol)
            and re.fullmatch(r"(?:M1|M3|M5|M15|M30|H1|H4|D1)", timeframe)
            and (symbol, timeframe) not in namespaces
        ):
            namespaces.append((symbol, timeframe))

    if (
        _safe_public_text(market_study.get("status"), "", limit=32).upper()
        == "STUDIED"
        and _safe_identifier(market_study.get("closed_candle_key"), "")
    ):
        remember_namespace(
            market_study.get("symbol"),
            market_study.get("timeframe"),
        )
    market = _mapping(published_market)
    remember_namespace(market.get("symbol"), market.get("timeframe"))
    for overlay in _rows(overlay_rows):
        if (
            _safe_public_text(
                overlay.get("instrument_identity_status"),
                "",
                limit=32,
            ).upper()
            == "LOCKED"
        ):
            remember_namespace(overlay.get("symbol"), overlay.get("timeframe"))

    observed_symbol = _instrument_token(observation.get("symbol"))
    observed_timeframe = _safe_public_text(
        observation.get("timeframe"), "", limit=32
    ).upper()
    return any(
        _instrument_token(symbol) != observed_symbol
        or timeframe != observed_timeframe
        for symbol, timeframe in namespaces
    )


def _identity_rebind_permission_v3(
    permission: Mapping[str, object],
    observation: Mapping[str, object],
) -> dict[str, object]:
    symbol = _safe_public_text(observation.get("symbol"), "current chart", limit=64)
    timeframe = _safe_public_text(observation.get("timeframe"), "", limit=32)
    row = dict(permission)
    row.update(
        {
            "action": "WAIT",
            "allowed": False,
            "side": "NEUTRAL",
            "message": (
                f"Classifying {symbol} {timeframe}. The prior pair's decision was "
                "revoked immediately and cannot authorize entry."
            ).strip(),
            "next_condition": (
                "Wait for this pair and timeframe to own a new completed-candle "
                "study and current overlay namespace."
            ),
            "expires_at": None,
            "window_open": False,
            "valid_for_seconds": 0.0,
            "window_label": "Closed",
        }
    )
    return row


def _identity_rebind_questions_v3(
    questions: Mapping[str, object],
    observation: Mapping[str, object],
) -> dict[str, object]:
    result = dict(questions)
    symbol = _safe_public_text(observation.get("symbol"), "Unknown", limit=64)
    timeframe = _safe_public_text(observation.get("timeframe"), "Unknown", limit=32)
    updated_at = _epoch(
        observation.get("received_epoch"), observation.get("observed_epoch")
    )
    evidence = {
        "identity_rebind_pending": True,
        "observed_symbol": symbol,
        "observed_timeframe": timeframe,
        "lease_fenced": True,
        "revocation_only": True,
        "study_authority": False,
        "overlay_authority": False,
        "decision_authority": False,
    }
    history = dict(_mapping(result.get("market_origin_history")))
    history.update(
        {
            "question": "Where is the market from, and how did history behave?",
            "headline": f"Classifying {symbol} · {timeframe}",
            "answer": (
                "The live chart identity changed. History from the previous pair "
                "was cleared and will return only after a completed study belongs "
                "to this exact pair and timeframe."
            ),
            "state": "IDENTITY_REBIND_PENDING",
            "side": "NEUTRAL",
            "confidence": 0.0,
            "evidence": evidence,
            "updated_at": updated_at,
        }
    )
    directional = dict(_mapping(result.get("studied_direction_current")))
    directional.update(
        {
            "question": "Which direction was studied, and what is being studied now?",
            "headline": f"{symbol} · {timeframe} is being classified",
            "answer": (
                "No prior BUY or SELL study is current for this chart. The heartbeat "
                "identity can revoke old evidence, but it cannot create direction."
            ),
            "state": "IDENTITY_REBIND_PENDING",
            "side": "NEUTRAL",
            "confidence": 0.0,
            "evidence": evidence,
            "updated_at": updated_at,
        }
    )
    entry = dict(_mapping(result.get("entry_now")))
    entry.update(
        {
            "question": "What is the best decision to do right now?",
            "headline": "CLASSIFYING CURRENT CHART",
            "answer": (
                "Stay out while this pair and timeframe receive their own completed "
                "study. The previous decision and entry window are revoked."
            ),
            "state": "INVALIDATED",
            "side": "NEUTRAL",
            "confidence": 0.0,
            "evidence": evidence,
            "updated_at": updated_at,
            "enter_now": False,
            "action": "DO_NOT_ENTER",
            "reason": "The chart identity changed before the new completed study published.",
            "next_trigger": "Wait for a new identity-matched completed-candle study.",
            "timing_state": "INVALIDATED",
            "permission_allowed": False,
            "entry_permission_authorized": False,
            "timing_supports_entry": False,
            "timing_veto": False,
            "timing_forecast": {
                "status": "CLASSIFYING",
                "study_only": True,
                "execution_authority": False,
                "can_grant_entry_permission": False,
            },
            "broker_expiry_v3": {},
            "operator_action": {
                "schema_version": "PG_OPERATOR_ACTION_V3",
                "state": "AVOID",
                "label": "STAY OUT",
                "instruction": "Wait for the new pair's completed study.",
                "enter_now": False,
                "entry_permission_authorized": False,
                "execution_authority": False,
                "broker_click_authority": False,
            },
            "identity_rebind_pending": True,
        }
    )
    result.update(
        {
            "schema_version": "PG_THREE_QUESTION_OPERATOR_BRIEF_V3",
            "market_origin_history": history,
            "studied_direction_current": directional,
            "entry_now": entry,
        }
    )
    return result


def _apply_heartbeat_identity_veto_v3(
    workspace: Mapping[str, object],
    observation: Mapping[str, object],
) -> dict[str, object]:
    result = dict(workspace)
    if not observation:
        return result
    symbol = _safe_public_text(observation.get("symbol"), "Unknown", limit=64)
    timeframe = _safe_public_text(observation.get("timeframe"), "Unknown", limit=32)
    result["market"] = {
        "symbol": symbol,
        "timeframe": timeframe,
        "identity_status": "Classifying current chart",
        "identity_pending": True,
        "identity_authority": False,
    }
    result["overlays"] = []
    result["history"] = []
    surface_frame = _frame_id(_mapping(result.get("surface")).get("frame_id"))
    result["current_move"] = _sanitize_event({}, surface_frame, pressure=False)
    result["pressure_event"] = _sanitize_event({}, surface_frame, pressure=True)
    result["permission"] = _identity_rebind_permission_v3(
        _mapping(result.get("permission")), observation
    )
    tracking = dict(_mapping(result.get("tracking")))
    tracking["history_count"] = 0
    tracking["market_study_v3"] = {}
    tracking["identity_rebind_pending"] = True
    result["tracking"] = tracking
    surface = dict(_mapping(result.get("surface")))
    rebind_identity = _stable_public_digest(
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "sequence_id": observation.get("sequence_id"),
            "observed_epoch": observation.get("observed_epoch"),
        },
        prefix="classifying",
    )
    surface["semantic_identity"] = rebind_identity
    surface["overlay_semantic_revision"] = rebind_identity
    result["surface"] = surface
    result["three_questions"] = _identity_rebind_questions_v3(
        _mapping(result.get("three_questions")), observation
    )
    return result


def _atomic_runtime_completed_study_v3(
    runtime_payload: Mapping[str, object],
) -> dict[str, object]:
    """Return one internally coherent completed runtime study, or nothing.

    The display state can advance while the expensive operator projection is
    still rebuilding.  This check intentionally mirrors the atomic display
    barrier used by the API cache: every public artifact must own the same
    frame and the overlay source signatures must still match that frame.
    """

    if runtime_payload.get("frame_bundle_complete_v3") is not True:
        return {}
    fast_path = _mapping(runtime_payload.get("display_fast_path_v3"))
    if any(
        (
            runtime_payload.get("display_snapshot_only_v3") is True,
            runtime_payload.get("display_busy_reuse_heartbeat_v3") is True,
            runtime_payload.get("display_reuse_only_heartbeat_v3") is True,
            fast_path.get("reuse_only_heartbeat") is True,
        )
    ):
        return {}
    frame_id = _integer(runtime_payload.get("display_frame_id"))
    if frame_id <= 0 or any(
        _integer(runtime_payload.get(key)) != frame_id
        for key in (
            "chart_frame_id",
            "overlay_frame_id",
            "full_overlay_frame_id",
            "model_vote_frame_id",
        )
    ):
        return {}
    display_signature = _text(
        runtime_payload.get("last_display_surface_signature")
        or runtime_payload.get("last_window_surface_signature"),
        "",
        limit=256,
    )
    study_signature = _text(
        runtime_payload.get("last_study_surface_signature"),
        "",
        limit=256,
    )
    overlay_window_signature = _text(
        runtime_payload.get("overlay_source_window_signature"),
        "",
        limit=256,
    )
    overlay_study_signature = _text(
        runtime_payload.get("overlay_source_study_signature"),
        "",
        limit=256,
    )
    signatures_published = any(
        (
            display_signature,
            study_signature,
            overlay_window_signature,
            overlay_study_signature,
        )
    )
    if signatures_published and (
        not display_signature
        or not study_signature
        or not overlay_window_signature
        or not overlay_study_signature
        or overlay_window_signature != display_signature
        or overlay_study_signature != study_signature
    ):
        return {}

    tracking_summary = _mapping(runtime_payload.get("tracking_summary"))
    latest_signal = _mapping(runtime_payload.get("latest_signal"))
    runtime_study = (
        _mapping(tracking_summary.get("market_study_v3"))
        or _mapping(latest_signal.get("market_study_v3"))
    )
    study_key = _safe_identifier(runtime_study.get("closed_candle_key"), "")
    study_sequence = _integer(runtime_study.get("closed_candle_sequence"))
    study_symbol = _safe_public_text(
        runtime_study.get("symbol"), "", limit=64
    )
    study_timeframe = _safe_public_text(
        runtime_study.get("timeframe"), "", limit=32
    ).upper()
    runtime_symbol = _safe_public_text(
        tracking_summary.get("detected_market")
        or latest_signal.get("symbol")
        or latest_signal.get("pair"),
        "",
        limit=64,
    )
    runtime_timeframe = _safe_public_text(
        tracking_summary.get("detected_timeframe")
        or latest_signal.get("timeframe"),
        "",
        limit=32,
    ).upper()
    directional_side = _side(
        _mapping(runtime_study.get("directional_read")).get("side")
    )
    broker_source_lock = _mapping(tracking_summary.get("broker_source_lock"))
    identity_confirmed = bool(
        tracking_summary.get("market_identity_confirmed") is True
        and tracking_summary.get("timeframe_identity_confirmed") is True
    )
    source_locked = bool(
        broker_source_lock.get("valid") is True
        and broker_source_lock.get("broker_source_locked") is True
        and _text(
            broker_source_lock.get("status"), "", limit=32
        ).upper()
        == "VALID"
    )
    if (
        _text(runtime_study.get("status"), "", limit=32).upper()
        != "STUDIED"
        or runtime_study.get("study_only") is not True
        or runtime_study.get("execution_authority") is not False
        or not study_key
        or study_sequence <= 0
        or directional_side not in _DIRECTIONAL_SIDES
        or not identity_confirmed
        or not source_locked
        or not runtime_symbol
        or not runtime_timeframe
        or _instrument_token(study_symbol) != _instrument_token(runtime_symbol)
        or study_timeframe != runtime_timeframe
    ):
        return {}
    return {
        "frame_id": frame_id,
        "symbol": runtime_symbol,
        "timeframe": runtime_timeframe,
        "closed_candle_key": study_key,
        "closed_candle_sequence": study_sequence,
        "side": directional_side,
        "study": runtime_study,
    }


def _adopt_newer_completed_runtime_study_v3(
    workspace: Mapping[str, object],
    runtime_payload: Mapping[str, object],
    bundle: Mapping[str, object],
    *,
    now_epoch: float,
) -> dict[str, object]:
    """Replace stale operator study fields from one exact same-identity bundle.

    This is a read-through for the lightweight operator cache, not a decision
    engine.  It clears old-frame overlays and always closes entry permission.
    """

    result = dict(workspace)
    runtime_study = _mapping(bundle.get("study"))
    frame_id = _integer(bundle.get("frame_id"))
    market = {
        "symbol": _safe_public_text(bundle.get("symbol"), "Unknown", limit=64),
        "timeframe": _safe_public_text(
            bundle.get("timeframe"), "Unknown", limit=32
        ).upper(),
    }
    tracking_summary = _mapping(runtime_payload.get("tracking_summary"))
    command = _mapping(runtime_payload.get("decision_command_center"))
    canonical_current, canonical_pressure = _canonical_candle_movement_fallback(
        runtime_payload,
        frame_id,
    )
    explicit_current = _mapping(command.get("current_movement")) or _mapping(
        runtime_payload.get("current_movement")
    )
    current_event = _reconcile_current_event(
        explicit_current,
        canonical_current,
        frame_id,
    )
    explicit_pressure = _mapping(command.get("pressure_event")) or _mapping(
        runtime_payload.get("pressure_event")
    )
    pressure_source = _reconcile_pressure_event(
        explicit_pressure,
        canonical_pressure,
        current_event,
        frame_id,
    )
    current_move = _sanitize_event(current_event, frame_id, pressure=False)
    pressure_event = _sanitize_event(pressure_source, frame_id, pressure=True)
    public_study = _market_study_contract(runtime_study)
    history = _history_contract(
        runtime_payload,
        current_symbol=str(market["symbol"]),
        current_timeframe=str(market["timeframe"]),
    )
    observed_at = _epoch(
        runtime_study.get("published_epoch"),
        runtime_study.get("observed_epoch"),
        tracking_summary.get("last_capture_epoch"),
        runtime_payload.get("display_published_epoch"),
        runtime_payload.get("last_capture_epoch"),
    )
    freshness = {
        "state": "UPDATING",
        "label": "Latest completed study shown while the next frame is analyzed",
        "observed_at": observed_at,
        "valid_until": None,
        "age_seconds": (
            round(max(0.0, now_epoch - observed_at), 3)
            if observed_at is not None
            else None
        ),
    }
    permission = dict(_mapping(result.get("permission")))
    permission.update(
        {
            "action": "WAIT",
            "allowed": False,
            "side": "NEUTRAL",
            "message": (
                "The latest completed study is current, but the next frame is still "
                "being analyzed and no entry permission is open."
            ),
            "next_condition": (
                "Wait for an independently verified entry window on a completed candle."
            ),
            "window_open": False,
            "valid_for_seconds": 0.0,
            "window_label": "Closed",
        }
    )

    # Deliberately exclude command/council fields here.  They may belong to an
    # older completed candle; the exact runtime study's directional_read and
    # lineage-bound timing field own this replacement.
    study_question_payload: dict[str, object] = {
        "tracking_summary": {"market_study_v3": runtime_study},
        "latest_signal": {},
    }
    questions = _three_question_brief_v3(
        study_question_payload,
        command={},
        market=market,
        market_study=public_study,
        history=history,
        freshness=freshness,
        current_move=current_move,
        pressure_event=pressure_event,
        permission=permission,
        now_epoch=now_epoch,
    )

    session_id = _safe_session_id(runtime_payload.get("session_id")) or _safe_session_id(
        result.get("session_id")
    )
    encoded_session_id = quote(session_id, safe="")
    surface_base = (
        f"/v1/mobile/window-tracker/sessions/{encoded_session_id}/artifacts"
        if encoded_session_id
        else ""
    )
    surface_query = f"?frame_id={frame_id}" if surface_base and frame_id > 0 else ""
    overlay_viewports = _overlay_viewports_contract(
        runtime_payload,
        tracking_summary,
    )
    surface_revisions = _surface_overlay_revision_contract(
        runtime_payload,
        tracking_summary,
        market,
        overlay_viewports,
        [],
    )
    surface = dict(_mapping(result.get("surface")))
    surface.update(
        {
            "primary_url": (
                f"{surface_base}/latest-window{surface_query}" if surface_query else ""
            ),
            "primary_space": "window",
            "fallback_url": (
                f"{surface_base}/latest-chart{surface_query}" if surface_query else ""
            ),
            "fallback_space": "chart",
            "focus_url": (
                f"{surface_base}/latest-chart{surface_query}" if surface_query else ""
            ),
            "overlay_viewport": overlay_viewports["window"],
            "overlay_viewports": overlay_viewports,
            "frame_id": frame_id,
            "updated_at": observed_at,
            "market_selector_visual_fingerprint": _safe_public_text(
                runtime_payload.get("market_selector_visual_fingerprint")
                or _mapping(runtime_payload.get("latest_signal")).get(
                    "market_selector_visual_fingerprint"
                )
                or tracking_summary.get("market_selector_visual_fingerprint"),
                "",
                limit=80,
            ),
            "overlay_state_version": _safe_public_text(
                runtime_payload.get("overlay_state_version"), "", limit=160
            ),
            "overlay_frame_state_version": _safe_public_text(
                runtime_payload.get("overlay_frame_state_version"), "", limit=160
            ),
            **surface_revisions,
        }
    )
    tracking = dict(_mapping(result.get("tracking")))
    tracking.update(
        {
            "active": True,
            "state": "UPDATING",
            "updated_at": observed_at,
            "history_count": len(history),
            "market_study_v3": public_study,
        }
    )
    result.update(
        {
            "revision": max(
                _integer(result.get("revision")),
                _integer(runtime_payload.get("state_version")),
                _integer(runtime_payload.get("decision_version")),
                _integer(runtime_payload.get("sequence_id")),
                frame_id,
                _integer(runtime_payload.get("capture_count")),
            ),
            "market": market,
            "three_questions": questions,
            "tracking": tracking,
            "freshness": freshness,
            "current_move": current_move,
            "permission": permission,
            "pressure_event": pressure_event,
            "surface": surface,
            # Never retain geometry from the older display frame.  The normal
            # projection refresh will republish exact-frame overlay rows.
            "overlays": [],
            "history": history,
        }
    )
    return result


def refresh_operator_streaming_read_v3(
    workspace: Mapping[str, object],
    runtime_payload: Mapping[str, object],
    *,
    now_epoch: float | None = None,
) -> dict[str, object]:
    """Attach the latest bounded stream heartbeat to a cached operator workspace."""

    current_epoch = float(now_epoch if now_epoch is not None else time.time())
    result = dict(workspace)
    cached_market = dict(_mapping(result.get("market")))
    tracking_summary = _mapping(runtime_payload.get("tracking_summary"))
    latest_signal = _mapping(runtime_payload.get("latest_signal"))
    visual_observation = _mapping(runtime_payload.get("visual_observation_v3"))
    capture_source = _mapping(runtime_payload.get("capture_source_v3"))
    capture_source_present = bool(capture_source)
    capture_transport = _capture_source_transport_read_v3(capture_source)
    capture_source_live = bool(capture_transport["active"])
    runtime_study = (
        _mapping(tracking_summary.get("market_study_v3"))
        or _mapping(latest_signal.get("market_study_v3"))
    )
    heartbeat_identity = _heartbeat_revocation_identity_v3(
        capture_source,
        now_epoch=current_epoch,
    )
    cached_study = _mapping(_mapping(result.get("tracking")).get("market_study_v3"))
    heartbeat_identity_mismatch = _heartbeat_identity_mismatches_published_namespace_v3(
        heartbeat_identity,
        runtime_study or cached_study,
        published_market=cached_market,
        overlay_rows=result.get("overlays"),
    )
    if heartbeat_identity_mismatch:
        result = _apply_heartbeat_identity_veto_v3(result, heartbeat_identity)
        cached_market = dict(_mapping(result.get("market")))
    live_frame_unchanged = bool(
        _text(visual_observation.get("status"), "", limit=48).upper()
        == "LIVE_FRAME_UNCHANGED"
        and _explicit_bool(visual_observation.get("transport_fresh")) is True
        and _explicit_bool(visual_observation.get("new_visual_evidence")) is not True
        and (capture_source_live or not capture_source_present)
    )
    runtime_symbol = _safe_public_text(
        tracking_summary.get("detected_market")
        or latest_signal.get("symbol")
        or latest_signal.get("pair"),
        "",
        limit=64,
    )
    runtime_timeframe = _safe_public_text(
        tracking_summary.get("detected_timeframe")
        or latest_signal.get("timeframe"),
        "",
        limit=32,
    ).upper()
    cached_symbol = _safe_public_text(
        cached_market.get("symbol"), "", limit=64
    )
    cached_timeframe = _safe_public_text(
        cached_market.get("timeframe"), "", limit=32
    ).upper()
    identity_change_detected = bool(
        runtime_symbol
        and runtime_timeframe
        and cached_symbol
        and cached_timeframe
        and (
            _instrument_token(runtime_symbol) != _instrument_token(cached_symbol)
            or runtime_timeframe != cached_timeframe
        )
    )
    cached_frame = _frame_id(_mapping(result.get("surface")).get("frame_id"))
    runtime_frame = _frame_id(
        runtime_payload.get("display_frame_id"),
        runtime_payload.get("chart_frame_id"),
        runtime_payload.get("frame_id"),
        tracking_summary.get("display_frame_id"),
        tracking_summary.get("frame_id"),
        latest_signal.get("frame_id"),
    )
    runtime_study_symbol = _safe_public_text(
        runtime_study.get("symbol"), "", limit=64
    )
    runtime_study_timeframe = _safe_public_text(
        runtime_study.get("timeframe"), "", limit=32
    ).upper()
    runtime_study_key = _safe_identifier(
        runtime_study.get("closed_candle_key"), ""
    )
    atomic_runtime_bundle = _atomic_runtime_completed_study_v3(runtime_payload)
    cached_study_key = _safe_identifier(
        cached_study.get("closed_candle_key"), ""
    )
    cached_study_sequence = _integer(
        cached_study.get("closed_candle_sequence")
    )
    atomic_study_sequence = _integer(
        atomic_runtime_bundle.get("closed_candle_sequence")
    )
    atomic_study_key = _safe_identifier(
        atomic_runtime_bundle.get("closed_candle_key"), ""
    )
    atomic_same_identity = bool(
        atomic_runtime_bundle
        and not heartbeat_identity_mismatch
        and cached_symbol
        and cached_timeframe
        and _instrument_token(atomic_runtime_bundle.get("symbol"))
        == _instrument_token(cached_symbol)
        and _safe_public_text(
            atomic_runtime_bundle.get("timeframe"), "", limit=32
        ).upper()
        == cached_timeframe
    )
    atomic_study_advanced = bool(
        atomic_same_identity
        and atomic_study_sequence > cached_study_sequence
        and atomic_study_key
        and atomic_study_key != cached_study_key
        and _integer(atomic_runtime_bundle.get("frame_id")) >= _integer(cached_frame)
    )
    if atomic_study_advanced:
        result = _adopt_newer_completed_runtime_study_v3(
            result,
            runtime_payload,
            atomic_runtime_bundle,
            now_epoch=current_epoch,
        )
        cached_market = dict(_mapping(result.get("market")))
        cached_symbol = _safe_public_text(
            cached_market.get("symbol"), "", limit=64
        )
        cached_timeframe = _safe_public_text(
            cached_market.get("timeframe"), "", limit=32
        ).upper()
        cached_frame = _frame_id(_mapping(result.get("surface")).get("frame_id"))
        cached_study = _mapping(
            _mapping(result.get("tracking")).get("market_study_v3")
        )
    coherent_new_identity = bool(
        identity_change_detected
        and runtime_frame is not None
        and runtime_frame != cached_frame
        and _text(runtime_study.get("status"), "", limit=32).upper()
        == "STUDIED"
        and runtime_study_key
        and _instrument_token(runtime_study_symbol)
        == _instrument_token(runtime_symbol)
        and runtime_study_timeframe == runtime_timeframe
    )
    identity_rebind_pending = bool(
        (identity_change_detected and not coherent_new_identity)
        or heartbeat_identity_mismatch
    )
    identity_matches = not heartbeat_identity_mismatch and not coherent_new_identity
    if identity_change_detected or heartbeat_identity_mismatch:
        # Compact OCR/sidecar identity can advance before the displayed frame.
        # Revoke permission immediately, but preserve the atomic market/forecast
        # bundle until a new display frame and completed study agree.
        current_permission = dict(_mapping(result.get("permission")))
        current_permission.update(
            {
                "allowed": False,
                "action": "WAIT",
                "side": "NEUTRAL",
                "message": (
                    "Chart identity is rebinding; a coherent new display frame and "
                    "completed-candle study are required."
                ),
            }
        )
        result["permission"] = current_permission
    if coherent_new_identity:
        result["market"] = {
            "symbol": runtime_symbol,
            "timeframe": runtime_timeframe,
        }
    tracking = dict(_mapping(result.get("tracking")))
    if coherent_new_identity:
        tracking["market_study_v3"] = _market_study_contract(runtime_study)
    elif heartbeat_identity_mismatch:
        tracking["market_study_v3"] = {}
        tracking["history_count"] = 0
        tracking["identity_rebind_pending"] = True
    stream = cpu_stream_tracking_contract_v3(
        runtime_payload,
        now_epoch=current_epoch,
    )
    tracking["stream"] = stream
    if capture_source_present:
        tracking["capture_source"] = capture_transport
    if capture_source_live:
        tracking["active"] = True
        if capture_transport["processing"] is True:
            tracking["state"] = "UPDATING"
    heartbeat_epoch = _epoch(stream.get("heartbeat_epoch"))
    if stream.get("fresh") is True and heartbeat_epoch is not None:
        tracking["updated_at"] = heartbeat_epoch
    if live_frame_unchanged:
        freshness = _freshness_contract(
            runtime_payload,
            {},
            _mapping(result.get("current_move")),
            _mapping(result.get("pressure_event")),
            now_epoch=current_epoch,
        )
        result["freshness"] = freshness
        tracking.update(
            {
                "active": True,
                "state": "UPDATING",
                "updated_at": visual_observation.get("attempted_epoch")
                or tracking.get("updated_at"),
            }
        )
        permission = dict(_mapping(result.get("permission")))
        unchanged_message = _safe_public_text(
            visual_observation.get("message"),
            "Chart stream live; the picture is unchanged. No new entry permission was created.",
            limit=180,
        )
        permission.update(
            {
                "action": "WAIT",
                "allowed": False,
                "side": "NEUTRAL",
                "message": unchanged_message,
                "next_condition": "Wait for a changed chart frame and a fresh completed study.",
                "window_open": False,
                "valid_for_seconds": 0.0,
                "window_label": "Closed",
            }
        )
        result["permission"] = permission
        overlay_rows = result.get("overlays")
        if isinstance(overlay_rows, list):
            current_rows: list[object] = []
            for item in cast(list[object], overlay_rows):
                if not isinstance(item, Mapping):
                    current_rows.append(item)
                    continue
                row = dict(cast(Mapping[str, object], item))
                if str(row.get("lifecycle") or "").lower() == "stale_diagnostic":
                    row["lifecycle"] = "current"
                current_rows.append(row)
            result["overlays"] = current_rows
    result["tracking"] = tracking
    completed_study_current = bool(
        atomic_runtime_bundle
        and not heartbeat_identity_mismatch
        and _integer(_mapping(result.get("surface")).get("frame_id"))
        == _integer(atomic_runtime_bundle.get("frame_id"))
        and _instrument_token(_mapping(result.get("market")).get("symbol"))
        == _instrument_token(atomic_runtime_bundle.get("symbol"))
        and _safe_public_text(
            _mapping(result.get("market")).get("timeframe"), "", limit=32
        ).upper()
        == _safe_public_text(
            atomic_runtime_bundle.get("timeframe"), "", limit=32
        ).upper()
        and _integer(
            _mapping(_mapping(result.get("tracking")).get("market_study_v3")).get(
                "closed_candle_sequence"
            )
        )
        == atomic_study_sequence
        and _safe_identifier(
            _mapping(_mapping(result.get("tracking")).get("market_study_v3")).get(
                "closed_candle_key"
            ),
            "",
        )
        == atomic_study_key
    )
    current_questions = _mapping(result.get("three_questions"))
    current_q2 = _mapping(current_questions.get("studied_direction_current"))
    current_q3 = _mapping(current_questions.get("entry_now"))
    current_q3_forecast = _mapping(current_q3.get("timing_forecast"))
    current_q3_projection = _mapping(current_q3.get("study_projection"))
    atomic_directional_side = _side(atomic_runtime_bundle.get("side"))
    q2_directional_side = _side(current_q2.get("side"))
    q3_directional_side = _side(current_q3.get("side"))
    q3_forecast_side = _side(current_q3_forecast.get("side"))
    q3_projection_side = _side(current_q3_projection.get("side"))
    atomic_question_side_conflict = bool(
        completed_study_current
        and atomic_directional_side in _DIRECTIONAL_SIDES
        and (
            q2_directional_side != atomic_directional_side
            or (
                q3_forecast_side in _DIRECTIONAL_SIDES
                and q3_forecast_side != atomic_directional_side
            )
            or (
                q3_directional_side in _DIRECTIONAL_SIDES
                and q3_directional_side != atomic_directional_side
            )
            or (
                q3_projection_side in _DIRECTIONAL_SIDES
                and q3_projection_side != atomic_directional_side
            )
        )
    )
    if atomic_question_side_conflict:
        # An operator cache can already own the newest candle key while its Q2
        # text still reflects an older command/council side.  Rebuild only the
        # study-facing contracts from the exact atomic market study; preserve
        # same-frame surface geometry and keep entry authority closed.
        atomic_market = {
            "symbol": _safe_public_text(
                atomic_runtime_bundle.get("symbol"), "Unknown", limit=64
            ),
            "timeframe": _safe_public_text(
                atomic_runtime_bundle.get("timeframe"), "Unknown", limit=32
            ).upper(),
        }
        atomic_study = _mapping(atomic_runtime_bundle.get("study"))
        public_atomic_study = _market_study_contract(atomic_study)
        atomic_history = _history_contract(
            runtime_payload,
            current_symbol=str(atomic_market["symbol"]),
            current_timeframe=str(atomic_market["timeframe"]),
        )
        atomic_permission = dict(_mapping(result.get("permission")))
        atomic_permission.update(
            {
                "action": "WAIT",
                "allowed": False,
                "side": "NEUTRAL",
                "message": (
                    "The exact completed study was reconciled to the current candle; "
                    "no entry permission is open."
                ),
                "next_condition": (
                    "Wait for an independently verified entry window on a completed candle."
                ),
                "window_open": False,
                "valid_for_seconds": 0.0,
                "window_label": "Closed",
            }
        )
        atomic_questions = _three_question_brief_v3(
            {
                "tracking_summary": {"market_study_v3": atomic_study},
                "latest_signal": {},
            },
            command={},
            market=atomic_market,
            market_study=public_atomic_study,
            history=atomic_history,
            freshness=_mapping(result.get("freshness")),
            current_move=_mapping(result.get("current_move")),
            pressure_event=_mapping(result.get("pressure_event")),
            permission=atomic_permission,
            now_epoch=current_epoch,
        )
        tracking["market_study_v3"] = public_atomic_study
        tracking["history_count"] = len(atomic_history)
        result.update(
            {
                "market": atomic_market,
                "tracking": tracking,
                "history": atomic_history,
                "permission": atomic_permission,
                "three_questions": atomic_questions,
            }
        )
    result["three_questions"] = _streaming_three_question_synthesis_v3(
        _mapping(result.get("three_questions")),
        permission=_mapping(result.get("permission")),
        stream=stream,
        order_reference_rows=result.get("overlays"),
        identity_matches=identity_matches,
        identity_rebind_pending=identity_rebind_pending,
        completed_study_current=completed_study_current,
        now_epoch=current_epoch,
    )
    if heartbeat_identity_mismatch:
        result = _apply_heartbeat_identity_veto_v3(result, heartbeat_identity)
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
    tracking_summary = _mapping(source.get("tracking_summary"))
    capture_source = _mapping(source.get("capture_source_v3"))
    visual_observation = _mapping(source.get("visual_observation_v3"))
    capture_transport = _capture_source_transport_read_v3(capture_source)
    external_capture_live = bool(
        capture_transport["active"] is True
        or (
            _text(visual_observation.get("transport_state"), "", limit=32).upper()
            == "LIVE"
            and _explicit_bool(visual_observation.get("transport_fresh")) is True
        )
    )
    stream = _cpu_stream_contract(
        source,
        tracking_summary,
        now_epoch=current_epoch,
    )
    market_study_v3 = _market_study_contract(
        tracking_summary.get("market_study_v3")
        or _mapping(source.get("latest_signal")).get("market_study_v3")
    )
    book_rule_action_signal_v3 = _book_rule_action_contract_v3(
        tracking_summary.get("book_rule_action_signal_v3")
        or _mapping(source.get("latest_signal")).get(
            "book_rule_action_signal_v3"
        )
        or source.get("book_rule_action_signal_v3")
    )
    aligned_chart_identity = _aligned_current_chart_identity_v3(
        source, display_frame
    )
    current_chart_identity = _current_chart_identity_v3(source, display_frame)
    heartbeat_identity = _heartbeat_revocation_identity_v3(
        capture_source,
        now_epoch=current_epoch,
    )
    heartbeat_identity_mismatch = _heartbeat_identity_mismatches_published_namespace_v3(
        heartbeat_identity,
        market_study_v3,
        published_market=(
            current_chart_identity
            or {
                "symbol": tracking_summary.get("detected_market"),
                "timeframe": tracking_summary.get("detected_timeframe"),
            }
        ),
        overlay_rows=overlays,
    )
    if heartbeat_identity_mismatch:
        market = {
            "symbol": _safe_public_text(
                heartbeat_identity.get("symbol"), "Unknown", limit=64
            ),
            "timeframe": _safe_public_text(
                heartbeat_identity.get("timeframe"), "Unknown", limit=32
            ),
            "identity_status": "Classifying current chart",
            "identity_pending": True,
            "identity_authority": False,
        }
    elif aligned_chart_identity and not current_chart_identity:
        market = {
            "symbol": "Unknown",
            "timeframe": "Unknown",
            "identity_status": "Identifying current chart",
            "identity_pending": True,
        }
    else:
        market = {
            "symbol": _safe_public_text(
                current_chart_identity.get("symbol")
                or current_chart_identity.get("market")
                or tracking_summary.get("detected_market")
                or _mapping(source.get("latest_signal")).get("symbol")
                or _mapping(source.get("latest_signal")).get("pair")
                or source.get("market")
                or market_study_v3.get("symbol")
            ),
            "timeframe": _safe_public_text(
                current_chart_identity.get("timeframe")
                or tracking_summary.get("detected_timeframe")
                or _mapping(source.get("latest_signal")).get("timeframe")
                or market_study_v3.get("timeframe"),
                "Unknown",
                limit=32,
            ),
        }
    history = _history_contract(
        source,
        current_symbol=str(market["symbol"]),
        current_timeframe=str(market["timeframe"]),
    )
    if heartbeat_identity_mismatch:
        overlays = []
        history = []
        current_move = _sanitize_event({}, display_frame, pressure=False)
        pressure_event = _sanitize_event({}, display_frame, pressure=True)
        permission = _identity_rebind_permission_v3(
            permission,
            heartbeat_identity,
        )
    tracking_flag = _explicit_bool(source.get("tracking_enabled"))
    observation_active = bool(tracking_flag is True or external_capture_live)
    if observation_active:
        tracking_state = (
            "UPDATING"
            if capture_transport["processing"] is True
            else "LIVE"
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
    three_questions = _three_question_brief_v3(
        source,
        command=command,
        market=market,
        market_study=market_study_v3,
        history=history,
        freshness=freshness,
        current_move=current_move,
        pressure_event=pressure_event,
        permission=permission,
        now_epoch=current_epoch,
    )
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
    overlay_viewports = _overlay_viewports_contract(source, tracking_summary)
    overlay_viewport = overlay_viewports["window"]
    surface_revisions = _surface_overlay_revision_contract(
        source,
        tracking_summary,
        market,
        overlay_viewports,
        overlays,
    )
    # Bind every public row to the exact surface namespace it was projected
    # for.  The browser verifies this again before counting or drawing the
    # row; this is a defense against stale cache merges and pair switches that
    # reuse a detector track id.
    surface_semantic_identity = surface_revisions["semantic_identity"]
    overlays = [
        {
            **overlay,
            "surface_semantic_identity": surface_semantic_identity,
        }
        for overlay in overlays
    ]
    result: dict[str, object] = {
        "schema_version": OPERATOR_WORKSPACE_SCHEMA_VERSION,
        "session_id": session_id,
        "revision": revision,
        "market": market,
        "three_questions": three_questions,
        "tracking": {
            "active": observation_active,
            "state": tracking_state,
            "updated_at": observed_at,
            "history_count": len(history),
            "market_study_v3": (
                {} if heartbeat_identity_mismatch else market_study_v3
            ),
            "book_rule_action_signal_v3": (
                {}
                if heartbeat_identity_mismatch
                else book_rule_action_signal_v3
            ),
            "stream": stream,
            "capture_source": capture_transport,
            # Overlay trend readings for the dashboard direction strip.
            # Binary BUY/SELL from slope sign; HOLD only before the first
            # accepted frame ever lands.
            "global_direction": _safe_public_text(
                _mapping(source.get("tracking_summary")).get("global_direction"),
                "",
                limit=8,
            ).upper(),
            "local_direction": _safe_public_text(
                _mapping(source.get("tracking_summary")).get("local_direction"),
                "",
                limit=8,
            ).upper(),
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
            "overlay_viewports": overlay_viewports,
            "frame_id": display_frame,
            "updated_at": observed_at,
            # Keep the selector proof beside the bitmap and geometry it owns.
            # The browser uses this value only as a fail-closed identity fence;
            # it never grants study or decision authority.
            "market_selector_visual_fingerprint": _safe_public_text(
                current_chart_identity.get(
                    "market_selector_visual_fingerprint"
                )
                or source.get("market_selector_visual_fingerprint")
                or _mapping(source.get("latest_signal")).get(
                    "market_selector_visual_fingerprint"
                )
                or tracking_summary.get(
                    "market_selector_visual_fingerprint"
                ),
                "",
                limit=80,
            ),
            "overlay_state_version": _safe_public_text(
                source.get("overlay_state_version"),
                "",
                limit=160,
            ),
            "overlay_frame_state_version": _safe_public_text(
                source.get("overlay_frame_state_version"),
                "",
                limit=160,
            ),
            **surface_revisions,
        },
        "overlays": overlays,
        "history": history,
    }
    result = refresh_operator_streaming_read_v3(
        result,
        source,
        now_epoch=current_epoch,
    )
    assert tuple(result) == _TOP_LEVEL_KEYS
    return result


__all__ = [
    "OPERATOR_WORKSPACE_SCHEMA_VERSION",
    "build_operator_workspace_v1",
    "cpu_stream_tracking_contract_v3",
    "path_clock_liquidity_contract_v3",
    "refresh_operator_streaming_read_v3",
]
