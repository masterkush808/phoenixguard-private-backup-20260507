from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import math
import os
from typing import Any, Literal, TypedDict, cast

from phoenixguard.vision.overlay_geometry import normalize_bbox


V3_OVERLAY_SCHEMA_VERSION = "PG_V3_OVERLAY_OBJECT_V1"
REQUIRED_FIELDS: tuple[str, ...] = (
    "overlay_id",
    "object_id",
    "track_id",
    "type",
    "side",
    "source_agent",
    "source_version",
    "broker_source_lock_id",
    "frame_id",
    "sequence_id",
    "chart_transform_id",
    "coordinate_mode",
    "anchor_type",
    "anchor_candles",
    "anchor_candle_indices",
    "anchor_price_band",
    "anchor_time_span",
    "anchor_evidence",
    "bounds",
    "truth_score",
    "confidence",
    "lifecycle_state",
    "layer",
    "visible_modes",
    "ttl_ms",
    "reason",
    "display_state",
    "style",
)
REQUIRED_V3_OVERLAY_FIELDS = REQUIRED_FIELDS
DEFAULTABLE_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "source_version",
        "broker_source_lock_id",
        "anchor_candles",
        "anchor_candle_indices",
        "anchor_price_band",
        "anchor_time_span",
        "anchor_evidence",
        "layer",
        "display_state",
        "style",
    }
)
LIVE_RENDER_REQUIRED_FIELDS: tuple[str, ...] = (
    "type",
    "layer",
    "frame_id",
    "chart_transform_id",
    "coordinate_mode",
    "bounds",
    "visible_modes",
    "ttl_ms",
    "truth_score",
    "source_agent",
)

OVERLAY_TYPES: tuple[str, ...] = (
    "CHART_BOUNDS",
    "CURRENT_CANDLE",
    "IMPULSE_BOX",
    "PULLBACK_BOX",
    "RETEST_BOX",
    "CONTINUATION_BOX",
    "SNIPER_ENTRY_BOX",
    "TARGET_ZONE_BOX",
    "INVALIDATION_BOX",
    "SUPPLY_ZONE",
    "DEMAND_ZONE",
    "OPPOSING_FORCE",
    "SUPPORT_TRENDLINE",
    "RESISTANCE_TRENDLINE",
    "INNER_TRENDLINE",
    "ANGLE_VECTOR",
    "PROGRESSION_PATH",
    "PREDICTION_PATH",
    "REPLAY_ENTRY",
    "REPLAY_EXIT",
    "MODEL_COUNCIL_MARKER",
    "REGIME_MARKER",
    "MARKET_PLAY_MARKER",
    "PRICE_LOCATION_MARKER",
    "TWO_CANDLE_STUDY",
    "LSTM_STUDY",
    "BROKER_CONTROL",
    "DEBUG_RAW_DETECTION",
    "REJECTED_OVERLAY",
    "STALE_OVERLAY",
    "TRANSFORM_DEBUG",
    "SCENE_GRAPH_DEBUG",
    "LABEL_COLLISION_DEBUG",
)
V3_OVERLAY_TYPES = OVERLAY_TYPES

DIAGNOSTIC_OVERLAY_TYPES: frozenset[str] = frozenset(
    {
        "DEBUG_RAW_DETECTION",
        "REJECTED_OVERLAY",
        "STALE_OVERLAY",
        "TRANSFORM_DEBUG",
        "SCENE_GRAPH_DEBUG",
        "LABEL_COLLISION_DEBUG",
    }
)

AnchorEvidenceType = Literal[
    "candle_body_cluster",
    "wick_rejection_cluster",
    "swing_high_cluster",
    "swing_low_cluster",
    "support_reclaim",
    "resistance_rejection",
    "break_and_retest",
    "failed_retest",
    "impulse_leg",
    "pullback_leg",
    "continuation_leg",
    "trendline_touch_points",
    "opposing_force_level",
    "current_candle",
    "target_band",
    "invalidation_band",
    "chart_bounds",
    "broker_control",
    "diagnostic",
]


class AnchorPriceBand(TypedDict, total=False):
    top_y: float
    bottom_y: float
    proximal_y: float
    distal_y: float
    source: str


class AnchorTimeSpan(TypedDict, total=False):
    left_x: float
    right_x: float
    start_candle_index: int
    end_candle_index: int
    source: str


class AnchorEvidence(TypedDict, total=False):
    evidence_type: AnchorEvidenceType | str
    valid: bool
    reason: str
    candle_indices: list[int]
    touch_points: list[list[float]]
    price_band: AnchorPriceBand
    time_span: AnchorTimeSpan
    source_agent: str
    source_path: str


class AnchorQuality(TypedDict, total=False):
    score: float
    has_candle_anchor: bool
    has_wick_anchor: bool
    has_sequence_anchor: bool
    inside_plot_area: bool
    matches_symbol_timeframe: bool
    chart_transform_valid: bool
    floating_risk: float
    reason: str


class OverlayStyle(TypedDict, total=False):
    color_token: str
    stroke: str
    fill: str
    line_style: str
    stroke_width: float
    opacity: float
    fill_opacity: float
    label_mode: str
    z_index: int


class OverlayHierarchy(TypedDict, total=False):
    parent_overlay_id: str
    child_overlay_ids: list[str]
    nesting_depth: int
    nesting_role: str
    group_id: str
    group_type: str


DisplayState = Literal[
    "FULL",
    "COMPACT",
    "GHOSTED",
    "GROUPED",
    "NESTED",
    "ICON_ONLY",
    "INSPECTOR_LABEL",
    "FOCUS_EXPANDED",
]

DISPLAY_STATES: tuple[str, ...] = (
    "FULL",
    "COMPACT",
    "GHOSTED",
    "GROUPED",
    "NESTED",
    "ICON_ONLY",
    "INSPECTOR_LABEL",
    "FOCUS_EXPANDED",
)
V3_DISPLAY_STATES = DISPLAY_STATES

ANCHOR_EVIDENCE_TYPES: tuple[str, ...] = (
    "candle_body_cluster",
    "wick_rejection_cluster",
    "swing_high_cluster",
    "swing_low_cluster",
    "support_reclaim",
    "resistance_rejection",
    "break_and_retest",
    "failed_retest",
    "impulse_leg",
    "pullback_leg",
    "continuation_leg",
    "trendline_touch_points",
    "opposing_force_level",
    "current_candle",
    "target_band",
    "invalidation_band",
    "chart_bounds",
    "broker_control",
    "diagnostic",
)

HARD_ANCHOR_REQUIRED_TYPES: frozenset[str] = frozenset(
    {
        "CURRENT_CANDLE",
        "IMPULSE_BOX",
        "PULLBACK_BOX",
        "RETEST_BOX",
        "CONTINUATION_BOX",
        "SNIPER_ENTRY_BOX",
        "TARGET_ZONE_BOX",
        "INVALIDATION_BOX",
        "SUPPLY_ZONE",
        "DEMAND_ZONE",
        "OPPOSING_FORCE",
        "SUPPORT_TRENDLINE",
        "RESISTANCE_TRENDLINE",
        "INNER_TRENDLINE",
        "ANGLE_VECTOR",
        "PROGRESSION_PATH",
        "REPLAY_ENTRY",
        "REPLAY_EXIT",
        "MODEL_COUNCIL_MARKER",
        "REGIME_MARKER",
        "MARKET_PLAY_MARKER",
        "PRICE_LOCATION_MARKER",
        "TWO_CANDLE_STUDY",
        "LSTM_STUDY",
    }
)

PREDICTION_OVERLAY_DISABLED_TYPES: frozenset[str] = frozenset(
    {
        "PREDICTION_PATH",
    }
)
PREDICTION_OVERLAY_DISABLED_LABEL_TOKENS: frozenset[str] = frozenset(
    {
        "BUY_TARGET_PERCENT",
        "SELL_TARGET_PERCENT",
        "BUY_RECLAIM_DATE",
        "SELL_RECLAIM_TRIGGER",
        "AGGRO_SNIPER_PREDICTION",
        "SYNTHETIC_CANDLE_PROJECTION",
    }
)
PREDICTION_OVERLAY_DISABLED_REASON = "Disabled because current prediction drawings are visually misleading."

OPERATOR_CHART_HIDDEN_TYPES: frozenset[str] = frozenset(
    {
        "RETEST_BOX",
        "PREDICTION_PATH",
        "ANGLE_VECTOR",
    }
)

VIEW_MODES: tuple[str, ...] = (
    "CLEAN_LIVE",
    "CHART_BOUNDS",
    "CANDLES",
    "GLOBAL",
    "LOCAL",
    "SUPPLY_DEMAND",
    "TRENDLINES",
    "TRIGGER",
    "TARGET",
    "INVALIDATION",
    "PATH",
    "COUNCIL",
    "TWO_CANDLE_STUDY",
    "LSTM_STUDY",
    "ACTIVE_CONTEXT",
    "FULL_HISTORY_READ",
    "REPLAY",
    "PREDICTION",
    "BROKER",
    "CALIBRATION",
    "DIAGNOSTICS",
    "DEBUG",
    "INSPECTOR",
)
V3_VISIBLE_MODES = VIEW_MODES
DIAGNOSTIC_VIEW_MODES: frozenset[str] = frozenset({"DIAGNOSTICS", "DEBUG", "INSPECTOR"})
LIVE_VIEW_MODES: frozenset[str] = frozenset(set[str](VIEW_MODES) - DIAGNOSTIC_VIEW_MODES)

APPROVED_OVERLAY_DISPLAY_LABELS: tuple[str, ...] = (
    "BROKER SURFACE",
    "CHART BOUNDS",
    "PLOT AREA",
    "PRICE AXIS",
    "TIME AXIS",
    "RIGHT ORDER PANEL",
    "TOP ASSET TABS",
    "BROKER CONTROL",
    "TIME BUTTON",
    "AMOUNT FIELD",
    "BUY BUTTON",
    "SELL BUTTON",
    "BUY ICON",
    "SELL ICON",
    "CANDLES",
    "CURRENT",
    "NOW",
    "MAJOR STRUCTURE",
    "GLOBAL STRUCTURE",
    "LOCAL STRUCTURE",
    "MAJOR SWING HIGH",
    "MAJOR SWING LOW",
    "LOCAL SWING HIGH",
    "LOCAL SWING LOW",
    "IMPULSE",
    "PULLBACK",
    "RETEST",
    "CONTINUATION",
    "PROGRESSION PATH",
    "ANGLE VECTOR",
    "SUPPORT TRENDLINE",
    "RESISTANCE TRENDLINE",
    "INNER TRENDLINE",
    "SUPPLY",
    "DEMAND",
    "SUPPLY ZONE",
    "DEMAND ZONE",
    "SUPPORT",
    "RESISTANCE",
    "OPPOSING",
    "OPPOSING FORCE",
    "SNIPER",
    "SNIPER BUY",
    "SNIPER SELL",
    "TRIGGER",
    "CONSERVATIVE TRIGGER",
    "TARGET",
    "INVALID",
    "INVALIDATION",
    "PATH",
    "REPLAY ENTRY",
    "REPLAY EXIT",
    "HISTORICAL PROGRESSION",
    "WOULD HAVE ENTERED",
    "WOULD HAVE EXITED",
    "MEMORY MATCH",
    "TWO CANDLE STUDY",
    "LSTM STUDY",
    "MODEL COUNCIL MARKER",
    "REGIME MARKER",
    "MARKET PLAY MARKER",
    "PRICE LOCATION MARKER",
    "DEBUG RAW DETECTION",
    "REJECTED OVERLAY",
    "STALE OVERLAY",
    "TRANSFORM DEBUG",
    "SCENE GRAPH DEBUG",
    "LABEL COLLISION DEBUG",
)

LEGACY_DISPLAY_LABEL_ALIASES: dict[str, str] = {
    "CURRENT_CANDLE_BOX": "NOW",
    "CURRENT_CANDLE": "NOW",
    "TARGET_ZONE_BOX": "TARGET",
    "TARGET_ZONE": "TARGET",
    "SNIPER_ENTRY_BOX": "SNIPER",
    "SNIPER_ENTRY": "SNIPER",
    "TRIGGER_ZONE_BOX": "TRIGGER",
    "TRIGGER_ZONE": "TRIGGER",
    "INVALIDATION_BOX": "INVALID",
    "INVALIDATION_ZONE": "INVALID",
    "CONT": "CONTINUATION",
    "P": "PATH",
    "T": "TRIGGER",
}

VIEW_MODE_ALIASES: dict[str, str] = {
    "ALL": "INSPECTOR",
    "CLEAN": "CLEAN_LIVE",
    "CLEANLIVE": "CLEAN_LIVE",
    "LIVE": "CLEAN_LIVE",
    "LIVE_SIGNAL": "CLEAN_LIVE",
    "SIGNAL": "CLEAN_LIVE",
    "SIGNAL_OVERLAY": "CLEAN_LIVE",
    "CHART": "CHART_BOUNDS",
    "CHART_BOUND": "CHART_BOUNDS",
    "CHART_BOUNDS_LAYER": "CHART_BOUNDS",
    "BOUNDS": "CHART_BOUNDS",
    "BOUNDING_BOX": "CHART_BOUNDS",
    "CANDLE": "CANDLES",
    "CURRENT_CANDLE": "CANDLES",
    "RECENT_CANDLES": "CANDLES",
    "CANDLE_LAYER": "CANDLES",
    "MAJOR": "GLOBAL",
    "MAJOR_GLOBAL": "GLOBAL",
    "GLOBAL_MAJOR": "GLOBAL",
    "MAJOR_SWINGS": "GLOBAL",
    "GLOBAL_SWINGS": "GLOBAL",
    "MINOR": "LOCAL",
    "LOCAL_SWINGS": "LOCAL",
    "SUPPLY": "SUPPLY_DEMAND",
    "DEMAND": "SUPPLY_DEMAND",
    "SUPPLYDEMAND": "SUPPLY_DEMAND",
    "SUPPLY_AND_DEMAND": "SUPPLY_DEMAND",
    "SUPPLY_DEMAND_LAYER": "SUPPLY_DEMAND",
    "TREND": "TRENDLINES",
    "TRENDS": "TRENDLINES",
    "TRENDLINE": "TRENDLINES",
    "TRENDLINES": "TRENDLINES",
    "TRENDLINE_LAYER": "TRENDLINES",
    "TRIGGERS": "TRIGGER",
    "TRIGGER_ZONE": "TRIGGER",
    "TRIGGER_ZONES": "TRIGGER",
    "TARGETS": "TARGET",
    "TARGET_ZONE": "TARGET",
    "TARGET_ZONES": "TARGET",
    "INVALIDATIONS": "INVALIDATION",
    "INVALIDATION_BOX": "INVALIDATION",
    "INVALIDATION_LAYER": "INVALIDATION",
    "PROGRESSION_PATH": "PATH",
    "PROGRESSION": "PATH",
    "PREDICTION_PATH": "PATH",
    "PATHS": "PATH",
    "PATH_LAYER": "PATH",
    "ACTIVE_COUNCIL": "COUNCIL",
    "ACTIVE_COUNCIL_DECISION": "COUNCIL",
    "COUNCIL_LAYER": "COUNCIL",
    "COUNCIL_LAYERS": "COUNCIL",
    "SMC": "COUNCIL",
    "SMC_COUNCIL": "COUNCIL",
    "SMC_COUNCIL_LAYER": "COUNCIL",
    "SMC_COUNCIL_LAYERS": "COUNCIL",
    "SMC_AND_COUNCIL": "COUNCIL",
    "SMC__COUNCIL": "COUNCIL",
    "SMART_MONEY": "COUNCIL",
    "SMART_MONEY_COUNCIL": "COUNCIL",
    "TWO_CANDLE": "TWO_CANDLE_STUDY",
    "TWO_CANDLE_STUDY": "TWO_CANDLE_STUDY",
    "TWO_CANDLE_STUDY_LAYER": "TWO_CANDLE_STUDY",
    "NEXT_TWO_CANDLES": "TWO_CANDLE_STUDY",
    "LSTM": "LSTM_STUDY",
    "LSTM_STUDY": "LSTM_STUDY",
    "LSTM_LAYER": "LSTM_STUDY",
    "FULL_HISTORY": "FULL_HISTORY_READ",
    "HISTORY": "FULL_HISTORY_READ",
    "HISTORY_READ": "FULL_HISTORY_READ",
    "FULL_HISTORY_LAYER": "FULL_HISTORY_READ",
    "HISTORICAL_REPLAY": "REPLAY",
    "REPLAY_LAYER": "REPLAY",
    "BROKER_CONTROL": "BROKER",
    "BROKER_CONTROLS": "BROKER",
    "BROKER_EXEC": "BROKER",
    "BROKER_LAYER": "BROKER",
    "DIAG": "DIAGNOSTICS",
    "DIAGNOSTIC": "DIAGNOSTICS",
    "DEBUG_DIAGNOSTICS": "DIAGNOSTICS",
    "DEEP_DEBUG": "DIAGNOSTICS",
}

