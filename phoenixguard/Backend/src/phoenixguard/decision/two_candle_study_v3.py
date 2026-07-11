from __future__ import annotations

import math
from statistics import median
from typing import Any, Mapping, Sequence, cast

from phoenixguard.decision.lstm_candle_sequence_contributor_v3 import (
    build_lstm_candle_sequence_contribution,
)


TWO_CANDLE_STUDY_SCHEMA_VERSION = "PG_TWO_CANDLE_STUDY_V3"
TEXT_AND_BANDS_ONLY = "TEXT_AND_BANDS_ONLY"
SIDES = {"BUY", "SELL"}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(number):
        return float(default)
    return float(number)


def _clip01(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _safe_float(value, default)))


def _side(value: Any, default: str = "HOLD") -> str:
    text = str(value or "").strip().upper()
    if text.startswith("BUY") or text in {"BULL", "BULLISH", "GREEN", "UP", "CALL"}:
        return "BUY"
    if text.startswith("SELL") or text in {"BEAR", "BEARISH", "RED", "MAGENTA", "DOWN", "PUT"}:
        return "SELL"
    if text in {"PAUSE", "HOLD", "WAIT", "READING"}:
        return "HOLD"
    return default


def _study_direction(value: Any, default: str = "READING") -> str:
    side = _side(value, "")
    return side if side in SIDES else default


