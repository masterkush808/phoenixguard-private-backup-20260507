from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, cast


LSTM_CANDLE_SEQUENCE_VERSION = "lstm_candle_v3"
LSTM_CONTRIBUTION_SCHEMA_VERSION = "PG_LSTM_CANDLE_SEQUENCE_CONTRIBUTION_V3"
DEFAULT_MODEL_PATH = Path("models/lstm_candle_sequence_v3.pt")
DEFAULT_CONFIG_PATH = Path("models/lstm_candle_sequence_v3_config.json")
DEFAULT_METRICS_PATH = Path("models/lstm_candle_sequence_v3_metrics.json")
FEATURE_SCHEMA: tuple[str, ...] = (
    "body_norm",
    "upper_wick_norm",
    "lower_wick_norm",
    "direction_value",
    "range_norm",
    "relative_price_location",
    "phase_value",
)
DEFAULT_SEQUENCE_LENGTH = 128

SIDES = {"BUY", "SELL"}
_ARTIFACT_CACHE: dict[tuple[str, str, str, float, float, float], dict[str, Any]] = {}


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
    return default


def _rows(value: Sequence[Mapping[str, Any]] | Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(cast(Mapping[str, Any], row)) for row in cast(Sequence[Any], value) if isinstance(row, Mapping)]


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def phase_value(value: Any) -> float:
    text = str(value or "").strip().upper()
    if not text:
        return 0.0
    if "PULLBACK" in text or "RETEST" in text:
        return 0.35
    if "CONTINUATION" in text or "IMPULSE" in text:
        return 0.70
    if "REVERSAL" in text or "RECLAIM" in text:
        return 0.90
    if "CONSOLIDATION" in text or "PAUSE" in text:
        return 0.20
    return 0.50


def _image_height(image_size: Any) -> float:
    if isinstance(image_size, Sequence) and not isinstance(image_size, (str, bytes, bytearray)):
        size = cast(Sequence[Any], image_size)
        if len(size) < 2:
            return 1.0
        return max(1.0, _safe_float(size[1], 1.0))
    return 1.0


def candle_sequence_features(
    candles: Sequence[Mapping[str, Any]],
    *,
    image_size: tuple[int, int] | Sequence[int] = (1, 1),
    sequence_phase: str = "",
) -> list[dict[str, Any]]:
    """Extract an observed-only feature sequence for LSTM training/inference.

    The function never creates future candles. Every row is derived from an
    existing candle box and its observed direction/color.
    """

    height = _image_height(image_size)
    rows = _rows(candles)
    features: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        bbox = row.get("bbox", [])
        if not isinstance(bbox, Sequence) or isinstance(bbox, (str, bytes, bytearray)):
            continue
        bbox_values = cast(Sequence[Any], bbox)
        if len(bbox_values) < 4:
            continue
        top = min(_safe_float(bbox_values[1]), _safe_float(bbox_values[3]))
        bottom = max(_safe_float(bbox_values[1]), _safe_float(bbox_values[3]))
        candle_range = max(0.001, (bottom - top) / height)
        body_norm = _clip01(row.get("body_height_pct"), candle_range * 0.58)
        direction = _side(row.get("direction") or row.get("color"), "HOLD")
        price_proxy = _clip01(row.get("price_proxy"), 1.0 - ((top + bottom) * 0.5 / height))
        upper_wick_norm = _clip01(row.get("upper_wick_pct"), max(0.0, candle_range - body_norm) * 0.5)
        lower_wick_norm = _clip01(row.get("lower_wick_pct"), max(0.0, candle_range - body_norm) * 0.5)
        features.append(
            {
                "index": index,
                "direction": direction,
                "direction_value": 1.0 if direction == "BUY" else -1.0 if direction == "SELL" else 0.0,
                "body_norm": round(body_norm, 6),
                "upper_wick_norm": round(upper_wick_norm, 6),
                "lower_wick_norm": round(lower_wick_norm, 6),
                "range_norm": round(_clip01(candle_range), 6),
                "relative_price_location": round(price_proxy, 6),
                "phase": str(row.get("phase") or sequence_phase or "UNKNOWN").upper(),
                "phase_value": round(phase_value(row.get("phase") or sequence_phase), 6),
            }
        )
    return features


def feature_vector(row: Mapping[str, Any]) -> list[float]:
    return [
        _clip01(row.get("body_norm"), 0.0),
        _clip01(row.get("upper_wick_norm"), 0.0),
        _clip01(row.get("lower_wick_norm"), 0.0),
        max(-1.0, min(1.0, _safe_float(row.get("direction_value"), 0.0))),
        _clip01(row.get("range_norm"), 0.0),
        _clip01(row.get("relative_price_location"), 0.0),
        _clip01(row.get("phase_value"), phase_value(row.get("phase"))),
    ]