MODE_VISIBLE_MODE_COMPATIBILITY: dict[str, set[str]] = {
    "CLEAN_LIVE": {
        "CLEAN_LIVE",
        "CHART_BOUNDS",
        "CANDLES",
        "GLOBAL",
        "LOCAL",
        "SUPPLY_DEMAND",
        "TRENDLINES",
        "TRIGGER",
        "TARGET",
        "COUNCIL",
        "ACTIVE_CONTEXT",
    },
    "CHART_BOUNDS": {"CHART_BOUNDS", "CLEAN_LIVE", "GLOBAL", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "INSPECTOR"},
    "CANDLES": {"CANDLES", "CLEAN_LIVE", "LOCAL", "ACTIVE_CONTEXT", "INSPECTOR"},
    "GLOBAL": {"GLOBAL", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "REPLAY", "INSPECTOR"},
    "LOCAL": {"LOCAL", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "REPLAY", "INSPECTOR"},
    "SUPPLY_DEMAND": {"SUPPLY_DEMAND", "CLEAN_LIVE", "GLOBAL", "LOCAL", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "INSPECTOR"},
    "TRENDLINES": {"TRENDLINES", "CLEAN_LIVE", "GLOBAL", "LOCAL", "PATH", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "REPLAY", "INSPECTOR"},
    "TRIGGER": {"TRIGGER", "CLEAN_LIVE", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "PREDICTION", "INSPECTOR"},
    "TARGET": {"TARGET", "CLEAN_LIVE", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "PREDICTION", "INSPECTOR"},
    "INVALIDATION": {"INVALIDATION", "TARGET", "CLEAN_LIVE", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "PREDICTION", "INSPECTOR"},
    "PATH": {"PATH", "PREDICTION", "ACTIVE_CONTEXT", "FULL_HISTORY_READ", "REPLAY", "INSPECTOR"},
    "COUNCIL": {"COUNCIL", "ACTIVE_CONTEXT", "CLEAN_LIVE", "FULL_HISTORY_READ", "PREDICTION", "INSPECTOR"},
    "TWO_CANDLE_STUDY": {"TWO_CANDLE_STUDY", "CLEAN_LIVE", "ACTIVE_CONTEXT", "COUNCIL", "INSPECTOR"},
    "LSTM_STUDY": {"LSTM_STUDY", "TWO_CANDLE_STUDY", "COUNCIL", "INSPECTOR", "DIAGNOSTICS"},
    "FULL_HISTORY_READ": {
        "FULL_HISTORY_READ",
        "ACTIVE_CONTEXT",
        "GLOBAL",
        "LOCAL",
        "SUPPLY_DEMAND",
        "TRENDLINES",
        "TRIGGER",
        "TARGET",
        "INVALIDATION",
        "PATH",
        "COUNCIL",
        "TWO_CANDLE_STUDY",
        "LSTM_STUDY",
        "REPLAY",
        "PREDICTION",
        "INSPECTOR",
    },
    "REPLAY": {
        "REPLAY",
        "FULL_HISTORY_READ",
        "PATH",
        "SUPPLY_DEMAND",
        "TRENDLINES",
        "GLOBAL",
        "LOCAL",
        "TRIGGER",
        "TARGET",
        "INVALIDATION",
        "ACTIVE_CONTEXT",
        "INSPECTOR",
    },
    "ACTIVE_CONTEXT": {
        "ACTIVE_CONTEXT",
        "CLEAN_LIVE",
        "GLOBAL",
        "LOCAL",
        "SUPPLY_DEMAND",
        "TRENDLINES",
        "TRIGGER",
        "TARGET",
        "INVALIDATION",
        "PATH",
        "COUNCIL",
        "TWO_CANDLE_STUDY",
        "LSTM_STUDY",
    },
    "BROKER": {"BROKER", "CALIBRATION", "INSPECTOR"},
    "DIAGNOSTICS": {"DIAGNOSTICS", "DEBUG", "INSPECTOR"},
}

ANCHOR_TYPES: tuple[str, ...] = (
    "BOX",
    "LINE",
    "POLYGON",
    "POINTS",
    "CANDLE",
    "CANDLES",
    "BROKER_SURFACE",
    "TRENDLINE_TOUCH_POINTS",
)
V3_ANCHOR_TYPES = ANCHOR_TYPES

ANCHOR_TYPE_ALIASES: dict[str, str] = {
    "ANCHOR": "POINTS",
    "ANCHORS": "POLYGON",
    "LINE": "LINE",
    "PATH": "POLYGON",
    "POLYLINE": "POLYGON",
    "RECT": "BOX",
    "RECTANGLE": "BOX",
    "TRENDLINE_TOUCH": "TRENDLINE_TOUCH_POINTS",
    "TRENDLINE_TOUCH_POINT": "TRENDLINE_TOUCH_POINTS",
    "TRENDLINE_TOUCH_POINTS": "TRENDLINE_TOUCH_POINTS",
    "CANDLE_RANGE": "CANDLES",
    "CANDLE_INDEX": "CANDLE",
    "CANDLE_INDICES": "CANDLES",
    "BROKER": "BROKER_SURFACE",
    "WINDOW": "BROKER_SURFACE",
    "FULL_BROKER_SURFACE": "BROKER_SURFACE",
}

COORDINATE_MODES: tuple[str, ...] = (
    "CHART_IMAGE_SPACE",
    "CHART_NORMALIZED",
    "FULL_BROKER_SURFACE",
    "WINDOW_SPACE",
    "PLOT_AREA_NORMALIZED",
)
V3_COORDINATE_MODES = COORDINATE_MODES

LIFECYCLE_STATES: tuple[str, ...] = (
    "ACTIVE",
    "CONFIRMED",
    "FRESH",
    "TESTED",
    "MITIGATED",
    "BROKEN",
    "FRESH_ACTIVE",
    "MITIGATED_ACTIVE",
    "HISTORICAL_ACTIVE",
    "CONTEXT_REFERENCE",
    "CONSUMED_REFERENCE",
    "BROKEN_REFERENCE",
    "ROLE_FLIP_CONFIRMED",
    "PREDICTED",
    "HISTORICAL",
    "STALE",
    "INVALIDATED",
    "MERGED",
    "DEBUG",
)
V3_LIFECYCLE_STATES = LIFECYCLE_STATES

TYPE_ALIASES: dict[str, str] = {
    "CHART_BOUNDS": "CHART_BOUNDS",
    "CHART_BOUND": "CHART_BOUNDS",
    "BOUNDS": "CHART_BOUNDS",
    "RECENT_CANDLE": "CURRENT_CANDLE",
    "RECENT_CANDLES": "CURRENT_CANDLE",
    "SNIPER": "SNIPER_ENTRY_BOX",
    "SNIPER_ENTRY": "SNIPER_ENTRY_BOX",
    "SNIPER_ENTRY_BOX": "SNIPER_ENTRY_BOX",
    "PRIMARY": "RETEST_BOX",
    "PRIMARY_TRIGGER": "RETEST_BOX",
    "TRIGGER_PRIMARY": "RETEST_BOX",
    "TARGET": "TARGET_ZONE_BOX",
    "TARGET_ZONE": "TARGET_ZONE_BOX",
    "TARGET_ZONE_BOX": "TARGET_ZONE_BOX",
    "INVALIDATION": "INVALIDATION_BOX",
    "INVALIDATION_BOX": "INVALIDATION_BOX",
    "HISTORICAL_PROGRESSION": "PROGRESSION_PATH",
    "HISTORICAL_REPLAY": "PROGRESSION_PATH",
    "HISTORY_REPLAY": "PROGRESSION_PATH",
    "FULL_HISTORY_REPLAY": "PROGRESSION_PATH",
    "PROGRESSION": "PROGRESSION_PATH",
    "PROGRESSION_PATH": "PROGRESSION_PATH",
    "PREDICTION": "PREDICTION_PATH",
    "PREDICTION_PATH": "PREDICTION_PATH",
    "BUY_TARGET_PERCENT": "PREDICTION_PATH",
    "SELL_TARGET_PERCENT": "PREDICTION_PATH",
    "BUY_RECLAIM_DATE": "PREDICTION_PATH",
    "SELL_RECLAIM_TRIGGER": "PREDICTION_PATH",
    "AGGRO_SNIPER_PREDICTION": "PREDICTION_PATH",
    "SYNTHETIC_CANDLE_PROJECTION": "PREDICTION_PATH",
    "REPLAY_ENTRY": "REPLAY_ENTRY",
    "REPLAY_EXIT": "REPLAY_EXIT",
    "WOULD_HAVE_ENTERED": "REPLAY_ENTRY",
    "WOULD_HAVE_EXITED": "REPLAY_EXIT",
    "MEMORY_MATCH": "PROGRESSION_PATH",
    "TRIGGER": "RETEST_BOX",
    "TRIGGER_ZONE": "RETEST_BOX",
    "CONSERVATIVE_TRIGGER": "RETEST_BOX",
    "RETEST": "RETEST_BOX",
    "RETEST_BOX": "RETEST_BOX",
    "PULLBACK": "PULLBACK_BOX",
    "PULLBACK_BOX": "PULLBACK_BOX",
    "CONTINUATION": "CONTINUATION_BOX",
    "CONTINUATION_BOX": "CONTINUATION_BOX",
    "IMPULSE": "IMPULSE_BOX",
    "IMPULSE_BOX": "IMPULSE_BOX",
    "CURRENT_CANDLE": "CURRENT_CANDLE",
    "CANDLE": "CURRENT_CANDLE",
    "NOW": "CURRENT_CANDLE",
    "MAJOR_SWING": "IMPULSE_BOX",
    "MAJOR_SWINGS": "IMPULSE_BOX",
    "GLOBAL_SWING": "IMPULSE_BOX",
    "GLOBAL_SWINGS": "IMPULSE_BOX",
    "LOCAL_SWING": "PULLBACK_BOX",
    "LOCAL_SWINGS": "PULLBACK_BOX",
    "MINOR_SWING": "PULLBACK_BOX",
    "MINOR_SWINGS": "PULLBACK_BOX",
    "SUPPORT": "DEMAND_ZONE",
    "SUPPORT_ZONE": "DEMAND_ZONE",
    "RESISTANCE": "SUPPLY_ZONE",
    "RESISTANCE_ZONE": "SUPPLY_ZONE",
    "SUPPORT_TREND": "SUPPORT_TRENDLINE",
    "SUPPORT_TRENDLINE": "SUPPORT_TRENDLINE",
    "SUPPORT_LINE": "SUPPORT_TRENDLINE",
    "RESISTANCE_TREND": "RESISTANCE_TRENDLINE",
    "RESISTANCE_TRENDLINE": "RESISTANCE_TRENDLINE",
    "RESISTANCE_LINE": "RESISTANCE_TRENDLINE",
    "INNER_TREND": "INNER_TRENDLINE",
    "INNER_TRENDLINE": "INNER_TRENDLINE",
    "INNER_LINE": "INNER_TRENDLINE",
    "TRENDLINE": "INNER_TRENDLINE",
    "SUPPLY": "SUPPLY_ZONE",
    "SUPPLY_ZONE": "SUPPLY_ZONE",
    "DEMAND": "DEMAND_ZONE",
    "DEMAND_ZONE": "DEMAND_ZONE",
    "OPPOSING": "OPPOSING_FORCE",
    "OPPOSING_FORCE": "OPPOSING_FORCE",
    "ANGLE": "ANGLE_VECTOR",
    "ANGLE_VECTOR": "ANGLE_VECTOR",
    "MODEL_COUNCIL_MARKER": "MODEL_COUNCIL_MARKER",
    "REGIME_MARKER": "REGIME_MARKER",
    "MARKET_PLAY_MARKER": "MARKET_PLAY_MARKER",
    "PRICE_LOCATION_MARKER": "PRICE_LOCATION_MARKER",
    "TWO_CANDLE_STUDY": "TWO_CANDLE_STUDY",
    "LSTM_STUDY": "LSTM_STUDY",
    "BROKER_CONTROL": "BROKER_CONTROL",
    "DEBUG": "DEBUG_RAW_DETECTION",
    "DEBUG_RAW_DETECTION": "DEBUG_RAW_DETECTION",
    "REJECTED_OVERLAY": "REJECTED_OVERLAY",
    "STALE_OVERLAY": "STALE_OVERLAY",
    "TRANSFORM_DEBUG": "TRANSFORM_DEBUG",
    "SCENE_GRAPH_DEBUG": "SCENE_GRAPH_DEBUG",
    "LABEL_COLLISION_DEBUG": "LABEL_COLLISION_DEBUG",
}

