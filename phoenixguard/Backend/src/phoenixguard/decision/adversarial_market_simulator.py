from __future__ import annotations

from typing import Any, Callable, Mapping, cast


ADVERSARIAL_MARKET_SIMULATOR_VERSION = "ADVERSARIAL_MARKET_SIMULATOR_V1"

ADVERSARIAL_SCENARIOS: tuple[str, ...] = (
    "steep_impulse_then_reversal",
    "fake_breakout_above_resistance",
    "fake_breakdown_below_support",
    "range_chop_alternating_candles",
    "liquidity_sweep_snapback",
    "slow_grind_trend",
    "news_like_spike",
    "gap_like_candle_jump",
    "overlapping_middle_range",
    "strong_candle_into_opposing_zone",
)


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def build_adversarial_snapshot(name: str) -> dict[str, Any]:
    scenario = str(name or "").strip().lower()
    base: dict[str, Any] = {
        "scenario_name": scenario,
        "session_id": "adversarial-replay",
        "symbol": "EUR/GBP OTC",
        "timeframe": "M5",
        "candidate_side": "BUY",
        "buy_score": 0.78,
        "sell_score": 0.14,
        "context_confirmed": True,
        "timing": {"state": "READY", "expiry_seconds": 300},
        "market_context": {
            "dominant_side": "BUY",
            "global_side": "BUY",
            "local_side": "BUY",
            "inside_valid_trigger_zone": True,
            "opposing_force_distance_ok": True,
        },
        "runtime_model_health": {"all_required_models_awake": True, "council_status": "AWAKE"},
        "live_integrity": {
            "is_live": True,
            "frame_advancing": True,
            "capture_advancing": True,
            "state_advancing": True,
            "source": "model_council",
            "cache_status": "fresh",
            "input_frame_hash": f"hash_{scenario}",
            "previous_frame_hash": f"hash_{scenario}_previous",
            "packet_age_ms": 100,
        },
        "instrument_context": {
            "identity_state": "IDENTITY_CONFIRMED",
            "display_symbol": "EUR/GBP OTC",
            "ocr_symbol": "EUR/GBP OTC",
            "timeframe": "M5",
            "viewport_hash": "adversarial",
            "broker_surface_hash": "broker",
            "confidence": 0.9,
            "paper_safe": True,
            "broker_click_safe": False,
        },
        "frame_id": 10,
        "capture_count": 11,
        "state_version": 12,
        "input_frame_hash": f"hash_{scenario}",
        "packet_valid_for_seconds": 30.0,
        "execution_mature": True,
    }
    if scenario in {"steep_impulse_then_reversal", "news_like_spike", "gap_like_candle_jump"}:
        base["angle_context"] = {
            "angle_class": "STEEP_IMPULSE",
            "late_chase_risk": True,
            "post_impulse_wait_required": True,
            "steepness_z_score": 2.4,
        }
        base["history_context"] = {"similarity_state": "RESEMBLES_LATE_LOSS", "historical_late_entry_risk": "HIGH"}
    elif scenario == "strong_candle_into_opposing_zone":
        base["market_context"]["opposing_force_distance_ok"] = False
        base["risk_context"] = {"distance_to_opposing_force": 0.08, "minimum_required_distance": 0.22, "distance_ok": False}
    elif scenario in {"fake_breakout_above_resistance", "fake_breakdown_below_support", "liquidity_sweep_snapback"}:
        base["false_breakout_risk"] = True
        base["liquidity_sweep_detected"] = True
    elif scenario in {"range_chop_alternating_candles", "overlapping_middle_range"}:
        base["buy_score"] = 0.62
        base["sell_score"] = 0.59
        base["recent_sides"] = ["BUY", "SELL", "BUY"]
        base["market_context"]["current_location"] = "MIDDLE_DANGER"
    elif scenario == "slow_grind_trend":
        base["angle_context"] = {"angle_class": "HEALTHY_TREND", "late_chase_risk": False}
        base["pullback_confirmed"] = True
    return base


def expected_adversarial_outcome(name: str) -> str:
    scenario = str(name or "").strip().lower()
    if scenario == "slow_grind_trend":
        return "PREPARES_OR_EXECUTES_ONLY_IF_MATURE"
    return "WATCHING_OR_BLOCKED"


def run_adversarial_market_suite(evaluator: Callable[[Mapping[str, Any]], object]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    for name in ADVERSARIAL_SCENARIOS:
        snapshot = build_adversarial_snapshot(name)
        result = evaluator(snapshot)
        result_map = _mapping(result)
        council_map = _mapping(result_map.get("model_council"))
        final_state = str(
            council_map.get("final_state", "")
        ).upper()
        expected = expected_adversarial_outcome(name)
        passed = final_state in {"WATCHING", "BLOCKED_BY_MARKET", "BLOCKED_BY_RUNTIME", "CONFLICT"}
        if expected == "PREPARES_OR_EXECUTES_ONLY_IF_MATURE":
            passed = final_state in {"PREPARING", "EXECUTABLE", "WATCHING", "BLOCKED_BY_RUNTIME"}
        results.append({"scenario": name, "final_state": final_state, "expected": expected, "passed": passed})
    return {
        "version": ADVERSARIAL_MARKET_SIMULATOR_VERSION,
        "passed": all(row["passed"] for row in results),
        "results": results,
    }


__all__ = [
    "ADVERSARIAL_MARKET_SIMULATOR_VERSION",
    "ADVERSARIAL_SCENARIOS",
    "build_adversarial_snapshot",
    "expected_adversarial_outcome",
    "run_adversarial_market_suite",
]