def _opposite(side: str) -> str:
    return "SELL" if side == "BUY" else "BUY" if side == "SELL" else "HOLD"


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def _rows(value: Sequence[Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_mapping(row) for row in cast(Sequence[Any], value) if isinstance(row, Mapping)]


def _image_height(image_size: Any) -> float:
    if isinstance(image_size, Sequence) and not isinstance(image_size, (str, bytes, bytearray)):
        size = cast(Sequence[Any], image_size)
        if len(size) < 2:
            return 1.0
        return max(1.0, _safe_float(size[1], 1.0))
    return 1.0


def _normalize_candle(row: Mapping[str, Any], image_height: float) -> dict[str, Any] | None:
    bbox = row.get("bbox", [])
    if not isinstance(bbox, Sequence) or isinstance(bbox, (str, bytes, bytearray)):
        return None
    bbox_values = cast(Sequence[Any], bbox)
    if len(bbox_values) < 4:
        return None
    top_px = min(_safe_float(bbox_values[1]), _safe_float(bbox_values[3]))
    bottom_px = max(_safe_float(bbox_values[1]), _safe_float(bbox_values[3]))
    height = max(1.0, float(image_height))
    range_norm = max(0.001, (bottom_px - top_px) / height)
    direction = _side(row.get("direction") or row.get("color"), "HOLD")
    body_strength = _clip01(row.get("body_height_pct"), range_norm * 0.58)
    upper_wick = _clip01(row.get("upper_wick_pct"), max(0.0, range_norm - body_strength) * 0.5)
    lower_wick = _clip01(row.get("lower_wick_pct"), max(0.0, range_norm - body_strength) * 0.5)
    price_proxy = _clip01(row.get("price_proxy"), 1.0 - ((top_px + bottom_px) * 0.5 / height))
    return {
        "direction": direction,
        "body_strength": round(body_strength, 4),
        "wick_risk": round(_clip01(max(upper_wick, lower_wick) / max(0.001, range_norm)), 4),
        "upper_wick_ratio": round(_clip01(upper_wick / max(0.001, range_norm)), 4),
        "lower_wick_ratio": round(_clip01(lower_wick / max(0.001, range_norm)), 4),
        "range_norm": round(_clip01(range_norm), 4),
        "relative_price_location": round(price_proxy, 4),
        "track_id": row.get("track_id", row.get("id", "")),
        "source": "observed_live_chart",
    }


def _direction_run(candles: Sequence[Mapping[str, Any]]) -> int:
    if not candles:
        return 0
    latest = _side(candles[-1].get("direction"), "HOLD")
    if latest not in SIDES:
        return 0
    count = 0
    for row in reversed(candles):
        if _side(row.get("direction"), "HOLD") != latest:
            break
        count += 1
    return count


def _score_side(
    side: str,
    *,
    kernel: Mapping[str, Any],
    stats: Mapping[str, Any],
    global_side: str,
    local_side: str,
    impulse_side: str,
    candidate_side: str,
    continuation_probability: float,
    reversal_probability: float,
    lstm: Mapping[str, Any],
) -> float:
    side_key = "p_next_buy" if side == "BUY" else "p_next_sell"
    ratio_key = "recent_buy_ratio" if side == "BUY" else "recent_sell_ratio"
    lstm_side = _side(lstm.get("next_1_direction"), "HOLD")
    lstm_fresh = bool(lstm.get("fresh"))
    score = 0.30 * _clip01(kernel.get(side_key), 0.0)
    score += 0.16 * float(impulse_side == side)
    score += 0.15 * float(local_side == side)
    score += 0.09 * float(global_side == side)
    score += 0.08 * float(candidate_side == side)
    score += 0.10 * _clip01(stats.get(ratio_key), 0.0)
    score += 0.07 * continuation_probability * float(side in {local_side, impulse_side, candidate_side})
    score += 0.05 * reversal_probability * float(side == impulse_side and side != global_side)
    if lstm_fresh:
        score += 0.10 * _clip01(lstm.get("next_1_probability"), 0.0) * float(lstm_side == side)
    return max(0.0, float(score))


def _normalize_probs(buy: float, sell: float, wait: float) -> dict[str, float]:
    values = {"BUY": max(0.0, buy), "SELL": max(0.0, sell), "WAIT": max(0.0, wait)}
    total = sum(values.values())
    if total <= 1e-9:
        return {"BUY": 0.34, "SELL": 0.33, "WAIT": 0.33}
    return {key: float(value / total) for key, value in values.items()}


def _confidence_label(value: float) -> str:
    if value >= 0.72:
        return "HIGH"
    if value >= 0.58:
        return "USABLE"
    if value >= 0.44:
        return "EARLY"
    return "LOW"


def _range_expectation(baseline_range: float, continuation_probability: float, pullback_probability: float) -> str:
    if pullback_probability >= 0.48:
        return "tight-to-normal range while the retest/pullback settles"
    if continuation_probability >= 0.64 and baseline_range >= 0.012:
        return "normal-to-wide range if the trigger confirms"
    return "normal range unless a wick trap appears"


def _risk_label(pullback: float, reversal: float, wick_risk: float, run_length: int) -> str:
    if reversal >= 0.48:
        return "reversal attempt risk"
    if pullback >= 0.44:
        return "pullback first"
    if wick_risk >= 0.54:
        return "wick rejection risk"
    if run_length >= 4:
        return "extension pause risk"
    return "normal confirmation risk"


def _expected_play(side: str, pullback: float, reversal: float, continuation: float) -> str:
    if side == "BUY":
        if reversal >= 0.48:
            return "BULLISH_REACTION_TEST"
        if pullback >= 0.44:
            return "PULLBACK_THEN_BUY"
        return "BULLISH_CONTINUATION" if continuation >= 0.54 else "BUY_RETEST_FORMING"
    if side == "SELL":
        if reversal >= 0.48:
            return "BEARISH_REACTION_TEST"
        if pullback >= 0.44:
            return "PULLBACK_THEN_SELL"
        return "BEARISH_CONTINUATION" if continuation >= 0.54 else "SELL_RETEST_FORMING"
    return "STUDY_WARMING"


def _study_step(
    *,
    label: str,
    side: str,
    confidence: float,
    expected_play: str,
    risk: str,
    range_expectation: str,
    wick_risk: float,
    continuation_probability: float,
    reversal_probability: float,
    pullback_first_probability: float,
    reason: str,
) -> dict[str, Any]:
    direction = _study_direction(side)
    return {
        "label": label,
        "direction": direction,
        "direction_bias": direction,
        "confidence": round(_clip01(confidence), 4),
        "confidence_label": _confidence_label(confidence),
        "expected_play": expected_play,
        "risk": risk,
        "range_expectation": range_expectation,
        "wick_risk": round(_clip01(wick_risk), 4),
        "continuation_probability": round(_clip01(continuation_probability), 4),
        "reversal_probability": round(_clip01(reversal_probability), 4),
        "pullback_first_probability": round(_clip01(pullback_first_probability), 4),
        "display_as": TEXT_AND_BANDS_ONLY,
        "do_not_render_synthetic_candles": True,
        "story": reason,
    }


def build_two_candle_study_v3(
    *,
    candles: Sequence[Mapping[str, Any]],
    image_size: tuple[int, int] | Sequence[int] = (1, 1),
    timeframe: str = "",
    candidate_action: str = "HOLD",
    global_direction: str = "HOLD",
    local_direction: str = "HOLD",
    impulse_direction: str = "HOLD",
    decision_kernel: Mapping[str, Any] | None = None,
    candle_statistics: Mapping[str, Any] | None = None,
    behavior: Mapping[str, Any] | None = None,
    setup: str = "",
    frame_id: int | str = 0,
    sequence_id: str = "",
    lstm_contribution: Mapping[str, Any] | None = None,
    model_council: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rows = _rows(candles)
    image_height = _image_height(image_size)
    features = [item for item in (_normalize_candle(row, image_height) for row in rows) if item]
    kernel = _mapping(decision_kernel)
    stats = _mapping(candle_statistics)
    behavior_payload = _mapping(behavior)
    lstm = _mapping(lstm_contribution)
    if not lstm:
        lstm = build_lstm_candle_sequence_contribution(
            candles=rows,
            image_size=image_size,
            timeframe=timeframe,
            sequence_phase=str(behavior_payload.get("current_state") or setup or ""),
            market_play_label=setup,
        )
    council = _mapping(model_council)
    timeframe_label = str(timeframe or "").upper()
    pressure_seed = _side(impulse_direction or candidate_action, "HOLD")

    if len(features) < 5:
        summary = "Need at least five visible candles before the two-candle study is reliable."
        payload: dict[str, Any] = {
            "schema_version": TWO_CANDLE_STUDY_SCHEMA_VERSION,
            "status": "WARMING",
            "horizon_candles": 2,
            "timeframe": timeframe_label,
            "primary_pressure": pressure_seed if pressure_seed in SIDES else "READING",
            "confidence": 0.0,
            "summary": summary,
            "study_rows": [],
            "candle_forecasts": [],
            "signals": [],
            "lstm_contribution": lstm,
            "do_not_render_synthetic_candles": True,
            "diagnostics": {"visible_candles": len(features), "minimum_visible_candles": 5},
        }
        payload["two_candle_study"] = {
            "schema_version": TWO_CANDLE_STUDY_SCHEMA_VERSION,
            "status": "WARMING",
            "timeframe": timeframe_label,
            "frame_id": frame_id,
            "sequence_id": sequence_id or f"seq_{frame_id}",
            "current_candle_state": "WARMING",
            "last_completed_candle": features[-1] if features else {},
            "next_candle_forecast": {},
            "second_next_candle_forecast": {},
            "lstm_contribution": lstm,
            "summary": summary,
            "do_not_render_synthetic_candles": True,
        }
        return payload

    candidate_side = _side(candidate_action, "HOLD")
    global_side = _side(global_direction, "HOLD")
    local_side = _side(local_direction, "HOLD")
    impulse_side = _side(impulse_direction, "HOLD")
    recent_ranges = [_safe_float(row.get("range_norm"), 0.001) for row in features[-min(12, len(features)) :]]
    baseline_range = median(recent_ranges) if recent_ranges else 0.01
    run_length = int(stats.get("direction_run", 0) or _direction_run(rows))
    opposing_ratio = _clip01(stats.get("opposing_ratio"), 0.0)
    continuation_probability = _clip01(
        behavior_payload.get("continuation_score", stats.get("momentum_consistency", 0.0))
    )
    reversal_probability = _clip01(
        behavior_payload.get("reversal_score", behavior_payload.get("failure_risk", opposing_ratio))
    )
    consolidation_probability = _clip01(behavior_payload.get("consolidation_score"), opposing_ratio)
    pullback_first_probability = _clip01(
        0.24 * _clip01(kernel.get("p_next_hold"), consolidation_probability)
        + 0.26 * opposing_ratio
        + 0.18 * consolidation_probability
        + 0.16 * float(run_length >= 3)
        + 0.16 * _clip01(_mapping(lstm).get("pullback_first_probability"), 0.0)
    )
    buy_score = _score_side(
        "BUY",
        kernel=kernel,
        stats=stats,
        global_side=global_side,
        local_side=local_side,
        impulse_side=impulse_side,
        candidate_side=candidate_side,
        continuation_probability=continuation_probability,
        reversal_probability=reversal_probability,
        lstm=lstm,
    )
    sell_score = _score_side(
        "SELL",
        kernel=kernel,
        stats=stats,
        global_side=global_side,
        local_side=local_side,
        impulse_side=impulse_side,
        candidate_side=candidate_side,
        continuation_probability=continuation_probability,
        reversal_probability=reversal_probability,
        lstm=lstm,
    )
    probabilities = _normalize_probs(buy_score, sell_score, pullback_first_probability)
    pressure_side = "BUY" if probabilities["BUY"] >= probabilities["SELL"] else "SELL"
    opposite = _opposite(pressure_side)
    pressure_probability = probabilities[pressure_side]
    edge = abs(probabilities["BUY"] - probabilities["SELL"])
    sample_weight = _clip01(stats.get("sample_weight"), len(features) / 32.0)
    latest_observed = features[-1]
    last_completed = features[-2] if len(features) >= 2 else features[-1]
    wick_risk = max(
        _clip01(latest_observed.get("wick_risk"), 0.0),
        _clip01(last_completed.get("wick_risk"), 0.0),
        opposing_ratio * 0.74,
    )
    agreement = 0.0
    for value in (candidate_side, global_side, local_side, impulse_side, _side(council.get("side") or council.get("final_side"), "HOLD")):
        if value == pressure_side:
            agreement += 0.18
        elif value == opposite:
            agreement -= 0.10
    confidence = _clip01(
        0.24 * sample_weight
        + 0.22 * pressure_probability
        + 0.20 * edge
        + 0.14 * max(continuation_probability, reversal_probability)
        + 0.12 * max(0.0, 1.0 - pullback_first_probability)
        + 0.08 * max(0.0, agreement)
    )
    if pressure_side in SIDES and confidence < 0.38:
        confidence = _clip01(confidence + 0.08)
    next_risk = _risk_label(pullback_first_probability, reversal_probability, wick_risk, run_length)
    next_play = _expected_play(pressure_side, pullback_first_probability, reversal_probability, continuation_probability)
    next_range = _range_expectation(baseline_range, continuation_probability, pullback_first_probability)
    if reversal_probability >= 0.48 and probabilities[opposite] >= pressure_probability - 0.08:
        next_direction = opposite
        second_direction = pressure_side
        pattern = "reversal_test_then_main_pressure"
        second_risk = "needs reclaim/rejection confirmation"
    elif pullback_first_probability >= 0.42 or run_length >= 4:
        next_direction = pressure_side
        second_direction = pressure_side
        pattern = "pullback_or_pause_then_continue"
        second_risk = "lower confidence after first candle reaction"
    else:
        next_direction = pressure_side
        second_direction = pressure_side
        pattern = "two_candle_continuation_study"
        second_risk = "target or opposing-force reaction can interrupt"
    next_reason = (
        f"NEXT 1 studies {next_direction} pressure from observed candle grouping; "
        f"risk is {next_risk}."
    )
    second_confidence = _clip01(confidence - 0.08 - 0.10 * pullback_first_probability)
    second_play = _expected_play(second_direction, pullback_first_probability * 0.75, reversal_probability * 0.85, continuation_probability)
    second_reason = "NEXT 2 remains a study only; confidence drops because the first candle must confirm or reject first."
    next_step = _study_step(
        label="NEXT 1",
        side=next_direction,
        confidence=confidence,
        expected_play=next_play,
        risk=next_risk,
        range_expectation=next_range,
        wick_risk=wick_risk,
        continuation_probability=continuation_probability,
        reversal_probability=reversal_probability,
        pullback_first_probability=pullback_first_probability,
        reason=next_reason,
    )
    second_step = _study_step(
        label="NEXT 2",
        side=second_direction,
        confidence=second_confidence,
        expected_play=second_play,
        risk=second_risk,
        range_expectation=next_range,
        wick_risk=wick_risk,
        continuation_probability=continuation_probability * 0.92,
        reversal_probability=reversal_probability * 0.85,
        pullback_first_probability=pullback_first_probability * 0.78,
        reason=second_reason,
    )
    council_side = _side(council.get("side") or council.get("final_side") or kernel.get("dominant_side"), "HOLD")
    council_agreement: dict[str, Any] = {
        "side": council_side,
        "agrees": bool(council_side == pressure_side),
        "state": str(council.get("state") or council.get("final_state") or kernel.get("state") or "WATCHING").upper(),
    }
    summary = (
        f"NEXT 1: {next_step['direction_bias']} bias {round(confidence * 100):.0f}% | {next_risk}. "
        f"NEXT 2: {second_step['direction_bias']} bias {round(second_confidence * 100):.0f}% | {second_risk}. "
        "Study only; no synthetic candles are rendered."
    )
    two_candle_study: dict[str, Any] = {
        "schema_version": TWO_CANDLE_STUDY_SCHEMA_VERSION,
        "status": "READY",
        "timeframe": timeframe_label,
        "frame_id": frame_id,
        "sequence_id": sequence_id or f"seq_{frame_id}",
        "current_candle_state": "DEVELOPING",
        "last_completed_candle": last_completed,
        "current_developing_candle": latest_observed,
        "next_candle_forecast": next_step,
        "second_next_candle_forecast": second_step,
        "study_steps": [next_step, second_step],
        "primary_pressure": pressure_side,
        "pattern": pattern,
        "model_council_agreement": council_agreement,
        "lstm_contribution": lstm,
        "display_as": TEXT_AND_BANDS_ONLY,
        "do_not_render_synthetic_candles": True,
        "summary": summary,
    }
    signals: list[dict[str, Any]] = [
        {
            "name": "observed current pressure",
            "side": pressure_side,
            "confidence": round(confidence, 4),
            "meaning": "direction favoured by observed candle grouping, kernel, and live pressure evidence",
        },
        {
            "name": "pullback first risk",
            "side": "WAIT",
            "confidence": round(pullback_first_probability, 4),
            "meaning": "chance the next candle pauses/retests before continuation",
        },
        {
            "name": "lstm sequence contributor",
            "side": _side(lstm.get("side"), "HOLD"),
            "confidence": round(_clip01(lstm.get("confidence"), 0.0), 4),
            "meaning": str(lstm.get("interpretation") or "diagnostic LSTM contribution"),
            "fresh": bool(lstm.get("fresh")),
            "blocker": False,
        },
    ]
    return {
        "schema_version": TWO_CANDLE_STUDY_SCHEMA_VERSION,
        "status": "READY",
        "horizon_candles": 2,
        "timeframe": timeframe_label,
        "primary_pressure": pressure_side,
        "pattern": pattern,
        "confidence": round(confidence, 4),
        "confidence_label": _confidence_label(confidence),
        "summary": summary,
        "study_rows": [next_step, second_step],
        "candle_forecasts": [next_step, second_step],
        "two_candle_study": two_candle_study,
        "lstm_contribution": lstm,
        "signals": signals,
        "probabilities": {
            "buy": round(probabilities["BUY"], 4),
            "sell": round(probabilities["SELL"], 4),
            "pullback_or_wait": round(probabilities["WAIT"], 4),
        },
        "do_not_render_synthetic_candles": True,
        "diagnostics": {
            "visible_candles": len(features),
            "direction_run": run_length,
            "baseline_range_norm": round(float(baseline_range), 4),
            "opposing_ratio": round(opposing_ratio, 4),
            "continuation_probability": round(continuation_probability, 4),
            "reversal_probability": round(reversal_probability, 4),
            "pullback_first_probability": round(pullback_first_probability, 4),
            "model_council_agrees": council_agreement["agrees"],
        },
    }


__all__ = [
    "TEXT_AND_BANDS_ONLY",
    "TWO_CANDLE_STUDY_SCHEMA_VERSION",
    "build_two_candle_study_v3",
]