TYPE_LAYER_MAP: dict[str, str] = {
    "CHART_BOUNDS": "chart_bounds",
    "CURRENT_CANDLE": "recent_candles",
    "IMPULSE_BOX": "major_swings",
    "PULLBACK_BOX": "local_swings",
    "RETEST_BOX": "trigger_zones",
    "CONTINUATION_BOX": "trigger_zones",
    "SNIPER_ENTRY_BOX": "trigger_zones",
    "TARGET_ZONE_BOX": "target_zones",
    "INVALIDATION_BOX": "invalidation",
    "SUPPLY_ZONE": "supply_demand",
    "DEMAND_ZONE": "supply_demand",
    "OPPOSING_FORCE": "supply_demand",
    "SUPPORT_TRENDLINE": "trendlines",
    "RESISTANCE_TRENDLINE": "trendlines",
    "INNER_TRENDLINE": "trendlines",
    "ANGLE_VECTOR": "prediction_path",
    "PROGRESSION_PATH": "historical_replay",
    "PREDICTION_PATH": "prediction_path",
    "REPLAY_ENTRY": "historical_replay",
    "REPLAY_EXIT": "historical_replay",
    "MODEL_COUNCIL_MARKER": "active_council_decision",
    "REGIME_MARKER": "active_council_decision",
    "MARKET_PLAY_MARKER": "active_council_decision",
    "PRICE_LOCATION_MARKER": "active_council_decision",
    "TWO_CANDLE_STUDY": "active_council_decision",
    "LSTM_STUDY": "active_council_decision",
    "BROKER_CONTROL": "broker_controls",
    "DEBUG_RAW_DETECTION": "diagnostics",
    "REJECTED_OVERLAY": "diagnostics",
    "STALE_OVERLAY": "diagnostics",
    "TRANSFORM_DEBUG": "diagnostics",
    "SCENE_GRAPH_DEBUG": "diagnostics",
    "LABEL_COLLISION_DEBUG": "diagnostics",
}

SEMANTIC_LAYER_LOCK_TYPES: set[str] = {
    "TARGET_ZONE_BOX",
    "INVALIDATION_BOX",
    "SUPPORT_TRENDLINE",
    "RESISTANCE_TRENDLINE",
    "INNER_TRENDLINE",
    "ANGLE_VECTOR",
    "PREDICTION_PATH",
}

OVERLAY_LAYER_ORDER: tuple[str, ...] = (
    "base_broker_surface",
    "chart_bounds",
    "recent_candles",
    "major_swings",
    "local_swings",
    "supply_demand",
    "trendlines",
    "trigger_zones",
    "target_zones",
    "invalidation",
    "prediction_path",
    "historical_replay",
    "active_council_decision",
    "broker_controls",
    "diagnostics",
    "labels",
)

LAYER_ALIASES: dict[str, str] = {
    "BASE": "base_broker_surface",
    "BASE_BROKER": "base_broker_surface",
    "BROKER_SURFACE": "base_broker_surface",
    "FULL_BROKER_SURFACE": "base_broker_surface",
    "CHART": "chart_bounds",
    "CHART_BOUND": "chart_bounds",
    "CHART_BOUNDS": "chart_bounds",
    "BOUNDS": "chart_bounds",
    "CANDLE": "recent_candles",
    "CANDLES": "recent_candles",
    "CURRENT_CANDLE": "recent_candles",
    "RECENT": "recent_candles",
    "RECENT_CANDLE": "recent_candles",
    "RECENT_CANDLES": "recent_candles",
    "MAJOR": "major_swings",
    "GLOBAL": "major_swings",
    "MAJOR_GLOBAL": "major_swings",
    "GLOBAL_MAJOR": "major_swings",
    "MAJOR_SWING": "major_swings",
    "MAJOR_SWINGS": "major_swings",
    "LOCAL": "local_swings",
    "MINOR": "local_swings",
    "LOCAL_SWING": "local_swings",
    "LOCAL_SWINGS": "local_swings",
    "SUPPLY": "supply_demand",
    "DEMAND": "supply_demand",
    "SUPPLY_DEMAND": "supply_demand",
    "SUPPLY_AND_DEMAND": "supply_demand",
    "TREND": "trendlines",
    "TRENDS": "trendlines",
    "TRENDLINE": "trendlines",
    "TRENDLINES": "trendlines",
    "SUPPORT_TRENDLINE": "trendlines",
    "RESISTANCE_TRENDLINE": "trendlines",
    "INNER_TRENDLINE": "trendlines",
    "TRIGGER": "trigger_zones",
    "TRIGGERS": "trigger_zones",
    "TRIGGER_ZONE": "trigger_zones",
    "TRIGGER_ZONES": "trigger_zones",
    "TARGET": "target_zones",
    "TARGETS": "target_zones",
    "TARGET_ZONE": "target_zones",
    "TARGET_ZONES": "target_zones",
    "INVALID": "invalidation",
    "INVALIDATION": "invalidation",
    "INVALIDATION_BOX": "invalidation",
    "PATH": "prediction_path",
    "PATHS": "prediction_path",
    "PREDICTION": "prediction_path",
    "PREDICTION_PATH": "prediction_path",
    "PROGRESSION": "historical_replay",
    "PROGRESSION_PATH": "historical_replay",
    "COUNCIL": "active_council_decision",
    "ACTIVE_COUNCIL": "active_council_decision",
    "ACTIVE_COUNCIL_DECISION": "active_council_decision",
    "HISTORY": "historical_replay",
    "FULL_HISTORY": "historical_replay",
    "FULL_HISTORY_READ": "historical_replay",
    "HISTORICAL": "historical_replay",
    "HISTORICAL_REPLAY": "historical_replay",
    "REPLAY": "historical_replay",
    "BROKER": "broker_controls",
    "BROKER_CONTROL": "broker_controls",
    "BROKER_CONTROLS": "broker_controls",
    "CALIBRATION": "broker_controls",
    "DIAG": "diagnostics",
    "DIAGNOSTIC": "diagnostics",
    "DIAGNOSTICS": "diagnostics",
    "DEBUG": "diagnostics",
    "DEEP_DEBUG": "diagnostics",
    "LABEL": "labels",
    "LABELS": "labels",
}

OVERLAY_TYPE_PRIORITY: dict[str, int] = {
    "CHART_BOUNDS": 105,
    "CURRENT_CANDLE": 100,
    "SNIPER_ENTRY_BOX": 95,
    "RETEST_BOX": 90,
    "CONTINUATION_BOX": 86,
    "TARGET_ZONE_BOX": 84,
    "INVALIDATION_BOX": 82,
    "OPPOSING_FORCE": 78,
    "SUPPLY_ZONE": 72,
    "DEMAND_ZONE": 72,
    "SUPPORT_TRENDLINE": 68,
    "RESISTANCE_TRENDLINE": 68,
    "INNER_TRENDLINE": 66,
    "ANGLE_VECTOR": 62,
    "PREDICTION_PATH": 60,
    "IMPULSE_BOX": 50,
    "PULLBACK_BOX": 48,
    "PROGRESSION_PATH": 34,
    "REPLAY_ENTRY": 32,
    "REPLAY_EXIT": 32,
    "MODEL_COUNCIL_MARKER": 58,
    "REGIME_MARKER": 56,
    "MARKET_PLAY_MARKER": 56,
    "PRICE_LOCATION_MARKER": 56,
    "TWO_CANDLE_STUDY": 44,
    "LSTM_STUDY": 42,
    "BROKER_CONTROL": 20,
    "DEBUG_RAW_DETECTION": 1,
    "REJECTED_OVERLAY": 1,
    "STALE_OVERLAY": 1,
    "TRANSFORM_DEBUG": 1,
    "SCENE_GRAPH_DEBUG": 1,
    "LABEL_COLLISION_DEBUG": 1,
}

TYPE_ROLE_MAP: dict[str, str] = {
    "CHART_BOUNDS": "chart_bounds",
    "CURRENT_CANDLE": "current_candle",
    "IMPULSE_BOX": "impulse",
    "PULLBACK_BOX": "pullback",
    "RETEST_BOX": "trigger",
    "CONTINUATION_BOX": "continuation",
    "SNIPER_ENTRY_BOX": "sniper",
    "TARGET_ZONE_BOX": "target",
    "INVALIDATION_BOX": "invalidation",
    "SUPPLY_ZONE": "supply",
    "DEMAND_ZONE": "demand",
    "OPPOSING_FORCE": "opposing_force",
    "SUPPORT_TRENDLINE": "support_trendline",
    "RESISTANCE_TRENDLINE": "resistance_trendline",
    "INNER_TRENDLINE": "inner_trendline",
    "ANGLE_VECTOR": "angle",
    "PROGRESSION_PATH": "history",
    "PREDICTION_PATH": "prediction",
    "REPLAY_ENTRY": "replay_entry",
    "REPLAY_EXIT": "replay_exit",
    "MODEL_COUNCIL_MARKER": "model_council_marker",
    "REGIME_MARKER": "regime_marker",
    "MARKET_PLAY_MARKER": "market_play_marker",
    "PRICE_LOCATION_MARKER": "price_location_marker",
    "TWO_CANDLE_STUDY": "two_candle_study",
    "LSTM_STUDY": "lstm_study",
    "BROKER_CONTROL": "broker_control",
    "DEBUG_RAW_DETECTION": "debug",
    "REJECTED_OVERLAY": "diagnostic",
    "STALE_OVERLAY": "diagnostic",
    "TRANSFORM_DEBUG": "diagnostic",
    "SCENE_GRAPH_DEBUG": "diagnostic",
    "LABEL_COLLISION_DEBUG": "diagnostic",
}


def _canonical_token(value: Any, default: str = "") -> str:
    normalized = str(value or default).strip().upper()
    for needle in ("-", " ", "/", "\\", ".", ":"):
        normalized = normalized.replace(needle, "_")
    while "__" in normalized:
        normalized = normalized.replace("__", "_")
    return normalized.strip("_")


def approved_overlay_display_labels() -> tuple[str, ...]:
    return APPROVED_OVERLAY_DISPLAY_LABELS


def _approved_display_label_by_token() -> dict[str, str]:
    return {_canonical_token(label): label for label in APPROVED_OVERLAY_DISPLAY_LABELS}


def is_approved_overlay_display_label(label: Any) -> bool:
    token = _canonical_token(label)
    return bool(token and token in _approved_display_label_by_token())


def normalize_view_mode(mode: Any) -> str:
    normalized = _canonical_token(mode, "CLEAN_LIVE")
    normalized = VIEW_MODE_ALIASES.get(normalized, normalized)
    return normalized if normalized in VIEW_MODES else "CLEAN_LIVE"


def prediction_overlay_enabled() -> bool:
    return str(os.getenv("PHOENIXGUARD_ENABLE_PREDICTION_OVERLAY", "") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "enabled",
    }


def prediction_overlay_config() -> dict[str, Any]:
    return {
        "enabled": prediction_overlay_enabled(),
        "reason": PREDICTION_OVERLAY_DISABLED_REASON,
        "disabled_types": sorted(PREDICTION_OVERLAY_DISABLED_TYPES),
        "disabled_label_tokens": sorted(PREDICTION_OVERLAY_DISABLED_LABEL_TOKENS),
        "diagnostics_override_env": "PHOENIXGUARD_ENABLE_PREDICTION_OVERLAY",
    }


def is_known_view_mode(mode: Any) -> bool:
    normalized = _canonical_token(mode)
    return normalized in VIEW_MODES or normalized in VIEW_MODE_ALIASES

MODE_ALLOWED_TYPES: dict[str, set[str]] = cast(dict[str, set[str]], {
    "CLEAN_LIVE": {
        "CHART_BOUNDS",
        "CURRENT_CANDLE",
        "PULLBACK_BOX",
        "CONTINUATION_BOX",
        "SNIPER_ENTRY_BOX",
        "TARGET_ZONE_BOX",
        "SUPPLY_ZONE",
        "DEMAND_ZONE",
        "OPPOSING_FORCE",
        "SUPPORT_TRENDLINE",
        "RESISTANCE_TRENDLINE",
        "INNER_TRENDLINE",
        "MODEL_COUNCIL_MARKER",
        "REGIME_MARKER",
        "MARKET_PLAY_MARKER",
        "PRICE_LOCATION_MARKER",
    },
    "CHART_BOUNDS": set(OVERLAY_TYPES),
    "CANDLES": {"CHART_BOUNDS", "CURRENT_CANDLE"},
    "GLOBAL": {
        "CHART_BOUNDS",
        "IMPULSE_BOX",
        "PROGRESSION_PATH",
    },
    "LOCAL": {
        "CHART_BOUNDS",
        "CURRENT_CANDLE",
        "PULLBACK_BOX",
        "CONTINUATION_BOX",
        "SNIPER_ENTRY_BOX",
    },
    "SUPPLY_DEMAND": {"SUPPLY_ZONE", "DEMAND_ZONE", "OPPOSING_FORCE"},
    "TRENDLINES": {"SUPPORT_TRENDLINE", "RESISTANCE_TRENDLINE", "INNER_TRENDLINE"},
    "TRIGGER": {"SNIPER_ENTRY_BOX", "TARGET_ZONE_BOX"},
    "TARGET": {"TARGET_ZONE_BOX", "OPPOSING_FORCE"},
    "INVALIDATION": {"OPPOSING_FORCE"},
    "PATH": {"PROGRESSION_PATH", "REPLAY_ENTRY", "REPLAY_EXIT"},
    "COUNCIL": {
        "MODEL_COUNCIL_MARKER",
        "REGIME_MARKER",
        "MARKET_PLAY_MARKER",
        "PRICE_LOCATION_MARKER",
    },
    "TWO_CANDLE_STUDY": {"TWO_CANDLE_STUDY"},
    "LSTM_STUDY": {"LSTM_STUDY"},
    "ACTIVE_CONTEXT": set[str](OVERLAY_TYPES)
    - DIAGNOSTIC_OVERLAY_TYPES
    - {"BROKER_CONTROL", "PREDICTION_PATH", "ANGLE_VECTOR", "RETEST_BOX", "INVALIDATION_BOX", "LSTM_STUDY"},
    "FULL_HISTORY_READ": set[str](OVERLAY_TYPES)
    - DIAGNOSTIC_OVERLAY_TYPES
    - {"BROKER_CONTROL", "PREDICTION_PATH", "ANGLE_VECTOR", "RETEST_BOX", "INVALIDATION_BOX", "LSTM_STUDY", "CURRENT_CANDLE"},
    "REPLAY": {
        "CHART_BOUNDS",
        "IMPULSE_BOX",
        "PULLBACK_BOX",
        "CONTINUATION_BOX",
        "SNIPER_ENTRY_BOX",
        "TARGET_ZONE_BOX",
        "PROGRESSION_PATH",
        "REPLAY_ENTRY",
        "REPLAY_EXIT",
        "SUPPORT_TRENDLINE",
        "RESISTANCE_TRENDLINE",
        "INNER_TRENDLINE",
        "SUPPLY_ZONE",
        "DEMAND_ZONE",
        "OPPOSING_FORCE",
    },
    "PREDICTION": {"CHART_BOUNDS", "SNIPER_ENTRY_BOX", "TARGET_ZONE_BOX", "OPPOSING_FORCE"},
    "BROKER": {"BROKER_CONTROL"},
    "CALIBRATION": {"BROKER_CONTROL", "DEBUG_RAW_DETECTION"},
    "DIAGNOSTICS": set(OVERLAY_TYPES),
    "DEBUG": set(OVERLAY_TYPES),
    "INSPECTOR": set(OVERLAY_TYPES),
})

_ALL_OVERLAY_LAYERS: tuple[str, ...] = (
    "chart_bounds",
    "recent_candles",
    "major_swings",
    "local_swings",
    "supply_demand",
    "trendlines",
    "trigger_zones",
    "target_zones",
    "invalidation",
    "prediction_path",
    "active_council_decision",
    "historical_replay",
    "broker_controls",
    "diagnostics",
)


