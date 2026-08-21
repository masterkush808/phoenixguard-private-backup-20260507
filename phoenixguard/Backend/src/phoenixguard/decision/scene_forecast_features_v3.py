from __future__ import annotations

import hashlib
import json
import math
import re
import statistics
from dataclasses import dataclass, field
from typing import Any, Iterator, Mapping, Sequence, cast


SCHEMA_VERSION = "PG_SCENE_FORECAST_FEATURES_V3"
MAX_CANDLES = 1_000_000
MAX_AUDIT_LEAVES = 1_000_000
# Published audit lists are bounded samples; the *_count fields stay exact.
_AUDIT_FIELD_SAMPLE = 256
_AUDIT_ROW_SAMPLE = 512
MISSING_CATEGORY = "__MISSING__"
OTHER_CATEGORY = "__OTHER__"

TIMEFRAME_SECONDS: dict[str, int] = {
    "M1": 60,
    "M2": 120,
    "M3": 180,
    "M5": 300,
    "M10": 600,
    "M15": 900,
    "M30": 1800,
    "H1": 3600,
    "H2": 7200,
    "H4": 14400,
    "H6": 21600,
    "H12": 43200,
    "D1": 86400,
}

CANDLE_NUMERIC_BASE_SCHEMA: tuple[str, ...] = (
    "open_offset",
    "high_offset",
    "low_offset",
    "close_offset",
    "close_delta",
    "range_scaled",
    "body_scaled",
    "upper_wick_scaled",
    "lower_wick_scaled",
    "body_fraction",
    "upper_wick_fraction",
    "lower_wick_fraction",
    "direction_value",
    "relative_position",
    "elapsed_steps",
    "timestamp_gap_steps",
    "center_x_norm",
    "center_y_norm",
    "width_vs_median",
    "height_vs_median",
    "parse_confidence",
    "ohlc_inferred",
)
CANDLE_NUMERIC_SCHEMA: tuple[str, ...] = CANDLE_NUMERIC_BASE_SCHEMA + tuple(
    f"{name}__missing" for name in CANDLE_NUMERIC_BASE_SCHEMA
)
CANDLE_CATEGORICAL_SCHEMA: tuple[str, ...] = (
    "direction",
    "price_source",
)

CONTEXT_NUMERIC_BASE_SCHEMA: tuple[str, ...] = (
    "meta.closed_candle_count",
    "meta.excluded_forming_count",
    "meta.history_truncated",
    "meta.timeframe_seconds",
    "meta.price_scale",
    "candle.net_move_3",
    "candle.net_move_6",
    "candle.net_move_12",
    "candle.historical_volatility_12",
    "candle.mean_range_12",
    "candle.direction_flip_rate_12",
    "projection.confidence",
    "projection.dominance",
    "projection.zone_count",
    "projection.buy_zone_fraction",
    "projection.sell_zone_fraction",
    "projection.zone_confidence_mean",
    "projection.zone_confidence_max",
    "candle_statistics.sample_size",
    "candle_statistics.sample_weight",
    "candle_statistics.buy_count",
    "candle_statistics.sell_count",
    "candle_statistics.buy_ratio",
    "candle_statistics.sell_ratio",
    "candle_statistics.recent_buy_count",
    "candle_statistics.recent_sell_count",
    "candle_statistics.recent_buy_ratio",
    "candle_statistics.recent_sell_ratio",
    "candle_statistics.direction_run",
    "candle_statistics.opposite_run",
    "candle_statistics.candidate_ratio",
    "candle_statistics.opposing_ratio",
    "candle_statistics.momentum_consistency",
    "candle_statistics.normalized_volatility",
    "candle_statistics.average_step",
    "behavior.state_confidence",
    "behavior.rejection_count",
    "behavior.compression_count",
    "behavior.impulse_count",
    "behavior.pullback_count",
    "behavior.pause_count",
    "behavior.exhaustion_count",
    "behavior.reversal_count",
    "behavior.box.candles_seen",
    "behavior.box.entry_quality",
    "behavior.box.rejection_count",
    "behavior.box.acceptance_count",
    "behavior.box.compression_score",
    "behavior.box.momentum_exit_score",
    "behavior.box.failure_risk",
    "behavior.trend.slope_global",
    "behavior.trend.slope_local",
    "behavior.trend.slope_current",
    "behavior.trend.strength",
    "behavior.trend.recent_range",
    "behavior.next.buy_probability",
    "behavior.next.sell_probability",
    "behavior.next.continuation_probability",
    "behavior.next.reversal_probability",
    "behavior.next.pause_probability",
    "behavior.next.max_probability",
    "behavior.next.entropy",
    "decision.bias_strength",
    "decision.setup_age_candles",
    "decision.freshness",
    "decision.structure_alignment",
    "decision.buy_evidence",
    "decision.sell_evidence",
    "decision.net_bias",
    "decision.conflict_score",
    "decision.belief_buy",
    "decision.belief_sell",
    "decision.belief_hold",
    "decision.belief_uncertainty",
    "decision.belief_conflict",
    "decision.directional_edge",
    "decision.evidence_mass",
    "decision.usable_bias",
    "decision.distance_to_trigger",
    "decision.distance_to_target",
    "decision.distance_to_invalidation",
    "decision.eta_trigger_candles",
    "decision.eta_target_after_trigger_candles",
    "decision.eta_invalidation_candles",
    "decision.target_horizon_candles",
    "decision.stale_after_candles",
    "decision.p_trigger_next_1",
    "decision.p_trigger_next_3",
    "decision.p_target_before_invalidation",
    "decision.p_expire_before_trigger",
    "decision.hazard_trigger",
    "decision.hazard_invalidation",
    "decision.hazard_expiry",
    "decision.expected_value_r",
    "decision.raw_expected_value_r",
    "decision.uncertainty_tax_r",
    "decision.reward_r",
    "decision.loss_r",
    "decision.cost_r",
    "decision.major_trend_confidence",
    "decision.p_next_buy",
    "decision.p_next_sell",
    "decision.p_next_hold",
    "decision.countertrend_window_candles",
    "decision.trend_follow_window_candles",
    "decision.hold_for_candles",
    "smart_money.confidence",
    "smart_money.confidence_delta",
    "smart_money.risk_delta",
    "smart_money.order_block_count",
    "smart_money.fair_value_gap_count",
    "smart_money.liquidity_sweep_count",
    "smart_money.liquidity_pool_count",
    "smart_money.buy_object_fraction",
    "smart_money.sell_object_fraction",
    "smart_money.object_confidence_mean",
    "smart_money.object_confidence_max",
    "smart_money.fresh_object_fraction",
    "smart_money.mitigated_object_fraction",
    "smart_money.mean_age_candles",
    "smart_money.structure_shift_confidence",
    "support_resistance.significant_count",
    "support_resistance.institutional_zone_count",
    "support_resistance.fresh_zone_count",
    "support_resistance.reference_zone_count",
    "support_resistance.active_authority_count",
    "support_resistance.buy_structure_score",
    "support_resistance.sell_structure_score",
    "support_resistance.zone_count",
    "support_resistance.support_fraction",
    "support_resistance.resistance_fraction",
    "support_resistance.buy_zone_fraction",
    "support_resistance.sell_zone_fraction",
    "support_resistance.confidence_mean",
    "support_resistance.confidence_max",
    "support_resistance.significance_mean",
    "support_resistance.nearest_distance",
    "support_resistance.mean_touch_count",
    "support_resistance.mean_age_candles",
    "support_resistance.fresh_fraction",
    "support_resistance.authority_fraction",
    "support_resistance.institutional_fraction",
    "trend.slope_global",
    "trend.slope_local",
    "trend.slope_current",
    "trend.slope_impulse",
)
CONTEXT_NUMERIC_SCHEMA: tuple[str, ...] = CONTEXT_NUMERIC_BASE_SCHEMA + tuple(
    f"{name}__missing" for name in CONTEXT_NUMERIC_BASE_SCHEMA
)

