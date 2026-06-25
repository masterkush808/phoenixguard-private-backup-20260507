from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from phoenixguard.simulation.synthetic_scenarios.generator import (
    DEFAULT_SYMBOL,
    DEFAULT_TIMEFRAME,
    export_scenarios_json,
    generate_synthetic_market_scenario,
)


ADVERSARIAL_TEST_VERSION = "PHOENIX_ADVERSARIAL_TESTS_V1"

ADVERSARIAL_TEST_CATEGORIES: tuple[str, ...] = (
    "steep_impulse_trap",
    "fake_breakout_trap",
    "fake_breakdown_trap",
    "range_chop_trap",
    "buy_into_supply",
    "sell_into_demand",
    "mid_range_no_edge",
    "liquidity_sweep_reversal",
    "angle_break_after_vertical_move",
    "pullback_that_becomes_full_reversal",
    "continuation_after_valid_retest",
    "middle_safe_continuation",
)

_LEGACY_CATEGORY_ALIASES: dict[str, str] = {
    "steep_impulse_then_reversal": "steep_impulse_trap",
    "fake_breakout_above_resistance": "fake_breakout_trap",
    "fake_breakdown_below_support": "fake_breakdown_trap",
    "range_chop_alternating_candles": "range_chop_trap",
    "liquidity_sweep_snapback": "liquidity_sweep_reversal",
    "slow_grind_trend": "continuation_after_valid_retest",
    "news_like_spike": "steep_impulse_trap",
    "gap_like_candle_jump": "angle_break_after_vertical_move",
    "overlapping_middle_range": "mid_range_no_edge",
    "strong_candle_into_opposing_zone": "buy_into_supply",
    "volatility_compression_breakout_trap": "fake_breakout_trap",
    "stale_frame_replay_signal": "mid_range_no_edge",
}

_CASE_BLUEPRINTS: dict[str, dict[str, Any]] = {
    "steep_impulse_trap": {
        "synthetic_category": "steep_bullish_impulse_then_reversal",
        "expected_action": "HOLD",
        "safe_actions": ("HOLD",),
        "expected_gate": "BLOCK_LATE_CHASE",
        "risk_tags": ("late_chase", "impulse_exhaustion", "snapback_risk"),
    },
    "fake_breakout_trap": {
        "synthetic_category": "fake_breakout",
        "expected_action": "HOLD",
        "safe_actions": ("HOLD",),
        "expected_gate": "BLOCK_FALSE_BREAKOUT",
        "risk_tags": ("false_breakout", "resistance_rejection", "liquidity_grab"),
    },
    "fake_breakdown_trap": {
        "synthetic_category": "fake_breakdown",
        "expected_action": "HOLD",
        "safe_actions": ("HOLD",),
        "expected_gate": "BLOCK_FALSE_BREAKDOWN",
        "risk_tags": ("false_breakdown", "support_sweep", "snapback_risk"),
    },
    "range_chop_trap": {
        "synthetic_category": "range_chop",
        "expected_action": "HOLD",
        "safe_actions": ("HOLD",),
        "expected_gate": "BLOCK_RANGE_CHOP",
        "risk_tags": ("alternating_candles", "low_directional_edge", "middle_range"),
    },
    "buy_into_supply": {
        "synthetic_category": "buy_into_supply",
        "expected_action": "HOLD",
        "safe_actions": ("HOLD",),
        "expected_gate": "BLOCK_BUY_INTO_SUPPLY",
        "risk_tags": ("buy_into_supply", "opposing_zone_nearby", "distance_fail"),
    },
    "sell_into_demand": {
        "synthetic_category": "sell_into_demand",
        "expected_action": "HOLD",
        "safe_actions": ("HOLD",),
        "expected_gate": "BLOCK_SELL_INTO_DEMAND",
        "risk_tags": ("sell_into_demand", "opposing_zone_nearby", "distance_fail"),
    },
    "mid_range_no_edge": {
        "synthetic_category": "middle_danger_entry",
        "expected_action": "HOLD",
        "safe_actions": ("HOLD",),
        "expected_gate": "BLOCK_MID_RANGE_NO_EDGE",
        "risk_tags": ("mid_range", "no_clean_location", "weak_edge"),
    },
    "liquidity_sweep_reversal": {
        "synthetic_category": "liquidity_sweep_then_snapback",
        "expected_action": "HOLD",
        "safe_actions": ("HOLD",),
        "expected_gate": "BLOCK_SWEEP_REVERSAL",
        "risk_tags": ("liquidity_sweep", "snapback", "wick_trap"),
    },
    "angle_break_after_vertical_move": {
        "synthetic_category": "angle_break_after_vertical_move",
        "expected_action": "HOLD",
        "safe_actions": ("HOLD",),
        "expected_gate": "BLOCK_ANGLE_BREAK_AFTER_VERTICAL_MOVE",
        "risk_tags": ("vertical_move", "angle_break", "late_chase"),
    },
    "pullback_that_becomes_full_reversal": {
        "synthetic_category": "impulse_reversal",
        "expected_action": "HOLD",
        "safe_actions": ("HOLD",),
        "expected_gate": "BLOCK_PULLBACK_BECOMES_REVERSAL",
        "risk_tags": ("failed_pullback", "full_reversal", "dominance_flip"),
    },
    "continuation_after_valid_retest": {
        "synthetic_category": "trend_continuation_after_retest",
        "expected_action": "BUY",
        "safe_actions": ("HOLD", "BUY"),
        "expected_gate": "ALLOW_ONLY_IF_MATURE",
        "risk_tags": ("valid_retest", "maturity_required", "continuation"),
        "requires_maturity": True,
    },
    "middle_safe_continuation": {
        "synthetic_category": "middle_safe_continuation",
        "expected_action": "BUY",
        "safe_actions": ("HOLD", "BUY"),
        "expected_gate": "ALLOW_MIDDLE_SAFE_CONTINUATION",
        "risk_tags": ("middle_safe", "continuation", "distance_ok"),
        "requires_maturity": True,
    },
}


