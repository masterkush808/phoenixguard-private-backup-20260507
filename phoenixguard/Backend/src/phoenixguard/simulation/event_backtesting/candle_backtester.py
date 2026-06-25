from __future__ import annotations

from dataclasses import dataclass, field
from itertools import product
from typing import Any, Iterable, Mapping, Sequence, cast

from phoenixguard.decision.candle_outcome_tracker import track_candle_outcome


EVENT_CANDLE_BACKTESTER_VERSION = "PG_EVENT_CANDLE_BACKTESTER_V1"


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [_mapping(item) for item in cast(Sequence[Any], value) if isinstance(item, Mapping)]


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


def _side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BULL", "BULLISH", "UP", "CALL"}:
        return "BUY"
    if text in {"SELL", "BEAR", "BEARISH", "DOWN", "PUT"}:
        return "SELL"
    return "HOLD"


def _candle_value(row: Mapping[str, Any], *names: str, default: float = 0.0) -> float:
    for name in names:
        if name in row:
            return _float(row.get(name), default)
    return float(default)


def _close(row: Mapping[str, Any]) -> float:
    return _candle_value(row, "close", "c", "price_proxy", "open", "o")


def _open(row: Mapping[str, Any]) -> float:
    return _candle_value(row, "open", "o", "close", "c", "price_proxy")


def _range(row: Mapping[str, Any]) -> float:
    high = _candle_value(row, "high", "h", "close", "c", "price_proxy")
    low = _candle_value(row, "low", "l", "close", "c", "price_proxy")
    return max(0.0, high - low)


def _rolling_mean(values: Sequence[float]) -> float:
    return sum(values) / max(1, len(values))


def _directional_features(candles: Sequence[Mapping[str, Any]], index: int, lookback: int) -> dict[str, float | str]:
    start = max(0, index - max(1, int(lookback)) + 1)
    window = list(candles[start : index + 1])
    if len(window) < 2:
        return {"side": "HOLD", "dominance_margin": 0.0, "angle": 0.0, "volatility": _range(window[0]) if window else 0.0}
    first = _close(window[0])
    last = _close(window[-1])
    delta = last - first
    body_sum = sum(abs(_close(row) - _open(row)) for row in window)
    range_sum = sum(_range(row) for row in window)
    volatility = _rolling_mean([_range(row) for row in window])
    side = "BUY" if delta > 0.0 else "SELL" if delta < 0.0 else "HOLD"
    dominance = min(1.0, abs(delta) / max(range_sum, 1e-9) + body_sum / max(range_sum, 1e-9) * 0.35)
    angle = delta / max(1, len(window) - 1)
    return {"side": side, "dominance_margin": dominance, "angle": angle, "volatility": volatility}


@dataclass(frozen=True)
class CandleBacktestConfig:
    angle_threshold: float = 0.02
    dominance_margin: float = 0.25
    opposing_force_distance: float = 0.18
    entry_quality_score: float = 0.6
    expiry_candles: int = 3
    flip_flop_hysteresis: int = 2
    lookback_candles: int = 5
    name: str = "default"

    def as_dict(self) -> dict[str, Any]:
        return {
            "angle_threshold": self.angle_threshold,
            "dominance_margin": self.dominance_margin,
            "opposing_force_distance": self.opposing_force_distance,
            "entry_quality_score": self.entry_quality_score,
            "expiry_candles": self.expiry_candles,
            "flip_flop_hysteresis": self.flip_flop_hysteresis,
            "lookback_candles": self.lookback_candles,
            "name": self.name,
        }


@dataclass(frozen=True)
class BacktestTrade:
    trade_id: str
    frame_index: int
    side: str
    entry_price: float
    expiry_candles: int
    features: Mapping[str, Any]
    outcome_metrics: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "trade_id": self.trade_id,
            "frame_index": self.frame_index,
            "side": self.side,
            "entry_price": self.entry_price,
            "expiry_candles": self.expiry_candles,
            "features": dict(self.features),
            "outcome_metrics": dict(self.outcome_metrics),
        }


@dataclass(frozen=True)
class CandleBacktestResult:
    version: str
    config: Mapping[str, Any]
    candles_processed: int
    trades: tuple[BacktestTrade, ...] = field(default_factory=tuple)
    blocked_candidates: int = 0

    @property
    def trade_count(self) -> int:
        return len(self.trades)

    def as_dict(self) -> dict[str, Any]:
        win_count = sum(1 for trade in self.trades if str(trade.outcome_metrics.get("final_outcome_proxy")) in {"WIN", "FAVORABLE"})
        loss_count = sum(1 for trade in self.trades if str(trade.outcome_metrics.get("final_outcome_proxy")) in {"LOSS", "ADVERSE"})
        mfe = [_float(trade.outcome_metrics.get("mfe"), 0.0) for trade in self.trades]
        mae = [_float(trade.outcome_metrics.get("mae"), 0.0) for trade in self.trades]
        avg_mfe = sum(mfe) / max(1, len(mfe))
        avg_mae = sum(mae) / max(1, len(mae))
        return {
            "version": self.version,
            "config": dict(self.config),
            "candles_processed": self.candles_processed,
            "trade_count": self.trade_count,
            "blocked_candidates": self.blocked_candidates,
            "win_count": win_count,
            "loss_count": loss_count,
            "average_MFE": round(float(avg_mfe), 8),
            "average_MAE": round(float(avg_mae), 8),
            "MFE/MAE ratio": round(float(avg_mfe / max(avg_mae, 1e-9)), 4) if self.trades else 0.0,
            "trades": [trade.as_dict() for trade in self.trades],
        }