CONTEXT_CATEGORICAL_SCHEMA: tuple[str, ...] = (
    "pair",
    "timeframe",
    "projection.direction",
    "behavior.current_state",
    "behavior.previous_state",
    "behavior.next_state",
    "behavior.trend_phase",
    "behavior.move_quality",
    "behavior.box.type",
    "behavior.box.state",
    "behavior.trend.global_bias",
    "behavior.trend.local_bias",
    "behavior.trend.micro_bias",
    "decision.dominant_side",
    "decision.major_trend_side",
    "decision.state",
    "decision.next_event",
    "decision.confidence_tier",
    "decision.firewall_action",
    "decision.decision",
    "decision.next_candle_bias",
    "decision.trade_mode",
    "decision.execution_side",
    "decision.countertrend_side",
    "smart_money.dominant_side",
    "smart_money.adjustment_side",
    "smart_money.structure_shift_direction",
    "support_resistance.dominant_side",
    "support_resistance.candidate_side",
    "trend.direction_global",
    "trend.direction_local",
    "trend.direction_current",
    "trend.direction_impulse",
)


_CONTEXT_LIMITS: dict[str, tuple[float, float]] = {
    name: (-1_000_000.0, 1_000_000.0) for name in CONTEXT_NUMERIC_BASE_SCHEMA
}
for _name in CONTEXT_NUMERIC_BASE_SCHEMA:
    if any(
        token in _name
        for token in (
            "confidence",
            "fraction",
            "ratio",
            "probability",
            "freshness",
            "alignment",
            "conflict",
            "uncertainty",
            "edge",
            "evidence_mass",
            "usable_bias",
            "hazard",
            "sample_weight",
            "normalized_volatility",
            "momentum_consistency",
            "entry_quality",
            "failure_risk",
            "strength",
        )
    ):
        _CONTEXT_LIMITS[_name] = (0.0, 1.0)
for _name in (
    "decision.net_bias",
    "decision.expected_value_r",
    "decision.raw_expected_value_r",
    "decision.uncertainty_tax_r",
    "decision.reward_r",
    "decision.loss_r",
    "decision.cost_r",
    "smart_money.confidence_delta",
    "smart_money.risk_delta",
    "trend.slope_global",
    "trend.slope_local",
    "trend.slope_current",
    "trend.slope_impulse",
    "behavior.trend.slope_global",
    "behavior.trend.slope_local",
    "behavior.trend.slope_current",
    "candle.net_move_3",
    "candle.net_move_6",
    "candle.net_move_12",
):
    _CONTEXT_LIMITS[_name] = (-32.0, 32.0)


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_mapping(item) for item in cast(Sequence[Any], value) if isinstance(item, Mapping)]


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clip(value: float, low: float, high: float) -> float:
    return float(max(low, min(high, value)))


def _normalized_category(value: Any, *, default: str = MISSING_CATEGORY) -> str:
    if value is None:
        return default
    text = str(value).strip().upper()
    if not text:
        return default
    normalized = re.sub(r"[^A-Z0-9]+", "_", text).strip("_")
    if not normalized:
        return OTHER_CATEGORY
    return normalized[:1_000_000]


def _side(value: Any) -> str:
    normalized = _normalized_category(value, default="HOLD")
    if normalized.startswith("BUY") or normalized in {
        "BULL",
        "BULLISH",
        "GREEN",
        "UP",
        "CALL",
        "DEMAND",
        "SUPPORT",
    }:
        return "BUY"
    if normalized.startswith("SELL") or normalized in {
        "BEAR",
        "BEARISH",
        "RED",
        "MAGENTA",
        "PINK",
        "DOWN",
        "PUT",
        "SUPPLY",
        "RESISTANCE",
    }:
        return "SELL"
    return "HOLD"


def _explicit_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "closed", "complete", "confirmed", "final"}:
        return True
    if normalized in {"0", "false", "no", "open", "forming", "incomplete", "live"}:
        return False
    return None


@dataclass
class _Audit:
    consumed: set[str] = field(default_factory=set[str])
    missing: set[str] = field(default_factory=set[str])
    rejected: dict[tuple[str, str], None] = field(
        default_factory=dict[tuple[str, str], None]
    )

    def consume(self, path: str) -> None:
        self.consumed.add(path)

    def miss(self, path: str) -> None:
        self.missing.add(path)

    def reject(self, path: str, reason: str) -> None:
        self.rejected[(path, reason)] = None

    @staticmethod
    def _covered(path: str, prefixes: set[str]) -> bool:
        if path in prefixes:
            return True
        # A prefix covers this leaf only when it is the leaf itself or one of
        # its exact ancestor boundaries ("prefix." / "prefix[...").  Testing
        # each dot/bracket boundary against the hash set is O(depth) instead
        # of scanning every recorded prefix (which ran ~100M string compares
        # per frame after the audit-leaf ceiling was raised).
        for index in range(1, len(path)):
            char = path[index]
            if char == "." or char == "[":
                if path[:index] in prefixes:
                    return True
        return False

    def finalize(self, roots: Mapping[str, Any]) -> dict[str, Any]:
        inspected = 0
        rejected_prefixes = {path for path, _ in self.rejected}
        for root, value in roots.items():
            for path, leaf in _leaf_paths(root, value):
                if inspected >= MAX_AUDIT_LEAVES:
                    self.reject("payload", "audit_leaf_limit_reached")
                    break
                inspected += 1
                if self._covered(path, self.consumed) or self._covered(path, rejected_prefixes):
                    continue
                self.reject(path, _rejection_reason(path, leaf))
        consumed_fields = sorted(self.consumed)
        missing_fields = sorted(self.missing)
        rejected_rows = [
            {"path": path, "reason": reason}
            for path, reason in sorted(self.rejected)
        ]
        # The counts above stay authoritative; the retained lists are a bounded
        # sample so the published payload cannot grow with session history.
        return {
            "consumed_field_count": len(consumed_fields),
            "missing_field_count": len(missing_fields),
            "rejected_field_count": len(rejected_rows),
            "consumed_fields": consumed_fields[-_AUDIT_FIELD_SAMPLE:],
            "missing_fields": missing_fields[-_AUDIT_FIELD_SAMPLE:],
            "rejected_fields": rejected_rows[-_AUDIT_ROW_SAMPLE:],
            "inspected_leaf_count": inspected,
        }


def _leaf_paths(root: str, value: Any) -> Iterator[tuple[str, Any]]:
    stack: list[tuple[str, Any]] = [(root, value)]
    while stack:
        path, item = stack.pop()
        if isinstance(item, Mapping):
            mapping = _mapping(item)
            if not mapping:
                continue
            for key in sorted(mapping, reverse=True):
                stack.append((f"{path}.{key}", mapping[key]))
            continue
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            sequence = list(cast(Sequence[Any], item))
            if not sequence:
                continue
            for index in range(len(sequence) - 1, -1, -1):
                stack.append((f"{path}[{index}]", sequence[index]))
            continue
        yield path, item


def _rejection_reason(path: str, value: Any) -> str:
    normalized = path.lower()
    if any(
        token in normalized
        for token in (
            "ground_truth",
            "target_label",
            "future_candle",
            "future_return",
            "forward_return",
            "realized_outcome",
            "subsequent_",
            "actual_future",
        )
    ):
        return "future_or_outcome_field"
    if "target_bbox" in normalized or normalized.endswith(".path") or ".path[" in normalized:
        return "forward_projection_geometry_not_observed"
    if value is None:
        return "null_or_missing"
    if isinstance(value, str):
        return "unapproved_text_or_categorical_field"
    if isinstance(value, (bool, int, float)):
        return "unapproved_numeric_field"
    return "unsupported_value_type"


