from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any, cast


FORECAST_HORIZON_STEPS = 12
_QUANTILE_KEYS = ("p10", "p50", "p90")
_EPSILON = 1e-9
_VIEWPORT_PADDING = 0.035


class ForecastPathGeometryError(ValueError):
    """Raised when a complete, truthful V3 path bundle cannot be decoded."""


def _unit_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool):
        raise ForecastPathGeometryError(f"{label} must be a finite normalized number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ForecastPathGeometryError(
            f"{label} must be a finite normalized number"
        ) from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise ForecastPathGeometryError(f"{label} must be within [0, 1]")
    return number


def _positive_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool):
        raise ForecastPathGeometryError(f"{label} must be a positive finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ForecastPathGeometryError(
            f"{label} must be a positive finite number"
        ) from exc
    if not math.isfinite(number) or number <= 0.0:
        raise ForecastPathGeometryError(f"{label} must be a positive finite number")
    return number


def _finite_number(value: Any, *, label: str) -> float:
    if isinstance(value, bool):
        raise ForecastPathGeometryError(f"{label} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ForecastPathGeometryError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ForecastPathGeometryError(f"{label} must be a finite number")
    return number


def _series(value: object, *, label: str) -> tuple[float, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ForecastPathGeometryError(
            f"{label} must contain exactly {FORECAST_HORIZON_STEPS} values"
        )
    sequence = cast(Sequence[Any], value)
    if len(sequence) != FORECAST_HORIZON_STEPS:
        raise ForecastPathGeometryError(
            f"{label} must contain exactly {FORECAST_HORIZON_STEPS} values"
        )
    return tuple(
        _finite_number(item, label=f"{label}[{index}]")
        for index, item in enumerate(sequence)
    )


def _quantile_field(value: object, *, label: str) -> dict[str, tuple[float, ...]]:
    if not isinstance(value, Mapping):
        raise ForecastPathGeometryError(
            f"{label} must provide p10, p50, and p90 trajectories"
        )
    quantiles = cast(Mapping[str, Any], value)
    result: dict[str, tuple[float, ...]] = {}
    for key in _QUANTILE_KEYS:
        if key not in quantiles:
            raise ForecastPathGeometryError(f"{label}.{key} is required")
        result[key] = _series(quantiles[key], label=f"{label}.{key}")
    for step in range(FORECAST_HORIZON_STEPS):
        if not (
            result["p10"][step]
            <= result["p50"][step]
            <= result["p90"][step]
        ):
            raise ForecastPathGeometryError(
                f"{label} quantiles cross at forecast event {step + 1}"
            )
    return result


def _validate_ohlc_quantiles(
    close: Mapping[str, Sequence[float]],
    upper: Mapping[str, Sequence[float]],
    lower: Mapping[str, Sequence[float]],
) -> None:
    for quantile in _QUANTILE_KEYS:
        for step in range(FORECAST_HORIZON_STEPS):
            if not (
                lower[quantile][step]
                <= close[quantile][step]
                <= upper[quantile][step]
            ):
                raise ForecastPathGeometryError(
                    "lower/close/upper geometry crosses at "
                    f"{quantile} forecast event {step + 1}"
                )


def _anchor_values(anchor: Mapping[str, Any]) -> tuple[float, float, float, float]:
    x_norm = _unit_number(anchor.get("x_norm"), label="anchor.x_norm")
    y_norm = _unit_number(anchor.get("y_norm"), label="anchor.y_norm")
    price_norm = _unit_number(anchor.get("price_norm"), label="anchor.price_norm")
    step_x = _positive_number(
        anchor.get("event_step_x_norm", anchor.get("step_x_norm")),
        label="anchor.event_step_x_norm",
    )
    if x_norm + FORECAST_HORIZON_STEPS * step_x > 1.0 + _EPSILON:
        raise ForecastPathGeometryError("twelve event slots do not fit on the chart plane")
    return x_norm, y_norm, price_norm, step_x


def _viewport_bounds(anchor_y: float) -> tuple[float, float]:
    # Keep ordinary anchors away from the canvas edge. An anchor already inside
    # that padding remains exact, so its nearest physical edge becomes the
    # truthful bound on movement in that direction.
    top = 0.0 if anchor_y < _VIEWPORT_PADDING else _VIEWPORT_PADDING
    bottom = 1.0 if anchor_y > 1.0 - _VIEWPORT_PADDING else 1.0 - _VIEWPORT_PADDING
    return top, bottom


def _shared_viewport_gain(
    price_fields: Sequence[Sequence[float]],
    *,
    anchor_price: float,
    anchor_y: float,
) -> tuple[float, tuple[float, float]]:
    """Return one anchor-preserving scale for every drawable price field.

    A model trajectory is allowed to leave the currently visible price plane.
    Fitting the complete bundle once preserves every turn, route separation,
    and OHLC relationship. Pointwise clipping cannot: it maps every off-plane
    value to the same edge and creates the misleading flat tail this decoder is
    specifically responsible for preventing.
    """

    top, bottom = _viewport_bounds(anchor_y)
    raw_y_values = [
        anchor_y - (price - anchor_price)
        for field in price_fields
        for price in field
    ]
    if not raw_y_values:
        return 1.0, (top, bottom)

    minimum_delta = min(raw_y_values) - anchor_y
    maximum_delta = max(raw_y_values) - anchor_y
    candidates = [1.0]
    if minimum_delta < -_EPSILON:
        available_up = anchor_y - top
        if available_up <= _EPSILON:
            raise ForecastPathGeometryError(
                "forecast cannot preserve upward movement from the chart's top edge"
            )
        candidates.append(available_up / -minimum_delta)
    if maximum_delta > _EPSILON:
        available_down = bottom - anchor_y
        if available_down <= _EPSILON:
            raise ForecastPathGeometryError(
                "forecast cannot preserve downward movement from the chart's bottom edge"
            )
        candidates.append(available_down / maximum_delta)
    gain = min(candidates)
    if not math.isfinite(gain) or gain <= 0.0:
        raise ForecastPathGeometryError("forecast viewport scale must be positive")
    return min(1.0, gain), (top, bottom)


def _project_price_y(
    price: float,
    *,
    anchor_price: float,
    anchor_y: float,
    geometry_gain: float,
) -> float:
    # Price increases upward while chart y increases downward. The same affine
    # gain is applied to every line, interval, and OHLC point in the bundle.
    projected = anchor_y - (price - anchor_price) * geometry_gain
    if projected < -_EPSILON or projected > 1.0 + _EPSILON:
        raise ForecastPathGeometryError(
            "forecast price cannot be projected onto the normalized chart plane"
        )
    if abs(projected) <= _EPSILON:
        return 0.0
    if abs(projected - 1.0) <= _EPSILON:
        return 1.0
    return projected


def _line_points(
    prices: Sequence[float],
    *,
    anchor_x: float,
    anchor_y: float,
    anchor_price: float,
    step_x: float,
    geometry_gain: float,
) -> list[list[float]]:
    points = [[anchor_x, anchor_y]]
    for index, price in enumerate(prices, start=1):
        points.append(
            [
                anchor_x + step_x * index,
                _project_price_y(
                    price,
                    anchor_price=anchor_price,
                    anchor_y=anchor_y,
                    geometry_gain=geometry_gain,
                ),
            ]
        )
    if len(points) != FORECAST_HORIZON_STEPS + 1 or points[0] != [anchor_x, anchor_y]:
        raise ForecastPathGeometryError("forecast path does not preserve its exact anchor")
    if any(right[0] <= left[0] for left, right in zip(points, points[1:])):
        raise ForecastPathGeometryError("forecast event slots must be strictly increasing")
    return points


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(abs(a - b) for a, b in zip(left, right))


def _medoid_index(
    closes: Sequence[Sequence[float]],
    candidates: Sequence[int],
    references: Sequence[int] | None = None,
) -> int:
    reference_indices = tuple(references if references is not None else range(len(closes)))
    if not candidates or not reference_indices:
        raise ForecastPathGeometryError("a trajectory medoid cannot be selected")
    return min(
        candidates,
        key=lambda candidate: (
            sum(
                _distance(closes[candidate], closes[reference])
                for reference in reference_indices
            ),
            candidate,
        ),
    )


def _point_quantile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower_index = int(math.floor(position))
    upper_index = int(math.ceil(position))
    if lower_index == upper_index:
        return ordered[lower_index]
    fraction = position - lower_index
    return ordered[lower_index] * (1.0 - fraction) + ordered[upper_index] * fraction


def _empirical_close_quantiles(
    closes: Sequence[Sequence[float]],
) -> dict[str, tuple[float, ...]]:
    return {
        key: tuple(
            _point_quantile(
                [trajectory[step] for trajectory in closes],
                probability,
            )
            for step in range(FORECAST_HORIZON_STEPS)
        )
        for key, probability in (("p10", 0.10), ("p50", 0.50), ("p90", 0.90))
    }


def _sample_row(
    value: object,
    *,
    index: int,
    anchor_price: float,
) -> dict[str, tuple[float, ...]]:
    if isinstance(value, Mapping):
        sample = cast(Mapping[str, Any], value)
        close_value = sample.get("close", sample.get("trajectory"))
        upper_value = sample.get("upper", sample.get("high"))
        lower_value = sample.get("lower", sample.get("low"))
    else:
        close_value = value
        upper_value = None
        lower_value = None
    close = _series(close_value, label=f"sampled_trajectories[{index}].close")
    if (upper_value is None) != (lower_value is None):
        raise ForecastPathGeometryError(
            f"sampled_trajectories[{index}] must provide both upper and lower or neither"
        )
    if upper_value is not None and lower_value is not None:
        upper = _series(upper_value, label=f"sampled_trajectories[{index}].upper")
        lower = _series(lower_value, label=f"sampled_trajectories[{index}].lower")
    else:
        prior = anchor_price
        upper_values: list[float] = []
        lower_values: list[float] = []
        for close_value_item in close:
            upper_values.append(max(prior, close_value_item))
            lower_values.append(min(prior, close_value_item))
            prior = close_value_item
        upper = tuple(upper_values)
        lower = tuple(lower_values)
    for step in range(FORECAST_HORIZON_STEPS):
        if not lower[step] <= close[step] <= upper[step]:
            raise ForecastPathGeometryError(
                f"sampled_trajectories[{index}] has crossed OHLC geometry at event {step + 1}"
            )
    return {"close": close, "upper": upper, "lower": lower}


def _sample_scenario_indices(
    closes: Sequence[Sequence[float]],
    *,
    base_index: int,
) -> tuple[int, int]:
    ordered = sorted(
        range(len(closes)),
        key=lambda index: (closes[index][-1], sum(closes[index]), index),
    )
    bucket_size = max(1, math.ceil(len(ordered) / 3.0))
    bear_pool = [index for index in ordered[:bucket_size] if index != base_index]
    bull_pool = [index for index in ordered[-bucket_size:] if index != base_index]
    available = [index for index in ordered if index != base_index]
    if not bear_pool:
        bear_pool = available[:1]
    if not bull_pool:
        bull_pool = available[-1:]
    bear_index = _medoid_index(closes, bear_pool, bear_pool)
    bull_index = _medoid_index(closes, bull_pool, bull_pool)
    if bull_index == bear_index:
        alternatives = [index for index in available if index != bear_index]
        if not alternatives:
            raise ForecastPathGeometryError("bull and bear scenarios require distinct samples")
        bull_index = max(
            alternatives,
            key=lambda index: (closes[index][-1], sum(closes[index]), -index),
        )
    return bull_index, bear_index


def _cluster_probabilities(
    closes: Sequence[Sequence[float]],
    *,
    base_index: int,
    bull_index: int,
    bear_index: int,
) -> dict[int, float]:
    medoids = (base_index, bull_index, bear_index)
    counts = {index: 0 for index in medoids}
    for trajectory in closes:
        selected = min(
            medoids,
            key=lambda index: (_distance(trajectory, closes[index]), medoids.index(index)),
        )
        counts[selected] += 1
    total = float(len(closes))
    return {index: counts[index] / total for index in medoids}


def _movement_side(delta: float) -> str:
    if delta > 0.0:
        return "BUY"
    if delta < 0.0:
        return "SELL"
    return "HOLD"


def _path_side(prices: Sequence[float], anchor_price: float) -> str:
    return _movement_side(prices[-1] - anchor_price)


def _forecast_candles(
    *,
    close_prices: Sequence[float],
    high_prices: Sequence[float],
    low_prices: Sequence[float],
    line_points: Sequence[Sequence[float]],
    anchor_price: float,
    anchor_y: float,
    geometry_gain: float,
    interval_top_points: Sequence[Sequence[float]] | None = None,
    interval_bottom_points: Sequence[Sequence[float]] | None = None,
) -> list[dict[str, Any]]:
    """Build connected OHLC candles for exactly one coherent scenario path."""

    if not (
        len(close_prices)
        == len(high_prices)
        == len(low_prices)
        == FORECAST_HORIZON_STEPS
        and len(line_points) == FORECAST_HORIZON_STEPS + 1
    ):
        raise ForecastPathGeometryError(
            "scenario OHLC geometry must contain exactly twelve forecast events"
        )
    if (interval_top_points is None) != (interval_bottom_points is None):
        raise ForecastPathGeometryError(
            "scenario intervals must provide both top and bottom paths or neither"
        )
    if interval_top_points is not None and interval_bottom_points is not None:
        if not (
            len(interval_top_points)
            == len(interval_bottom_points)
            == FORECAST_HORIZON_STEPS + 1
        ):
            raise ForecastPathGeometryError(
                "scenario interval geometry must contain the anchor and twelve events"
            )

    candles: list[dict[str, Any]] = []
    prior_close = anchor_price
    for index in range(FORECAST_HORIZON_STEPS):
        close_price = close_prices[index]
        high_price = high_prices[index]
        low_price = low_prices[index]
        if not low_price <= min(prior_close, close_price):
            raise ForecastPathGeometryError(
                "scenario low does not contain the candle body at "
                f"forecast event {index + 1}"
            )
        if not high_price >= max(prior_close, close_price):
            raise ForecastPathGeometryError(
                "scenario high does not contain the candle body at "
                f"forecast event {index + 1}"
            )

        movement = _movement_side(close_price - prior_close)
        candle: dict[str, Any] = {
            "step": index + 1,
            "label": f"E{index + 1}",
            "x_norm": line_points[index + 1][0],
            "open_y_norm": line_points[index][1],
            "high_y_norm": _project_price_y(
                high_price,
                anchor_price=anchor_price,
                anchor_y=anchor_y,
                geometry_gain=geometry_gain,
            ),
            "low_y_norm": _project_price_y(
                low_price,
                anchor_price=anchor_price,
                anchor_y=anchor_y,
                geometry_gain=geometry_gain,
            ),
            "close_y_norm": line_points[index + 1][1],
            "movement_side": movement,
            "body_bias": movement,
            "direction_conflict": False,
        }
        if interval_top_points is not None and interval_bottom_points is not None:
            candle.update(
                {
                    "interval_top_y_norm": interval_top_points[index + 1][1],
                    "interval_bottom_y_norm": interval_bottom_points[index + 1][1],
                }
            )
        if not (
            candle["high_y_norm"]
            <= min(candle["open_y_norm"], candle["close_y_norm"])
            <= max(candle["open_y_norm"], candle["close_y_norm"])
            <= candle["low_y_norm"]
        ):
            raise ForecastPathGeometryError(
                f"projected OHLC geometry is invalid at forecast event {index + 1}"
            )
        candles.append(candle)
        prior_close = close_price
    return candles


def decode_forecast_path_geometry_v3(
    *,
    anchor: Mapping[str, Any],
    close_quantiles: Mapping[str, Sequence[object]] | None = None,
    upper_quantiles: Mapping[str, Sequence[object]] | None = None,
    lower_quantiles: Mapping[str, Sequence[object]] | None = None,
    sampled_trajectories: Sequence[object] | None = None,
    calibrated: bool = False,
    calibration_method: str = "",
) -> dict[str, Any]:
    """Decode a complete, coherent 12-event V3 forecast geometry bundle.

    Inputs are chart-relative *price* locations: a larger value means a higher
    market price, and complete trajectories may extend beyond the currently
    visible unit plane. ``anchor`` must provide ``x_norm``, ``y_norm``,
    ``price_norm`` and ``event_step_x_norm``. One shared anchor-preserving
    affine gain fits every scenario, interval, and OHLC point into the viewport
    when necessary. The decoder never forces monotonic paths, enlarges small
    displacements, or clips individual events.

    Quantile mode requires p10/p50/p90 mappings for close, upper (high), and
    lower (low).  Sample mode requires at least three complete trajectories;
    its base path is an observed trajectory medoid, never a pointwise median.
    Any invalid component aborts before a public bundle is returned.
    """

    anchor_x, anchor_y, anchor_price, step_x = _anchor_values(anchor)
    has_quantiles = any(
        value is not None
        for value in (close_quantiles, upper_quantiles, lower_quantiles)
    )
    has_samples = sampled_trajectories is not None
    if has_quantiles == has_samples:
        raise ForecastPathGeometryError(
            "provide either complete quantiles or sampled trajectories, but not both"
        )

    selected_sample_index: int | None = None
    scenario_probabilities: dict[str, float]
    if has_quantiles:
        close = _quantile_field(close_quantiles, label="close_quantiles")
        upper = _quantile_field(upper_quantiles, label="upper_quantiles")
        lower = _quantile_field(lower_quantiles, label="lower_quantiles")
        _validate_ohlc_quantiles(close, upper, lower)
        base_close = close["p50"]
        base_upper = upper["p50"]
        base_lower = lower["p50"]
        bull_close = close["p90"]
        bull_upper = upper["p90"]
        bull_lower = lower["p90"]
        bear_close = close["p10"]
        bear_upper = upper["p10"]
        bear_lower = lower["p10"]
        envelope_close = close
        source_mode = "QUANTILE_CURVES"
        scenario_probabilities = {"base": 0.0, "bull": 0.0, "bear": 0.0}
    else:
        if not isinstance(sampled_trajectories, Sequence) or isinstance(
            sampled_trajectories, (str, bytes, bytearray)
        ):
            raise ForecastPathGeometryError("sampled_trajectories must be a sequence")
        if len(sampled_trajectories) < 3:
            raise ForecastPathGeometryError(
                "at least three complete sampled trajectories are required"
            )
        samples = [
            _sample_row(value, index=index, anchor_price=anchor_price)
            for index, value in enumerate(sampled_trajectories)
        ]
        closes = [sample["close"] for sample in samples]
        empirical = _empirical_close_quantiles(closes)
        if calibrated:
            eligible = [
                index
                for index, trajectory in enumerate(closes)
                if all(
                    empirical["p10"][step] - _EPSILON
                    <= trajectory[step]
                    <= empirical["p90"][step] + _EPSILON
                    for step in range(FORECAST_HORIZON_STEPS)
                )
            ]
            if not eligible:
                raise ForecastPathGeometryError(
                    "no sampled trajectory forms a coherent calibrated central path"
                )
            base_index = _medoid_index(closes, eligible)
        else:
            base_index = _medoid_index(closes, tuple(range(len(closes))))
        bull_index, bear_index = _sample_scenario_indices(
            closes,
            base_index=base_index,
        )
        selected_sample_index = base_index
        base_close = samples[base_index]["close"]
        base_upper = samples[base_index]["upper"]
        base_lower = samples[base_index]["lower"]
        bull_close = samples[bull_index]["close"]
        bull_upper = samples[bull_index]["upper"]
        bull_lower = samples[bull_index]["lower"]
        bear_close = samples[bear_index]["close"]
        bear_upper = samples[bear_index]["upper"]
        bear_lower = samples[bear_index]["lower"]
        envelope_close = {
            "p10": empirical["p10"],
            "p50": base_close,
            "p90": empirical["p90"],
        }
        if calibrated:
            for step in range(FORECAST_HORIZON_STEPS):
                if not (
                    envelope_close["p10"][step]
                    <= envelope_close["p50"][step]
                    <= envelope_close["p90"][step]
                ):
                    raise ForecastPathGeometryError(
                        "sampled calibrated envelope crosses its medoid at "
                        f"forecast event {step + 1}"
                    )
        probabilities = _cluster_probabilities(
            closes,
            base_index=base_index,
            bull_index=bull_index,
            bear_index=bear_index,
        )
        scenario_probabilities = {
            "base": probabilities[base_index],
            "bull": probabilities[bull_index],
            "bear": probabilities[bear_index],
        }
        source_mode = "SAMPLED_TRAJECTORY_MEDOIDS"

    drawable_price_fields: list[Sequence[float]] = [
        base_close,
        base_upper,
        base_lower,
        bull_close,
        bull_upper,
        bull_lower,
        bear_close,
        bear_upper,
        bear_lower,
    ]
    if calibrated:
        drawable_price_fields.extend(envelope_close[key] for key in _QUANTILE_KEYS)
    geometry_gain, viewport_bounds = _shared_viewport_gain(
        drawable_price_fields,
        anchor_price=anchor_price,
        anchor_y=anchor_y,
    )

    base_points = _line_points(
        base_close,
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        anchor_price=anchor_price,
        step_x=step_x,
        geometry_gain=geometry_gain,
    )
    bull_points = _line_points(
        bull_close,
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        anchor_price=anchor_price,
        step_x=step_x,
        geometry_gain=geometry_gain,
    )
    bear_points = _line_points(
        bear_close,
        anchor_x=anchor_x,
        anchor_y=anchor_y,
        anchor_price=anchor_price,
        step_x=step_x,
        geometry_gain=geometry_gain,
    )

    quantile_points: dict[str, list[list[float]]] = {}
    band_points: list[list[float]] = []
    if calibrated:
        quantile_points = {
            key: _line_points(
                envelope_close[key],
                anchor_x=anchor_x,
                anchor_y=anchor_y,
                anchor_price=anchor_price,
                step_x=step_x,
                geometry_gain=geometry_gain,
            )
            for key in _QUANTILE_KEYS
        }
        # p90 is the upper price path and therefore the smaller chart-y path.
        band_points = [
            *[list(point) for point in quantile_points["p90"]],
            *[list(point) for point in reversed(quantile_points["p10"])],
            list(quantile_points["p90"][0]),
        ]

    forecast_candles = _forecast_candles(
        close_prices=base_close,
        high_prices=base_upper,
        low_prices=base_lower,
        line_points=base_points,
        anchor_price=anchor_price,
        anchor_y=anchor_y,
        geometry_gain=geometry_gain,
        interval_top_points=(quantile_points.get("p90") if calibrated else None),
        interval_bottom_points=(quantile_points.get("p10") if calibrated else None),
    )
    bull_forecast_candles = _forecast_candles(
        close_prices=bull_close,
        high_prices=bull_upper,
        low_prices=bull_lower,
        line_points=bull_points,
        anchor_price=anchor_price,
        anchor_y=anchor_y,
        geometry_gain=geometry_gain,
    )
    bear_forecast_candles = _forecast_candles(
        close_prices=bear_close,
        high_prices=bear_upper,
        low_prices=bear_lower,
        line_points=bear_points,
        anchor_price=anchor_price,
        anchor_y=anchor_y,
        geometry_gain=geometry_gain,
    )

    base_side = _path_side(base_close, anchor_price)
    bull_side = _path_side(bull_close, anchor_price)
    bear_side = _path_side(bear_close, anchor_price)
    scenarios: list[dict[str, Any]] = [
        {
            "side": base_side,
            "role": "base",
            "label": "MEDOID PATH",
            "probability": scenario_probabilities["base"],
            "probability_calibrated": False,
            "selected": True,
            "line_points": base_points,
            "forecast_candles": [dict(candle) for candle in forecast_candles],
            "event_count": FORECAST_HORIZON_STEPS,
        },
        {
            "side": bull_side,
            "role": "bull",
            "label": "UPPER PATH",
            "probability": scenario_probabilities["bull"],
            "probability_calibrated": False,
            "selected": False,
            "line_points": bull_points,
            "forecast_candles": bull_forecast_candles,
            "event_count": FORECAST_HORIZON_STEPS,
        },
        {
            "side": bear_side,
            "role": "bear",
            "label": "LOWER PATH",
            "probability": scenario_probabilities["bear"],
            "probability_calibrated": False,
            "selected": False,
            "line_points": bear_points,
            "forecast_candles": bear_forecast_candles,
            "event_count": FORECAST_HORIZON_STEPS,
        },
    ]
    if any(scenario["line_points"][0] != [anchor_x, anchor_y] for scenario in scenarios):
        raise ForecastPathGeometryError("all forecast scenarios must share the exact anchor")

    return {
        "schema_version": "PG_FORECAST_PATH_GEOMETRY_V3",
        "status": "READY",
        "source_mode": source_mode,
        "horizon_steps": FORECAST_HORIZON_STEPS,
        "horizon_unit": "CANDLE_EVENTS",
        "clock_time_assumption": "NONE",
        "forecast_anchor": {
            "x_norm": anchor_x,
            "y_norm": anchor_y,
            "price_norm": anchor_price,
            "verified_latest_close": bool(anchor.get("verified_latest_close", False)),
            "source": str(anchor.get("source") or "MODEL_CAUSAL_CANDLE").upper(),
        },
        "line_points": base_points,
        "forecast_candles": forecast_candles,
        "forecast_scenarios": scenarios,
        "forecast_band_points": band_points,
        "forecast_quantiles": quantile_points,
        "interval": {
            "status": "READY" if calibrated else "UNAVAILABLE",
            "calibrated": calibrated,
            "method": (
                str(calibration_method or "EXTERNAL_CALIBRATED_QUANTILES").upper()
                if calibrated
                else "UNAVAILABLE"
            ),
            "lower_quantile": 0.10 if calibrated else None,
            "center_quantile": 0.50 if calibrated else None,
            "upper_quantile": 0.90 if calibrated else None,
            "nominal_coverage": 0.80 if calibrated else None,
        },
        "path_side": base_side,
        "selected_scenario_role": "base",
        "selected_sample_index": selected_sample_index,
        "visual_amplification_applied": False,
        "viewport_fit_applied": geometry_gain < 1.0 - _EPSILON,
        "geometry_gain": geometry_gain,
        "geometry_transform": {
            "mode": "SHARED_ANCHOR_AFFINE_FIT",
            "anchor_y_norm": anchor_y,
            "gain": geometry_gain,
            "viewport_top_norm": viewport_bounds[0],
            "viewport_bottom_norm": viewport_bounds[1],
            "pointwise_clipping_applied": False,
        },
    }


__all__ = [
    "FORECAST_HORIZON_STEPS",
    "ForecastPathGeometryError",
    "decode_forecast_path_geometry_v3",
]