def sequence_features_to_matrix(
    features: Sequence[Mapping[str, Any]],
    *,
    sequence_length: int = DEFAULT_SEQUENCE_LENGTH,
) -> list[list[float]]:
    rows = [feature_vector(row) for row in features][-max(1, int(sequence_length)) :]
    pad = max(0, int(sequence_length) - len(rows))
    return ([[0.0 for _ in FEATURE_SCHEMA] for _ in range(pad)] if False else [[0.0] * len(FEATURE_SCHEMA) for _ in range(pad)]) + rows


def _torch_modules() -> tuple[Any, Any] | tuple[None, None]:
    try:
        import torch
        import torch.nn as nn
    except Exception:
        return None, None
    return torch, nn


def create_lstm_candle_sequence_model(
    *,
    input_dim: int = len(FEATURE_SCHEMA),
    hidden_dim: int = 48,
    num_layers: int = 1,
    dropout: float = 0.0,
) -> Any:
    torch, nn = _torch_modules()
    if torch is None or nn is None:
        raise RuntimeError("PyTorch is required for LSTM candle-sequence model creation.")

    module_base: type[Any] = cast(type[Any], nn.Module)

    class LSTMCandleSequenceModel(module_base):
        def __init__(self) -> None:
            module_init = cast(Callable[[Any], None], module_base.__init__)
            module_init(self)
            self.lstm = nn.LSTM(
                input_size=int(input_dim),
                hidden_size=int(hidden_dim),
                num_layers=int(num_layers),
                dropout=float(dropout) if int(num_layers) > 1 else 0.0,
                batch_first=True,
            )
            self.norm = nn.LayerNorm(int(hidden_dim))
            self.next_1_head = nn.Linear(int(hidden_dim), 2)
            self.next_2_head = nn.Linear(int(hidden_dim), 2)
            self.play_head = nn.Linear(int(hidden_dim), 3)

        def forward(self, sequence: Any) -> dict[str, Any]:
            output, _ = self.lstm(sequence)
            pooled = self.norm(output[:, -1, :])
            return {
                "next_1_logits": self.next_1_head(pooled),
                "next_2_logits": self.next_2_head(pooled),
                "play_logits": self.play_head(pooled),
            }

    return LSTMCandleSequenceModel()


def _artifact_cache_key(model_path: Path, config_path: Path, metrics_path: Path) -> tuple[str, str, str, float, float, float]:
    def mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    return (
        str(model_path.resolve()),
        str(config_path.resolve()),
        str(metrics_path.resolve()),
        mtime(model_path),
        mtime(config_path),
        mtime(metrics_path),
    )


def _load_artifact_bundle(model_path: Path, config_path: Path, metrics_path: Path) -> dict[str, Any]:
    key = _artifact_cache_key(model_path, config_path, metrics_path)
    cached = _ARTIFACT_CACHE.get(key)
    if cached is not None:
        return cached
    config = _read_json(config_path)
    metrics = _read_json(metrics_path)
    bundle: dict[str, Any] = {
        "config": config,
        "metrics": metrics,
        "model": None,
        "ready": False,
        "error": "",
    }
    if not (model_path.exists() and config and metrics):
        _ARTIFACT_CACHE[key] = bundle
        return bundle
    torch, _nn = _torch_modules()
    if torch is None:
        bundle["error"] = "PyTorch unavailable; LSTM artifact ignored."
        _ARTIFACT_CACHE[key] = bundle
        return bundle
    try:
        model = create_lstm_candle_sequence_model(
            input_dim=int(config.get("input_dim", len(FEATURE_SCHEMA)) or len(FEATURE_SCHEMA)),
            hidden_dim=int(config.get("hidden_dim", 48) or 48),
            num_layers=int(config.get("num_layers", 1) or 1),
            dropout=float(config.get("dropout", 0.0) or 0.0),
        )
        payload: Any = torch.load(model_path, map_location="cpu", weights_only=False)
        if isinstance(payload, Mapping):
            payload_map = cast(Mapping[str, Any], payload)
            state_dict: Any = payload_map.get("state_dict", payload_map)
        else:
            state_dict = payload
        model.load_state_dict(state_dict)
        model.eval()
        bundle["model"] = model
        bundle["ready"] = True
    except Exception as exc:
        bundle["error"] = f"failed to load LSTM artifact: {exc}"
    _ARTIFACT_CACHE.clear()
    _ARTIFACT_CACHE[key] = bundle
    return bundle