def _candle_closed_status(
    candle: Mapping[str, Any],
    *,
    index: int,
    total: int,
    audit: _Audit,
) -> bool:
    root = f"candles[{index}]"
    for key in (
        "is_closed",
        "closed",
        "candle_closed",
        "closed_candle",
        "is_complete",
        "candle_complete",
    ):
        if key not in candle or candle.get(key) is None:
            continue
        parsed = _explicit_bool(candle.get(key))
        if parsed is not None:
            audit.consume(f"{root}.{key}")
            return parsed
    for key in ("is_forming", "forming", "candle_forming", "in_progress"):
        if key not in candle or candle.get(key) is None:
            continue
        parsed = _explicit_bool(candle.get(key))
        if parsed is not None:
            audit.consume(f"{root}.{key}")
            return not parsed
    for key in ("candle_state", "bar_state"):
        if key not in candle or candle.get(key) is None:
            continue
        parsed = _explicit_bool(candle.get(key))
        if parsed is not None:
            audit.consume(f"{root}.{key}")
            return parsed
    return 0 <= index < max(0, total - 1)


def _first_number(
    row: Mapping[str, Any],
    aliases: Sequence[str],
    *,
    root: str,
    audit: _Audit,
) -> tuple[float | None, str | None]:
    for key in aliases:
        if key not in row:
            continue
        path = f"{root}.{key}"
        number = _finite(row.get(key))
        if number is None:
            audit.reject(path, "non_finite_or_invalid_numeric")
            continue
        audit.consume(path)
        return number, key
    return None, None


def _bbox(
    row: Mapping[str, Any],
    *,
    root: str,
    audit: _Audit,
) -> tuple[float, float, float, float] | None:
    for key in ("bbox", "bounds", "box", "rect"):
        raw = row.get(key)
        if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
            continue
        values = list(cast(Sequence[Any], raw))[:4]
        parsed = [_finite(value) for value in values]
        if len(parsed) < 4 or any(value is None for value in parsed):
            audit.reject(f"{root}.{key}", "invalid_bounds")
            continue
        x0, y0, x1, y1 = cast(list[float], parsed)
        if x0 == x1 or y0 == y1:
            audit.reject(f"{root}.{key}", "degenerate_bounds")
            continue
        audit.consume(f"{root}.{key}")
        return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)
    return None


@dataclass(frozen=True)
class _Candle:
    source_index: int
    open: float
    high: float
    low: float
    close: float
    direction: str
    price_source: str
    missing_ohlc: tuple[bool, bool, bool, bool]
    bbox: tuple[float, float, float, float] | None
    timestamp: float | None
    parse_confidence: float | None


def _candle_values(
    row: Mapping[str, Any],
    *,
    index: int,
    audit: _Audit,
) -> _Candle | None:
    root = f"candles[{index}]"
    open_value, open_key = _first_number(row, ("open", "o", "open_price"), root=root, audit=audit)
    high_value, high_key = _first_number(row, ("high", "h", "high_price"), root=root, audit=audit)
    low_value, low_key = _first_number(row, ("low", "l", "low_price"), root=root, audit=audit)
    close_value, close_key = _first_number(
        row,
        ("close", "c", "close_price", "price_proxy", "close_proxy"),
        root=root,
        audit=audit,
    )
    bounds = _bbox(row, root=root, audit=audit)
    direct_present = any(key is not None for key in (open_key, high_key, low_key))
    source = "OHLC" if direct_present else "PRICE_PROXY" if close_key is not None else ""
    missing: tuple[bool, bool, bool, bool] = (
        open_value is None,
        high_value is None,
        low_value is None,
        close_value is None,
    )

    if not direct_present:
        open_y, _ = _first_number(
            row,
            ("open_y_px", "open_y", "open_px", "open_price_y"),
            root=root,
            audit=audit,
        )
        close_y, _ = _first_number(
            row,
            ("close_y_px", "close_y", "close_px", "close_price_y"),
            root=root,
            audit=audit,
        )
        wick_top, _ = _first_number(
            row,
            ("wick_top_px", "wick_top", "high_y_px", "high_y"),
            root=root,
            audit=audit,
        )
        wick_bottom, _ = _first_number(
            row,
            ("wick_bottom_px", "wick_bottom", "low_y_px", "low_y"),
            root=root,
            audit=audit,
        )
        body_top, _ = _first_number(
            row,
            ("body_top_px", "body_top", "body_y0"),
            root=root,
            audit=audit,
        )
        body_bottom, _ = _first_number(
            row,
            ("body_bottom_px", "body_bottom", "body_y1"),
            root=root,
            audit=audit,
        )
        explicit_direction = _side(row.get("direction") or row.get("side") or row.get("color"))
        for key in ("direction", "side", "color"):
            if key in row and row.get(key) not in (None, ""):
                audit.consume(f"{root}.{key}")
                break
        if bounds is not None:
            _, bbox_top, _, bbox_bottom = bounds
            wick_top = bbox_top if wick_top is None else wick_top
            wick_bottom = bbox_bottom if wick_bottom is None else wick_bottom
        if open_y is None or close_y is None:
            if body_top is not None and body_bottom is not None:
                if explicit_direction == "SELL":
                    open_y = body_top
                    close_y = body_bottom
                else:
                    open_y = body_bottom
                    close_y = body_top
        if all(value is not None for value in (open_y, close_y, wick_top, wick_bottom)):
            open_value = -cast(float, open_y)
            close_value = -cast(float, close_y)
            high_value = -min(cast(float, wick_top), cast(float, wick_bottom))
            low_value = -max(cast(float, wick_top), cast(float, wick_bottom))
            missing = (False, False, False, False)
            source = "PIXEL_OHLC"

    fallback = close_value if close_value is not None else open_value
    if fallback is None:
        audit.reject(root, "no_causal_price_observation")
        return None
    open_value = fallback if open_value is None else open_value
    close_value = fallback if close_value is None else close_value
    high_value = max(open_value, close_value) if high_value is None else high_value
    low_value = min(open_value, close_value) if low_value is None else low_value
    coherent_high = max(high_value, open_value, close_value)
    coherent_low = min(low_value, open_value, close_value)
    if coherent_high != high_value or coherent_low != low_value:
        audit.reject(root, "incoherent_ohlc_repaired")
    high_value = coherent_high
    low_value = coherent_low

    direction = _side(row.get("direction") or row.get("side") or row.get("color"))
    for key in ("direction", "side", "color"):
        if key in row and row.get(key) not in (None, ""):
            audit.consume(f"{root}.{key}")
            break
    if direction == "HOLD":
        direction = "BUY" if close_value > open_value else "SELL" if close_value < open_value else "HOLD"

    timestamp, _ = _first_number(
        row,
        ("timestamp", "time", "epoch", "closed_candle_epoch", "t"),
        root=root,
        audit=audit,
    )
    parse_confidence, _ = _first_number(
        row,
        ("parse_confidence", "confidence", "detection_confidence"),
        root=root,
        audit=audit,
    )
    return _Candle(
        source_index=index,
        open=float(open_value),
        high=float(high_value),
        low=float(low_value),
        close=float(close_value),
        direction=direction,
        price_source=source or "INFERRED",
        missing_ohlc=missing,
        bbox=bounds,
        timestamp=timestamp,
        parse_confidence=parse_confidence,
    )


@dataclass
class _FeatureSet:
    numeric: dict[str, float] = field(
        default_factory=lambda: {name: 0.0 for name in CONTEXT_NUMERIC_BASE_SCHEMA}
    )
    numeric_missing: dict[str, float] = field(
        default_factory=lambda: {name: 1.0 for name in CONTEXT_NUMERIC_BASE_SCHEMA}
    )
    categorical: dict[str, str] = field(
        default_factory=lambda: {name: MISSING_CATEGORY for name in CONTEXT_CATEGORICAL_SCHEMA}
    )
    categorical_missing: dict[str, int] = field(
        default_factory=lambda: {name: 1 for name in CONTEXT_CATEGORICAL_SCHEMA}
    )

    def number(self, name: str, value: float | None) -> None:
        if value is None or not math.isfinite(value):
            return
        low, high = _CONTEXT_LIMITS[name]
        self.numeric[name] = _clip(value, low, high)
        self.numeric_missing[name] = 0.0

    def category(self, name: str, value: Any) -> None:
        normalized = _normalized_category(value)
        if normalized == MISSING_CATEGORY:
            return
        self.categorical[name] = normalized
        self.categorical_missing[name] = 0