def _layer_visibility(*enabled: str) -> dict[str, bool]:
    enabled_set = set(enabled)
    return {layer: layer in enabled_set for layer in _ALL_OVERLAY_LAYERS}


MODE_LAYER_VISIBILITY: dict[str, dict[str, bool]] = {
    "CLEAN_LIVE": _layer_visibility(
        "chart_bounds",
        "recent_candles",
        "local_swings",
        "supply_demand",
        "trendlines",
        "trigger_zones",
        "target_zones",
        "active_council_decision",
    ),
    "CHART_BOUNDS": _layer_visibility("chart_bounds"),
    "CANDLES": _layer_visibility("chart_bounds", "recent_candles"),
    "GLOBAL": _layer_visibility("chart_bounds", "major_swings", "historical_replay"),
    "LOCAL": _layer_visibility("chart_bounds", "recent_candles", "local_swings", "trigger_zones"),
    "SUPPLY_DEMAND": _layer_visibility("supply_demand"),
    "TRENDLINES": _layer_visibility("trendlines"),
    "TRIGGER": _layer_visibility("trigger_zones", "target_zones"),
    "TARGET": _layer_visibility("target_zones", "supply_demand"),
    "INVALIDATION": _layer_visibility("supply_demand"),
    "PATH": _layer_visibility("prediction_path", "historical_replay"),
    "COUNCIL": _layer_visibility("active_council_decision"),
    "TWO_CANDLE_STUDY": _layer_visibility("active_council_decision"),
    "LSTM_STUDY": _layer_visibility("active_council_decision"),
    "ACTIVE_CONTEXT": _layer_visibility(
        "chart_bounds",
        "recent_candles",
        "major_swings",
        "local_swings",
        "supply_demand",
        "trendlines",
        "trigger_zones",
        "target_zones",
        "active_council_decision",
        "historical_replay",
    ),
    "FULL_HISTORY_READ": _layer_visibility(
        "chart_bounds",
        "major_swings",
        "local_swings",
        "supply_demand",
        "trendlines",
        "trigger_zones",
        "target_zones",
        "active_council_decision",
        "historical_replay",
    ),
    "REPLAY": _layer_visibility(
        "chart_bounds",
        "major_swings",
        "local_swings",
        "supply_demand",
        "trendlines",
        "trigger_zones",
        "target_zones",
        "active_council_decision",
        "historical_replay",
    ),
    "PREDICTION": _layer_visibility("chart_bounds", "supply_demand", "trigger_zones", "target_zones", "active_council_decision"),
    "BROKER": _layer_visibility("broker_controls"),
    "CALIBRATION": _layer_visibility("broker_controls", "diagnostics"),
    "DIAGNOSTICS": _layer_visibility(*_ALL_OVERLAY_LAYERS),
    "DEBUG": _layer_visibility(*_ALL_OVERLAY_LAYERS),
    "INSPECTOR": _layer_visibility(*_ALL_OVERLAY_LAYERS),
}


class V3OverlayContractError(ValueError):
    pass


@dataclass(frozen=True)
class OverlayContractIssue:
    field: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"field": self.field, "reason": self.reason}


@dataclass(frozen=True)
class OverlayValidationResult:
    ok: bool
    errors: tuple[OverlayContractIssue, ...]
    object_id: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": [error.as_dict() for error in self.errors],
            "object_id": self.object_id,
        }


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(number):
        return float(default)
    return float(number)


def _clip01(value: Any) -> float:
    return max(0.0, min(1.0, _float(value, 0.0)))


def _object_mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return cast(Mapping[str, object], value)