def _model_probabilities(model: Any, features: Sequence[Mapping[str, Any]], sequence_length: int) -> dict[str, Any]:
    torch, _nn = _torch_modules()
    if torch is None or model is None:
        return {}
    matrix = sequence_features_to_matrix(features, sequence_length=sequence_length)
    tensor = torch.tensor([matrix], dtype=torch.float32)
    with torch.inference_mode():
        outputs = model(tensor)
        next_1 = torch.softmax(outputs["next_1_logits"], dim=-1).squeeze(0)
        next_2 = torch.softmax(outputs["next_2_logits"], dim=-1).squeeze(0)
        play = torch.softmax(outputs["play_logits"], dim=-1).squeeze(0)
    return {
        "next_1_buy": float(next_1[0].item()),
        "next_1_sell": float(next_1[1].item()),
        "next_2_buy": float(next_2[0].item()),
        "next_2_sell": float(next_2[1].item()),
        "continuation_probability": float(play[0].item()),
        "reversal_probability": float(play[1].item()),
        "pullback_first_probability": float(play[2].item()),
    }


def _direction_probabilities(features: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    if not features:
        return {"BUY": 0.34, "SELL": 0.33, "HOLD": 0.33}
    recent = list(features[-min(24, len(features)) :])
    weighted_buy = 0.0
    weighted_sell = 0.0
    weighted_hold = 0.18
    total_weight = 0.18
    for offset, row in enumerate(recent):
        weight = 1.0 + (offset / max(1, len(recent) - 1)) * 1.4
        body = _clip01(row.get("body_norm"), 0.0)
        wick_penalty = 0.45 * max(_clip01(row.get("upper_wick_norm")), _clip01(row.get("lower_wick_norm")))
        weight *= max(0.35, 0.65 + body - wick_penalty)
        side = _side(row.get("direction"), "HOLD")
        total_weight += weight
        if side == "BUY":
            weighted_buy += weight
        elif side == "SELL":
            weighted_sell += weight
        else:
            weighted_hold += weight
    return {
        "BUY": weighted_buy / max(1e-9, total_weight),
        "SELL": weighted_sell / max(1e-9, total_weight),
        "HOLD": weighted_hold / max(1e-9, total_weight),
    }


def _run_length(features: Sequence[Mapping[str, Any]]) -> int:
    if not features:
        return 0
    side = _side(features[-1].get("direction"), "HOLD")
    if side not in SIDES:
        return 0
    count = 0
    for row in reversed(features):
        if _side(row.get("direction"), "HOLD") != side:
            break
        count += 1
    return count


def build_lstm_candle_sequence_contribution(
    *,
    candles: Sequence[Mapping[str, Any]],
    image_size: tuple[int, int] | Sequence[int] = (1, 1),
    timeframe: str = "",
    pair_profile: Mapping[str, Any] | None = None,
    sequence_phase: str = "",
    market_play_label: str = "",
    model_path: Path | str = DEFAULT_MODEL_PATH,
    config_path: Path | str = DEFAULT_CONFIG_PATH,
    metrics_path: Path | str = DEFAULT_METRICS_PATH,
) -> dict[str, Any]:
    features = candle_sequence_features(candles, image_size=image_size, sequence_phase=sequence_phase)
    model_file = Path(model_path)
    config_file = Path(config_path)
    metrics_file = Path(metrics_path)
    artifact = _load_artifact_bundle(model_file, config_file, metrics_file)
    config = _mapping(artifact.get("config"))
    metrics = _mapping(artifact.get("metrics"))
    artifact_available = bool(model_file.exists() and config and metrics)
    probabilities = _direction_probabilities(features)
    model_probabilities = _model_probabilities(
        artifact.get("model"),
        features,
        int(config.get("sequence_length", DEFAULT_SEQUENCE_LENGTH) or DEFAULT_SEQUENCE_LENGTH),
    )
    if model_probabilities:
        probabilities = {
            "BUY": _clip01(model_probabilities.get("next_1_buy"), probabilities["BUY"]),
            "SELL": _clip01(model_probabilities.get("next_1_sell"), probabilities["SELL"]),
            "HOLD": probabilities["HOLD"],
        }
    side = "BUY" if probabilities["BUY"] >= probabilities["SELL"] else "SELL"
    opposite = "SELL" if side == "BUY" else "BUY"
    primary_probability = probabilities[side]
    opposite_probability = probabilities[opposite]
    hold_probability = probabilities["HOLD"]
    run = _run_length(features)
    continuation_probability = _clip01(
        model_probabilities.get("continuation_probability")
        if model_probabilities
        else primary_probability + 0.10 * min(run, 4) / 4.0
    )
    reversal_probability = _clip01(
        model_probabilities.get("reversal_probability")
        if model_probabilities
        else opposite_probability + 0.08 * float(run >= 4)
    )
    pullback_first_probability = _clip01(
        model_probabilities.get("pullback_first_probability")
        if model_probabilities
        else hold_probability + 0.08 * float(run >= 3) + 0.10 * reversal_probability
    )
    confidence = _clip01(
        0.22
        + 0.36 * abs(primary_probability - opposite_probability)
        + 0.18 * min(1.0, len(features) / 64.0)
        + 0.14 * continuation_probability
        + 0.10 * (1.0 - pullback_first_probability)
    )
    artifact_ready = bool(artifact.get("ready"))
    if artifact_ready:
        contribution = round(0.05 * confidence, 4)
        reason = "LSTM artifact is loaded; contribution is diagnostic evidence only."
        fresh = True
    else:
        contribution = 0.0
        reason = str(artifact.get("error") or "LSTM model artifact/config/metrics not present; output ignored and cannot block execution.")
        fresh = False

    return {
        "schema_version": LSTM_CONTRIBUTION_SCHEMA_VERSION,
        "skill": "LSTM_CANDLE_SEQUENCE",
        "model_version": str(config.get("model_version") or LSTM_CANDLE_SEQUENCE_VERSION),
        "artifact_path": str(model_file),
        "config_path": str(config_file),
        "metrics_path": str(metrics_file),
        "artifact_available": artifact_available,
        "artifact_loaded": artifact_ready,
        "fresh": fresh,
        "blocker": False,
        "contribution": contribution,
        "side": side if side in SIDES else "HOLD",
        "next_1_direction": side,
        "next_1_probability": round(primary_probability, 4),
        "next_2_direction": (
            ("BUY" if _clip01(model_probabilities.get("next_2_buy"), 0.0) >= _clip01(model_probabilities.get("next_2_sell"), 0.0) else "SELL")
            if model_probabilities
            else side if pullback_first_probability < 0.48 else "HOLD"
        ),
        "next_2_probability": round(
            max(
                _clip01(model_probabilities.get("next_2_buy"), 0.0),
                _clip01(model_probabilities.get("next_2_sell"), 0.0),
            )
            if model_probabilities
            else max(primary_probability - 0.08, 0.0),
            4,
        ),
        "continuation_probability": round(continuation_probability, 4),
        "reversal_probability": round(reversal_probability, 4),
        "pullback_first_probability": round(pullback_first_probability, 4),
        "confidence": round(confidence, 4),
        "sequence_length": len(features),
        "timeframe": str(timeframe or "").upper(),
        "sequence_phase": str(sequence_phase or "").upper(),
        "market_play_label": str(market_play_label or "").upper(),
        "pair_profile": _mapping(pair_profile),
        "usage": {
            "default": str(config.get("default_usage", "HIGH_FREQUENCY")).upper(),
            "high_frequency_enabled": bool(config.get("high_frequency_enabled", True)),
            "normal_analysis_enabled": bool(config.get("normal_analysis_enabled", False)),
            "normal_analysis_env": "PHOENIXGUARD_LSTM_NORMAL_ANALYSIS",
        },
        "metrics": {
            "next_1_direction_accuracy": metrics.get("next_1_direction_accuracy"),
            "next_2_direction_accuracy": metrics.get("next_2_direction_accuracy"),
            "balanced_accuracy": metrics.get("balanced_accuracy"),
            "production_ready": bool(metrics.get("production_ready")),
        },
        "interpretation": (
            f"LSTM sequence contributor favours {side} continuation over the next 1-2 candles."
            if fresh
            else "LSTM sequence contributor is offline; two-candle study uses observed candle statistics and kernel evidence."
        ),
        "reason": reason,
        "features": features[-128:],
    }


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_METRICS_PATH",
    "DEFAULT_MODEL_PATH",
    "LSTM_CANDLE_SEQUENCE_VERSION",
    "LSTM_CONTRIBUTION_SCHEMA_VERSION",
    "DEFAULT_SEQUENCE_LENGTH",
    "FEATURE_SCHEMA",
    "build_lstm_candle_sequence_contribution",
    "candle_sequence_features",
    "create_lstm_candle_sequence_model",
    "feature_vector",
    "phase_value",
    "sequence_features_to_matrix",
]