def _mapping_number(
    features: _FeatureSet,
    *,
    feature: str,
    source: Mapping[str, Any],
    source_root: str,
    aliases: Sequence[str],
    audit: _Audit,
) -> None:
    for key in aliases:
        if key not in source:
            continue
        path = f"{source_root}.{key}"
        value = _finite(source.get(key))
        if value is None:
            audit.reject(path, "non_finite_or_invalid_numeric")
            break
        audit.consume(path)
        features.number(feature, value)
        return
    audit.miss(f"{source_root}.{aliases[0]}")


def _mapping_category(
    features: _FeatureSet,
    *,
    feature: str,
    source: Mapping[str, Any],
    source_root: str,
    aliases: Sequence[str],
    audit: _Audit,
) -> None:
    for key in aliases:
        if key not in source or source.get(key) in (None, ""):
            continue
        audit.consume(f"{source_root}.{key}")
        features.category(feature, source.get(key))
        return
    audit.miss(f"{source_root}.{aliases[0]}")


def _numbers_from_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    aliases: Sequence[str],
    root: str,
    audit: _Audit,
) -> list[float]:
    values: list[float] = []
    for index, row in enumerate(rows):
        for key in aliases:
            if key not in row:
                continue
            path = f"{root}[{index}].{key}"
            number = _finite(row.get(key))
            if number is None:
                audit.reject(path, "non_finite_or_invalid_numeric")
            else:
                audit.consume(path)
                values.append(number)
            break
    return values


def _sides_from_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    root: str,
    audit: _Audit,
) -> list[str]:
    output: list[str] = []
    for index, row in enumerate(rows):
        value = None
        used_key = ""
        for key in ("direction", "side", "role", "type"):
            if key in row and row.get(key) not in (None, ""):
                value = row.get(key)
                used_key = key
                break
        if not used_key:
            output.append("HOLD")
            continue
        audit.consume(f"{root}[{index}].{used_key}")
        output.append(_side(value))
    return output


def _projection_features(
    features: _FeatureSet,
    projection: Mapping[str, Any],
    audit: _Audit,
) -> None:
    _mapping_number(
        features,
        feature="projection.confidence",
        source=projection,
        source_root="projection",
        aliases=("confidence",),
        audit=audit,
    )
    _mapping_number(
        features,
        feature="projection.dominance",
        source=projection,
        source_root="projection",
        aliases=("dominance", "edge"),
        audit=audit,
    )
    _mapping_category(
        features,
        feature="projection.direction",
        source=projection,
        source_root="projection",
        aliases=("direction", "side"),
        audit=audit,
    )
    zones = _rows(projection.get("zones"))
    if "zones" in projection and not zones and projection.get("zones") not in (None, []):
        audit.reject("projection.zones", "expected_sequence_of_mappings")
    if not zones:
        audit.miss("projection.zones")
        return
    features.number("projection.zone_count", float(len(zones)))
    sides = _sides_from_rows(zones, root="projection.zones", audit=audit)
    confidences = _numbers_from_rows(
        zones,
        aliases=("confidence", "score"),
        root="projection.zones",
        audit=audit,
    )
    features.number("projection.buy_zone_fraction", sides.count("BUY") / len(zones))
    features.number("projection.sell_zone_fraction", sides.count("SELL") / len(zones))
    if confidences:
        features.number("projection.zone_confidence_mean", statistics.fmean(confidences))
        features.number("projection.zone_confidence_max", max(confidences))
    for index, row in enumerate(zones):
        if "kind" in row:
            audit.consume(f"projection.zones[{index}].kind")
        for key in ("bbox", "target_bbox", "path", "invalidation_y"):
            if key in row:
                reason = (
                    "forward_projection_geometry_not_observed"
                    if key in {"target_bbox", "path"}
                    else "projection_geometry_excluded_from_forecast_features"
                )
                audit.reject(f"projection.zones[{index}].{key}", reason)


_CANDLE_STAT_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("sample_size", ("sample_size",)),
    ("sample_weight", ("sample_weight",)),
    ("buy_count", ("buy_count",)),
    ("sell_count", ("sell_count",)),
    ("buy_ratio", ("buy_ratio",)),
    ("sell_ratio", ("sell_ratio",)),
    ("recent_buy_count", ("recent_buy_count",)),
    ("recent_sell_count", ("recent_sell_count",)),
    ("recent_buy_ratio", ("recent_buy_ratio",)),
    ("recent_sell_ratio", ("recent_sell_ratio",)),
    ("direction_run", ("direction_run",)),
    ("opposite_run", ("opposite_run",)),
    ("candidate_ratio", ("candidate_ratio",)),
    ("opposing_ratio", ("opposing_ratio",)),
    ("momentum_consistency", ("momentum_consistency",)),
    ("normalized_volatility", ("normalized_volatility",)),
    ("average_step", ("average_step",)),
)


def _candle_statistics_features(
    features: _FeatureSet,
    stats: Mapping[str, Any],
    audit: _Audit,
) -> None:
    for suffix, aliases in _CANDLE_STAT_FIELDS:
        _mapping_number(
            features,
            feature=f"candle_statistics.{suffix}",
            source=stats,
            source_root="candle_statistics",
            aliases=aliases,
            audit=audit,
        )


def _behavior_features(
    features: _FeatureSet,
    behavior: Mapping[str, Any],
    audit: _Audit,
) -> None:
    _mapping_number(
        features,
        feature="behavior.state_confidence",
        source=behavior,
        source_root="behavior_payload",
        aliases=("state_confidence",),
        audit=audit,
    )
    for feature, aliases in (
        ("behavior.current_state", ("current_state",)),
        ("behavior.previous_state", ("previous_state",)),
        ("behavior.next_state", ("next_most_likely_state",)),
        ("behavior.trend_phase", ("trend_phase",)),
        ("behavior.move_quality", ("move_quality",)),
    ):
        _mapping_category(
            features,
            feature=feature,
            source=behavior,
            source_root="behavior_payload",
            aliases=aliases,
            audit=audit,
        )
    counts = _mapping(behavior.get("behavior_counts"))
    for key in (
        "rejection_count",
        "compression_count",
        "impulse_count",
        "pullback_count",
        "pause_count",
        "exhaustion_count",
        "reversal_count",
    ):
        _mapping_number(
            features,
            feature=f"behavior.{key}",
            source=counts,
            source_root="behavior_payload.behavior_counts",
            aliases=(key,),
            audit=audit,
        )
    box = _mapping(behavior.get("box_context"))
    for feature, aliases in (
        ("behavior.box.candles_seen", ("candles_seen_in_box",)),
        ("behavior.box.entry_quality", ("entry_quality",)),
        ("behavior.box.rejection_count", ("rejection_count",)),
        ("behavior.box.acceptance_count", ("acceptance_count",)),
        ("behavior.box.compression_score", ("compression_score",)),
        ("behavior.box.momentum_exit_score", ("momentum_exit_score",)),
        ("behavior.box.failure_risk", ("failure_risk",)),
    ):
        _mapping_number(
            features,
            feature=feature,
            source=box,
            source_root="behavior_payload.box_context",
            aliases=aliases,
            audit=audit,
        )
    for feature, aliases in (
        ("behavior.box.type", ("box_type",)),
        ("behavior.box.state", ("behavior_state",)),
    ):
        _mapping_category(
            features,
            feature=feature,
            source=box,
            source_root="behavior_payload.box_context",
            aliases=aliases,
            audit=audit,
        )
    trend = _mapping(behavior.get("trend_context"))
    for feature, aliases in (
        ("behavior.trend.slope_global", ("slope_global",)),
        ("behavior.trend.slope_local", ("slope_local",)),
        ("behavior.trend.slope_current", ("slope_current",)),
        ("behavior.trend.strength", ("trend_strength",)),
        ("behavior.trend.recent_range", ("recent_range",)),
    ):
        _mapping_number(
            features,
            feature=feature,
            source=trend,
            source_root="behavior_payload.trend_context",
            aliases=aliases,
            audit=audit,
        )
    for feature, aliases in (
        ("behavior.trend.global_bias", ("global_bias",)),
        ("behavior.trend.local_bias", ("local_bias",)),
        ("behavior.trend.micro_bias", ("micro_bias",)),
    ):
        _mapping_category(
            features,
            feature=feature,
            source=trend,
            source_root="behavior_payload.trend_context",
            aliases=aliases,
            audit=audit,
        )

    probabilities = _mapping(behavior.get("next_state_probs"))
    parsed: list[tuple[str, float]] = []
    for key in sorted(probabilities):
        path = f"behavior_payload.next_state_probs.{key}"
        number = _finite(probabilities.get(key))
        if number is None:
            audit.reject(path, "non_finite_or_invalid_probability")
            continue
        audit.consume(path)
        parsed.append((key.lower(), _clip(number, 0.0, 1.0)))
    if not parsed:
        audit.miss("behavior_payload.next_state_probs")
        return
    total = sum(value for _, value in parsed)
    normalized = [(key, value / total) for key, value in parsed] if total > 0.0 else parsed
    features.number(
        "behavior.next.buy_probability",
        sum(value for key, value in normalized if "bullish" in key or key.startswith("buy")),
    )
    features.number(
        "behavior.next.sell_probability",
        sum(value for key, value in normalized if "bearish" in key or key.startswith("sell")),
    )
    features.number(
        "behavior.next.continuation_probability",
        sum(value for key, value in normalized if "continuation" in key or "breakout_attempt" in key),
    )
    features.number(
        "behavior.next.reversal_probability",
        sum(value for key, value in normalized if "reversal" in key or "failed_breakout" in key),
    )
    features.number(
        "behavior.next.pause_probability",
        sum(value for key, value in normalized if "pause" in key or "compression" in key),
    )
    features.number("behavior.next.max_probability", max(value for _, value in normalized))
    entropy = -sum(value * math.log(value) for _, value in normalized if value > 0.0)
    max_entropy = math.log(max(2, len(normalized)))
    features.number("behavior.next.entropy", entropy / max_entropy)