def _sequence(value: object) -> list[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(cast(Sequence[object], value))
    return []


def _normalized_layer_value(value: Any) -> str:
    token = _canonical_token(value)
    if token in LAYER_ALIASES:
        return LAYER_ALIASES[token]
    lowered = str(value or "").strip().lower().replace("-", "_").replace(" ", "_").replace("/", "_")
    while "__" in lowered:
        lowered = lowered.replace("__", "_")
    return lowered if lowered in set(OVERLAY_LAYER_ORDER) else ""


def _normalize_anchor_type(value: Any, inferred_anchor: str) -> str:
    normalized = _canonical_token(value or inferred_anchor or "BOX")
    normalized = ANCHOR_TYPE_ALIASES.get(normalized, normalized)
    return normalized if normalized in ANCHOR_TYPES else "BOX"


def _anchor_candle_index(value: object) -> int | None:
    mapping = _object_mapping(value)
    if mapping is not None:
        for key in ("candle_index", "index", "idx", "candle", "bar_index", "source_index"):
            if key in mapping:
                return _anchor_candle_index(mapping.get(key))
        return None
    number = _float(value, float("nan"))
    if not math.isfinite(number) or number < 0:
        return None
    return int(number)


def _normalize_anchor_candles(value: object) -> list[int]:
    if isinstance(value, str):
        raw_items: list[Any] = [item for item in value.replace(";", ",").split(",") if item.strip()]
    elif (mapping := _object_mapping(value)) is not None:
        raw_items = []
        for key in ("anchor_candles", "candles", "indices", "source_indices"):
            if key in mapping:
                raw_items = _sequence(mapping.get(key))
                break
        if not raw_items:
            maybe_index = _anchor_candle_index(mapping)
            raw_items = [maybe_index] if maybe_index is not None else []
    else:
        raw_items = _sequence(value)

    indexes: list[int] = []
    seen: set[int] = set()
    for item in raw_items:
        index = _anchor_candle_index(item)
        if index is None or index in seen:
            continue
        indexes.append(index)
        seen.add(index)
    return indexes


def _first_present(raw: Mapping[str, object], keys: Sequence[str]) -> object:
    for key in keys:
        if key in raw and raw.get(key) not in (None, "", [], {}):
            return raw.get(key)
    return None


def _normalize_anchor_candle_indices(raw: Mapping[str, object]) -> list[int]:
    return _normalize_anchor_candles(
        _first_present(
            raw,
            (
                "anchor_candle_indices",
                "anchor_candles",
                "anchor_indices",
                "source_indices",
                "candle_indices",
                "candles",
                "indices",
            ),
        )
    )


def _normalize_float_pair(value: object) -> tuple[float, float] | None:
    items = _sequence(value)
    if len(items) < 2:
        return None
    first = _float(items[0], float("nan"))
    second = _float(items[1], float("nan"))
    if not math.isfinite(first) or not math.isfinite(second):
        return None
    return (float(first), float(second))


def _normalize_anchor_price_band(raw: Mapping[str, object], bounds: Sequence[float]) -> AnchorPriceBand:
    band_value = _first_present(raw, ("anchor_price_band", "price_band", "price_range", "y_range", "zone_band"))
    mapping = _object_mapping(band_value)
    if mapping is not None:
        top = _float(mapping.get("top_y", mapping.get("top", mapping.get("upper_y"))), float("nan"))
        bottom = _float(mapping.get("bottom_y", mapping.get("bottom", mapping.get("lower_y"))), float("nan"))
        proximal = _float(mapping.get("proximal_y", mapping.get("proximal", top)), top)
        distal = _float(mapping.get("distal_y", mapping.get("distal", bottom)), bottom)
        if math.isfinite(top) and math.isfinite(bottom):
            high = max(top, bottom)
            low = min(top, bottom)
            return {
                "top_y": round(low, 6),
                "bottom_y": round(high, 6),
                "proximal_y": round(proximal, 6),
                "distal_y": round(distal, 6),
                "source": _text(mapping.get("source"), "provided"),
            }
    pair = _normalize_float_pair(band_value)
    if pair is not None:
        high = max(pair)
        low = min(pair)
        return {"top_y": round(low, 6), "bottom_y": round(high, 6), "proximal_y": round(low, 6), "distal_y": round(high, 6), "source": "provided"}
    top = min(float(bounds[1]), float(bounds[3]))
    bottom = max(float(bounds[1]), float(bounds[3]))
    return {"top_y": round(top, 6), "bottom_y": round(bottom, 6), "proximal_y": round(top, 6), "distal_y": round(bottom, 6), "source": "bounds"}


def _normalize_anchor_time_span(raw: Mapping[str, object], bounds: Sequence[float], anchor_indices: Sequence[int]) -> AnchorTimeSpan:
    span_value = _first_present(raw, ("anchor_time_span", "time_span", "time_range", "x_range", "anchor_times"))
    mapping = _object_mapping(span_value)
    if mapping is not None:
        left = _float(mapping.get("left_x", mapping.get("left", mapping.get("start_x"))), float("nan"))
        right = _float(mapping.get("right_x", mapping.get("right", mapping.get("end_x"))), float("nan"))
        if math.isfinite(left) and math.isfinite(right):
            span: AnchorTimeSpan = {
                "left_x": round(min(left, right), 6),
                "right_x": round(max(left, right), 6),
                "source": _text(mapping.get("source"), "provided"),
            }
            start_index = _anchor_candle_index(mapping.get("start_candle_index", mapping.get("start_index")))
            end_index = _anchor_candle_index(mapping.get("end_candle_index", mapping.get("end_index")))
            if start_index is not None:
                span["start_candle_index"] = start_index
            if end_index is not None:
                span["end_candle_index"] = end_index
            return span
    pair = _normalize_float_pair(span_value)
    if pair is not None:
        return {"left_x": round(min(pair), 6), "right_x": round(max(pair), 6), "source": "provided"}
    span = {"left_x": round(min(float(bounds[0]), float(bounds[2])), 6), "right_x": round(max(float(bounds[0]), float(bounds[2])), 6), "source": "bounds"}
    if anchor_indices:
        span["start_candle_index"] = int(min(anchor_indices))
        span["end_candle_index"] = int(max(anchor_indices))
    return span


def _normalize_point_rows(value: object) -> list[list[float]]:
    points: list[list[float]] = []
    for item in _sequence(value):
        pair = _sequence(item)
        if len(pair) < 2:
            continue
        x_value = _float(pair[0], float("nan"))
        y_value = _float(pair[1], float("nan"))
        if math.isfinite(x_value) and math.isfinite(y_value):
            points.append([round(float(x_value), 6), round(float(y_value), 6)])
    return points


def _anchor_evidence_type_for_overlay(overlay_type: str, role: str) -> str:
    role_text = role.lower()
    if overlay_type == "CHART_BOUNDS":
        return "chart_bounds"
    if overlay_type == "CURRENT_CANDLE":
        return "current_candle"
    if overlay_type == "IMPULSE_BOX":
        return "impulse_leg"
    if overlay_type == "PULLBACK_BOX":
        return "pullback_leg"
    if overlay_type == "CONTINUATION_BOX":
        return "continuation_leg"
    if overlay_type == "RETEST_BOX":
        return "failed_retest" if "failed" in role_text else "break_and_retest"
    if overlay_type == "SNIPER_ENTRY_BOX":
        return "break_and_retest"
    if overlay_type == "TARGET_ZONE_BOX":
        return "target_band"
    if overlay_type == "INVALIDATION_BOX":
        return "invalidation_band"
    if overlay_type == "SUPPLY_ZONE":
        return "resistance_rejection"
    if overlay_type == "DEMAND_ZONE":
        return "support_reclaim"
    if overlay_type == "OPPOSING_FORCE":
        return "opposing_force_level"
    if overlay_type in {"SUPPORT_TRENDLINE", "RESISTANCE_TRENDLINE", "INNER_TRENDLINE"}:
        return "trendline_touch_points"
    if overlay_type == "BROKER_CONTROL":
        return "broker_control"
    if overlay_type in DIAGNOSTIC_OVERLAY_TYPES:
        return "diagnostic"
    return "candle_body_cluster"


def _normalize_anchor_evidence(
    raw: Mapping[str, object],
    *,
    overlay_type: str,
    role: str,
    anchor_indices: Sequence[int],
    price_band: AnchorPriceBand,
    time_span: AnchorTimeSpan,
    source_agent: str,
) -> AnchorEvidence:
    mapping = _object_mapping(raw.get("anchor_evidence"))
    touch_points = _normalize_point_rows(
        _first_present(raw, ("touch_points", "anchor_points", "line_points", "points", "anchors", "path"))
    )
    line_touch_count = _float(raw.get("touch_count", raw.get("wick_probe_count", raw.get("reaction_count"))), 0.0)
    line_level = raw.get("line_y") is not None and (raw.get("line_x0") is not None or raw.get("line_x1") is not None)
    line_level_has_evidence = bool(line_level and line_touch_count > 0.0)
    hard_anchor_present = bool(anchor_indices or touch_points or line_level_has_evidence)
    if mapping is not None:
        evidence_type = _text(mapping.get("evidence_type") or mapping.get("type"), _anchor_evidence_type_for_overlay(overlay_type, role))
        candle_indices = _normalize_anchor_candles(mapping.get("candle_indices", mapping.get("anchor_candle_indices", anchor_indices)))
        provided_touch_points = _normalize_point_rows(mapping.get("touch_points"))
        provided_hard_anchor = bool(candle_indices or provided_touch_points or hard_anchor_present)
        valid = bool(mapping.get("valid", True)) and provided_hard_anchor
        reason = _text(
            mapping.get("reason"),
            "provided hard anchor evidence" if valid else "MISSING_ANCHOR_EVIDENCE",
        )
        return {
            "evidence_type": evidence_type,
            "valid": valid,
            "reason": reason,
            "candle_indices": candle_indices,
            "touch_points": provided_touch_points or touch_points,
            "price_band": price_band,
            "time_span": time_span,
            "source_agent": _text(mapping.get("source_agent"), source_agent),
            "source_path": _text(mapping.get("source_path") or raw.get("source_path")),
        }
    has_anchor = hard_anchor_present
    if overlay_type in {"CHART_BOUNDS", "BROKER_CONTROL"} or overlay_type in DIAGNOSTIC_OVERLAY_TYPES:
        has_anchor = True
    return {
        "evidence_type": _anchor_evidence_type_for_overlay(overlay_type, role),
        "valid": has_anchor,
        "reason": "hard anchor evidence inferred from tracked source fields" if has_anchor else "MISSING_ANCHOR_EVIDENCE",
        "candle_indices": [int(item) for item in anchor_indices],
        "touch_points": touch_points,
        "price_band": price_band,
        "time_span": time_span,
        "source_agent": source_agent,
        "source_path": _text(raw.get("source_path")),
    }


def _normalize_display_state(raw: Mapping[str, object], overlay_type: str, lifecycle_state: str) -> str:
    token = _canonical_token(raw.get("display_state") or raw.get("presentation_state") or raw.get("render_state"))
    if token == "INSPECTOR_ONLY_LABEL":
        token = "INSPECTOR_LABEL"
    if token in DISPLAY_STATES:
        return token
    if overlay_type in {"CURRENT_CANDLE", "SNIPER_ENTRY_BOX", "RETEST_BOX", "TARGET_ZONE_BOX", "INVALIDATION_BOX"}:
        return "FULL"
    if overlay_type in {"PROGRESSION_PATH", "REPLAY_ENTRY", "REPLAY_EXIT"} or lifecycle_state in {"HISTORICAL", "STALE", "BROKEN_REFERENCE", "CONSUMED_REFERENCE"}:
        return "GHOSTED"
    if overlay_type in DIAGNOSTIC_OVERLAY_TYPES:
        return "INSPECTOR_LABEL"
    if overlay_type in {"SUPPLY_ZONE", "DEMAND_ZONE", "OPPOSING_FORCE", "SUPPORT_TRENDLINE", "RESISTANCE_TRENDLINE", "INNER_TRENDLINE", "IMPULSE_BOX", "PULLBACK_BOX", "CONTINUATION_BOX"}:
        return "COMPACT"
    return "COMPACT"


def _anchor_evidence_valid(value: object) -> bool:
    mapping = _object_mapping(value)
    if mapping is None:
        return False
    if mapping.get("valid") is not True:
        return False
    candle_indices = _normalize_anchor_candles(mapping.get("candle_indices") or mapping.get("anchor_candle_indices"))
    touch_points = _normalize_point_rows(mapping.get("touch_points"))
    return bool(candle_indices or touch_points)


def _normalize_anchor_wick_points(raw: Mapping[str, object], anchor_evidence: Mapping[str, object]) -> list[list[float]]:
    direct = _normalize_point_rows(
        _first_present(
            raw,
            (
                "anchor_wick_points",
                "wick_points",
                "wick_touch_points",
                "trendline_touch_points",
                "touch_points",
                "anchor_points",
                "line_points",
                "points",
            ),
        )
    )
    if direct:
        return direct
    return _normalize_point_rows(anchor_evidence.get("touch_points"))


def _bounds_inside_plot_area(bounds: Sequence[float]) -> bool:
    if len(bounds) < 4:
        return False
    values = [float(value) for value in bounds[:4]]
    if any(not math.isfinite(value) for value in values):
        return False
    width = abs(values[2] - values[0])
    height = abs(values[3] - values[1])
    return width >= 1.0 and height >= 1.0


def _anchor_quality_reason(score: float, missing: Sequence[str]) -> str:
    if score >= 0.85:
        return "anchor_evidence_strong"
    if score >= 0.65:
        detail = ",".join(missing) if missing else "lower_confidence_geometry"
        return f"anchor_evidence_soft:{detail}"
    detail = ",".join(missing) if missing else "insufficient_anchor_evidence"
    return f"MISSING_ANCHOR_EVIDENCE:{detail}"


def _anchor_quality_for_overlay(
    raw: Mapping[str, object],
    *,
    overlay_type: str,
    bounds: Sequence[float],
    anchor_candles: Sequence[int],
    anchor_wick_points: Sequence[Sequence[float]],
    anchor_evidence: Mapping[str, object],
    sequence_id: str,
    chart_transform_id: str,
) -> AnchorQuality:
    has_candle_anchor = bool(anchor_candles)
    has_wick_anchor = bool(anchor_wick_points)
    has_sequence_anchor = bool(sequence_id and sequence_id != "sequence_pending")
    inside_plot_area = _bounds_inside_plot_area(bounds)
    chart_transform_valid = bool(chart_transform_id and chart_transform_id != "chart_transform_pending")
    matches_symbol_timeframe = not any(
        bool(raw.get(key))
        for key in (
            "wrong_pair",
            "wrong_symbol",
            "wrong_timeframe",
            "symbol_mismatch",
            "timeframe_mismatch",
            "pair_mismatch",
        )
    )
    evidence_valid = bool(anchor_evidence.get("valid") is True)
    score = 0.0
    score += 0.26 if evidence_valid else 0.0
    score += 0.18 if has_candle_anchor else 0.0
    score += 0.22 if has_wick_anchor else 0.0
    score += 0.12 if has_sequence_anchor else 0.0
    score += 0.10 if inside_plot_area else 0.0
    score += 0.10 if chart_transform_valid else 0.0
    score += 0.05 if matches_symbol_timeframe else 0.0
    if overlay_type == "CURRENT_CANDLE" and has_candle_anchor:
        score += 0.10
    if overlay_type in {"SUPPORT_TRENDLINE", "RESISTANCE_TRENDLINE", "INNER_TRENDLINE"} and len(anchor_wick_points) >= 2:
        score += 0.07
    if overlay_type in {"CHART_BOUNDS"} or overlay_type in DIAGNOSTIC_OVERLAY_TYPES:
        score = max(score, 0.95)
    missing: list[str] = []
    if not evidence_valid:
        missing.append("evidence")
    if overlay_type in HARD_ANCHOR_REQUIRED_TYPES and not has_candle_anchor:
        missing.append("candle")
    if overlay_type in {
        "SUPPLY_ZONE",
        "DEMAND_ZONE",
        "OPPOSING_FORCE",
        "SUPPORT_TRENDLINE",
        "RESISTANCE_TRENDLINE",
        "INNER_TRENDLINE",
        "SNIPER_ENTRY_BOX",
        "TARGET_ZONE_BOX",
        "INVALIDATION_BOX",
    } and not has_wick_anchor:
        missing.append("wick")
    if not has_sequence_anchor:
        missing.append("sequence")
    if not inside_plot_area:
        missing.append("bounds")
    if not chart_transform_valid:
        missing.append("chart_transform")
    if not matches_symbol_timeframe:
        missing.append("symbol_timeframe")
    score = _clip01(score)
    return {
        "score": round(float(score), 4),
        "has_candle_anchor": has_candle_anchor,
        "has_wick_anchor": has_wick_anchor,
        "has_sequence_anchor": has_sequence_anchor,
        "inside_plot_area": inside_plot_area,
        "matches_symbol_timeframe": matches_symbol_timeframe,
        "chart_transform_valid": chart_transform_valid,
        "floating_risk": round(float(1.0 - score), 4),
        "reason": _anchor_quality_reason(score, missing),
    }


def _semantic_overlay_style(raw: Mapping[str, object], overlay_type: str, side: str, lifecycle_state: str, display_state: str, z_index: int) -> OverlayStyle:
    provided = _object_mapping(raw.get("style"))
    style: OverlayStyle = {}
    if provided is not None:
        if "color_token" in provided:
            style["color_token"] = _text(provided.get("color_token"))
        if "stroke" in provided:
            style["stroke"] = _text(provided.get("stroke"))
        if "fill" in provided:
            style["fill"] = _text(provided.get("fill"))
        if "line_style" in provided:
            style["line_style"] = _text(provided.get("line_style"))
        if "stroke_width" in provided:
            style["stroke_width"] = _float(provided.get("stroke_width"), 2.0)
        if "opacity" in provided:
            style["opacity"] = _clip01(provided.get("opacity"))
        if "fill_opacity" in provided:
            style["fill_opacity"] = _clip01(provided.get("fill_opacity"))
        if "label_mode" in provided:
            style["label_mode"] = _text(provided.get("label_mode"))
        if "z_index" in provided:
            style["z_index"] = int(_float(provided.get("z_index"), z_index))
    side_value = _normalize_side(side)
    if overlay_type in {"DEMAND_ZONE", "SUPPORT_TRENDLINE"} or side_value == "BUY":
        color_token = "buy-demand-support"
        stroke = "#20d49b"
        fill = "rgba(32, 212, 155, 0.16)"
    elif overlay_type in {"SUPPLY_ZONE", "RESISTANCE_TRENDLINE"} or side_value == "SELL":
        color_token = "sell-supply-resistance"
        stroke = "#f58b45"
        fill = "rgba(245, 139, 69, 0.16)"
    else:
        color_token = "neutral-context"
        stroke = "#d6e2f0"
        fill = "rgba(214, 226, 240, 0.12)"
    if overlay_type in {"RETEST_BOX", "SNIPER_ENTRY_BOX"}:
        color_token = "trigger-confirmation"
        stroke = "#f4c95d"
        fill = "rgba(244, 201, 93, 0.14)"
    elif overlay_type == "TARGET_ZONE_BOX":
        color_token = "target"
        stroke = "#35b7ff"
        fill = "rgba(53, 183, 255, 0.12)"
    elif overlay_type == "INVALIDATION_BOX":
        color_token = "invalidation"
        stroke = "#ff4d5c"
        fill = "rgba(255, 77, 92, 0.08)"
    elif overlay_type == "OPPOSING_FORCE":
        color_token = "opposing-force"
        stroke = "#b58cff"
        fill = "rgba(181, 140, 255, 0.12)"
    elif overlay_type == "INNER_TRENDLINE":
        color_token = "inner-trendline"
        stroke = "#9ec7d9"
        fill = "rgba(158, 199, 217, 0.05)"
    elif overlay_type in {"PROGRESSION_PATH", "REPLAY_ENTRY", "REPLAY_EXIT"}:
        color_token = "replay-history"
        stroke = "#8fa7bb"
        fill = "rgba(143, 167, 187, 0.07)"
    line_style = "solid"
    if overlay_type == "INVALIDATION_BOX":
        line_style = "dashed"
    if display_state == "GHOSTED" or lifecycle_state in {"HISTORICAL", "STALE", "BROKEN_REFERENCE", "CONSUMED_REFERENCE"}:
        line_style = "dotted"
    stroke_width = 2.0
    if overlay_type in {"SNIPER_ENTRY_BOX", "RETEST_BOX", "TARGET_ZONE_BOX", "CURRENT_CANDLE"}:
        stroke_width = 2.6
    elif overlay_type in {"SUPPORT_TRENDLINE", "RESISTANCE_TRENDLINE"}:
        stroke_width = 2.1
    elif overlay_type == "INNER_TRENDLINE":
        stroke_width = 1.4
    opacity = 0.9
    if display_state == "GHOSTED":
        opacity = 0.42
    elif display_state in {"COMPACT", "NESTED"}:
        opacity = 0.68
    if "color_token" not in style:
        style["color_token"] = color_token
    if "stroke" not in style:
        style["stroke"] = stroke
    if "fill" not in style:
        style["fill"] = fill
    if "line_style" not in style:
        style["line_style"] = line_style
    if "stroke_width" not in style:
        style["stroke_width"] = stroke_width
    if "opacity" not in style:
        style["opacity"] = opacity
    if "fill_opacity" not in style:
        style["fill_opacity"] = 0.16 if display_state == "FULL" else 0.08
    if "label_mode" not in style:
        style["label_mode"] = "full" if display_state in {"FULL", "FOCUS_EXPANDED"} else "compact" if display_state in {"COMPACT", "NESTED"} else "inspector"
    if "z_index" not in style:
        style["z_index"] = z_index
    return style


def _normalize_frame_id(value: Any) -> int | str:
    text = _text(value)
    if not text:
        return 0
    number = _float(text, float("nan"))
    if math.isfinite(number) and float(int(number)) == number:
        return int(number)
    return text


def _normalize_ttl_ms(raw: Mapping[str, Any], overlay_type: str, lifecycle_state: str) -> int:
    default = 30000.0
    if overlay_type in {"PROGRESSION_PATH", "REPLAY_ENTRY", "REPLAY_EXIT"} or lifecycle_state == "HISTORICAL":
        default = 300000.0
    if overlay_type == "BROKER_CONTROL":
        default = 15000.0
    if "ttl_ms" in raw:
        ttl = _float(raw.get("ttl_ms"), default)
    else:
        ttl = _float(raw.get("ttl_sec"), default / 1000.0) * 1000.0
    if not math.isfinite(ttl) or ttl <= 0.0:
        ttl = default
    return int(min(max(round(ttl), 1), 86_400_000))


def _normalize_source_version(raw: Mapping[str, Any]) -> str:
    return _text(
        raw.get("source_version")
        or raw.get("model_version")
        or raw.get("contract_version")
        or raw.get("version"),
        V3_OVERLAY_SCHEMA_VERSION,
    )


def _normalize_broker_source_lock_id(
    raw: Mapping[str, Any],
    *,
    chart_transform_id: str,
    frame_id: int | str,
    source_agent: str,
    sequence_id: str,
) -> str:
    explicit = _text(
        raw.get("broker_source_lock_id")
        or raw.get("source_lock_id")
        or raw.get("broker_lock_id")
        or raw.get("surface_lock_id")
        or raw.get("capture_lock_id")
    )
    if explicit:
        return explicit
    return stable_overlay_id("broker_source_lock", chart_transform_id, frame_id, source_agent, sequence_id)


def stable_overlay_id(*parts: Any) -> str:
    raw = "|".join(str(part or "") for part in parts)
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:16]
    return f"v3ov_{digest}"


def normalize_bounds(value: object) -> list[float] | None:
    mapping = _object_mapping(value)
    if mapping is not None:
        for key in ("bbox", "pixel_bbox", "normalized_bbox", "xyxy"):
            if key in mapping:
                nested = normalize_bounds(mapping.get(key))
                if nested is not None:
                    return nested
        x = _float(mapping.get("x", mapping.get("left", float("nan"))), float("nan"))
        y = _float(mapping.get("y", mapping.get("top", float("nan"))), float("nan"))
        width = _float(mapping.get("width", mapping.get("w", float("nan"))), float("nan"))
        height = _float(mapping.get("height", mapping.get("h", float("nan"))), float("nan"))
        if math.isfinite(x) and math.isfinite(y) and math.isfinite(width) and math.isfinite(height):
            return normalize_bounds([x, y, x + width, y + height])
        right = _float(mapping.get("right"), float("nan"))
        bottom = _float(mapping.get("bottom"), float("nan"))
        if math.isfinite(x) and math.isfinite(y) and math.isfinite(right) and math.isfinite(bottom):
            return normalize_bounds([x, y, right, bottom])
        return None
    sequence = _sequence(value)
    if not sequence:
        return None
    if len(sequence) >= 4 and all(not _sequence(item) for item in sequence[:4]):
        bbox = normalize_bbox(sequence[:4])
        return [float(item) for item in bbox] if bbox is not None else None
    points: list[tuple[float, float]] = []
    for item in sequence:
        point = _sequence(item)
        if len(point) >= 2:
            x = _float(point[0], float("nan"))
            y = _float(point[1], float("nan"))
            if math.isfinite(x) and math.isfinite(y):
                points.append((x, y))
    if not points:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    left = min(xs)
    right = max(xs)
    top = min(ys)
    bottom = max(ys)
    if right <= left:
        left -= 3.0
        right += 3.0
    if bottom <= top:
        top -= 3.0
        bottom += 3.0
    bbox = normalize_bbox([left, top, right, bottom])
    return [float(item) for item in bbox] if bbox is not None else None


def _raw_bounds(raw: Mapping[str, object]) -> tuple[list[float] | None, str]:
    for key in ("bounds", "bbox", "pixel_bbox", "box", "rect"):
        bounds = normalize_bounds(raw.get(key))
        if bounds is not None:
            return bounds, "BOX"
    for key in ("line_points", "anchors", "points", "path"):
        bounds = normalize_bounds(raw.get(key))
        if bounds is not None:
            return bounds, "POLYGON"
    return None, "BOX"


def _normalize_overlay_points(value: object) -> list[list[float]]:
    points: list[list[float]] = []
    for item in _sequence(value):
        mapping = _object_mapping(item)
        point = _sequence(item)
        if mapping is not None:
            x = _float(mapping.get("x", mapping.get("left", mapping.get("center_x", float("nan")))), float("nan"))
            y = _float(mapping.get("y", mapping.get("top", mapping.get("center_y", float("nan")))), float("nan"))
        elif len(point) >= 2:
            x = _float(point[0], float("nan"))
            y = _float(point[1], float("nan"))
        else:
            continue
        if math.isfinite(x) and math.isfinite(y):
            points.append([round(float(x), 6), round(float(y), 6)])
    return points


