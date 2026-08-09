"""Observation-only latent-state discovery for PhoenixGuard V3.

The contract produced here has no strategy, setup, zone, indicator, blocker, or
execution input. It studies closed-candle sequence state and Pair DNA history.
Temporal association is retained as a hypothesis, never promoted to causality.
"""

from __future__ import annotations

from collections import defaultdict
from math import isfinite, log2, sqrt
from statistics import median
from typing import Any, Iterable, Mapping, Sequence, cast


LATENT_STATE_DISCOVERY_SCHEMA_VERSION = "PG_LATENT_STATE_DISCOVERY_V3"
MIN_TRANSITION_SUPPORT = 3
MIN_CONTROL_STATE_AGE_CANDLES = 3
UP_STATE = "UP_SWING"
DOWN_STATE = "DOWN_SWING"
REST_STATE = "REST"
_DIRECTIONAL_STATES = {UP_STATE, DOWN_STATE}


def _mapping(value: Any) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return cast(Sequence[Any], value)
    return ()


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if isfinite(result) else None


def _rounded(value: float | None, digits: int = 6) -> float | None:
    return round(value, digits) if value is not None and isfinite(value) else None


def _count(value: Any) -> int:
    direct = _number(value)
    if direct is not None:
        return max(0, int(direct))
    record = _mapping(value)
    for key in ("count", "support", "observations", "sample_count", "n"):
        parsed = _number(record.get(key))
        if parsed is not None:
            return max(0, int(parsed))
    return 0


def _find(value: Any, key: str, depth: int = 0) -> Any:
    if depth > 6:
        return None
    record = _mapping(value)
    if key in record:
        return record[key]
    for child in record.values():
        if isinstance(child, Mapping):
            found = _find(child, key, depth + 1)
            if found is not None:
                return found
    return None


def _state(value: Any) -> str:
    raw = (
        str(value or "")
        .strip()
        .upper()
        .replace("-", "_")
        .replace(" ", "_")
    )
    aliases = {
        "UP": UP_STATE,
        "BUY": UP_STATE,
        "BULL": UP_STATE,
        "BULLISH": UP_STATE,
        "UPTREND": UP_STATE,
        "UP_MOVE": UP_STATE,
        "DOWN": DOWN_STATE,
        "SELL": DOWN_STATE,
        "BEAR": DOWN_STATE,
        "BEARISH": DOWN_STATE,
        "DOWNTREND": DOWN_STATE,
        "DOWN_MOVE": DOWN_STATE,
        "PAUSE": REST_STATE,
        "STALL": REST_STATE,
        "STALLED": REST_STATE,
        "RANGE": REST_STATE,
        "SIDEWAYS": REST_STATE,
        "CONSOLIDATION": REST_STATE,
    }
    normalized = aliases.get(raw, raw)
    return (
        normalized
        if normalized in {UP_STATE, DOWN_STATE, REST_STATE}
        else "UNRESOLVED"
    )