_DECISION_NUMERIC_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("bias_strength", ("bias_strength",)),
    ("setup_age_candles", ("setup_age_candles",)),
    ("freshness", ("freshness",)),
    ("structure_alignment", ("structure_alignment",)),
    ("buy_evidence", ("buy_evidence",)),
    ("sell_evidence", ("sell_evidence",)),
    ("net_bias", ("net_bias",)),
    ("conflict_score", ("conflict_score",)),
    ("belief_buy", ("belief_buy",)),
    ("belief_sell", ("belief_sell",)),
    ("belief_hold", ("belief_hold",)),
    ("belief_uncertainty", ("belief_uncertainty",)),
    ("belief_conflict", ("belief_conflict",)),
    ("directional_edge", ("directional_edge",)),
    ("evidence_mass", ("evidence_mass",)),
    ("usable_bias", ("usable_bias",)),
    ("distance_to_trigger", ("distance_to_trigger",)),
    ("distance_to_target", ("distance_to_target",)),
    ("distance_to_invalidation", ("distance_to_invalidation",)),
    ("eta_trigger_candles", ("eta_trigger_candles",)),
    ("eta_target_after_trigger_candles", ("eta_target_after_trigger_candles",)),
    ("eta_invalidation_candles", ("eta_invalidation_candles",)),
    ("target_horizon_candles", ("target_horizon_candles",)),
    ("stale_after_candles", ("stale_after_candles",)),
    ("p_trigger_next_1", ("p_trigger_next_1",)),
    ("p_trigger_next_3", ("p_trigger_next_3",)),
    ("p_target_before_invalidation", ("p_target_before_invalidation",)),
    ("p_expire_before_trigger", ("p_expire_before_trigger",)),
    ("hazard_trigger", ("hazard_trigger",)),
    ("hazard_invalidation", ("hazard_invalidation",)),
    ("hazard_expiry", ("hazard_expiry",)),
    ("expected_value_r", ("expected_value_R", "expected_value_r")),
    ("raw_expected_value_r", ("raw_expected_value_R", "raw_expected_value_r")),
    ("uncertainty_tax_r", ("uncertainty_tax_R", "uncertainty_tax_r")),
    ("reward_r", ("reward_R", "reward_r")),
    ("loss_r", ("loss_R", "loss_r")),
    ("cost_r", ("cost_R", "cost_r")),
    ("major_trend_confidence", ("major_trend_confidence",)),
    ("p_next_buy", ("p_next_buy",)),
    ("p_next_sell", ("p_next_sell",)),
    ("p_next_hold", ("p_next_hold",)),
    ("countertrend_window_candles", ("countertrend_window_candles",)),
    ("trend_follow_window_candles", ("trend_follow_window_candles",)),
    ("hold_for_candles", ("hold_for_candles",)),
)

_DECISION_CATEGORY_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("dominant_side", ("dominant_side",)),
    ("major_trend_side", ("major_trend_side",)),
    ("state", ("state",)),
    ("next_event", ("next_most_likely_event",)),
    ("confidence_tier", ("confidence_tier",)),
    ("firewall_action", ("firewall_action",)),
    ("decision", ("decision",)),
    ("next_candle_bias", ("next_candle_bias",)),
    ("trade_mode", ("trade_mode",)),
    ("execution_side", ("candle_execution_side",)),
    ("countertrend_side", ("countertrend_side",)),
)


def _decision_features(
    features: _FeatureSet,
    decision: Mapping[str, Any],
    audit: _Audit,
) -> None:
    for suffix, aliases in _DECISION_NUMERIC_FIELDS:
        _mapping_number(
            features,
            feature=f"decision.{suffix}",
            source=decision,
            source_root="decision_kernel",
            aliases=aliases,
            audit=audit,
        )
    for suffix, aliases in _DECISION_CATEGORY_FIELDS:
        _mapping_category(
            features,
            feature=f"decision.{suffix}",
            source=decision,
            source_root="decision_kernel",
            aliases=aliases,
            audit=audit,
        )