def normalize_overlay_type(raw: Any, *, layer: Any = "", role: Any = "", side: Any = "") -> str:
    normalized = _canonical_token(raw)
    if normalized in TYPE_ALIASES:
        return TYPE_ALIASES[normalized]
    if normalized and normalized not in OVERLAY_TYPES:
        return "DEBUG_RAW_DETECTION"
    layer_value = _normalized_layer_value(layer) or str(layer or "").strip().lower()
    role_value = str(role or "").strip().lower()
    if role_value in {"support_trend", "support_trendline", "support_line", "trendline_support"}:
        return "SUPPORT_TRENDLINE"
    if role_value in {"resistance_trend", "resistance_trendline", "resistance_line", "trendline_resistance"}:
        return "RESISTANCE_TRENDLINE"
    if role_value in {"inner_trend", "inner_trendline", "inner_line", "micro_trendline", "local_trendline"}:
        return "INNER_TRENDLINE"
    if role_value in {"sniper", "entry", "aggressive_sniper", "sniper_entry"}:
        return "SNIPER_ENTRY_BOX"
    if role_value in {"target", "buy_target", "sell_target"}:
        return "TARGET_ZONE_BOX"
    if role_value in {"invalidation", "cancel", "cancel_invalidate"}:
        return "INVALIDATION_BOX"
    if role_value in {"pullback", "reclaim"}:
        return "PULLBACK_BOX"
    if role_value in {"continuation", "continue", "primary"}:
        return "CONTINUATION_BOX"
    if role_value in {"retest", "trigger"}:
        return "RETEST_BOX"
    if role_value in {"support", "demand"}:
        return "DEMAND_ZONE" if str(side or "").upper() != "SELL" else "SUPPLY_ZONE"
    if role_value in {"resistance", "supply"}:
        return "SUPPLY_ZONE" if str(side or "").upper() != "BUY" else "DEMAND_ZONE"
    if layer_value == "broker_controls":
        return "BROKER_CONTROL"
    if layer_value == "historical_replay":
        return "PROGRESSION_PATH"
    if layer_value == "supply_demand":
        return "SUPPLY_ZONE" if str(side or "").upper() == "SELL" else "DEMAND_ZONE"
    if layer_value == "trendlines":
        return "INNER_TRENDLINE"
    if layer_value in {"trigger_zones", "active_council_decision"}:
        return "CONTINUATION_BOX"
    if layer_value == "recent_candles":
        return "CURRENT_CANDLE"
    if layer_value == "major_swings":
        return "IMPULSE_BOX"
    if layer_value == "local_swings":
        return "PULLBACK_BOX"
    return "DEBUG_RAW_DETECTION"


def is_known_overlay_type(raw: Any) -> bool:
    normalized = _canonical_token(raw)
    return normalized in OVERLAY_TYPES or normalized in TYPE_ALIASES


def _normalize_side(value: Any) -> str:
    side = str(value or "HOLD").strip().upper()
    return side if side in {"BUY", "SELL", "HOLD"} else "HOLD"


COORDINATE_MODE_ALIASES: dict[str, str] = {
    "CHART": "CHART_IMAGE_SPACE",
    "CHART_IMAGE": "CHART_IMAGE_SPACE",
    "IMAGE": "CHART_IMAGE_SPACE",
    "PIXEL": "CHART_IMAGE_SPACE",
    "PIXELS": "CHART_IMAGE_SPACE",
    "CHART_PIXELS": "CHART_IMAGE_SPACE",
    "CHART_PIXEL_SPACE": "CHART_IMAGE_SPACE",
    "NORMALIZED": "CHART_NORMALIZED",
    "CHART_NORM": "CHART_NORMALIZED",
    "CHART_NORMALISED": "CHART_NORMALIZED",
    "PLOT": "PLOT_AREA_NORMALIZED",
    "PLOT_AREA": "PLOT_AREA_NORMALIZED",
    "PLOT_NORMALIZED": "PLOT_AREA_NORMALIZED",
    "WINDOW": "WINDOW_SPACE",
    "WINDOW_SPACE": "WINDOW_SPACE",
    "FULL_WINDOW": "FULL_BROKER_SURFACE",
    "BROKER": "FULL_BROKER_SURFACE",
    "BROKER_SURFACE": "FULL_BROKER_SURFACE",
    "FULL_BROKER": "FULL_BROKER_SURFACE",
}


def is_known_coordinate_mode(value: Any) -> bool:
    mode = _canonical_token(value)
    return mode in COORDINATE_MODES or mode in COORDINATE_MODE_ALIASES


def _normalize_coordinate_mode(value: Any, bounds: Sequence[float]) -> str:
    mode = _canonical_token(value)
    mode = COORDINATE_MODE_ALIASES.get(mode, mode)
    if mode in COORDINATE_MODES:
        return mode
    if max(abs(value) for value in bounds) <= 1.0001:
        return "CHART_NORMALIZED"
    return "CHART_IMAGE_SPACE"


def _convert_bounds_for_mode(bounds: list[float], mode: str, image_size: Sequence[Any] | None) -> list[float]:
    if not image_size or len(image_size) < 2:
        return bounds
    width = max(1.0, _float(image_size[0], 1.0))
    height = max(1.0, _float(image_size[1], 1.0))
    max_value = max(abs(value) for value in bounds)
    if mode == "CHART_NORMALIZED" and max_value > 1.0001:
        return [bounds[0] / width, bounds[1] / height, bounds[2] / width, bounds[3] / height]
    if mode == "CHART_IMAGE_SPACE" and max_value <= 1.0001:
        return [bounds[0] * width, bounds[1] * height, bounds[2] * width, bounds[3] * height]
    return bounds


def _normalize_visible_modes(raw: Mapping[str, object], overlay_type: str) -> list[str]:
    raw_modes = raw.get("visible_modes") or raw.get("modes") or raw.get("visible_in_modes")
    modes: list[str] = []
    for item in _sequence(raw_modes):
        if _canonical_token(item) == "ALL":
            modes.extend(VIEW_MODES)
            continue
        if is_known_view_mode(item):
            modes.append(normalize_view_mode(item))
    if modes:
        return list(dict.fromkeys(modes))
    return [mode for mode, allowed in MODE_ALLOWED_TYPES.items() if overlay_type in allowed]


def overlay_layer_name(overlay_type: Any, raw_layer: Any = "") -> str:
    normalized_type = normalize_overlay_type(overlay_type)
    if normalized_type in SEMANTIC_LAYER_LOCK_TYPES:
        return TYPE_LAYER_MAP.get(normalized_type, "diagnostics")
    layer = _normalized_layer_value(raw_layer)
    if layer:
        return layer
    return TYPE_LAYER_MAP.get(normalized_type, "diagnostics")


def overlay_type_priority(overlay_type: Any) -> int:
    return int(OVERLAY_TYPE_PRIORITY.get(normalize_overlay_type(overlay_type), 0))


def short_label_for_overlay(overlay_type: Any, side: Any = "", label: Any = "") -> str:
    overlay_type_value = normalize_overlay_type(overlay_type)
    side_value = _normalize_side(side)
    if overlay_type_value == "CURRENT_CANDLE":
        return "NOW"
    if overlay_type_value == "CHART_BOUNDS":
        return "CHART BOUNDS"
    if overlay_type_value == "SNIPER_ENTRY_BOX":
        return f"SNIPER {side_value}" if side_value != "HOLD" else "SNIPER"
    if overlay_type_value == "RETEST_BOX":
        return "TRIGGER"
    if overlay_type_value == "CONTINUATION_BOX":
        return "CONTINUATION"
    if overlay_type_value == "TARGET_ZONE_BOX":
        return "TARGET"
    if overlay_type_value == "INVALIDATION_BOX":
        return "INVALID"
    if overlay_type_value == "SUPPLY_ZONE":
        return "SUPPLY"
    if overlay_type_value == "DEMAND_ZONE":
        return "DEMAND"
    if overlay_type_value == "OPPOSING_FORCE":
        return "OPPOSING FORCE"
    if overlay_type_value == "SUPPORT_TRENDLINE":
        return "SUPPORT TRENDLINE"
    if overlay_type_value == "RESISTANCE_TRENDLINE":
        return "RESISTANCE TRENDLINE"
    if overlay_type_value == "INNER_TRENDLINE":
        return "INNER TRENDLINE"
    if overlay_type_value == "ANGLE_VECTOR":
        return "ANGLE VECTOR"
    if overlay_type_value == "IMPULSE_BOX":
        return "IMPULSE"
    if overlay_type_value == "PULLBACK_BOX":
        return "PULLBACK"
    if overlay_type_value == "REPLAY_ENTRY":
        return "REPLAY ENTRY"
    if overlay_type_value == "REPLAY_EXIT":
        return "REPLAY EXIT"
    if overlay_type_value == "PREDICTION_PATH":
        return "PATH"
    if overlay_type_value == "PROGRESSION_PATH":
        return "HISTORICAL PROGRESSION"
    if overlay_type_value == "MODEL_COUNCIL_MARKER":
        return "MODEL COUNCIL MARKER"
    if overlay_type_value == "REGIME_MARKER":
        return "REGIME MARKER"
    if overlay_type_value == "MARKET_PLAY_MARKER":
        return "MARKET PLAY MARKER"
    if overlay_type_value == "PRICE_LOCATION_MARKER":
        return "PRICE LOCATION MARKER"
    if overlay_type_value == "TWO_CANDLE_STUDY":
        return "TWO CANDLE STUDY"
    if overlay_type_value == "LSTM_STUDY":
        return "LSTM STUDY"
    if overlay_type_value == "BROKER_CONTROL":
        return abbreviate_label(str(label or "BROKER"), max_words=2)
    if overlay_type_value == "DEBUG_RAW_DETECTION":
        return "DEBUG RAW DETECTION"
    if overlay_type_value == "REJECTED_OVERLAY":
        return "REJECTED OVERLAY"
    if overlay_type_value == "STALE_OVERLAY":
        return "STALE OVERLAY"
    if overlay_type_value == "TRANSFORM_DEBUG":
        return "TRANSFORM DEBUG"
    if overlay_type_value == "SCENE_GRAPH_DEBUG":
        return "SCENE GRAPH DEBUG"
    if overlay_type_value == "LABEL_COLLISION_DEBUG":
        return "LABEL COLLISION DEBUG"
    return abbreviate_label(str(label or overlay_type_value.replace("_", " ")), max_words=2)


def normalize_overlay_display_label(label: Any, overlay_type: Any, side: Any = "") -> tuple[str, str, str]:
    fallback = short_label_for_overlay(overlay_type, side, label)
    fallback_token = _canonical_token(fallback)
    approved = _approved_display_label_by_token()
    canonical_fallback = approved.get(fallback_token, fallback)
    raw_text = _text(label)
    raw_token = _canonical_token(raw_text)
    normalized_type = normalize_overlay_type(overlay_type)
    if not raw_token:
        return canonical_fallback, "canonical", ""
    if raw_token in approved:
        if raw_token in {"NOW", "CURRENT"} and normalized_type != "CURRENT_CANDLE":
            return canonical_fallback, "remapped", raw_text
        return approved[raw_token], "approved", ""
    alias = LEGACY_DISPLAY_LABEL_ALIASES.get(raw_token)
    if alias:
        alias_token = _canonical_token(alias)
        if alias_token == "SNIPER":
            return canonical_fallback, "remapped", raw_text
        return approved.get(alias_token, alias.replace("_", " ")), "remapped", raw_text
    if normalized_type in DIAGNOSTIC_OVERLAY_TYPES:
        return canonical_fallback, "unmapped", raw_text
    return canonical_fallback, "remapped", raw_text


def _missing_strict_fields(raw: Mapping[str, object]) -> list[str]:
    missing: list[str] = []
    for field in REQUIRED_FIELDS:
        if field in DEFAULTABLE_REQUIRED_FIELDS:
            continue
        if field == "bounds":
            if _raw_bounds(raw)[0] is None:
                missing.append(field)
        elif field not in raw or raw.get(field) in (None, ""):
            missing.append(field)
    return missing