def normalize_adversarial_category(category: str) -> str:
    normalized = str(category or "").strip().lower().replace("-", "_").replace(" ", "_")
    normalized = _LEGACY_CATEGORY_ALIASES.get(normalized, normalized)
    if normalized not in _CASE_BLUEPRINTS:
        allowed = ", ".join(ADVERSARIAL_TEST_CATEGORIES)
        raise ValueError(f"unknown adversarial category {category!r}; expected one of: {allowed}")
    return normalized


def build_adversarial_case(
    category: str,
    *,
    seed: int = 0,
    frame_count: int = 48,
    symbol: str = DEFAULT_SYMBOL,
    timeframe: str = DEFAULT_TIMEFRAME,
) -> dict[str, Any]:
    """Build one adversarial offline test case with dictionary frames, labels, and expected metadata."""
    normalized = normalize_adversarial_category(category)
    blueprint = _CASE_BLUEPRINTS[normalized]
    scenario_id = f"adversarial:{normalized}:{int(seed)}:{int(frame_count)}"

    scenario = generate_synthetic_market_scenario(
        str(blueprint["synthetic_category"]),
        seed=seed,
        frame_count=frame_count,
        symbol=symbol,
        timeframe=timeframe,
        scenario_id=scenario_id,
    )
    case = copy.deepcopy(scenario)
    case["version"] = ADVERSARIAL_TEST_VERSION
    case["category"] = normalized
    case["synthetic_source_category"] = str(blueprint["synthetic_category"])
    case["adversarial"] = {
        "category": normalized,
        "risk_tags": list(blueprint["risk_tags"]),
        "expected_gate": str(blueprint["expected_gate"]),
        "requires_maturity": bool(blueprint.get("requires_maturity", False)),
    }
    case["labels"] = _adversarial_labels(case["labels"], normalized, blueprint)
    _annotate_frames(case["frames"], normalized, blueprint)
    case["expected"] = _expected_metadata(normalized, blueprint, len(case["frames"]))
    case["metadata"]["adversarial_test"] = True
    case["metadata"]["adversarial_version"] = ADVERSARIAL_TEST_VERSION
    case["metadata"]["broker_actions_allowed"] = False
    case["metadata"]["offline_only"] = True
    return case