def _smart_money_features(
    features: _FeatureSet,
    smart_money: Mapping[str, Any],
    audit: _Audit,
) -> None:
    _mapping_number(
        features,
        feature="smart_money.confidence",
        source=smart_money,
        source_root="smart_money_context",
        aliases=("confidence",),
        audit=audit,
    )
    _mapping_category(
        features,
        feature="smart_money.dominant_side",
        source=smart_money,
        source_root="smart_money_context",
        aliases=("dominant_side",),
        audit=audit,
    )
    adjustment = _mapping(smart_money.get("decision_adjustment"))
    _mapping_number(
        features,
        feature="smart_money.confidence_delta",
        source=adjustment,
        source_root="smart_money_context.decision_adjustment",
        aliases=("confidence_delta",),
        audit=audit,
    )
    _mapping_number(
        features,
        feature="smart_money.risk_delta",
        source=adjustment,
        source_root="smart_money_context.decision_adjustment",
        aliases=("risk_delta",),
        audit=audit,
    )
    _mapping_category(
        features,
        feature="smart_money.adjustment_side",
        source=adjustment,
        source_root="smart_money_context.decision_adjustment",
        aliases=("side",),
        audit=audit,
    )

    groups = (
        ("order_blocks", "smart_money.order_block_count"),
        ("fair_value_gaps", "smart_money.fair_value_gap_count"),
        ("liquidity_sweeps", "smart_money.liquidity_sweep_count"),
        ("liquidity_pools", "smart_money.liquidity_pool_count"),
    )
    all_rows: list[tuple[str, int, dict[str, Any]]] = []
    for key, feature in groups:
        rows = _rows(smart_money.get(key))
        if key in smart_money:
            audit.consume(f"smart_money_context.{key}") if not rows else None
        if not rows:
            audit.miss(f"smart_money_context.{key}")
            continue
        features.number(feature, float(len(rows)))
        all_rows.extend((key, index, row) for index, row in enumerate(rows))
    sides: list[str] = []
    confidences: list[float] = []
    ages: list[float] = []
    fresh = 0
    mitigated = 0
    for group, index, row in all_rows:
        root = f"smart_money_context.{group}[{index}]"
        side_value = row.get("direction", row.get("side", row.get("role")))
        if side_value not in (None, ""):
            used_key = "direction" if "direction" in row else "side" if "side" in row else "role"
            audit.consume(f"{root}.{used_key}")
        sides.append(_side(side_value))
        for key in ("confidence", "score", "strength"):
            if key in row:
                value = _finite(row.get(key))
                if value is not None:
                    audit.consume(f"{root}.{key}")
                    confidences.append(value)
                else:
                    audit.reject(f"{root}.{key}", "non_finite_or_invalid_numeric")
                break
        for key in ("age_candles", "age", "source_age_candles"):
            if key in row:
                value = _finite(row.get(key))
                if value is not None:
                    audit.consume(f"{root}.{key}")
                    ages.append(value)
                break
        if "mitigated" in row:
            audit.consume(f"{root}.mitigated")
            mitigated += int(bool(_explicit_bool(row.get("mitigated"))))
        freshness = _normalized_category(row.get("freshness_state") or row.get("mitigation_state"), default="")
        if freshness:
            used_key = "freshness_state" if "freshness_state" in row else "mitigation_state"
            audit.consume(f"{root}.{used_key}")
            fresh += int(any(token in freshness for token in ("FRESH", "OPEN", "UNMITIGATED")))
    if all_rows:
        total = len(all_rows)
        features.number("smart_money.buy_object_fraction", sides.count("BUY") / total)
        features.number("smart_money.sell_object_fraction", sides.count("SELL") / total)
        features.number("smart_money.fresh_object_fraction", fresh / total)
        features.number("smart_money.mitigated_object_fraction", mitigated / total)
    if confidences:
        features.number("smart_money.object_confidence_mean", statistics.fmean(confidences))
        features.number("smart_money.object_confidence_max", max(confidences))
    if ages:
        features.number("smart_money.mean_age_candles", statistics.fmean(ages))

    shift = _mapping(smart_money.get("market_structure_shift"))
    _mapping_number(
        features,
        feature="smart_money.structure_shift_confidence",
        source=shift,
        source_root="smart_money_context.market_structure_shift",
        aliases=("confidence", "score"),
        audit=audit,
    )
    _mapping_category(
        features,
        feature="smart_money.structure_shift_direction",
        source=shift,
        source_root="smart_money_context.market_structure_shift",
        aliases=("direction", "side"),
        audit=audit,
    )


def _support_resistance_features(
    features: _FeatureSet,
    context: Mapping[str, Any],
    zones: Sequence[Mapping[str, Any]],
    *,
    zone_root: str,
    audit: _Audit,
) -> None:
    for suffix, aliases in (
        ("significant_count", ("significant_count",)),
        ("institutional_zone_count", ("institutional_zone_count",)),
        ("fresh_zone_count", ("fresh_zone_count",)),
        ("reference_zone_count", ("reference_zone_count",)),
        ("active_authority_count", ("active_authority_count",)),
        ("buy_structure_score", ("buy_structure_score",)),
        ("sell_structure_score", ("sell_structure_score",)),
    ):
        _mapping_number(
            features,
            feature=f"support_resistance.{suffix}",
            source=context,
            source_root="support_resistance_context",
            aliases=aliases,
            audit=audit,
        )
    for suffix, aliases in (
        ("dominant_side", ("dominant_side",)),
        ("candidate_side", ("candidate_side",)),
    ):
        _mapping_category(
            features,
            feature=f"support_resistance.{suffix}",
            source=context,
            source_root="support_resistance_context",
            aliases=aliases,
            audit=audit,
        )
    if not zones:
        audit.miss("support_resistance_zones")
        return
    features.number("support_resistance.zone_count", float(len(zones)))
    sides = _sides_from_rows(zones, root=zone_root, audit=audit)
    roles: list[str] = []
    confidences: list[float] = []
    significance: list[float] = []
    distances: list[float] = []
    touches: list[float] = []
    ages: list[float] = []
    fresh_count = 0
    authority_count = 0
    institutional_count = 0
    for index, row in enumerate(zones):
        root = f"{zone_root}[{index}]"
        role = _normalized_category(row.get("role") or row.get("kind") or row.get("type"), default="HOLD")
        if any(key in row for key in ("role", "kind", "type")):
            key = "role" if "role" in row else "kind" if "kind" in row else "type"
            audit.consume(f"{root}.{key}")
        roles.append(role)
        for output, aliases in (
            (confidences, ("confidence", "score")),
            (significance, ("significance_score", "historical_significance", "anchor_quality")),
            (distances, ("distance_to_latest_norm", "distance_norm", "distance_to_price")),
            (touches, ("touch_count", "wick_probe_count", "mitigation_count")),
            (ages, ("age_candles", "age", "source_age_candles")),
        ):
            for key in aliases:
                if key not in row:
                    continue
                value = _finite(row.get(key))
                if value is None:
                    audit.reject(f"{root}.{key}", "non_finite_or_invalid_numeric")
                else:
                    audit.consume(f"{root}.{key}")
                    output.append(value)
                break
        freshness = _normalized_category(row.get("freshness_state"), default="")
        if "freshness_state" in row:
            audit.consume(f"{root}.freshness_state")
        fresh_count += int(freshness in {"FRESH", "TESTED_ONCE", "ACTIVE"})
        for key in ("entry_authority_allowed", "active_authority", "still_significant"):
            if key in row:
                audit.consume(f"{root}.{key}")
                authority_count += int(bool(_explicit_bool(row.get(key))))
                break
        institutional = _finite(row.get("institutional_zone_score"))
        if institutional is not None:
            audit.consume(f"{root}.institutional_zone_score")
            institutional_count += int(institutional >= 0.52)
    total = len(zones)
    features.number("support_resistance.support_fraction", roles.count("SUPPORT") / total)
    features.number("support_resistance.resistance_fraction", roles.count("RESISTANCE") / total)
    features.number("support_resistance.buy_zone_fraction", sides.count("BUY") / total)
    features.number("support_resistance.sell_zone_fraction", sides.count("SELL") / total)
    features.number("support_resistance.fresh_fraction", fresh_count / total)
    features.number("support_resistance.authority_fraction", authority_count / total)
    features.number("support_resistance.institutional_fraction", institutional_count / total)
    if confidences:
        features.number("support_resistance.confidence_mean", statistics.fmean(confidences))
        features.number("support_resistance.confidence_max", max(confidences))
    if significance:
        features.number("support_resistance.significance_mean", statistics.fmean(significance))
    if distances:
        features.number("support_resistance.nearest_distance", min(abs(value) for value in distances))
    if touches:
        features.number("support_resistance.mean_touch_count", statistics.fmean(touches))
    if ages:
        features.number("support_resistance.mean_age_candles", statistics.fmean(ages))


def _trend_features(
    features: _FeatureSet,
    slopes: Mapping[str, Any],
    directions: Mapping[str, Any],
    audit: _Audit,
) -> None:
    aliases_by_level = {
        "global": ("global", "global_slope", "slope_global"),
        "local": ("local", "local_slope", "slope_local"),
        "current": ("current", "current_slope", "slope_current"),
        "impulse": ("impulse", "impulse_slope", "slope_impulse", "impulse_delta"),
    }
    for level, aliases in aliases_by_level.items():
        _mapping_number(
            features,
            feature=f"trend.slope_{level}",
            source=slopes,
            source_root="trend_slopes",
            aliases=aliases,
            audit=audit,
        )
        direction_aliases = tuple(alias.replace("slope", "direction") for alias in aliases)
        _mapping_category(
            features,
            feature=f"trend.direction_{level}",
            source=directions,
            source_root="trend_directions",
            aliases=direction_aliases,
            audit=audit,
        )