def normalize_v3_overlay_object(
    raw: Mapping[str, object],
    *,
    strict: bool = True,
    image_size: Sequence[object] | None = None,
    fallback_index: int = 0,
    frame_id: int | str | None = None,
    sequence_id: str = "",
    chart_transform_id: str = "",
    source_agent: str = "market_object_tracker_v3",
) -> dict[str, object]:
    if strict:
        missing = _missing_strict_fields(raw)
        if missing:
            raise V3OverlayContractError(f"V3 overlay object missing required fields: {', '.join(missing)}")

    bounds, inferred_anchor = _raw_bounds(raw)
    if bounds is None:
        raise V3OverlayContractError("V3 overlay object has invalid bounds")

    side = _normalize_side(raw.get("side") or raw.get("direction") or raw.get("action"))
    layer = _normalized_layer_value(raw.get("layer") or raw.get("_layer"))
    role = str(raw.get("role") or raw.get("kind") or raw.get("box_type") or "").strip().lower()
    overlay_type = normalize_overlay_type(raw.get("type"), layer=layer, role=role, side=side)
    coordinate_mode = _normalize_coordinate_mode(raw.get("coordinate_mode") or raw.get("space"), bounds)
    bounds = _convert_bounds_for_mode(bounds, coordinate_mode, image_size)
    bounds = [round(float(value), 6) for value in bounds]
    if coordinate_mode == "WINDOW_SPACE":
        coordinate_mode = "FULL_BROKER_SURFACE"

    frame_value = _normalize_frame_id(frame_id if frame_id is not None else raw.get("frame_id", raw.get("frame_index", 0)))
    sequence_value = _text(sequence_id or raw.get("sequence_id") or raw.get("session_id") or "sequence_pending")
    chart_transform_value = _text(
        chart_transform_id or raw.get("chart_transform_id") or raw.get("transform_id"),
        "chart_transform_pending",
    )
    source_agent_value = _text(raw.get("source_agent") or raw.get("source"), source_agent)
    source_version_value = _normalize_source_version(raw)
    broker_source_lock_id = _normalize_broker_source_lock_id(
        raw,
        chart_transform_id=chart_transform_value,
        frame_id=frame_value,
        source_agent=source_agent_value,
        sequence_id=sequence_value,
    )
    overlay_id = _text(
        raw.get("overlay_id")
        or raw.get("id")
        or raw.get("key")
        or stable_overlay_id(sequence_value, frame_value, overlay_type, bounds, fallback_index)
    )
    object_id = _text(raw.get("object_id") or raw.get("market_object_id") or overlay_id)
    track_id = _text(raw.get("track_id") or raw.get("persistent_id") or object_id)
    lifecycle = str(raw.get("lifecycle_state") or "").strip().upper()
    if lifecycle not in LIFECYCLE_STATES:
        lifecycle = "PREDICTED" if overlay_type == "PREDICTION_PATH" else "HISTORICAL" if overlay_type in {"PROGRESSION_PATH", "REPLAY_ENTRY", "REPLAY_EXIT"} else "ACTIVE"
    anchor_type = _normalize_anchor_type(raw.get("anchor_type"), inferred_anchor)
    anchor_candles = _normalize_anchor_candle_indices(raw)
    anchor_price_band = _normalize_anchor_price_band(raw, bounds)
    anchor_time_span = _normalize_anchor_time_span(raw, bounds, anchor_candles)
    label = _text(raw.get("label") or raw.get("key") or overlay_type.replace("_", " "))
    raw_display_label = _text(
        raw.get("raw_display_label")
        or raw.get("short_label")
        or raw.get("display_label")
        or raw.get("label")
        or raw.get("key")
    )
    display_label, display_label_status, unmapped_display_label = normalize_overlay_display_label(
        raw_display_label,
        overlay_type,
        side,
    )
    confidence = _clip01(raw.get("confidence", raw.get("truth_score", 0.0)))
    truth_score = _clip01(raw.get("truth_score", confidence))
    resolved_layer = overlay_layer_name(overlay_type, raw.get("layer") or raw.get("_layer"))
    display_state = _normalize_display_state(raw, overlay_type, lifecycle)
    z_index = int(_float(raw.get("z_index"), overlay_type_priority(overlay_type)))
    anchor_evidence = _normalize_anchor_evidence(
        raw,
        overlay_type=overlay_type,
        role=role,
        anchor_indices=anchor_candles,
        price_band=anchor_price_band,
        time_span=anchor_time_span,
        source_agent=source_agent_value,
    )
    anchor_wick_points = _normalize_anchor_wick_points(raw, anchor_evidence)
    anchor_quality = _anchor_quality_for_overlay(
        raw,
        overlay_type=overlay_type,
        bounds=bounds,
        anchor_candles=anchor_candles,
        anchor_wick_points=anchor_wick_points,
        anchor_evidence=anchor_evidence,
        sequence_id=sequence_value,
        chart_transform_id=chart_transform_value,
    )
    style = _semantic_overlay_style(raw, overlay_type, side, lifecycle, display_state, z_index)

    row: dict[str, object] = {
        "schema_version": V3_OVERLAY_SCHEMA_VERSION,
        "overlay_id": overlay_id,
        "id": overlay_id,
        "object_id": object_id,
        "track_id": track_id,
        "type": overlay_type,
        "side": side,
        "source_agent": source_agent_value,
        "source_version": source_version_value,
        "broker_source_lock_id": broker_source_lock_id,
        "frame_id": frame_value,
        "sequence_id": sequence_value,
        "chart_transform_id": chart_transform_value,
        "coordinate_mode": coordinate_mode,
        "anchor_type": anchor_type,
        "anchor_candles": anchor_candles,
        "anchor_candle_indices": list(anchor_candles),
        "anchor_price_band": anchor_price_band,
        "anchor_time_span": anchor_time_span,
        "anchor_wick_points": anchor_wick_points,
        "anchor_sequence_id": sequence_value,
        "anchor_confidence": float(anchor_quality.get("score", 0.0)),
        "anchor_quality": anchor_quality,
        "anchor_evidence": anchor_evidence,
        "anchor_evidence_status": "VALID" if bool(anchor_evidence.get("valid", False)) else "MISSING_ANCHOR_EVIDENCE",
        "bounds": bounds,
        "bbox": list(bounds),
        "truth_score": truth_score,
        "confidence": confidence,
        "lifecycle_state": lifecycle,
        "visible_modes": _normalize_visible_modes(raw, overlay_type),
        "ttl_ms": _normalize_ttl_ms(raw, overlay_type, lifecycle),
        "reason": _text(raw.get("reason") or raw.get("message") or raw.get("summary") or f"{overlay_type} tracked from market object"),
        "label": label,
        "raw_display_label": raw_display_label,
        "display_label": display_label,
        "short_label": display_label,
        "display_label_status": display_label_status,
        "unmapped_display_label": unmapped_display_label,
        "symbol": _text(raw.get("symbol") or raw.get("asset") or raw.get("pair")),
        "timeframe": _text(raw.get("timeframe") or raw.get("tf") or raw.get("interval")),
        "layer": resolved_layer,
        "role": _text(raw.get("role") or role, TYPE_ROLE_MAP.get(overlay_type, "")),
        "visible_default": bool(
            raw.get(
                "visible_default",
                overlay_type in MODE_ALLOWED_TYPES["CLEAN_LIVE"] and overlay_type not in OPERATOR_CHART_HIDDEN_TYPES,
            )
        ),
        "created_at_ms": int(_float(raw.get("created_at_ms"), 0.0)),
        "z_index": z_index,
        "display_state": display_state,
        "style": style,
        "hierarchy": {
            "parent_overlay_id": _text(raw.get("parent_overlay_id") or raw.get("parent_id")),
            "child_overlay_ids": [str(item) for item in _sequence(raw.get("child_overlay_ids") or raw.get("children"))],
            "nesting_depth": int(_float(raw.get("nesting_depth"), 0.0)),
            "nesting_role": _text(raw.get("nesting_role")),
            "group_id": _text(raw.get("group_id")),
            "group_type": _text(raw.get("group_type")),
        },
    }
    geometry_points = _normalize_overlay_points(
        raw.get("line_points")
        or raw.get("points")
        or raw.get("path")
        or raw.get("anchors")
    )
    line_geometry_types = {
        "SUPPORT_TRENDLINE",
        "RESISTANCE_TRENDLINE",
        "INNER_TRENDLINE",
        "ANGLE_VECTOR",
        "PROGRESSION_PATH",
        "PREDICTION_PATH",
    }
    if geometry_points and overlay_type in line_geometry_types:
        geometry_bounds = normalize_bounds(geometry_points)
        row["points"] = geometry_points
        row["line_points"] = geometry_points
        if overlay_type in {"SUPPORT_TRENDLINE", "RESISTANCE_TRENDLINE", "INNER_TRENDLINE"}:
            row["anchor_type"] = _normalize_anchor_type(raw.get("anchor_type") or row.get("anchor_type") or "LINE", "LINE")
        else:
            row["anchor_type"] = "POLYGON"
        if geometry_bounds is not None:
            row["bounds"] = [round(float(value), 6) for value in geometry_bounds]
            row["bbox"] = list(row["bounds"])
    for key in (
        "anchor_candle_indices",
        "anchor_price_band",
        "anchor_time_span",
        "anchor_wick_points",
        "anchor_sequence_id",
        "anchor_confidence",
        "anchor_quality",
        "anchor_evidence",
        "anchor_evidence_status",
        "trendline_role",
        "trendline_scope",
        "trendline_validation",
        "skill_gate",
        "source_path",
        "source_key",
        "structural_anchor",
        "anchored",
        "source_indices",
        "start_point",
        "end_point",
        "candle_count",
        "line_y",
        "line_x0",
        "line_x1",
        "touch_count",
        "touch_points",
        "wick_probe_count",
        "line_obstruction_count",
        "body_cross_fraction",
        "close_distance_norm",
        "significant_close",
        "touch_quality",
        "breach_state",
        "validation_reason",
        "zone_family",
        "entry_authority_active",
        "liquidity_pool_type",
        "liquidity_source",
        "role_flip_state",
        "zone_stack_id",
        "source_zone_id",
        "side_blocked",
        "distance_to_current_price",
        "distance_to_target",
        "force_type",
        "force_strength",
        "anchor_level",
        "source_rule",
        "knowledge_tags",
        "replay_sequence",
        "replay_action",
        "story",
        "parent_label",
        "display_state",
        "visual_weight",
        "geometry_visible",
        "label_hidden",
        "label_visible",
        "inspector_visible",
        "label_mode",
        "label_lane",
        "representation_reason",
        "style",
        "group_id",
        "group_type",
        "group_bounds",
        "summary_label",
        "expand_on_hover",
        "expand_on_click",
        "parent_overlay_id",
        "child_overlay_ids",
        "nesting_depth",
        "nesting_role",
        "hierarchy",
    ):
        value = raw.get(key)
        if value not in (None, "", [], {}):
            row.setdefault(key, value)
    return row


def validate_v3_overlay_object(overlay: Mapping[str, object]) -> OverlayValidationResult:
    issues: list[OverlayContractIssue] = []
    for field in REQUIRED_FIELDS:
        if field == "bounds":
            if _raw_bounds(overlay)[0] is None:
                issues.append(OverlayContractIssue(field, "missing_or_invalid"))
        elif field == "visible_modes":
            modes = overlay.get(field)
            if not _sequence(modes):
                issues.append(OverlayContractIssue(field, "missing"))
        elif field not in overlay or overlay.get(field) in (None, ""):
            issues.append(OverlayContractIssue(field, "missing"))
    overlay_type = str(overlay.get("type") or "").strip().upper()
    if overlay_type and not is_known_overlay_type(overlay_type):
        issues.append(OverlayContractIssue("type", f"invalid:{overlay_type}"))
    if overlay.get("coordinate_mode") not in (None, "") and not is_known_coordinate_mode(overlay.get("coordinate_mode")):
        issues.append(OverlayContractIssue("coordinate_mode", f"invalid:{overlay.get('coordinate_mode')}"))
    raw_anchor_type = _canonical_token(overlay.get("anchor_type"))
    if raw_anchor_type:
        anchor_type = ANCHOR_TYPE_ALIASES.get(raw_anchor_type, raw_anchor_type)
        if anchor_type not in ANCHOR_TYPES:
            issues.append(OverlayContractIssue("anchor_type", f"invalid:{overlay.get('anchor_type')}"))
    if "anchor_candles" in overlay and overlay.get("anchor_candles") not in (None, "", []):
        if not _normalize_anchor_candles(overlay.get("anchor_candles")):
            issues.append(OverlayContractIssue("anchor_candles", "invalid"))
    if "anchor_candle_indices" in overlay and overlay.get("anchor_candle_indices") not in (None, "", []):
        if not _normalize_anchor_candle_indices(overlay):
            issues.append(OverlayContractIssue("anchor_candle_indices", "invalid"))
    if "anchor_price_band" in overlay and overlay.get("anchor_price_band") not in (None, "", {}):
        if _object_mapping(overlay.get("anchor_price_band")) is None:
            issues.append(OverlayContractIssue("anchor_price_band", "invalid"))
    if "anchor_time_span" in overlay and overlay.get("anchor_time_span") not in (None, "", {}):
        if _object_mapping(overlay.get("anchor_time_span")) is None:
            issues.append(OverlayContractIssue("anchor_time_span", "invalid"))
    if "anchor_evidence" in overlay and overlay.get("anchor_evidence") not in (None, "", {}):
        if _object_mapping(overlay.get("anchor_evidence")) is None:
            issues.append(OverlayContractIssue("anchor_evidence", "invalid"))
    if "anchor_quality" in overlay and overlay.get("anchor_quality") not in (None, "", {}):
        quality = _object_mapping(overlay.get("anchor_quality"))
        if quality is None:
            issues.append(OverlayContractIssue("anchor_quality", "invalid"))
        else:
            score = _float(quality.get("score"), float("nan"))
            if not math.isfinite(score) or score < 0.0 or score > 1.0:
                issues.append(OverlayContractIssue("anchor_quality", f"invalid_score:{quality.get('score')}"))
    if overlay.get("layer") not in (None, "") and not _normalized_layer_value(overlay.get("layer")):
        issues.append(OverlayContractIssue("layer", f"invalid:{overlay.get('layer')}"))
    if overlay.get("ttl_ms") not in (None, ""):
        ttl = _float(overlay.get("ttl_ms"), float("nan"))
        if not math.isfinite(ttl) or ttl <= 0:
            issues.append(OverlayContractIssue("ttl_ms", f"invalid:{overlay.get('ttl_ms')}"))
    lifecycle = str(overlay.get("lifecycle_state") or "").strip().upper()
    if lifecycle and lifecycle not in LIFECYCLE_STATES:
        issues.append(OverlayContractIssue("lifecycle_state", f"invalid:{lifecycle}"))
    display_state = str(overlay.get("display_state") or "").strip().upper()
    if display_state and display_state not in DISPLAY_STATES:
        issues.append(OverlayContractIssue("display_state", f"invalid:{display_state}"))
    if "style" in overlay and overlay.get("style") not in (None, "", {}):
        if _object_mapping(overlay.get("style")) is None:
            issues.append(OverlayContractIssue("style", "invalid"))
    modes = overlay.get("visible_modes")
    if "visible_modes" in overlay and (
        not isinstance(modes, Sequence)
        or isinstance(modes, (str, bytes, bytearray))
        or any(not is_known_view_mode(mode) for mode in cast(Sequence[Any], modes))
    ):
        issues.append(OverlayContractIssue("visible_modes", "invalid"))
    return OverlayValidationResult(
        ok=not issues,
        errors=tuple(issues),
        object_id=str(overlay.get("object_id") or overlay.get("overlay_id") or ""),
    )


def view_mode_profile(mode: str) -> dict[str, Any]:
    normalized = normalize_view_mode(mode)
    return {
        "mode": normalized,
        "allowed_types": sorted(MODE_ALLOWED_TYPES[normalized]),
        "layer_visibility": dict(MODE_LAYER_VISIBILITY[normalized]),
        "allow_selection": normalized
        in {
            "CHART_BOUNDS",
            "CANDLES",
            "GLOBAL",
            "LOCAL",
            "SUPPLY_DEMAND",
            "TRENDLINES",
            "TRIGGER",
            "TARGET",
            "INVALIDATION",
            "PATH",
            "COUNCIL",
            "TWO_CANDLE_STUDY",
            "LSTM_STUDY",
            "ACTIVE_CONTEXT",
            "FULL_HISTORY_READ",
            "REPLAY",
            "PREDICTION",
            "BROKER",
            "DIAGNOSTICS",
            "DEBUG",
            "INSPECTOR",
        },
    }


def overlay_is_visible(
    overlay: Mapping[str, Any],
    mode: str,
    *,
    now_ms: int | float | None = None,
    layer_overrides: Mapping[str, bool] | None = None,
) -> bool:
    return not overlay_rejection_reasons(overlay, mode, now_ms=now_ms, layer_overrides=layer_overrides)