def build_adversarial_suite(
    categories: Iterable[str] | None = None,
    *,
    seed: int = 0,
    frame_count: int = 48,
    symbol: str = DEFAULT_SYMBOL,
    timeframe: str = DEFAULT_TIMEFRAME,
) -> list[dict[str, Any]]:
    selected = tuple(categories) if categories is not None else ADVERSARIAL_TEST_CATEGORIES
    cases: list[dict[str, Any]] = []
    for index, category in enumerate(selected):
        cases.append(
            build_adversarial_case(
                category,
                seed=int(seed) + index,
                frame_count=frame_count,
                symbol=symbol,
                timeframe=timeframe,
            )
        )
    return cases


def export_adversarial_suite_json(
    cases: Mapping[str, Any] | Sequence[Mapping[str, Any]],
    path: str | Path,
    *,
    indent: int = 2,
) -> Path:
    return export_scenarios_json(cases, path, indent=indent)


def _adversarial_labels(
    labels: Sequence[Mapping[str, Any]],
    category: str,
    blueprint: Mapping[str, Any],
) -> list[dict[str, Any]]:
    expected_action = str(blueprint["expected_action"]).upper()
    safe_actions = {str(action).upper() for action in blueprint["safe_actions"]}
    risk_tags = list(blueprint["risk_tags"])
    adjusted: list[dict[str, Any]] = []

    for raw_label in labels:
        label = dict(raw_label)
        if expected_action == "HOLD":
            label["target_action"] = "HOLD"
            label["target_direction"] = "HOLD"
            label["trade_allowed"] = False
            label["confidence"] = max(float(label.get("confidence", 0.55)), 0.82)
        elif category == "slow_grind_trend":
            label["trade_allowed"] = label.get("target_action") == expected_action
            label["confidence"] = max(float(label.get("confidence", 0.60)), 0.68)
        label["adversarial_category"] = category
        label["safe_actions"] = sorted(safe_actions)
        label["unsafe_actions"] = sorted({"HOLD", "BUY", "SELL"} - safe_actions)
        label["expected_gate"] = str(blueprint["expected_gate"])
        label["risk_tags"] = risk_tags
        adjusted.append(label)
    return adjusted


def _annotate_frames(
    frames: Sequence[Mapping[str, Any]],
    category: str,
    blueprint: Mapping[str, Any],
) -> None:
    frame_total = len(frames)
    midpoint = frame_total // 2
    risk_tags = list(blueprint["risk_tags"])
    for index, raw_frame in enumerate(frames):
        frame = raw_frame if isinstance(raw_frame, dict) else dict(raw_frame)
        features = frame.setdefault("features", {})
        features["adversarial_category"] = category
        features["risk_tags"] = risk_tags
        features["expected_gate"] = str(blueprint["expected_gate"])
        features["adversarial_pressure"] = _pressure(index, frame_total)
        if category == "stale_frame_replay_signal" and index >= midpoint:
            features["stale_frame"] = True
            features["source_frame_index"] = max(0, index - 3)
            features["timestamp_skew_frames"] = 3
        else:
            features["stale_frame"] = False
            features["source_frame_index"] = index
            features["timestamp_skew_frames"] = 0


def _expected_metadata(category: str, blueprint: Mapping[str, Any], frame_count: int) -> dict[str, Any]:
    safe_actions = [str(action).upper() for action in blueprint["safe_actions"]]
    expected_action = str(blueprint["expected_action"]).upper()
    should_block = expected_action == "HOLD"
    return {
        "adversarial_category": category,
        "expected_action": expected_action,
        "safe_actions": safe_actions,
        "unsafe_actions": sorted({"HOLD", "BUY", "SELL"} - set(safe_actions)),
        "expected_gate": str(blueprint["expected_gate"]),
        "risk_tags": list(blueprint["risk_tags"]),
        "requires_maturity": bool(blueprint.get("requires_maturity", False)),
        "should_block": should_block,
        "frame_count": int(frame_count),
        "offline_only": True,
        "broker_actions_allowed": False,
        "should_trigger_broker_action": False,
    }


def _pressure(index: int, frame_count: int) -> float:
    if frame_count <= 1:
        return 1.0
    centered = abs((index / (frame_count - 1)) - 0.5)
    return round(1.0 - min(centered * 2.0, 1.0), 4)


__all__ = [
    "ADVERSARIAL_TEST_CATEGORIES",
    "ADVERSARIAL_TEST_VERSION",
    "build_adversarial_case",
    "build_adversarial_suite",
    "export_adversarial_suite_json",
    "normalize_adversarial_category",
]