def _timeframe_seconds(timeframe: str) -> float | None:
    normalized = _normalized_category(timeframe, default="")
    if normalized in TIMEFRAME_SECONDS:
        return float(TIMEFRAME_SECONDS[normalized])
    match = re.fullmatch(r"([MHD])(\d+)", normalized)
    if not match:
        return None
    multiplier = {"M": 60, "H": 3600, "D": 86400}[match.group(1)]
    return float(multiplier * int(match.group(2)))


def _sequence_payload(
    candles: Sequence[_Candle],
    *,
    timeframe_seconds: float | None,
) -> tuple[dict[str, Any], float, list[float], list[float]]:
    if not candles:
        return (
            {
                "numeric_schema": list(CANDLE_NUMERIC_SCHEMA),
                "categorical_schema": list(CANDLE_CATEGORICAL_SCHEMA),
                "numeric_rows": [],
                "categorical_rows": [],
                "categorical_missing_rows": [],
                "source_indices": [],
            },
            1.0,
            [],
            [],
        )
    ranges = [max(0.0, candle.high - candle.low) for candle in candles]
    close_deltas = [abs(right.close - left.close) for left, right in zip(candles, candles[1:])]
    positive_scale = [value for value in [*ranges, *close_deltas] if value > 1e-12]
    anchor = candles[-1].close
    scale_floor = max(abs(anchor) * 1e-6, 1e-6)
    scale = max(scale_floor, statistics.median(positive_scale) if positive_scale else scale_floor)
    boxes = [candle.bbox for candle in candles if candle.bbox is not None]
    x_min = min(box[0] for box in boxes) if boxes else 0.0
    x_max = max(box[2] for box in boxes) if boxes else float(max(1, len(candles) - 1))
    y_min = min(box[1] for box in boxes) if boxes else 0.0
    y_max = max(box[3] for box in boxes) if boxes else 1.0
    widths = [box[2] - box[0] for box in boxes]
    heights = [box[3] - box[1] for box in boxes]
    median_width = statistics.median(widths) if widths else 1.0
    median_height = statistics.median(heights) if heights else 1.0
    last_timestamp = candles[-1].timestamp
    numeric_rows: list[list[float]] = []
    categorical_rows: list[list[str]] = []
    categorical_missing_rows: list[list[int]] = []
    normalized_closes: list[float] = []
    normalized_ranges: list[float] = []
    for position, candle in enumerate(candles):
        previous_close = candles[position - 1].close if position > 0 else candle.open
        candle_range = max(0.0, candle.high - candle.low)
        body = abs(candle.close - candle.open)
        upper_wick = max(0.0, candle.high - max(candle.open, candle.close))
        lower_wick = max(0.0, min(candle.open, candle.close) - candle.low)
        relative_position = position / max(1, len(candles) - 1)
        elapsed_steps = float(position - (len(candles) - 1))
        timestamp_gap: float | None = None
        if candle.timestamp is not None and last_timestamp is not None and timeframe_seconds:
            timestamp_gap = (candle.timestamp - last_timestamp) / timeframe_seconds
        if candle.bbox is not None:
            x0, y0, x1, y1 = candle.bbox
            center_x = ((x0 + x1) * 0.5 - x_min) / max(1e-9, x_max - x_min)
            center_y = ((y0 + y1) * 0.5 - y_min) / max(1e-9, y_max - y_min)
            width_norm = (x1 - x0) / max(1e-9, median_width)
            height_norm = (y1 - y0) / max(1e-9, median_height)
            geometry_missing = False
        else:
            center_x = relative_position
            center_y = 0.5
            width_norm = 1.0
            height_norm = 1.0
            geometry_missing = True
        base = {
            "open_offset": _clip((candle.open - anchor) / scale, -32.0, 32.0),
            "high_offset": _clip((candle.high - anchor) / scale, -32.0, 32.0),
            "low_offset": _clip((candle.low - anchor) / scale, -32.0, 32.0),
            "close_offset": _clip((candle.close - anchor) / scale, -32.0, 32.0),
            "close_delta": _clip((candle.close - previous_close) / scale, -32.0, 32.0),
            "range_scaled": _clip(candle_range / scale, 0.0, 32.0),
            "body_scaled": _clip(body / scale, 0.0, 32.0),
            "upper_wick_scaled": _clip(upper_wick / scale, 0.0, 32.0),
            "lower_wick_scaled": _clip(lower_wick / scale, 0.0, 32.0),
            "body_fraction": _clip(body / max(1e-12, candle_range), 0.0, 1.0),
            "upper_wick_fraction": _clip(upper_wick / max(1e-12, candle_range), 0.0, 1.0),
            "lower_wick_fraction": _clip(lower_wick / max(1e-12, candle_range), 0.0, 1.0),
            "direction_value": 1.0 if candle.direction == "BUY" else -1.0 if candle.direction == "SELL" else 0.0,
            "relative_position": relative_position,
            "elapsed_steps": _clip(elapsed_steps, -float(MAX_CANDLES), 0.0),
            "timestamp_gap_steps": _clip(timestamp_gap or 0.0, -float(MAX_CANDLES * 4), 0.0),
            "center_x_norm": _clip(center_x, 0.0, 1.0),
            "center_y_norm": _clip(center_y, 0.0, 1.0),
            "width_vs_median": _clip(width_norm, 0.0, 16.0),
            "height_vs_median": _clip(height_norm, 0.0, 16.0),
            "parse_confidence": _clip(candle.parse_confidence or 0.0, 0.0, 1.0),
            "ohlc_inferred": float(candle.price_source != "OHLC"),
        }
        missing = {name: 0.0 for name in CANDLE_NUMERIC_BASE_SCHEMA}
        for name, is_missing in zip(
            ("open_offset", "high_offset", "low_offset", "close_offset"),
            candle.missing_ohlc,
        ):
            missing[name] = float(is_missing)
        missing["timestamp_gap_steps"] = float(timestamp_gap is None)
        for name in ("center_x_norm", "center_y_norm", "width_vs_median", "height_vs_median"):
            missing[name] = float(geometry_missing)
        missing["parse_confidence"] = float(candle.parse_confidence is None)
        numeric_rows.append(
            [base[name] for name in CANDLE_NUMERIC_BASE_SCHEMA]
            + [missing[name] for name in CANDLE_NUMERIC_BASE_SCHEMA]
        )
        categorical_rows.append([candle.direction, candle.price_source])
        categorical_missing_rows.append([0, 0])
        normalized_closes.append((candle.close - anchor) / scale)
        normalized_ranges.append(candle_range / scale)
    return (
        {
            "numeric_schema": list(CANDLE_NUMERIC_SCHEMA),
            "categorical_schema": list(CANDLE_CATEGORICAL_SCHEMA),
            "numeric_rows": numeric_rows,
            "categorical_rows": categorical_rows,
            "categorical_missing_rows": categorical_missing_rows,
            "source_indices": [candle.source_index for candle in candles],
        },
        scale,
        normalized_closes,
        normalized_ranges,
    )


def _candle_context_features(
    features: _FeatureSet,
    closes: Sequence[float],
    ranges: Sequence[float],
    directions: Sequence[str],
) -> None:
    for window in (3, 6, 12):
        if len(closes) >= 2:
            segment = closes[-min(window, len(closes)) :]
            features.number(f"candle.net_move_{window}", segment[-1] - segment[0])
    if len(closes) >= 2:
        segment = closes[-min(12, len(closes)) :]
        deltas = [right - left for left, right in zip(segment, segment[1:])]
        if deltas:
            features.number(
                "candle.historical_volatility_12",
                math.sqrt(statistics.fmean(value * value for value in deltas)),
            )
    if ranges:
        features.number("candle.mean_range_12", statistics.fmean(ranges[-12:]))
    recent_directions = list(directions[-12:])
    if len(recent_directions) >= 2:
        comparable = [
            (left, right)
            for left, right in zip(recent_directions, recent_directions[1:])
            if left in {"BUY", "SELL"} and right in {"BUY", "SELL"}
        ]
        if comparable:
            features.number(
                "candle.direction_flip_rate_12",
                sum(1 for left, right in comparable if left != right) / len(comparable),
            )


