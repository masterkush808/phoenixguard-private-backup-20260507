from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, cast

import pytest

from phoenixguard.decision.forecast_path_geometry_v3 import (
    FORECAST_HORIZON_STEPS,
    ForecastPathGeometryError,
    decode_forecast_path_geometry_v3,
)


def _anchor(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "x_norm": 0.40,
        "y_norm": 0.50,
        "price_norm": 0.50,
        "event_step_x_norm": 0.025,
        "verified_latest_close": True,
        "source": "TRACKER_LATEST_CLOSE",
    }
    payload.update(overrides)
    return payload


def _ohlc_quantiles() -> tuple[
    dict[str, list[float]],
    dict[str, list[float]],
    dict[str, list[float]],
]:
    base = [
        0.52,
        0.49,
        0.535,
        0.505,
        0.55,
        0.525,
        0.565,
        0.54,
        0.575,
        0.555,
        0.59,
        0.57,
    ]
    close = {
        "p10": [value - 0.02 for value in base],
        "p50": list(base),
        "p90": [value + 0.02 for value in base],
    }
    upper: dict[str, list[float]] = {}
    lower: dict[str, list[float]] = {}
    for key, trajectory in close.items():
        prior = 0.50
        upper[key] = []
        lower[key] = []
        for value in trajectory:
            upper[key].append(max(prior, value) + 0.008)
            lower[key].append(min(prior, value) - 0.008)
            prior = value
    return close, upper, lower