def overlay_rejection_reasons(
    overlay: Mapping[str, Any],
    mode: str,
    *,
    now_ms: int | float | None = None,
    layer_overrides: Mapping[str, bool] | None = None,
) -> tuple[str, ...]:
    reasons: list[str] = []
    normalized_mode = normalize_view_mode(mode)
    if normalized_mode in LIVE_VIEW_MODES:
        missing_live_fields: list[str] = []
        for field in LIVE_RENDER_REQUIRED_FIELDS:
            if field == "bounds":
                if _raw_bounds(overlay)[0] is None:
                    missing_live_fields.append(field)
            elif field not in overlay or overlay.get(field) in (None, "", []):
                missing_live_fields.append(field)
        if missing_live_fields:
            return tuple(f"missing_live_render_field:{field}" for field in missing_live_fields)
    raw_has_created_at = "created_at_ms" in overlay or "created_epoch_ms" in overlay
    try:
        normalized = normalize_v3_overlay_object(overlay, strict=False)
    except V3OverlayContractError as exc:
        return (f"invalid_contract:{str(exc)}",)
    if (
        normalized_mode in LIVE_VIEW_MODES
        and str(normalized.get("type") or "") in HARD_ANCHOR_REQUIRED_TYPES
        and not _anchor_evidence_valid(normalized.get("anchor_evidence"))
    ):
        reasons.append("missing_anchor_evidence")
    if normalized_mode in LIVE_VIEW_MODES and str(normalized.get("type") or "") in HARD_ANCHOR_REQUIRED_TYPES:
        quality = _object_mapping(normalized.get("anchor_quality"))
        quality_score = _float(quality.get("score") if quality is not None else None, 0.0)
        if quality_score < 0.65:
            reasons.append(f"anchor_quality_below_live_threshold:{quality_score:.2f}")
    if str(normalized.get("type") or "") in OPERATOR_CHART_HIDDEN_TYPES and normalized_mode not in {"DIAGNOSTICS", "DEBUG", "INSPECTOR"}:
        reasons.append(f"operator_chart_hidden:{normalized['type']}")
    label_texts = {
        _canonical_token(normalized.get(key))
        for key in ("label", "role", "reason", "overlay_id", "object_id", "track_id")
        if normalized.get(key) is not None
    }
    disabled_prediction_label = any(
        token and any(token in text for text in label_texts)
        for token in PREDICTION_OVERLAY_DISABLED_LABEL_TOKENS
    )
    if (normalized["type"] in PREDICTION_OVERLAY_DISABLED_TYPES or disabled_prediction_label) and not (
        prediction_overlay_enabled() and normalized_mode in {"DIAGNOSTICS", "DEBUG", "INSPECTOR"}
    ):
        reasons.append("prediction_overlay_disabled")
    if normalized["type"] == "INVALIDATION_BOX" and normalized_mode not in {"DEBUG", "INSPECTOR"}:
        reasons.append("invalidation_overlay_disabled")
    raw_precision_flags = {_canonical_token(item) for item in _sequence(overlay.get("precision_flags"))}
    if normalized_mode == "CLEAN_LIVE" and "DUPLICATE_NOW_MAPPED_TO_HISTORY" in raw_precision_flags:
        reasons.append("historical_now_marker_hidden_from_clean_live")
    if str(normalized.get("lifecycle_state") or "").upper() in {"INVALIDATED", "STALE", "MERGED"}:
        reasons.append(f"lifecycle:{str(normalized.get('lifecycle_state') or '').upper()}")
    created = _float(normalized.get("created_at_ms"), 0.0)
    ttl = _float(normalized.get("ttl_ms"), 0.0)
    if now_ms is not None and raw_has_created_at and ttl > 0.0 and created + ttl < float(now_ms):
        reasons.append("expired_ttl")
    if normalized["type"] not in MODE_ALLOWED_TYPES[normalized_mode]:
        reasons.append(f"type_not_allowed:{normalized['type']}:{normalized_mode}")
    visible_modes = [normalize_view_mode(item) for item in _sequence(normalized.get("visible_modes"))]
    compatible_modes = MODE_VISIBLE_MODE_COMPATIBILITY.get(normalized_mode, {normalized_mode})
    if visible_modes and not (set(visible_modes) & compatible_modes):
        reasons.append(f"visible_modes_exclude:{normalized_mode}")
    layer_visibility = dict(MODE_LAYER_VISIBILITY[normalized_mode])
    if layer_overrides:
        layer_visibility.update({(_normalized_layer_value(key) or str(key)): bool(value) for key, value in layer_overrides.items()})
    layer = str(normalized.get("layer") or TYPE_LAYER_MAP.get(str(normalized.get("type") or ""), "diagnostics"))
    if not bool(layer_visibility.get(layer, True)):
        reasons.append(f"layer_hidden:{layer}:{normalized_mode}")
    return tuple(reasons)


def reason_if_empty(
    overlays: Sequence[Mapping[str, Any]] | None,
    *,
    mode: str | None = None,
    now_ms: int | float | None = None,
    layer_overrides: Mapping[str, bool] | None = None,
) -> str:
    rows = list(overlays or [])
    normalized_mode = normalize_view_mode(mode) if mode is not None else ""
    if not rows:
        return f"no_v3_overlay_objects:{normalized_mode}" if normalized_mode else "no_v3_overlay_objects"
    if mode is None:
        return ""

    rejection_counts: dict[str, int] = {}
    for row in rows:
        reasons = overlay_rejection_reasons(row, normalized_mode, now_ms=now_ms, layer_overrides=layer_overrides)
        if not reasons:
            return ""
        for reason in reasons:
            reason_key = reason.split(":", 1)[0]
            rejection_counts[reason_key] = rejection_counts.get(reason_key, 0) + 1
    reason_summary = ",".join(f"{key}={rejection_counts[key]}" for key in sorted(rejection_counts))
    return f"no_visible_v3_overlay_objects:{normalized_mode}:{reason_summary}"


def rectangles_overlap(first: Sequence[Any], second: Sequence[Any], *, padding: float = 0.0) -> bool:
    a = normalize_bounds(first)
    b = normalize_bounds(second)
    if a is None or b is None:
        return False
    pad = float(padding)
    return not (a[2] + pad <= b[0] or a[0] - pad >= b[2] or a[3] + pad <= b[1] or a[1] - pad >= b[3])


def abbreviate_label(label: str, *, max_words: int = 3) -> str:
    word_map = {
        "RECLAIM": "RECL",
        "TRIGGER": "TRIG",
        "CONTINUATION": "CONT",
        "RESISTANCE": "RES",
        "SUPPORT": "SUP",
        "TARGET": "TGT",
        "INVALIDATION": "INV",
    }
    stop_words = {"BOX", "ZONE", "AREA"}
    words = [
        word_map.get(word.upper(), word.upper())
        for word in str(label or "").split()
        if word.strip() and word.upper() not in stop_words
    ]
    return " ".join(words[:max_words])


def _label_size(label: str, chart_width: float) -> tuple[float, float]:
    abbreviated = abbreviate_label(label)
    width = min(max(48.0, len(abbreviated) * 6.5 + 14.0), max(56.0, chart_width * 0.42))
    return width, 18.0


def layout_overlay_labels(
    overlays: Sequence[Mapping[str, object]],
    *,
    chart_bounds: Sequence[object] | None = None,
    max_attempts_per_label: int = 10,
) -> list[dict[str, object]]:
    chart = normalize_bounds(chart_bounds or [0, 0, 1000, 700]) or [0.0, 0.0, 1000.0, 700.0]
    chart_width = max(1.0, chart[2] - chart[0])
    normalized: list[dict[str, object]] = []
    for overlay in overlays:
        row = normalize_v3_overlay_object(overlay, strict=False)
        for key, value in dict(overlay).items():
            row.setdefault(str(key), value)
        normalized.append(row)

    def priority(row: Mapping[str, object]) -> tuple[float, float, float]:
        return (
            float(overlay_type_priority(row.get("type"))) / 100.0,
            _float(row.get("truth_score", row.get("confidence", 0.0))),
            0.0 - _float(row.get("z_index"), 0.0),
        )

    placed: list[list[float]] = []
    output: list[dict[str, object]] = []
    for row in sorted(normalized, key=priority, reverse=True):
        bounds = normalize_bounds(row["bounds"]) or [0.0, 0.0, 0.0, 0.0]
        label = _text(row.get("short_label") or row.get("display_label"), abbreviate_label(str(row.get("label") or row.get("type") or "")))
        label_width, label_height = _label_size(label, chart_width)
        parent_style_candidates = [
            (bounds[0], bounds[1] - label_height - 4.0, "top"),
            (bounds[0] - label_width - 6.0, bounds[1], "left"),
            (bounds[2] + 6.0, bounds[1], "right"),
            (bounds[0], bounds[3] + 4.0, "bottom"),
            (bounds[0] + 3.0, bounds[1] + 3.0, "inside"),
            (bounds[0], bounds[1] - (label_height + 8.0) * 2.0, "top"),
            (bounds[0] + 26.0, bounds[1] - label_height - 4.0, "top"),
            (bounds[0] - 26.0, bounds[1] - label_height - 4.0, "top"),
            (bounds[2] + 6.0, bounds[1] + 22.0, "right"),
            (bounds[0] - label_width - 6.0, bounds[1] + 22.0, "left"),
        ]
        child_style_candidates = [
            (bounds[0] + 3.0, bounds[1] + 3.0, "inside"),
            (bounds[0], bounds[3] + 4.0, "bottom"),
            (bounds[2] + 6.0, bounds[1], "right"),
            (bounds[0] - label_width - 6.0, bounds[1], "left"),
            (bounds[0], bounds[1] - label_height - 4.0, "top"),
            (bounds[2] + 6.0, bounds[1] + 22.0, "right"),
            (bounds[0], bounds[3] + label_height + 8.0, "bottom"),
            (bounds[0] + 26.0, bounds[1] + 3.0, "inside"),
            (bounds[0], bounds[1] - (label_height + 8.0) * 2.0, "top"),
            (bounds[0] - 26.0, bounds[1] - label_height - 4.0, "top"),
        ]
        standard_candidates = [
            (bounds[0], bounds[1] - label_height - 4.0, "top"),
            (bounds[0], bounds[3] + 4.0, "bottom"),
            (bounds[2] + 6.0, bounds[1], "right"),
            (bounds[0] - label_width - 6.0, bounds[1], "left"),
            (bounds[0] + 3.0, bounds[1] + 3.0, "inside"),
            (bounds[0], bounds[1] - (label_height + 8.0) * 2.0, "top"),
            (bounds[0] + 26.0, bounds[1] - label_height - 4.0, "top"),
            (bounds[0] - 26.0, bounds[1] - label_height - 4.0, "top"),
            (bounds[2] + 6.0, bounds[1] + 22.0, "right"),
            (bounds[0] - label_width - 6.0, bounds[1] + 22.0, "left"),
        ]
        if _text(row.get("parent_overlay_id")) or _float(row.get("nesting_depth"), 0.0) > 0.0:
            candidates = child_style_candidates
        elif str(row.get("nesting_role") or "").lower() == "parent" or row.get("child_overlay_ids"):
            candidates = parent_style_candidates
        else:
            candidates = standard_candidates
        candidates = candidates[: max(1, int(max_attempts_per_label))]
        selected: list[float] | None = None
        anchor = "hidden"
        for left, top, candidate_anchor in candidates:
            clamped_left = min(max(chart[0], left), chart[2] - label_width)
            clamped_top = min(max(chart[1], top), chart[3] - label_height)
            candidate = [clamped_left, clamped_top, clamped_left + label_width, clamped_top + label_height]
            if not any(rectangles_overlap(candidate, existing, padding=2.0) for existing in placed):
                selected = candidate
                anchor = candidate_anchor
                break
        if selected is not None:
            placed.append(selected)
            row["label_bounds"] = [round(float(value), 3) for value in selected]
            row["label_anchor"] = anchor
            row["label_hidden"] = False
            row["display_label"] = label
            row["short_label"] = label
        else:
            row["label_bounds"] = []
            row["label_anchor"] = "hidden"
            row["label_hidden"] = True
            row["display_label"] = label
            row["short_label"] = label
        output.append(row)
    return output


def stack_overlay_labels(overlays: Sequence[Mapping[str, Any]], *, chart_bounds: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    return layout_overlay_labels(overlays, chart_bounds=chart_bounds)


def stack_overlay_label_lanes(overlays: Sequence[Mapping[str, Any]], *, proximity_px: float = 28.0) -> list[dict[str, Any]]:
    del proximity_px
    return layout_overlay_labels(overlays)


def resolve_visible_overlays(
    overlays: Sequence[Mapping[str, Any]],
    mode: str,
    *,
    now_ms: int | float | None = None,
    layer_overrides: Mapping[str, bool] | None = None,
    apply_label_layout: bool = True,
    chart_bounds: Sequence[Any] | None = None,
) -> list[dict[str, Any]]:
    visible = [
        normalize_v3_overlay_object(overlay, strict=False)
        for overlay in overlays
        if overlay_is_visible(overlay, mode, now_ms=now_ms, layer_overrides=layer_overrides)
    ]
    if apply_label_layout:
        return layout_overlay_labels(visible, chart_bounds=chart_bounds)
    return visible


def validate_overlay_payload(overlays: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    results = [validate_v3_overlay_object(overlay) for overlay in overlays]
    return {
        "schema_version": "PG_V3_OVERLAY_CONTRACT_AUDIT",
        "ok": all(result.ok for result in results),
        "count": len(results),
        "errors": [result.as_dict() for result in results if not result.ok],
        "required_types": list(OVERLAY_TYPES),
    }


__all__ = [
    "ANCHOR_EVIDENCE_TYPES",
    "ANCHOR_TYPES",
    "APPROVED_OVERLAY_DISPLAY_LABELS",
    "COORDINATE_MODES",
    "DIAGNOSTIC_OVERLAY_TYPES",
    "DIAGNOSTIC_VIEW_MODES",
    "DISPLAY_STATES",
    "HARD_ANCHOR_REQUIRED_TYPES",
    "LIVE_VIEW_MODES",
    "LIFECYCLE_STATES",
    "LEGACY_DISPLAY_LABEL_ALIASES",
    "MODE_ALLOWED_TYPES",
    "OVERLAY_LAYER_ORDER",
    "OVERLAY_TYPES",
    "OVERLAY_TYPE_PRIORITY",
    "OPERATOR_CHART_HIDDEN_TYPES",
    "PREDICTION_OVERLAY_DISABLED_LABEL_TOKENS",
    "PREDICTION_OVERLAY_DISABLED_REASON",
    "PREDICTION_OVERLAY_DISABLED_TYPES",
    "REQUIRED_FIELDS",
    "REQUIRED_V3_OVERLAY_FIELDS",
    "TYPE_LAYER_MAP",
    "TYPE_ROLE_MAP",
    "V3OverlayContractError",
    "V3_ANCHOR_TYPES",
    "V3_COORDINATE_MODES",
    "V3_DISPLAY_STATES",
    "V3_LIFECYCLE_STATES",
    "V3_OVERLAY_SCHEMA_VERSION",
    "V3_OVERLAY_TYPES",
    "V3_VISIBLE_MODES",
    "VIEW_MODES",
    "abbreviate_label",
    "approved_overlay_display_labels",
    "is_known_coordinate_mode",
    "is_known_overlay_type",
    "is_known_view_mode",
    "is_approved_overlay_display_label",
    "layout_overlay_labels",
    "normalize_bounds",
    "normalize_overlay_display_label",
    "normalize_overlay_type",
    "normalize_v3_overlay_object",
    "normalize_view_mode",
    "overlay_layer_name",
    "overlay_is_visible",
    "overlay_rejection_reasons",
    "overlay_type_priority",
    "prediction_overlay_config",
    "prediction_overlay_enabled",
    "rectangles_overlap",
    "reason_if_empty",
    "resolve_visible_overlays",
    "short_label_for_overlay",
    "stable_overlay_id",
    "stack_overlay_label_lanes",
    "stack_overlay_labels",
    "validate_overlay_payload",
    "validate_v3_overlay_object",
    "view_mode_profile",
]