def _schema_fingerprint() -> str:
    payload = {
        "version": SCHEMA_VERSION,
        "candle_numeric": CANDLE_NUMERIC_SCHEMA,
        "candle_categorical": CANDLE_CATEGORICAL_SCHEMA,
        "context_numeric": CONTEXT_NUMERIC_SCHEMA,
        "context_categorical": CONTEXT_CATEGORICAL_SCHEMA,
    }
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


SCHEMA_FINGERPRINT = _schema_fingerprint()


def extract_scene_forecast_features_v3(
    *,
    candles: Sequence[Mapping[str, Any]] | None,
    projection: Mapping[str, Any] | None = None,
    candle_statistics: Mapping[str, Any] | None = None,
    behavior_payload: Mapping[str, Any] | None = None,
    decision_kernel: Mapping[str, Any] | None = None,
    smart_money_context: Mapping[str, Any] | None = None,
    support_resistance_context: Mapping[str, Any] | None = None,
    support_resistance_zones: Sequence[Mapping[str, Any]] | None = None,
    trend_slopes: Mapping[str, Any] | None = None,
    trend_directions: Mapping[str, Any] | None = None,
    timeframe: str | None = None,
    pair: str | None = None,
) -> dict[str, Any]:
    """Build the immutable V3 causal scene-covariate contract.

    The function is deliberately pure and has no runtime/model dependency. It
    consumes closed candles plus a bounded whitelist of contemporaneous suite
    outputs. Unknown fields, free text, projected path geometry, outcome labels,
    and the forming candle are reported but never enter the feature vectors.
    """

    raw_candles = _rows(candles or [])
    projection_map = _mapping(projection)
    statistics_map = _mapping(candle_statistics)
    behavior_map = _mapping(behavior_payload)
    decision_map = _mapping(decision_kernel)
    smart_money_map = _mapping(smart_money_context)
    support_context_map = _mapping(support_resistance_context)
    zone_rows = _rows(support_resistance_zones or [])
    slopes_map = _mapping(trend_slopes)
    directions_map = _mapping(trend_directions)
    audit = _Audit()
    features = _FeatureSet()

    closed_rows: list[tuple[int, dict[str, Any]]] = []
    excluded_forming = 0
    for index, row in enumerate(raw_candles):
        if _candle_closed_status(row, index=index, total=len(raw_candles), audit=audit):
            closed_rows.append((index, row))
        else:
            excluded_forming += 1
            audit.reject(f"candles[{index}]", "forming_or_unclosed_candle_excluded")
    truncated = max(0, len(closed_rows) - MAX_CANDLES)
    if truncated:
        for index, _ in closed_rows[:truncated]:
            audit.reject(f"candles[{index}]", "outside_bounded_history_window")
        closed_rows = closed_rows[-MAX_CANDLES:]
    parsed_candles = [
        parsed
        for index, row in closed_rows
        if (parsed := _candle_values(row, index=index, audit=audit)) is not None
    ]

    timeframe_text = str(timeframe or "")
    seconds = _timeframe_seconds(timeframe_text)
    sequence, price_scale, closes, ranges = _sequence_payload(
        parsed_candles,
        timeframe_seconds=seconds,
    )
    features.number("meta.closed_candle_count", float(len(parsed_candles)))
    features.number("meta.excluded_forming_count", float(excluded_forming))
    features.number("meta.history_truncated", float(bool(truncated)))
    features.number("meta.price_scale", price_scale if parsed_candles else None)
    features.number("meta.timeframe_seconds", seconds)
    if seconds is None:
        audit.miss("timeframe")
    elif timeframe is not None:
        audit.consume("timeframe")
    features.category("pair", pair)
    features.category("timeframe", timeframe)
    if pair not in (None, ""):
        audit.consume("pair")
    else:
        audit.miss("pair")
    if timeframe not in (None, ""):
        audit.consume("timeframe")
    _candle_context_features(
        features,
        closes,
        ranges,
        [candle.direction for candle in parsed_candles],
    )

    _projection_features(features, projection_map, audit)
    _candle_statistics_features(features, statistics_map, audit)
    _behavior_features(features, behavior_map, audit)
    _decision_features(features, decision_map, audit)
    _smart_money_features(features, smart_money_map, audit)

    zone_root = "support_resistance_zones"
    if not zone_rows:
        zone_rows = _rows(support_context_map.get("significant_zones"))
        if zone_rows:
            zone_root = "support_resistance_context.significant_zones"
    _support_resistance_features(
        features,
        support_context_map,
        zone_rows,
        zone_root=zone_root,
        audit=audit,
    )
    _trend_features(features, slopes_map, directions_map, audit)

    roots: dict[str, Any] = {
        "candles": raw_candles,
        "projection": projection_map,
        "candle_statistics": statistics_map,
        "behavior_payload": behavior_map,
        "decision_kernel": decision_map,
        "smart_money_context": smart_money_map,
        "support_resistance_context": support_context_map,
        "support_resistance_zones": _rows(support_resistance_zones or []),
        "trend_slopes": slopes_map,
        "trend_directions": directions_map,
        "timeframe": timeframe,
        "pair": pair,
    }
    audit_payload = audit.finalize(roots)
    audit_payload["source_presence"] = {
        "candles": bool(raw_candles),
        "projection": bool(projection_map),
        "candle_statistics": bool(statistics_map),
        "behavior_payload": bool(behavior_map),
        "decision_kernel": bool(decision_map),
        "smart_money_context": bool(smart_money_map),
        "support_resistance_context": bool(support_context_map),
        "support_resistance_zones": bool(zone_rows),
        "trend_slopes": bool(slopes_map),
        "trend_directions": bool(directions_map),
        "timeframe": bool(timeframe),
        "pair": bool(pair),
    }
    audit_payload["causal_exclusions"] = {
        "forming_candles": excluded_forming,
        "history_rows_outside_window": truncated,
        "projected_geometry_is_feature": False,
        "future_outcome_fields_are_feature": False,
    }

    numeric_values = [features.numeric[name] for name in CONTEXT_NUMERIC_BASE_SCHEMA] + [
        features.numeric_missing[name] for name in CONTEXT_NUMERIC_BASE_SCHEMA
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "schema_fingerprint": SCHEMA_FINGERPRINT,
        "contract": {
            "causal_cut": "CLOSED_CANDLES_ONLY",
            "max_candles": MAX_CANDLES,
            "sequence_order": "OLDEST_TO_NEWEST",
            "unknown_fields": "AUDIT_AND_REJECT",
            "non_finite_numbers": "MISSING_AND_REJECT",
        },
        "context": {
            "numeric_schema": list(CONTEXT_NUMERIC_SCHEMA),
            "numeric_values": numeric_values,
            "numeric_by_name": {
                **features.numeric,
                **{
                    f"{name}__missing": features.numeric_missing[name]
                    for name in CONTEXT_NUMERIC_BASE_SCHEMA
                },
            },
            "categorical_schema": list(CONTEXT_CATEGORICAL_SCHEMA),
            "categorical_values": [
                features.categorical[name] for name in CONTEXT_CATEGORICAL_SCHEMA
            ],
            "categorical_missing": [
                features.categorical_missing[name] for name in CONTEXT_CATEGORICAL_SCHEMA
            ],
            "categorical_by_name": dict(features.categorical),
        },
        "sequence": sequence,
        "audit": audit_payload,
    }


build_scene_forecast_features_v3 = extract_scene_forecast_features_v3


__all__ = [
    "CANDLE_CATEGORICAL_SCHEMA",
    "CANDLE_NUMERIC_SCHEMA",
    "CONTEXT_CATEGORICAL_SCHEMA",
    "CONTEXT_NUMERIC_SCHEMA",
    "MAX_CANDLES",
    "SCHEMA_FINGERPRINT",
    "SCHEMA_VERSION",
    "build_scene_forecast_features_v3",
    "extract_scene_forecast_features_v3",
]