@dataclass(frozen=True)
class ParameterSweepResult:
    version: str
    results: tuple[CandleBacktestResult, ...]

    def ranked(self, *, key: str = "MFE/MAE ratio") -> list[dict[str, Any]]:
        rows = [result.as_dict() for result in self.results]
        return sorted(rows, key=lambda row: _float(row.get(key), 0.0), reverse=True)

    def as_dict(self) -> dict[str, Any]:
        ranked = self.ranked()
        return {
            "version": self.version,
            "run_count": len(self.results),
            "best": ranked[0] if ranked else None,
            "results": ranked,
        }


def run_event_candle_backtest(
    candles: Sequence[Mapping[str, Any]],
    config: CandleBacktestConfig | Mapping[str, Any] | None = None,
) -> CandleBacktestResult:
    rows = _rows(candles)
    cfg = _coerce_config(config)
    trades: list[BacktestTrade] = []
    blocked = 0
    recent_sides: list[str] = []
    expiry = max(1, int(cfg.expiry_candles))
    for index in range(max(1, int(cfg.lookback_candles)) - 1, max(0, len(rows) - expiry)):
        features = _directional_features(rows, index, cfg.lookback_candles)
        side = _side(features["side"])
        recent_sides.append(side)
        recent_sides = recent_sides[-max(1, int(cfg.flip_flop_hysteresis)) :]
        flip_flop = len(set(item for item in recent_sides if item in {"BUY", "SELL"})) > 1
        angle = abs(_float(features.get("angle"), 0.0))
        dominance = _float(features.get("dominance_margin"), 0.0)
        quality = min(1.0, dominance * 0.65 + min(1.0, angle / max(abs(cfg.angle_threshold), 1e-9)) * 0.35)
        opposing_distance = _float(rows[index].get("opposing_force_distance"), cfg.opposing_force_distance + 0.01)
        executable = (
            side in {"BUY", "SELL"}
            and not flip_flop
            and angle >= abs(float(cfg.angle_threshold))
            and dominance >= float(cfg.dominance_margin)
            and quality >= float(cfg.entry_quality_score)
            and opposing_distance >= float(cfg.opposing_force_distance)
        )
        if not executable:
            blocked += 1
            continue
        entry_price = _close(rows[index])
        volatility = _float(features.get("volatility"), 0.0)
        future = rows[index + 1 : index + 1 + expiry]
        metrics = track_candle_outcome(
            {
                "side": side,
                "entry_price": entry_price,
                "target_price": entry_price + (1.5 * volatility if side == "BUY" else -1.5 * volatility),
                "stop_price": entry_price - (volatility if side == "BUY" else -volatility),
                "dominance_score": dominance,
                "active_trend_angle_degrees": angle,
            },
            future,
        )
        trades.append(
            BacktestTrade(
                trade_id=f"bt-{cfg.name}-{index}-{side.lower()}",
                frame_index=index,
                side=side,
                entry_price=round(float(entry_price), 8),
                expiry_candles=expiry,
                features={**features, "entry_quality_score": round(float(quality), 4), "flip_flop": flip_flop},
                outcome_metrics=metrics,
            )
        )
    return CandleBacktestResult(
        version=EVENT_CANDLE_BACKTESTER_VERSION,
        config=cfg.as_dict(),
        candles_processed=len(rows),
        trades=tuple(trades),
        blocked_candidates=blocked,
    )


def run_parameter_sweep(
    candles: Sequence[Mapping[str, Any]],
    grid: Mapping[str, Iterable[Any]],
    *,
    base_config: CandleBacktestConfig | Mapping[str, Any] | None = None,
) -> ParameterSweepResult:
    base = _coerce_config(base_config).as_dict()
    keys = tuple(grid.keys())
    values = [list(grid[key]) for key in keys]
    results: list[CandleBacktestResult] = []
    for index, combo in enumerate(product(*values)):
        config = dict(base)
        config.update(dict(zip(keys, combo)))
        config["name"] = str(config.get("name") or f"sweep_{index}")
        if str(config["name"]) == base.get("name", "default"):
            config["name"] = f"sweep_{index}"
        results.append(run_event_candle_backtest(candles, config))
    return ParameterSweepResult(version=EVENT_CANDLE_BACKTESTER_VERSION, results=tuple(results))


def _coerce_config(config: CandleBacktestConfig | Mapping[str, Any] | None) -> CandleBacktestConfig:
    if isinstance(config, CandleBacktestConfig):
        return config
    payload = _mapping(config)
    return CandleBacktestConfig(
        angle_threshold=_float(payload.get("angle_threshold"), CandleBacktestConfig.angle_threshold),
        dominance_margin=_float(payload.get("dominance_margin"), CandleBacktestConfig.dominance_margin),
        opposing_force_distance=_float(payload.get("opposing_force_distance"), CandleBacktestConfig.opposing_force_distance),
        entry_quality_score=_float(payload.get("entry_quality_score"), CandleBacktestConfig.entry_quality_score),
        expiry_candles=int(_float(payload.get("expiry_candles"), CandleBacktestConfig.expiry_candles)),
        flip_flop_hysteresis=int(_float(payload.get("flip_flop_hysteresis"), CandleBacktestConfig.flip_flop_hysteresis)),
        lookback_candles=int(_float(payload.get("lookback_candles"), CandleBacktestConfig.lookback_candles)),
        name=str(payload.get("name") or CandleBacktestConfig.name),
    )