def _pointwise_median(trajectories: Sequence[Sequence[float]]) -> list[float]:
    return [
        sorted(trajectory[step] for trajectory in trajectories)[len(trajectories) // 2]
        for step in range(FORECAST_HORIZON_STEPS)
    ]


def _projected_prices(bundle: dict[str, Any]) -> list[float]:
    anchor_value = bundle["forecast_anchor"]
    points_value = bundle["line_points"]
    assert isinstance(anchor_value, dict)
    assert isinstance(points_value, list)
    anchor = cast(dict[str, Any], anchor_value)
    points = cast(list[list[float]], points_value)
    anchor_y = float(anchor["y_norm"])
    anchor_price = float(anchor["price_norm"])
    return [
        anchor_price - (float(point[1]) - anchor_y)
        for point in points[1:]
    ]


def _assert_scenario_candles_match_lines(bundle: dict[str, Any]) -> None:
    anchor_value = bundle["forecast_anchor"]
    scenarios_value = bundle["forecast_scenarios"]
    assert isinstance(anchor_value, dict)
    assert isinstance(scenarios_value, list)
    anchor = cast(dict[str, Any], anchor_value)
    scenarios = cast(list[dict[str, Any]], scenarios_value)
    anchor_y = float(anchor["y_norm"])

    for scenario in scenarios:
        points = cast(list[list[float]], scenario["line_points"])
        candles = cast(list[dict[str, Any]], scenario["forecast_candles"])
        assert len(points) == FORECAST_HORIZON_STEPS + 1
        assert len(candles) == FORECAST_HORIZON_STEPS
        assert math.isclose(points[0][1], anchor_y)

        endpoint_delta = anchor_y - float(points[-1][1])
        expected_path_side = (
            "BUY" if endpoint_delta > 0.0 else "SELL" if endpoint_delta < 0.0 else "HOLD"
        )
        assert scenario["side"] == expected_path_side

        for index, candle in enumerate(candles):
            assert candle["step"] == index + 1
            assert math.isclose(candle["x_norm"], points[index + 1][0])
            assert math.isclose(candle["open_y_norm"], points[index][1])
            assert math.isclose(candle["close_y_norm"], points[index + 1][1])
            candle_price_delta = float(points[index][1]) - float(points[index + 1][1])
            expected_candle_side = (
                "BUY"
                if candle_price_delta > 0.0
                else "SELL"
                if candle_price_delta < 0.0
                else "HOLD"
            )
            assert candle["movement_side"] == expected_candle_side
            assert candle["body_bias"] == expected_candle_side
            assert candle["high_y_norm"] <= min(
                candle["open_y_norm"], candle["close_y_norm"]
            )
            assert candle["low_y_norm"] >= max(
                candle["open_y_norm"], candle["close_y_norm"]
            )


def test_quantile_decoder_emits_atomic_connected_v3_geometry() -> None:
    close, upper, lower = _ohlc_quantiles()

    result = decode_forecast_path_geometry_v3(
        anchor=_anchor(),
        close_quantiles=close,
        upper_quantiles=upper,
        lower_quantiles=lower,
        calibrated=True,
        calibration_method="walk_forward_conformal",
    )

    points = cast(list[list[float]], result["line_points"])
    candles = cast(list[dict[str, Any]], result["forecast_candles"])
    scenarios = cast(list[dict[str, Any]], result["forecast_scenarios"])
    assert isinstance(points, list)
    assert isinstance(candles, list)
    assert isinstance(scenarios, list)
    assert len(points) == 13
    assert len(candles) == 12
    assert len(scenarios) == 3
    assert points[0] == [0.40, 0.50]
    assert all(right[0] > left[0] for left, right in zip(points, points[1:]))
    assert {scenario["role"] for scenario in scenarios} == {"bull", "base", "bear"}
    assert {scenario["side"] for scenario in scenarios} == {"BUY"}
    assert {scenario["label"] for scenario in scenarios} == {
        "MEDOID PATH",
        "UPPER PATH",
        "LOWER PATH",
    }
    assert sum(bool(scenario["selected"]) for scenario in scenarios) == 1
    assert all(scenario["line_points"][0] == points[0] for scenario in scenarios)
    assert all(len(scenario["forecast_candles"]) == 12 for scenario in scenarios)
    base_scenario = next(scenario for scenario in scenarios if scenario["role"] == "base")
    assert base_scenario["forecast_candles"] == candles
    _assert_scenario_candles_match_lines(result)
    assert result["forecast_band_points"]
    assert set(result["forecast_quantiles"]) == {"p10", "p50", "p90"}
    assert result["interval"] == {
        "status": "READY",
        "calibrated": True,
        "method": "WALK_FORWARD_CONFORMAL",
        "lower_quantile": 0.10,
        "center_quantile": 0.50,
        "upper_quantile": 0.90,
        "nominal_coverage": 0.80,
    }
    assert result["visual_amplification_applied"] is False
    assert result["geometry_gain"] == 1.0

    assert [row["step"] for row in candles] == list(range(1, 13))
    assert {row["movement_side"] for row in candles} >= {"BUY", "SELL"}
    for candle in candles:
        assert candle["high_y_norm"] <= min(
            candle["open_y_norm"], candle["close_y_norm"]
        )
        assert candle["low_y_norm"] >= max(
            candle["open_y_norm"], candle["close_y_norm"]
        )
        assert candle["interval_top_y_norm"] <= candle["interval_bottom_y_norm"]


def test_quantile_scenario_directions_come_only_from_their_endpoints() -> None:
    close = {
        "p10": [0.48] * FORECAST_HORIZON_STEPS,
        "p50": [0.50] * FORECAST_HORIZON_STEPS,
        "p90": [0.52] * FORECAST_HORIZON_STEPS,
    }
    upper: dict[str, list[float]] = {}
    lower: dict[str, list[float]] = {}
    for key, trajectory in close.items():
        prior = 0.50
        upper[key] = []
        lower[key] = []
        for value in trajectory:
            upper[key].append(max(prior, value) + 0.005)
            lower[key].append(min(prior, value) - 0.005)
            prior = value

    result = decode_forecast_path_geometry_v3(
        anchor=_anchor(),
        close_quantiles=close,
        upper_quantiles=upper,
        lower_quantiles=lower,
        calibrated=False,
    )

    scenario_rows = cast(list[dict[str, Any]], result["forecast_scenarios"])
    scenarios = {str(row["role"]): row for row in scenario_rows}
    assert scenarios["bull"]["side"] == "BUY"
    assert scenarios["base"]["side"] == "HOLD"
    assert scenarios["bear"]["side"] == "SELL"
    _assert_scenario_candles_match_lines(result)


def test_uncalibrated_quantiles_never_publish_an_envelope() -> None:
    close, upper, lower = _ohlc_quantiles()

    result = decode_forecast_path_geometry_v3(
        anchor=_anchor(),
        close_quantiles=close,
        upper_quantiles=upper,
        lower_quantiles=lower,
        calibrated=False,
    )

    assert result["forecast_band_points"] == []
    assert result["forecast_quantiles"] == {}
    assert result["interval"]["status"] == "UNAVAILABLE"
    assert result["interval"]["calibrated"] is False
    assert all(
        "interval_top_y_norm" not in candle
        for candle in result["forecast_candles"]
    )


def test_sample_decoder_selects_a_coherent_medoid_not_pointwise_medians() -> None:
    sample_a = [0.40, 0.60] * 6
    sample_b = [0.50, 0.40] * 6
    sample_c = [0.60, 0.50] * 6
    samples = [sample_a, sample_b, sample_c]
    independent_median = _pointwise_median(samples)
    assert independent_median == [0.50] * 12

    result = decode_forecast_path_geometry_v3(
        anchor=_anchor(y_norm=0.50, price_norm=0.50),
        sampled_trajectories=samples,
        calibrated=False,
    )

    selected_prices = _projected_prices(result)
    assert result["source_mode"] == "SAMPLED_TRAJECTORY_MEDOIDS"
    assert result["selected_sample_index"] == 1
    assert all(
        math.isclose(actual, expected)
        for actual, expected in zip(selected_prices, sample_b, strict=True)
    )
    assert not all(
        math.isclose(actual, expected)
        for actual, expected in zip(selected_prices, independent_median, strict=True)
    )
    forecast_candles = cast(list[dict[str, Any]], result["forecast_candles"])
    assert {row["movement_side"] for row in forecast_candles} >= {
        "BUY",
        "SELL",
    }
    forecast_scenarios = cast(list[dict[str, Any]], result["forecast_scenarios"])
    assert math.isclose(
        sum(float(scenario["probability"]) for scenario in forecast_scenarios),
        1.0,
    )


def test_sample_scenarios_keep_their_own_connected_ohlc_paths() -> None:
    bear = [0.49 - 0.01 * index for index in range(FORECAST_HORIZON_STEPS)]
    base = [0.505 if index % 2 == 0 else 0.50 for index in range(FORECAST_HORIZON_STEPS)]
    bull = [0.51 + 0.01 * index for index in range(FORECAST_HORIZON_STEPS)]

    def sampled_ohlc(close: list[float]) -> dict[str, list[float]]:
        prior = 0.50
        high: list[float] = []
        low: list[float] = []
        for value in close:
            high.append(max(prior, value) + 0.004)
            low.append(min(prior, value) - 0.006)
            prior = value
        return {"close": close, "high": high, "low": low}

    result = decode_forecast_path_geometry_v3(
        anchor=_anchor(),
        sampled_trajectories=[
            sampled_ohlc(bear),
            sampled_ohlc(base),
            sampled_ohlc(bull),
        ],
        calibrated=False,
    )

    scenario_rows = cast(list[dict[str, Any]], result["forecast_scenarios"])
    scenarios = {str(row["role"]): row for row in scenario_rows}
    assert scenarios["bull"]["side"] == "BUY"
    assert scenarios["base"]["side"] == "HOLD"
    assert scenarios["bear"]["side"] == "SELL"
    assert result["forecast_candles"] == scenarios["base"]["forecast_candles"]
    _assert_scenario_candles_match_lines(result)


def test_sampled_calibrated_envelope_contains_the_coherent_medoid() -> None:
    shape = [
        0.51,
        0.49,
        0.52,
        0.50,
        0.53,
        0.51,
        0.54,
        0.52,
        0.55,
        0.53,
        0.56,
        0.54,
    ]
    samples = [[value + offset for value in shape] for offset in (-0.04, -0.02, 0, 0.02, 0.04)]

    result = decode_forecast_path_geometry_v3(
        anchor=_anchor(),
        sampled_trajectories=samples,
        calibrated=True,
    )

    quantiles = cast(dict[str, list[list[float]]], result["forecast_quantiles"])
    assert result["selected_sample_index"] == 2
    assert result["forecast_band_points"]
    for step in range(1, 13):
        # Higher normalized price becomes a smaller normalized chart y.
        assert quantiles["p90"][step][1] <= quantiles["p50"][step][1]
        assert quantiles["p50"][step][1] <= quantiles["p10"][step][1]


def test_decoder_preserves_true_displacement_without_monotonic_forcing() -> None:
    close, upper, lower = _ohlc_quantiles()
    result = decode_forecast_path_geometry_v3(
        anchor=_anchor(),
        close_quantiles=close,
        upper_quantiles=upper,
        lower_quantiles=lower,
        calibrated=False,
    )

    points = cast(list[list[float]], result["line_points"])
    for step, expected_price in enumerate(close["p50"], start=1):
        expected_y = 0.50 - (expected_price - 0.50)
        assert math.isclose(points[step][1], expected_y)
    deltas = [right[1] - left[1] for left, right in zip(points, points[1:])]
    assert any(delta > 0.0 for delta in deltas)
    assert any(delta < 0.0 for delta in deltas)


def test_shared_viewport_fit_preserves_all_routes_turns_and_ohlc_without_flat_tail() -> None:
    base = [
        0.12,
        0.06,
        0.09,
        -0.02,
        -0.09,
        -0.06,
        -0.18,
        -0.14,
        -0.27,
        -0.24,
        -0.36,
        -0.31,
    ]
    close = {
        "p10": [value - 0.12 for value in base],
        "p50": base,
        "p90": [value + 0.60 for value in base],
    }
    upper: dict[str, list[float]] = {}
    lower: dict[str, list[float]] = {}
    for key, trajectory in close.items():
        prior = 0.18
        upper[key] = []
        lower[key] = []
        for value in trajectory:
            upper[key].append(max(prior, value) + 0.01)
            lower[key].append(min(prior, value) - 0.01)
            prior = value

    result = decode_forecast_path_geometry_v3(
        anchor=_anchor(y_norm=0.82, price_norm=0.18),
        close_quantiles=close,
        upper_quantiles=upper,
        lower_quantiles=lower,
        calibrated=True,
    )

    gain = float(result["geometry_gain"])
    assert 0.0 < gain < 1.0
    assert result["viewport_fit_applied"] is True
    assert result["visual_amplification_applied"] is False
    assert result["geometry_transform"] == {
        "mode": "SHARED_ANCHOR_AFFINE_FIT",
        "anchor_y_norm": 0.82,
        "gain": gain,
        "viewport_top_norm": 0.035,
        "viewport_bottom_norm": 0.965,
        "pointwise_clipping_applied": False,
    }

    scenarios = {
        str(row["role"]): row
        for row in cast(list[dict[str, Any]], result["forecast_scenarios"])
    }
    assert {role: row["side"] for role, row in scenarios.items()} == {
        "base": "SELL",
        "bull": "BUY",
        "bear": "SELL",
    }
    source_by_role = {"base": close["p50"], "bull": close["p90"], "bear": close["p10"]}
    for role, scenario in scenarios.items():
        points = cast(list[list[float]], scenario["line_points"])
        candles = cast(list[dict[str, Any]], scenario["forecast_candles"])
        source = source_by_role[role]
        assert points[0] == [0.40, 0.82]
        assert len(points) == 13
        assert len(candles) == 12
        assert all(0.035 - 1e-12 <= point[1] <= 0.965 + 1e-12 for point in points)
        assert len({round(point[1], 12) for point in points[-8:]}) > 4
        for index, price in enumerate(source, start=1):
            expected_y = 0.82 - (price - 0.18) * gain
            assert math.isclose(points[index][1], expected_y)
        source_deltas = [right - left for left, right in zip([0.18, *source], source)]
        chart_deltas = [right[1] - left[1] for left, right in zip(points, points[1:])]
        assert [math.copysign(1.0, value) for value in chart_deltas] == [
            -math.copysign(1.0, value) for value in source_deltas
        ]
        assert all(
            0.0 <= float(candle[field]) <= 1.0
            for candle in candles
            for field in (
                "open_y_norm",
                "high_y_norm",
                "low_y_norm",
                "close_y_norm",
            )
        )

    base_candles = cast(list[dict[str, Any]], scenarios["base"]["forecast_candles"])
    for index, candle in enumerate(base_candles):
        expected_high = 0.82 - (upper["p50"][index] - 0.18) * gain
        expected_low = 0.82 - (lower["p50"][index] - 0.18) * gain
        assert math.isclose(float(candle["high_y_norm"]), expected_high)
        assert math.isclose(float(candle["low_y_norm"]), expected_low)

    _assert_scenario_candles_match_lines(result)


def test_crossed_quantiles_fail_atomically() -> None:
    close, upper, lower = _ohlc_quantiles()
    close["p10"][4] = close["p50"][4] + 0.01

    with pytest.raises(ForecastPathGeometryError, match="quantiles cross"):
        decode_forecast_path_geometry_v3(
            anchor=_anchor(),
            close_quantiles=close,
            upper_quantiles=upper,
            lower_quantiles=lower,
            calibrated=True,
        )


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("incomplete", "exactly 12"),
        ("nonfinite", "finite number"),
        ("crossed_ohlc", "lower/close/upper geometry crosses"),
    ],
)
def test_incomplete_nonfinite_or_crossed_ohlc_quantiles_fail_atomically(
    mutation: str,
    message: str,
) -> None:
    close, upper, lower = _ohlc_quantiles()
    if mutation == "incomplete":
        close["p90"] = close["p90"][:-1]
    elif mutation == "nonfinite":
        close["p50"][2] = math.nan
    else:
        lower["p50"][3] = close["p50"][3] + 0.01

    with pytest.raises(ForecastPathGeometryError, match=message):
        decode_forecast_path_geometry_v3(
            anchor=_anchor(),
            close_quantiles=close,
            upper_quantiles=upper,
            lower_quantiles=lower,
            calibrated=True,
        )


def test_incomplete_sample_bundle_and_event_slot_overflow_fail_atomically() -> None:
    with pytest.raises(ForecastPathGeometryError, match="at least three"):
        decode_forecast_path_geometry_v3(
            anchor=_anchor(),
            sampled_trajectories=[[0.50] * 12, [0.51] * 12],
        )

    close, upper, lower = _ohlc_quantiles()
    with pytest.raises(ForecastPathGeometryError, match="do not fit"):
        decode_forecast_path_geometry_v3(
            anchor=_anchor(x_norm=0.90, event_step_x_norm=0.02),
            close_quantiles=close,
            upper_quantiles=upper,
            lower_quantiles=lower,
        )