def _record_number(record: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _number(record.get(key))
        if value is not None:
            return value
    return None


def _field(item: Any, *names: str) -> Any:
    record = _mapping(item)
    for name in names:
        if name in record and record[name] is not None:
            return record[name]
    for name in names:
        if hasattr(item, name):
            value = getattr(item, name)
            if value is not None:
                return value
    for container_name in ("ohlc", "candle", "values", "source_values"):
        nested = _mapping(record.get(container_name))
        for name in names:
            if name in nested and nested[name] is not None:
                return nested[name]
    pixel_aliases = {
        "open": ("open_y_px", "open_y"),
        "o": ("open_y_px", "open_y"),
        "close": ("close_y_px", "close_y"),
        "c": ("close_y_px", "close_y"),
        "high": ("wick_top_px", "wick_top_y", "top"),
        "h": ("wick_top_px", "wick_top_y", "top"),
        "low": ("wick_bottom_px", "wick_bottom_y", "bottom"),
        "l": ("wick_bottom_px", "wick_bottom_y", "bottom"),
    }
    for name in names:
        for pixel_name in pixel_aliases.get(name, ()):
            raw = record.get(pixel_name)
            if raw is None:
                raw = _mapping(record.get("source_values")).get(pixel_name)
            value = _number(raw)
            if value is not None:
                return -value
    return None


def _current_context(behavior: Mapping[str, Any]) -> dict[str, Any]:
    current_raw = behavior.get("current_state", behavior.get("state"))
    current_record = _mapping(current_raw)
    current_state = _state(
        current_record.get("state", current_record.get("label", current_raw))
    )
    segments = [_mapping(item) for item in _sequence(behavior.get("segments"))]
    active: Mapping[str, Any] = {}
    active_index = -1
    for index in range(len(segments) - 1, -1, -1):
        candidate = _state(segments[index].get("state", segments[index].get("label")))
        if current_state == "UNRESOLVED" or candidate == current_state:
            active = segments[index]
            active_index = index
            if current_state == "UNRESOLVED":
                current_state = candidate
            break
    previous: Mapping[str, Any] = (
        segments[active_index - 1] if active_index > 0 else {}
    )
    previous_state = _state(previous.get("state", previous.get("label")))
    age = _record_number(
        current_record,
        "candle_count",
        "age_candles",
        "duration_candles",
    )
    if age is None:
        age = _record_number(active, "candle_count", "age_candles")
    efficiency = _record_number(active, "path_efficiency", "efficiency")
    previous_efficiency = _record_number(
        previous, "path_efficiency", "efficiency"
    )
    efficiency_change = (
        efficiency - previous_efficiency
        if efficiency is not None and previous_efficiency is not None
        else None
    )
    transition_character = (
        "RESTING"
        if current_state == REST_STATE
        else "RELEASE_FROM_REST"
        if previous_state == REST_STATE
        else "DIRECTION_FLIP"
        if previous_state in _DIRECTIONAL_STATES
        and current_state in _DIRECTIONAL_STATES
        and previous_state != current_state
        else "DIRECTIONAL_PERSISTENCE"
        if current_state in _DIRECTIONAL_STATES
        else "UNRESOLVED"
    )
    return {
        "state": current_state,
        "previous_state": previous_state,
        "transition_character": transition_character,
        "direction": (
            "BUY"
            if current_state == UP_STATE
            else "SELL"
            if current_state == DOWN_STATE
            else "REST"
        ),
        "age_candles": max(0, int(age)) if age is not None else None,
        "path_efficiency": _rounded(efficiency),
        "previous_path_efficiency": _rounded(previous_efficiency),
        "efficiency_change": _rounded(efficiency_change),
        "efficiency_direction": (
            "INCREASING"
            if efficiency_change is not None and efficiency_change > 0
            else "DECREASING"
            if efficiency_change is not None and efficiency_change < 0
            else "STABLE_OR_UNRESOLVED"
        ),
        "segment_count": len(segments),
        "coordinate_space": str(behavior.get("coordinate_space", "")).strip(),
    }


def _walk_transition_counts(
    value: Any, prefix: str = ""
) -> Iterable[tuple[str, int]]:
    for raw_key, raw_value in _mapping(value).items():
        key = f"{prefix}|{raw_key}" if prefix else str(raw_key)
        if "->" in str(raw_key):
            yield key, _count(raw_value)
        elif isinstance(raw_value, Mapping):
            yield from _walk_transition_counts(raw_value, key)


def _transition_graph(
    pair_profile: Mapping[str, Any], preferred_space: str
) -> tuple[dict[str, dict[str, int]], str, int]:
    by_space: dict[str, dict[str, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    for key, support in _walk_transition_counts(
        _find(pair_profile, "transition_counts")
    ):
        relation = key.rsplit("|", 1)[-1]
        if "->" not in relation or support <= 0:
            continue
        source_raw, destination_raw = relation.split("->", 1)
        source = _state(source_raw)
        destination = _state(destination_raw)
        if "UNRESOLVED" in {source, destination}:
            continue
        coordinate = key.rsplit("|", 1)[0] if "|" in key else "default"
        by_space[coordinate][source][destination] += support
    if not by_space:
        return {}, "unavailable", 0
    preferred = preferred_space.strip().lower()
    matches = [
        coordinate
        for coordinate in by_space
        if preferred and preferred in coordinate.lower()
    ]
    candidates = matches or list(by_space)
    selected = max(
        candidates,
        key=lambda coordinate: sum(
            count
            for destinations in by_space[coordinate].values()
            for count in destinations.values()
        ),
    )
    graph = {
        source: dict(destinations)
        for source, destinations in by_space[selected].items()
    }
    total = sum(
        count for destinations in graph.values() for count in destinations.values()
    )
    return graph, selected, total


def _distribution(
    graph: Mapping[str, Mapping[str, int]], source: str
) -> dict[str, Any]:
    counts = {
        state: int(graph.get(source, {}).get(state, 0) or 0)
        for state in (UP_STATE, DOWN_STATE, REST_STATE)
    }
    support = sum(counts.values())
    supported = support >= MIN_TRANSITION_SUPPORT
    probabilities = {
        destination: _rounded(count / support) if supported and support else None
        for destination, count in counts.items()
    }
    prior = 0.5
    posterior_total = support + prior * len(counts)
    posterior: dict[str, Any] = {}
    for destination, count in counts.items():
        alpha = count + prior
        beta = support - count + prior * (len(counts) - 1)
        mean = alpha / posterior_total
        variance = alpha * beta / (
            posterior_total * posterior_total * (posterior_total + 1.0)
        )
        radius = 1.96 * sqrt(max(0.0, variance))
        posterior[destination] = {
            "mean": _rounded(mean),
            "credible_interval_95_approx": [
                _rounded(max(0.0, mean - radius)),
                _rounded(min(1.0, mean + radius)),
            ],
        }
    entropy = None
    if supported:
        nonzero = [
            value for value in probabilities.values() if value and value > 0.0
        ]
        entropy = -sum(value * log2(value) for value in nonzero)
    return {
        "source_state": source,
        "status": "SUPPORTED" if supported else "INSUFFICIENT_SUPPORT",
        "support": support,
        "minimum_support": MIN_TRANSITION_SUPPORT,
        "counts": counts,
        "probabilities": probabilities,
        "posterior": posterior,
        "posterior_prior": "symmetric_jeffreys_dirichlet_alpha_0_5",
        "entropy_bits": _rounded(entropy),
        "normalized_entropy": (
            _rounded(entropy / log2(3.0)) if entropy is not None else None
        ),
        "estimator": "empirical_mle_plus_bayesian_uncertainty_diagnostic",
    }


def _walk_segment_stats(
    value: Any, prefix: str = ""
) -> Iterable[tuple[str, str, Mapping[str, Any]]]:
    average_keys = {
        "average_candle_count",
        "avg_candle_count",
        "mean_candle_count",
        "average_candles",
        "avg_candles",
        "path_efficiency",
        "average_path_efficiency",
        "candle_count",
        "duration_seconds",
        "normalized_change",
    }
    for raw_key, raw_value in _mapping(value).items():
        key = f"{prefix}|{raw_key}" if prefix else str(raw_key)
        record = _mapping(raw_value)
        candidate_state = _state(str(raw_key).rsplit("|", 1)[-1])
        if record and candidate_state != "UNRESOLVED" and average_keys & record.keys():
            yield key, candidate_state, record
        elif record:
            yield from _walk_segment_stats(record, key)


def _segment_stats(
    pair_profile: Mapping[str, Any], preferred_space: str
) -> dict[str, dict[str, Any]]:
    rows = list(
        _walk_segment_stats(_find(pair_profile, "segment_averages"))
    )
    preferred = preferred_space.strip().lower()
    if preferred and any(preferred in key.lower() for key, _, _ in rows):
        rows = [row for row in rows if preferred in row[0].lower()]
    result: dict[str, dict[str, Any]] = {}
    for key, state, record in rows:
        support = _count(record)
        candidate = {
            "state": state,
            "coordinate_space": (
                key.rsplit("|", 1)[0] if "|" in key else "default"
            ),
            "support": support,
            "average_candles": _rounded(
                _record_number(
                    record,
                    "average_candle_count",
                    "avg_candle_count",
                    "mean_candle_count",
                    "average_candles",
                    "avg_candles",
                    "candle_count",
                ),
                3,
            ),
            "average_duration_seconds": _rounded(
                _record_number(
                    record,
                    "average_duration_seconds",
                    "avg_duration_seconds",
                    "duration_seconds",
                ),
                3,
            ),
            "average_path_efficiency": _rounded(
                _record_number(
                    record, "average_path_efficiency", "path_efficiency"
                )
            ),
            "average_normalized_change": _rounded(
                _record_number(
                    record,
                    "average_normalized_change",
                    "normalized_change",
                )
            ),
        }
        if state not in result or support > int(result[state].get("support", 0)):
            result[state] = candidate
    return result


def _observed_segment_stats(
    behavior: Mapping[str, Any], preferred_space: str
) -> dict[str, dict[str, Any]]:
    """Measure state durations from the current closed-candle sequence."""

    buckets: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for raw in _sequence(behavior.get("segments")):
        record = _mapping(raw)
        state = _state(record.get("state", record.get("label")))
        candle_count = _record_number(
            record, "candle_count", "age_candles", "duration_candles"
        )
        if state == "UNRESOLVED" or candle_count is None or candle_count <= 0:
            continue
        buckets[state].append(record)

    result: dict[str, dict[str, Any]] = {}
    for state, records in buckets.items():
        candle_counts = [
            value
            for record in records
            if (
                value := _record_number(
                    record, "candle_count", "age_candles", "duration_candles"
                )
            )
            is not None
            and value > 0
        ]
        durations = [
            value
            for record in records
            if (value := _record_number(record, "duration_seconds")) is not None
            and value >= 0
        ]
        efficiencies = [
            value
            for record in records
            if (value := _record_number(record, "path_efficiency")) is not None
        ]
        normalized_changes = [
            value
            for record in records
            if (
                value := _record_number(
                    record,
                    "normalized_change",
                    "absolute_change_in_median_ranges",
                )
            )
            is not None
        ]
        coordinate_spaces = {
            str(record.get("coordinate_space", "")).strip()
            for record in records
            if str(record.get("coordinate_space", "")).strip()
        }
        result[state] = {
            "state": state,
            "coordinate_space": (
                preferred_space
                or (sorted(coordinate_spaces)[0] if coordinate_spaces else "current_sequence")
            ),
            "support": len(candle_counts),
            "average_candles": _rounded(
                sum(candle_counts) / len(candle_counts), 3
            ),
            "average_duration_seconds": _rounded(
                sum(durations) / len(durations), 3
            )
            if durations
            else None,
            "average_path_efficiency": _rounded(
                sum(efficiencies) / len(efficiencies)
            )
            if efficiencies
            else None,
            "average_normalized_change": _rounded(
                sum(normalized_changes) / len(normalized_changes)
            )
            if normalized_changes
            else None,
            "source": "CURRENT_CLOSED_CANDLE_SEQUENCE_SEGMENTS",
        }
    return result


def _candle_rows(candles: Sequence[Any]) -> list[dict[str, float | int]]:
    rows: list[dict[str, float | int]] = []
    for index, candle in enumerate(candles):
        close = _number(_field(candle, "close", "c"))
        high = _number(_field(candle, "high", "h"))
        low = _number(_field(candle, "low", "l"))
        if close is None:
            continue
        candle_range = (
            high - low
            if high is not None and low is not None and high >= low
            else 0.0
        )
        rows.append({"index": index, "close": close, "range": candle_range})
    return rows


def _masked_reconstruction(candles: Sequence[Any]) -> dict[str, Any]:
    rows = _candle_rows(candles)
    if len(rows) < 3:
        return {
            "status": "INSUFFICIENT_SEQUENCE",
            "method": "leave_one_out_linear_close_reconstruction",
            "sample_count": 0,
        }
    ranges = [
        float(row["range"]) for row in rows if float(row["range"]) > 0.0
    ]
    scale = median(ranges) if ranges else None
    errors: list[tuple[int, float]] = []
    for position in range(1, len(rows) - 1):
        predicted = (
            float(rows[position - 1]["close"])
            + float(rows[position + 1]["close"])
        ) / 2.0
        error = abs(float(rows[position]["close"]) - predicted)
        errors.append(
            (
                int(rows[position]["index"]),
                error / scale if scale and scale > 0 else error,
            )
        )
    values = [error for _, error in errors]
    cutoff = sorted(values)[max(0, int(0.9 * (len(values) - 1)))]
    return {
        "status": "ACTIVE_DIAGNOSTIC",
        "method": "leave_one_out_linear_close_reconstruction",
        "model_claim": "BASELINE_DIAGNOSTIC_NOT_A_TRAINED_CAUSAL_MODEL",
        "sample_count": len(values),
        "mean_normalized_error": _rounded(sum(values) / len(values)),
        "median_normalized_error": _rounded(median(values)),
        "maximum_normalized_error": _rounded(max(values)),
        "anomalous_candle_indexes": [
            index for index, error in errors if error >= cutoff
        ][-8:],
    }


def _range_dynamics(candles: Sequence[Any]) -> dict[str, Any]:
    ranges = [
        float(row["range"])
        for row in _candle_rows(candles)
        if float(row["range"]) > 0.0
    ]
    if not ranges:
        return {
            "state": "UNRESOLVED",
            "range_percentile": None,
            "sample_count": 0,
        }
    current = ranges[-1]
    percentile = sum(value <= current for value in ranges) / len(ranges)
    state = (
        "COMPRESSION"
        if percentile <= 1.0 / 3.0
        else "EXPANSION"
        if percentile >= 2.0 / 3.0
        else "BALANCED"
    )
    return {
        "state": state,
        "range_percentile": _rounded(percentile),
        "current_range": _rounded(current),
        "median_range": _rounded(median(ranges)),
        "sample_count": len(ranges),
        "basis": "empirical_rank_in_current_closed_candle_sequence",
    }


def _controller(
    current: Mapping[str, Any], distribution: Mapping[str, Any]
) -> dict[str, Any]:
    current_state = str(current.get("state", "UNRESOLVED"))
    probabilities = _mapping(distribution.get("probabilities"))
    transition_support = int(distribution.get("support", 0) or 0)
    state_age = int(current.get("age_candles", 0) or 0)
    common = {
        "side": "UNRESOLVED",
        "candidate_side": "UNRESOLVED",
        "local_leg_side": (
            "BUY"
            if current_state == UP_STATE
            else "SELL"
            if current_state == DOWN_STATE
            else "REST"
        ),
        "minimum_completed_candles": MIN_CONTROL_STATE_AGE_CANDLES,
        "observed_completed_candles": state_age,
        "minimum_transition_support": MIN_TRANSITION_SUPPORT,
        "observed_transition_support": transition_support,
        "minimum_trendline_touches": 3,
        "minimum_anchor_span_bars": 5,
        "requires_structural_confirmation": True,
        "authority": "STATE_EVIDENCE_NOT_ENTRY_INSTRUCTION",
    }
    if current_state == UP_STATE:
        return {
            **common,
            "candidate_side": "BUY",
            "status": (
                "DEVELOPING_LOCAL_STATE"
                if state_age < MIN_CONTROL_STATE_AGE_CANDLES
                else "AWAITING_PAIR_DNA_SUPPORT"
                if distribution.get("status") != "SUPPORTED"
                else "AWAITING_STRUCTURAL_CONFIRMATION"
            ),
            "basis": "local_up_swing_is_candidate_evidence_not_market_control",
        }
    if current_state == DOWN_STATE:
        return {
            **common,
            "candidate_side": "SELL",
            "status": (
                "DEVELOPING_LOCAL_STATE"
                if state_age < MIN_CONTROL_STATE_AGE_CANDLES
                else "AWAITING_PAIR_DNA_SUPPORT"
                if distribution.get("status") != "SUPPORTED"
                else "AWAITING_STRUCTURAL_CONFIRMATION"
            ),
            "basis": "local_down_swing_is_candidate_evidence_not_market_control",
        }
    if current_state == REST_STATE and distribution.get("status") == "SUPPORTED":
        buy_mass = _number(probabilities.get(UP_STATE)) or 0.0
        sell_mass = _number(probabilities.get(DOWN_STATE)) or 0.0
        if buy_mass > sell_mass:
            return {
                **common,
                "candidate_side": "BUY",
                "status": "REST_NEXT_STATE_LEAD_ONLY",
                "basis": "buy_leads_rest_transitions_but_does_not_control",
            }
        if sell_mass > buy_mass:
            return {
                **common,
                "candidate_side": "SELL",
                "status": "REST_NEXT_STATE_LEAD_ONLY",
                "basis": "sell_leads_rest_transitions_but_does_not_control",
            }
        return {
            **common,
            "status": "TIED_TRANSITION_MASS",
            "basis": "neither_side_leads",
        }
    return {
        **common,
        "status": "INSUFFICIENT_EMPIRICAL_SUPPORT",
        "basis": "no_strategy_rule_substitution",
    }


def _components(
    current_state: str, distribution: Mapping[str, Any]
) -> dict[str, Any]:
    counts = _mapping(distribution.get("counts"))
    probabilities = _mapping(distribution.get("probabilities"))
    support = int(distribution.get("support", 0) or 0)

    def directional(side: str, state: str) -> dict[str, Any]:
        return {
            "side": side,
            "state": state,
            "currently_active": current_state == state,
            "next_state_probability": _number(probabilities.get(state)),
            "next_state_count": int(counts.get(state, 0) or 0),
            "outgoing_transition_support": support,
            "evidence_status": distribution.get("status"),
            "authority": "STATE_EVIDENCE_NOT_TRADE_COMMAND",
        }

    return {
        "BUY": directional("BUY", UP_STATE),
        "SELL": directional("SELL", DOWN_STATE),
        "REST": {
            "state": REST_STATE,
            "currently_active": current_state == REST_STATE,
            "next_state_probability": _number(probabilities.get(REST_STATE)),
            "next_state_count": int(counts.get(REST_STATE, 0) or 0),
            "outgoing_transition_support": support,
            "evidence_status": distribution.get("status"),
        },
    }


def _leader(distribution: Mapping[str, Any]) -> tuple[str | None, float | None]:
    if distribution.get("status") != "SUPPORTED":
        return None, None
    probabilities = {
        state: probability
        for state, raw in _mapping(distribution.get("probabilities")).items()
        if (probability := _number(raw)) is not None
    }
    if not probabilities:
        return None, None
    highest = max(probabilities.values())
    leaders = [state for state, value in probabilities.items() if value == highest]
    return (leaders[0], highest) if len(leaders) == 1 else (None, None)


def _duration_payload(
    candle_count: float | None, timeframe_seconds: int | float | None
) -> dict[str, Any]:
    seconds_per_candle = _number(timeframe_seconds)
    if candle_count is None or seconds_per_candle is None or seconds_per_candle <= 0:
        return {
            "status": "UNSUPPORTED",
            "seconds": None,
            "minutes": None,
            "hours": None,
            "days": None,
            "display": "duration unsupported",
        }
    seconds = max(0.0, float(candle_count) * seconds_per_candle)
    whole_seconds = int(round(seconds))
    if whole_seconds >= 86400:
        days, remainder = divmod(whole_seconds, 86400)
        hours = remainder // 3600
        display = f"{days}d {hours}h" if hours else f"{days}d"
    elif whole_seconds >= 3600:
        hours, remainder = divmod(whole_seconds, 3600)
        minutes = remainder // 60
        display = f"{hours}h {minutes}m" if minutes else f"{hours}h"
    elif whole_seconds >= 60:
        minutes, remainder = divmod(whole_seconds, 60)
        display = f"{minutes}m {remainder}s" if remainder else f"{minutes}m"
    else:
        display = f"{whole_seconds}s"
    return {
        "status": "CALCULATED_FROM_VERIFIED_TIMEFRAME",
        "seconds": whole_seconds,
        "minutes": _rounded(seconds / 60.0, 3),
        "hours": _rounded(seconds / 3600.0, 3),
        "days": _rounded(seconds / 86400.0, 4),
        "display": display,
    }


def _whole_state_cycle(
    current: Mapping[str, Any],
    graph: Mapping[str, Mapping[str, int]],
    segment_stats: Mapping[str, Mapping[str, Any]],
    timeframe_seconds: int | float | None,
) -> dict[str, Any]:
    current_state = str(current.get("state", "UNRESOLVED"))
    if current_state == "UNRESOLVED":
        return {
            "status": "UNRESOLVED_CURRENT_STATE",
            "mode": "STATE_CYCLE_TO_NEXT_DIRECTIONAL_SWING_COMPLETION",
            "fixed_candle_horizon": False,
            "rests_included": True,
            "expected_candles": None,
            "path": [],
        }
    current_average = _number(
        _mapping(segment_stats.get(current_state)).get("average_candles")
    )
    age = _number(current.get("age_candles")) or 0.0
    remaining = (
        max(0.0, current_average - age) if current_average is not None else None
    )
    complete_duration = remaining is not None
    total = remaining or 0.0
    path = [
        {
            "state": current_state,
            "phase": "CURRENT_REMAINDER",
            "expected_candles": _rounded(remaining, 3),
            "duration": _duration_payload(remaining, timeframe_seconds),
            "segment_support": int(
                _mapping(segment_stats.get(current_state)).get("support", 0) or 0
            ),
        }
    ]
    path_probability = 1.0
    transition_support: list[int] = []
    cursor = current_state
    completed = False
    for _ in range(4):
        distribution = _distribution(graph, cursor)
        destination, probability = _leader(distribution)
        if destination is None or probability is None:
            break
        stats = _mapping(segment_stats.get(destination))
        expected = _number(stats.get("average_candles"))
        complete_duration = complete_duration and expected is not None
        total += expected or 0.0
        path_probability *= probability
        transition_support.append(int(distribution.get("support", 0) or 0))
        path.append(
            {
                "state": destination,
                "phase": (
                    "REST"
                    if destination == REST_STATE
                    else "NEXT_DIRECTIONAL_SWING"
                ),
                "expected_candles": _rounded(expected, 3),
                "duration": _duration_payload(expected, timeframe_seconds),
                "segment_support": int(stats.get("support", 0) or 0),
                "transition_probability": _rounded(probability),
                "transition_support": int(distribution.get("support", 0) or 0),
            }
        )
        cursor = destination
        if destination in _DIRECTIONAL_STATES:
            completed = True
            break
    return {
        "status": (
            "EMPIRICAL_STATE_CYCLE"
            if completed
            else "PARTIAL_OR_INSUFFICIENT_PATH_EVIDENCE"
        ),
        "mode": "STATE_CYCLE_TO_NEXT_DIRECTIONAL_SWING_COMPLETION",
        "fixed_candle_horizon": False,
        "rests_included": True,
        "expected_candles": _rounded(total, 3) if complete_duration else None,
        "duration": _duration_payload(
            total if complete_duration else None,
            timeframe_seconds,
        ),
        "path_probability": _rounded(path_probability) if completed else None,
        "minimum_transition_support": (
            min(transition_support) if transition_support else 0
        ),
        "path": path,
    }


def _walk_mappings(value: Any, depth: int = 0) -> Iterable[Mapping[str, Any]]:
    if depth > 6:
        return
    record = _mapping(value)
    if not record:
        return
    yield record
    for child in record.values():
        if isinstance(child, Mapping):
            yield from _walk_mappings(child, depth + 1)
        elif isinstance(child, Sequence) and not isinstance(
            child, (str, bytes, bytearray)
        ):
            for item in _sequence(child):
                if isinstance(item, Mapping):
                    yield from _walk_mappings(item, depth + 1)


def _survival(
    advanced_studies: Mapping[str, Any],
    current: Mapping[str, Any],
    segment_stats: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    current_state = str(current.get("state", "UNRESOLVED"))
    age = current.get("age_candles")
    stats = _mapping(segment_stats.get(current_state))
    average = _number(stats.get("average_candles"))
    base = {
        "state": current_state,
        "age_candles": age,
        "historical_average_segment_candles": _rounded(average, 3),
        "segment_support": int(stats.get("support", 0) or 0),
        "survival_probability": None,
        "collapse_probability": None,
    }
    if age is None:
        return {**base, "status": "AGE_UNAVAILABLE"}
    network = _mapping(advanced_studies.get("survival_network"))
    for candidate in _walk_mappings(network):
        candidate_state = _state(
            candidate.get(
                "state", candidate.get("subject_state", candidate.get("label"))
            )
        )
        if candidate_state != current_state:
            continue
        points = _sequence(
            candidate.get(
                "points", candidate.get("curve", candidate.get("survival_curve"))
            )
        )
        selected: tuple[float, float, Mapping[str, Any]] | None = None
        for raw_point in points:
            point = _mapping(raw_point)
            point_age = _record_number(
                point, "age_candles", "candle_age", "duration", "time"
            )
            probability = _record_number(
                point, "survival_probability", "survival", "probability"
            )
            if point_age is None or probability is None or point_age > float(age):
                continue
            if selected is None or point_age > selected[0]:
                selected = (point_age, probability, point)
        if selected:
            probability = min(1.0, max(0.0, selected[1]))
            return {
                **base,
                "status": "EMPIRICAL_SURVIVAL_CURVE",
                "curve_age_candles": _rounded(selected[0], 3),
                "survival_probability": _rounded(probability),
                "collapse_probability": _rounded(1.0 - probability),
                "curve_support": _count(candidate) or _count(selected[2]),
            }
    return {
        **base,
        "status": "INSUFFICIENT_EMPIRICAL_SURVIVAL_CURVE",
        "maturity_vs_historical_average": (
            _rounded(float(age) / average) if average and average > 0 else None
        ),
    }


def _directional_outcomes(
    current_state: str, graph: Mapping[str, Mapping[str, int]]
) -> dict[str, Any]:
    first = _distribution(graph, current_state)
    if first.get("status") != "SUPPORTED":
        return {
            "status": "INSUFFICIENT_SUPPORT",
            "support": first.get("support", 0),
        }
    mass = {UP_STATE: 0.0, DOWN_STATE: 0.0}
    minimum_support = int(first.get("support", 0) or 0)
    for destination, raw_probability in _mapping(
        first.get("probabilities")
    ).items():
        probability = _number(raw_probability)
        if probability is None:
            continue
        if destination in _DIRECTIONAL_STATES:
            mass[destination] += probability
        elif destination == REST_STATE:
            second = _distribution(graph, REST_STATE)
            if second.get("status") != "SUPPORTED":
                continue
            minimum_support = min(
                minimum_support, int(second.get("support", 0) or 0)
            )
            for final_state in _DIRECTIONAL_STATES:
                final_probability = _number(
                    _mapping(second.get("probabilities")).get(final_state)
                )
                if final_probability is not None:
                    mass[final_state] += probability * final_probability
    total = sum(mass.values())
    buy_probability = _rounded(mass[UP_STATE] / total) if total else None
    sell_probability = _rounded(mass[DOWN_STATE] / total) if total else None
    result: dict[str, Any] = {
        "status": "SUPPORTED" if total else "NO_DIRECTIONAL_PATH_SUPPORT",
        "BUY": buy_probability,
        "SELL": sell_probability,
        "minimum_transition_support": minimum_support,
        "path_basis": "direct_or_rest_mediated_empirical_transitions",
    }
    if current_state in _DIRECTIONAL_STATES:
        result["continuation_probability"] = (
            buy_probability if current_state == UP_STATE else sell_probability
        )
        result["reversal_probability"] = (
            sell_probability if current_state == UP_STATE else buy_probability
        )
    return result


def _causal_hypotheses(
    current_state: str, distribution: Mapping[str, Any]
) -> list[dict[str, Any]]:
    if distribution.get("status") != "SUPPORTED":
        return []
    counts = _mapping(distribution.get("counts"))
    ranked = sorted(
        (
            (destination, probability)
            for destination, raw in _mapping(
                distribution.get("probabilities")
            ).items()
            if (probability := _number(raw)) is not None
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    return [
        {
            "temporal_antecedent": current_state,
            "observed_following_state": destination,
            "observed_probability": _rounded(probability),
            "support": int(counts.get(destination, 0) or 0),
            "causal_status": "UNPROVEN_OBSERVATIONAL_ASSOCIATION",
            "interpretation": (
                "candidate_for_out_of_sample_or_intervention_testing_not_a_causal_claim"
            ),
        }
        for destination, probability in ranked[:3]
    ]


def build_latent_state_discovery_v3(
    *,
    candles: Sequence[Any],
    behavior: Mapping[str, Any],
    pair_profile: Mapping[str, Any],
    advanced_studies: Mapping[str, Any],
    research_studies: Mapping[str, Any],
    symbol: str,
    timeframe: str,
    timeframe_seconds: int | float | None,
) -> dict[str, Any]:
    """Build the V3 latent-state contract from observation evidence."""

    behavior = _mapping(behavior)
    pair_profile = _mapping(pair_profile)
    advanced_studies = _mapping(advanced_studies)
    research_studies = _mapping(research_studies)
    current = _current_context(behavior)
    graph, coordinate_space, total_transition_support = _transition_graph(
        pair_profile, str(current.get("coordinate_space", ""))
    )
    segment_stats = _segment_stats(pair_profile, coordinate_space)
    observed_segment_stats = _observed_segment_stats(behavior, coordinate_space)
    for state, statistics in observed_segment_stats.items():
        retained = _mapping(segment_stats.get(state))
        retained_average = _number(retained.get("average_candles"))
        retained_support = int(retained.get("support", 0) or 0)
        if (
            not retained
            or retained_average is None
            or retained_average <= 0
            or retained_support <= 0
        ):
            segment_stats[state] = statistics
    distribution = _distribution(graph, str(current["state"]))
    outcomes = _directional_outcomes(str(current["state"]), graph)
    transition_signatures = [
        segment_stats[state] for state in sorted(segment_stats)
    ]
    multi_timeframe = _find(
        research_studies, "multi_timeframe_consistency"
    )
    session_invariants = _find(pair_profile, "session_invariants")

    return {
        "schema_version": LATENT_STATE_DISCOVERY_SCHEMA_VERSION,
        "status": (
            "ACTIVE"
            if current["state"] != "UNRESOLVED"
            else "INSUFFICIENT_STATE_EVIDENCE"
        ),
        "study_only": True,
        "observation_only": True,
        "strategy_authority": False,
        "blocker_authority": False,
        "execution_authority": False,
        "grants_entry_permission": False,
        "symbol": str(symbol or "").upper(),
        "timeframe": str(timeframe or ""),
        "timeframe_seconds": _rounded(_number(timeframe_seconds), 3),
        "input_authority": {
            "closed_candle_sequence": True,
            "pair_dna_history": True,
            "object_observations": True,
            "strategy_rules": False,
            "setups": False,
            "support_resistance_rules": False,
            "liquidity_trap_rules": False,
            "trade_commands": False,
        },
        "publication_policy": {
            "strategy_blockers_can_suppress_discovery": False,
            "invalid_or_unowned_source_can_publish_as_live": False,
            "causal_claims_require_more_than_temporal_association": True,
        },
        "hidden_state": {
            **current,
            "range_dynamics": _range_dynamics(candles),
            "accumulation_distribution": {
                "state": "UNRESOLVED_FROM_PRICE_ONLY",
                "reason": (
                    "no_human_pattern_proxy_or_unvalidated_semantic_label_is_used"
                ),
            },
            "trapped_side": {
                "state": "UNRESOLVED",
                "causal_status": "REQUIRES_VALIDATED_OUTCOME_CONDITIONING",
            },
        },
        "control": _controller(current, distribution),
        "directional_components": _components(
            str(current["state"]), distribution
        ),
        "next_state_distribution": distribution,
        "state_survival": _survival(
            advanced_studies, current, segment_stats
        ),
        "state_cycle_horizon": _whole_state_cycle(
            current, graph, segment_stats, timeframe_seconds
        ),
        "directional_outcome_distribution": outcomes,
        "pair_dna": {
            "coordinate_space": coordinate_space,
            "transition_support": total_transition_support,
            "state_segment_signatures": transition_signatures,
            "pair_specific": True,
            "session_invariants_status": (
                "ACTIVE" if session_invariants else "NOT_YET_SUPPORTED"
            ),
        },
        "learning_objectives": {
            "masked_price_reconstruction": _masked_reconstruction(candles),
            "next_state_prediction": {
                "status": distribution["status"],
                "distribution": distribution,
            },
            "transition_clustering": {
                "status": (
                    "ACTIVE"
                    if transition_signatures
                    else "INSUFFICIENT_SEGMENT_HISTORY"
                ),
                "method": "pair_specific_empirical_state_signatures",
                "signatures": transition_signatures,
            },
            "multi_timeframe_consistency": {
                "status": "ACTIVE" if multi_timeframe else "CURRENT_TIMEFRAME_ONLY",
                "evidence": multi_timeframe if multi_timeframe else None,
            },
            "reversal_continuation_outcomes": outcomes,
            "pair_session_invariants": {
                "pair_status": (
                    "ACTIVE" if total_transition_support else "INSUFFICIENT_HISTORY"
                ),
                "session_status": (
                    "ACTIVE" if session_invariants else "NOT_YET_SUPPORTED"
                ),
            },
        },
        "causal_hypotheses": _causal_hypotheses(
            str(current["state"]), distribution
        ),
        "causal_limit": "temporal_association_is_not_interventional_causality",
        "operator_interpretation": (
            "BUY_and_SELL_are_state_components_not_trade_commands"
        ),
    }
