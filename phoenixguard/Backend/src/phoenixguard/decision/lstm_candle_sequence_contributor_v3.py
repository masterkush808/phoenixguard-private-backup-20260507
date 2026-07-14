from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, cast

from phoenixguard.decision.retrieval_forecast_v3 import (
    retrieve_forecast_v3,
    validate_retrieval_bank_v3,
)
from phoenixguard.decision.selective_risk_v3 import temperature_softmax


# PhoenixGuard is one continuously upgraded V3 stack.  The artifact and schema
# names intentionally stay on V3 while the internals improve in place.
LSTM_CANDLE_SEQUENCE_VERSION = "lstm_candle_sequence_v3"
LSTM_CONTRIBUTION_SCHEMA_VERSION = "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3"
DEFAULT_MODEL_PATH = Path("models/lstm_candle_sequence_v3.pt")
DEFAULT_CONFIG_PATH = Path("models/lstm_candle_sequence_v3_config.json")
DEFAULT_METRICS_PATH = Path("models/lstm_candle_sequence_v3_metrics.json")

MAX_PRICE_DELTA = 0.25
FEATURE_SCHEMA: tuple[str, ...] = (
    "body_norm",
    "upper_wick_norm",
    "lower_wick_norm",
    "direction_value",
    "range_norm",
    "relative_price_location",
    "relative_price_delta_scaled",
    "range_vs_recent",
    "body_vs_recent",
    "momentum_5",
    "direction_run_norm",
    "parse_confidence",
    "phase_value",
)
PREDICTION_SCHEMA: tuple[str, ...] = (
    "body_norm",
    "upper_wick_norm",
    "lower_wick_norm",
    "range_norm",
    "relative_price_delta_scaled",
)
PREDICTION_FEATURE_INDICES: tuple[int, ...] = tuple(FEATURE_SCHEMA.index(name) for name in PREDICTION_SCHEMA)
DEFAULT_SEQUENCE_LENGTH = 96
DEFAULT_HORIZON_STEPS = 12
LEGACY_MULTISCALE_ARCHITECTURE = "CAUSAL_CV_MULTISCALE_ATTENTION_LSTM"
DIRECT_RAW_CV_ARCHITECTURE = "CAUSAL_PIXEL_CNN_MASKED_LSTM_DIRECT_HORIZON_ATTENTION"

SIDES = {"BUY", "SELL"}
PLAY_LABELS = ("CONTINUATION", "REVERSAL", "PULLBACK")
_ARTIFACT_CACHE: dict[tuple[str, str, str, float, float, float], dict[str, Any]] = {}


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    if not math.isfinite(number):
        return float(default)
    return float(number)


def _clip(value: Any, low: float, high: float, default: float = 0.0) -> float:
    return max(low, min(high, _safe_float(value, default)))


def _clip01(value: Any, default: float = 0.0) -> float:
    return _clip(value, 0.0, 1.0, default)


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
        if len(size) >= 2:
            return max(1.0, _safe_float(size[1], 1.0))
    return 1.0


def _rolling_median(values: Sequence[float], default: float) -> float:
    usable = [float(value) for value in values if math.isfinite(float(value)) and float(value) > 1e-8]
    return float(statistics.median(usable)) if usable else float(default)


def candle_sequence_features(
    candles: Sequence[Mapping[str, Any]],
    *,
    image_size: tuple[int, int] | Sequence[int] = (1, 1),
    sequence_phase: str = "",
) -> list[dict[str, Any]]:
    """Build causal candle-event features shared by raw-suite training and live inference.

    Rolling context at index ``t`` uses only candles ``<= t``. No future candle,
    elapsed-time assumption, or folder-level BUY/SELL label enters these features.
    """

    height = _image_height(image_size)
    base_rows: list[dict[str, Any]] = []
    for index, row in enumerate(_rows(candles)):
        bbox = row.get("bbox", [])
        if not isinstance(bbox, Sequence) or isinstance(bbox, (str, bytes, bytearray)):
            continue
        bbox_values = cast(Sequence[Any], bbox)
        if len(bbox_values) < 4:
            continue
        top = min(_safe_float(bbox_values[1]), _safe_float(bbox_values[3]))
        bottom = max(_safe_float(bbox_values[1]), _safe_float(bbox_values[3]))
        candle_range = max(0.0005, (bottom - top) / height)
        body_norm = _clip01(row.get("body_height_pct"), candle_range * 0.58)
        direction = _side(row.get("direction") or row.get("color"), "HOLD")
        price_proxy = _clip01(
            row.get("price_proxy", row.get("close_norm")),
            1.0 - ((top + bottom) * 0.5 / height),
        )
        upper_wick_norm = _clip01(row.get("upper_wick_pct"), max(0.0, 1.0 - body_norm) * 0.5)
        lower_wick_norm = _clip01(row.get("lower_wick_pct"), max(0.0, 1.0 - body_norm) * 0.5)
        base_rows.append(
            {
                "index": index,
                "bbox": [float(value) for value in bbox_values[:4]],
                "center_x_px": _safe_float(
                    row.get("center_x_px", row.get("center_x")),
                    0.5 * (_safe_float(bbox_values[0]) + _safe_float(bbox_values[2])),
                ),
                "direction": direction,
                "direction_value": 1.0 if direction == "BUY" else -1.0 if direction == "SELL" else 0.0,
                "body_norm": body_norm,
                "upper_wick_norm": upper_wick_norm,
                "lower_wick_norm": lower_wick_norm,
                "range_norm": _clip01(candle_range),
                "relative_price_location": price_proxy,
                "parse_confidence": _clip01(row.get("parse_confidence", row.get("parse_conf")), 1.0),
                "phase": str(row.get("phase") or sequence_phase or "UNKNOWN").upper(),
                "phase_value": phase_value(row.get("phase") or sequence_phase),
            }
        )

    features: list[dict[str, Any]] = []
    run_side = "HOLD"
    run_length = 0
    for index, row in enumerate(base_rows):
        previous_price = base_rows[index - 1]["relative_price_location"] if index else row["relative_price_location"]
        price_delta = _clip(row["relative_price_location"] - previous_price, -MAX_PRICE_DELTA, MAX_PRICE_DELTA)
        recent = base_rows[max(0, index - 11) : index + 1]
        recent_ranges = [float(item["range_norm"]) for item in recent]
        recent_bodies = [float(item["body_norm"]) for item in recent]
        range_reference = _rolling_median(recent_ranges, float(row["range_norm"]))
        body_reference = _rolling_median(recent_bodies, float(row["body_norm"]))
        current_side = str(row["direction"])
        if current_side in SIDES and current_side == run_side:
            run_length += 1
        elif current_side in SIDES:
            run_side = current_side
            run_length = 1
        else:
            run_side = "HOLD"
            run_length = 0
        momentum_rows = base_rows[max(0, index - 4) : index + 1]
        momentum = sum(float(item["direction_value"]) for item in momentum_rows) / max(1, len(momentum_rows))
        features.append(
            {
                **row,
                "body_norm": round(float(row["body_norm"]), 6),
                "upper_wick_norm": round(float(row["upper_wick_norm"]), 6),
                "lower_wick_norm": round(float(row["lower_wick_norm"]), 6),
                "range_norm": round(float(row["range_norm"]), 6),
                "relative_price_location": round(float(row["relative_price_location"]), 6),
                "relative_price_delta": round(price_delta, 6),
                "relative_price_delta_scaled": round(price_delta / MAX_PRICE_DELTA, 6),
                "range_vs_recent": round(_clip(float(row["range_norm"]) / max(1e-6, range_reference) / 3.0, 0.0, 1.0), 6),
                "body_vs_recent": round(_clip(float(row["body_norm"]) / max(1e-6, body_reference) / 3.0, 0.0, 1.0), 6),
                "momentum_5": round(_clip(momentum, -1.0, 1.0), 6),
                "direction_run_norm": round(_clip(run_length / 8.0, 0.0, 1.0), 6),
                "parse_confidence": round(float(row["parse_confidence"]), 6),
                "phase_value": round(float(row["phase_value"]), 6),
            }
        )
    return features


def feature_vector(row: Mapping[str, Any]) -> list[float]:
    return [
        _clip01(row.get("body_norm")),
        _clip01(row.get("upper_wick_norm")),
        _clip01(row.get("lower_wick_norm")),
        _clip(row.get("direction_value"), -1.0, 1.0),
        _clip01(row.get("range_norm")),
        _clip01(row.get("relative_price_location"), 0.5),
        _clip(row.get("relative_price_delta_scaled"), -1.0, 1.0),
        _clip01(row.get("range_vs_recent"), 1.0 / 3.0),
        _clip01(row.get("body_vs_recent"), 1.0 / 3.0),
        _clip(row.get("momentum_5"), -1.0, 1.0),
        _clip01(row.get("direction_run_norm")),
        _clip01(row.get("parse_confidence"), 1.0),
        _clip01(row.get("phase_value"), phase_value(row.get("phase"))),
    ]


def sequence_features_to_matrix(
    features: Sequence[Mapping[str, Any]],
    *,
    sequence_length: int = DEFAULT_SEQUENCE_LENGTH,
) -> list[list[float]]:
    """Return a fixed-width, right-padded causal feature matrix.

    Right padding is required by the packed V3 encoder: the first ``n`` rows
    are real observations and all rows after the true sequence length are
    padding. Callers that batch matrices must pass the corresponding lengths
    (or an equivalent right-padding mask) to the model.
    """

    length = max(1, int(sequence_length))
    rows = [feature_vector(row) for row in features][-length:]
    return rows + [[0.0] * len(FEATURE_SCHEMA) for _ in range(max(0, length - len(rows)))]


def legacy_sequence_features_to_matrix(
    features: Sequence[Mapping[str, Any]],
    *,
    sequence_length: int = DEFAULT_SEQUENCE_LENGTH,
) -> list[list[float]]:
    """Reproduce the left-padding contract used by the restored V3 export."""

    length = max(1, int(sequence_length))
    rows = [feature_vector(row) for row in features][-length:]
    return [[0.0] * len(FEATURE_SCHEMA) for _ in range(max(0, length - len(rows)))] + rows


def _torch_modules() -> tuple[Any, Any] | tuple[None, None]:
    try:
        import torch
        import torch.nn as nn
    except Exception:
        return None, None
    return torch, nn


def causal_chart_context_tensor(
    image: Any,
    *,
    cut_x: int | float | None = None,
    output_size: tuple[int, int] = (96, 192),
) -> Any:
    """Convert a chart image to the shared V3 pixel-CNN tensor contract.

    The returned tensor is float32 in ``[0, 1]`` and has shape ``[3, H, W]``
    for a single image or ``[B, 3, H, W]`` for a tensor batch. ``cut_x`` is an
    absolute source-image column when passed as an integer, or a normalized
    ``0..1`` location when passed as a float. Pixels at and to the right of the
    cut are zeroed *before* resizing so future chart content cannot leak into
    a causal training example.
    """

    torch, _nn = _torch_modules()
    if torch is None:
        raise RuntimeError("PyTorch is required for chart-context preprocessing.")
    if image is None:
        raise ValueError("chart image cannot be None")

    is_batched = False
    if isinstance(image, torch.Tensor):
        tensor = image.detach()
    else:
        try:
            import numpy as np

            if hasattr(image, "convert"):
                image = image.convert("RGB")
            array = np.asarray(image)
            # Read-only PIL-backed arrays cause a PyTorch warning and cannot be
            # safely masked, so always take an owned copy at this boundary.
            tensor = torch.from_numpy(np.array(array, copy=True))
        except Exception as exc:
            raise TypeError("chart image must be a PIL image, numpy array, or torch tensor") from exc

    if tensor.ndim == 2:
        tensor = tensor.unsqueeze(0)
    elif tensor.ndim == 3:
        if int(tensor.shape[0]) not in {1, 3, 4} and int(tensor.shape[-1]) in {1, 3, 4}:
            tensor = tensor.permute(2, 0, 1)
    elif tensor.ndim == 4:
        is_batched = True
        if int(tensor.shape[1]) not in {1, 3, 4} and int(tensor.shape[-1]) in {1, 3, 4}:
            tensor = tensor.permute(0, 3, 1, 2)
    else:
        raise ValueError("chart image must have 2, 3, or 4 dimensions")

    channel_axis = 1 if is_batched else 0
    channel_count = int(tensor.shape[channel_axis])
    if channel_count == 1:
        repeats = (1, 3, 1, 1) if is_batched else (3, 1, 1)
        tensor = tensor.repeat(*repeats)
    elif channel_count == 4:
        tensor = tensor[:, :3, :, :] if is_batched else tensor[:3, :, :]
    elif channel_count != 3:
        raise ValueError("chart image must contain 1, 3, or 4 channels")

    was_integer = not tensor.dtype.is_floating_point
    tensor = tensor.to(dtype=torch.float32)
    if was_integer or (tensor.numel() and float(tensor.detach().amax().item()) > 1.5):
        tensor = tensor / 255.0
    tensor = torch.clamp(tensor, 0.0, 1.0)

    source_width = int(tensor.shape[-1])
    cut_ratio: float | None = None
    if cut_x is not None:
        if isinstance(cut_x, float):
            if not math.isfinite(cut_x) or not 0.0 <= cut_x <= 1.0:
                raise ValueError("floating cut_x must be a normalized value in [0, 1]")
            cut_column = int(round(cut_x * source_width))
        else:
            cut_column = int(cut_x)
        cut_column = max(0, min(source_width, cut_column))
        cut_ratio = cut_column / max(1, source_width)
        tensor = tensor.clone()
        tensor[..., :, cut_column:] = 0.0

    height, width = max(8, int(output_size[0])), max(8, int(output_size[1]))
    batch = tensor if is_batched else tensor.unsqueeze(0)
    resized = torch.nn.functional.interpolate(batch, size=(height, width), mode="bilinear", align_corners=False)
    if cut_ratio is not None:
        resized = resized.clone()
        resized[..., :, max(0, min(width, int(round(cut_ratio * width)))) :] = 0.0
    return resized if is_batched else resized.squeeze(0)


def create_lstm_candle_sequence_model(
    *,
    input_dim: int = len(FEATURE_SCHEMA),
    hidden_dim: int = 96,
    num_layers: int = 2,
    dropout: float = 0.15,
    horizon_steps: int = DEFAULT_HORIZON_STEPS,
) -> Any:
    """Create the V3 raw-vision encoder with direct horizon decoding.

    The public forward call remains compatible with the former decoder, but
    ``targets`` and ``teacher_forcing_ratio`` are deliberately ignored. Every
    horizon is decoded in parallel from observed, masked history; no predicted
    candle is fed back as if it were an observation.
    """

    torch, nn = _torch_modules()
    if torch is None or nn is None:
        raise RuntimeError("PyTorch is required for LSTM candle-path model creation.")
    module_base: type[Any] = cast(type[Any], nn.Module)
    max_horizon = max(1, int(horizon_steps))
    feature_dim = len(PREDICTION_SCHEMA)

    class LSTMCandlePathModel(module_base):
        def __init__(self) -> None:
            cast(Callable[[Any], None], module_base.__init__)(self)
            self.max_horizon_steps = max_horizon
            pattern_dim = max(16, int(hidden_dim) // 2)
            self.short_pattern = nn.Conv1d(int(input_dim), pattern_dim, kernel_size=3, padding=1)
            self.medium_pattern = nn.Conv1d(int(input_dim), pattern_dim, kernel_size=7, padding=3)
            self.long_pattern = nn.Conv1d(int(input_dim), pattern_dim, kernel_size=15, padding=7)
            self.pattern_fusion = nn.Sequential(
                nn.Linear(pattern_dim * 3, int(input_dim)),
                nn.GELU(),
                nn.LayerNorm(int(input_dim)),
            )
            self.encoder = nn.LSTM(
                input_size=int(input_dim),
                hidden_size=int(hidden_dim),
                num_layers=int(num_layers),
                dropout=float(dropout) if int(num_layers) > 1 else 0.0,
                batch_first=True,
            )
            attention_heads = 4 if int(hidden_dim) % 4 == 0 else 2 if int(hidden_dim) % 2 == 0 else 1
            self.horizon_queries = nn.Embedding(max_horizon, int(hidden_dim))
            self.context_to_query = nn.Linear(int(hidden_dim), int(hidden_dim))
            self.horizon_attention = nn.MultiheadAttention(
                int(hidden_dim),
                num_heads=attention_heads,
                dropout=float(dropout),
                batch_first=True,
            )
            self.encoder_norm = nn.LayerNorm(int(hidden_dim))
            self.query_norm = nn.LayerNorm(int(hidden_dim))
            self.query_feed_forward = nn.Sequential(
                nn.Linear(int(hidden_dim), int(hidden_dim) * 2),
                nn.GELU(),
                nn.Dropout(float(dropout)),
                nn.Linear(int(hidden_dim) * 2, int(hidden_dim)),
            )
            self.decoder_norm = nn.LayerNorm(int(hidden_dim))
            self.decoder_dropout = nn.Dropout(float(dropout))

            # Each horizontal convolution is left-padded in forward. Thus a
            # feature at chart column x can only contain pixels from <= x.
            chart_dim = max(16, int(hidden_dim) // 2)
            self.chart_conv_1 = nn.Conv2d(3, 16, kernel_size=(3, 5), stride=2, padding=0)
            self.chart_conv_2 = nn.Conv2d(16, 32, kernel_size=(3, 5), stride=2, padding=0)
            self.chart_conv_3 = nn.Conv2d(32, chart_dim, kernel_size=(3, 5), stride=2, padding=0)
            self.chart_projection = nn.Sequential(
                nn.Linear(chart_dim, int(hidden_dim)),
                nn.GELU(),
                nn.LayerNorm(int(hidden_dim)),
            )
            self.chart_gate = nn.Parameter(torch.tensor(-1.0, dtype=torch.float32))
            self.context_norm = nn.LayerNorm(int(hidden_dim))

            # Natural logits learn the empirical outcome distribution. The
            # independent decision head can receive class-balanced loss without
            # destroying probability calibration in direction_logits.
            self.direction_head = nn.Linear(int(hidden_dim), 2)
            self.decision_head = nn.Linear(int(hidden_dim), 2)
            self.feature_mean_head = nn.Linear(int(hidden_dim), feature_dim)
            self.feature_scale_head = nn.Linear(int(hidden_dim), feature_dim)
            self.play_head = nn.Linear(int(hidden_dim), len(PLAY_LABELS))

        def _lengths_and_mask(self, sequence: Any, lengths: Any | None, mask: Any | None) -> tuple[Any, Any]:
            batch_size, width = int(sequence.shape[0]), int(sequence.shape[1])
            provided_mask: Any = None
            if mask is not None:
                provided_mask = torch.as_tensor(mask, dtype=torch.bool, device=sequence.device)
                if tuple(provided_mask.shape) != (batch_size, width):
                    raise ValueError(f"mask must have shape {(batch_size, width)}")
                mask_lengths = provided_mask.to(dtype=torch.long).sum(dim=1)
                resolved_lengths = mask_lengths if lengths is None else torch.as_tensor(lengths, device=sequence.device)
            elif lengths is None:
                resolved_lengths = torch.full((batch_size,), width, dtype=torch.long, device=sequence.device)
            else:
                resolved_lengths = torch.as_tensor(lengths, device=sequence.device)

            resolved_lengths = resolved_lengths.to(dtype=torch.long).reshape(-1)
            if int(resolved_lengths.numel()) != batch_size:
                raise ValueError(f"lengths must contain {batch_size} values")
            if bool(torch.any(resolved_lengths < 1)) or bool(torch.any(resolved_lengths > width)):
                raise ValueError(f"lengths must be within [1, {width}]")
            positions = torch.arange(width, device=sequence.device).unsqueeze(0)
            right_padding_mask = positions < resolved_lengths.unsqueeze(1)
            if provided_mask is not None and bool(torch.any(provided_mask != right_padding_mask)):
                raise ValueError("mask must describe contiguous observations followed by right padding")
            return resolved_lengths, right_padding_mask

        @staticmethod
        def _causal_chart_conv(values: Any, layer: Any) -> Any:
            kernel_height, kernel_width = layer.kernel_size
            top = (int(kernel_height) - 1) // 2
            bottom = int(kernel_height) - 1 - top
            values = torch.nn.functional.pad(values, (int(kernel_width) - 1, 0, top, bottom))
            return torch.nn.functional.gelu(layer(values))

        def _chart_embedding(self, chart_context: Any, batch_size: int, dtype: Any, device: Any) -> Any:
            pixels = torch.as_tensor(chart_context, device=device)
            if pixels.ndim == 3:
                pixels = pixels.unsqueeze(0)
            if pixels.ndim != 4 or int(pixels.shape[1]) != 3:
                raise ValueError("chart_context must have shape [B, 3, H, W]")
            if int(pixels.shape[0]) == 1 and batch_size > 1:
                pixels = pixels.expand(batch_size, -1, -1, -1)
            elif int(pixels.shape[0]) != batch_size:
                raise ValueError(f"chart_context batch must be 1 or {batch_size}")
            pixels = pixels.to(dtype=dtype)
            if pixels.numel() and float(pixels.detach().amax().item()) > 1.5:
                pixels = pixels / 255.0
            pixels = torch.clamp(pixels, 0.0, 1.0)
            values = self._causal_chart_conv(pixels, self.chart_conv_1)
            values = self._causal_chart_conv(values, self.chart_conv_2)
            values = self._causal_chart_conv(values, self.chart_conv_3)
            pooled = torch.nn.functional.adaptive_avg_pool2d(values, (1, 1)).flatten(1)
            return self.chart_projection(pooled)

        def forward(
            self,
            sequence: Any,
            targets: Any | None = None,
            teacher_forcing_ratio: float = 0.0,
            horizon_steps: int | None = None,
            lengths: Any | None = None,
            mask: Any | None = None,
            chart_context: Any | None = None,
        ) -> dict[str, Any]:
            del targets, teacher_forcing_ratio
            if sequence.ndim != 3 or int(sequence.shape[-1]) != int(input_dim):
                raise ValueError(f"sequence must have shape [B, T, {int(input_dim)}]")
            steps = min(self.max_horizon_steps, max(1, int(horizon_steps or self.max_horizon_steps)))
            resolved_lengths, valid_mask = self._lengths_and_mask(sequence, lengths, mask)
            visual_sequence = sequence.transpose(1, 2)
            pattern_features = torch.cat(
                (
                    torch.nn.functional.gelu(self.short_pattern(visual_sequence)),
                    torch.nn.functional.gelu(self.medium_pattern(visual_sequence)),
                    torch.nn.functional.gelu(self.long_pattern(visual_sequence)),
                ),
                dim=1,
            ).transpose(1, 2)
            fused_sequence = sequence + self.pattern_fusion(pattern_features)
            fused_sequence = fused_sequence * valid_mask.unsqueeze(-1).to(dtype=fused_sequence.dtype)
            packed = torch.nn.utils.rnn.pack_padded_sequence(
                fused_sequence,
                resolved_lengths.detach().to(device="cpu"),
                batch_first=True,
                enforce_sorted=False,
            )
            packed_encoded, (hidden, _cell) = self.encoder(packed)
            encoded, _ = torch.nn.utils.rnn.pad_packed_sequence(
                packed_encoded,
                batch_first=True,
                total_length=int(sequence.shape[1]),
            )
            pooled = self.encoder_norm(hidden[-1])

            step_ids = torch.arange(steps, dtype=torch.long, device=sequence.device)
            queries = self.horizon_queries(step_ids).unsqueeze(0).expand(int(sequence.shape[0]), -1, -1)
            context_state = pooled
            if chart_context is not None:
                chart_embedding = self._chart_embedding(
                    chart_context,
                    int(sequence.shape[0]),
                    sequence.dtype,
                    sequence.device,
                )
                gated_chart = torch.sigmoid(self.chart_gate) * chart_embedding
                queries = queries + gated_chart.unsqueeze(1)
                context_state = self.context_norm(context_state + gated_chart)
            queries = self.query_norm(queries + self.context_to_query(context_state).unsqueeze(1))
            attended, _weights = self.horizon_attention(
                queries,
                encoded,
                encoded,
                key_padding_mask=~valid_mask,
                need_weights=False,
            )
            decoded = self.query_norm(queries + self.decoder_dropout(attended))
            decoded = self.decoder_norm(decoded + self.query_feed_forward(decoded))
            direction_logits = self.direction_head(decoded)
            decision_logits = self.decision_head(decoded)
            raw_mean = self.feature_mean_head(decoded)
            mean = torch.cat((torch.sigmoid(raw_mean[..., :4]), torch.tanh(raw_mean[..., 4:5])), dim=-1)
            scale = 0.01 + 0.34 * torch.sigmoid(self.feature_scale_head(decoded))
            context_embedding = torch.nn.functional.normalize(context_state, p=2.0, dim=-1, eps=1e-8)
            return {
                "direction_logits": direction_logits,
                "decision_logits": decision_logits,
                "feature_mean": mean,
                "feature_scale": scale,
                "play_logits": self.play_head(context_state),
                "context_embedding": context_embedding,
            }

    return LSTMCandlePathModel()


def create_legacy_lstm_candle_sequence_model(
    *,
    input_dim: int = len(FEATURE_SCHEMA),
    hidden_dim: int = 96,
    num_layers: int = 2,
    dropout: float = 0.15,
    horizon_steps: int = DEFAULT_HORIZON_STEPS,
) -> Any:
    """Recreate the exact V3 architecture used by the 67.26% export.

    This factory intentionally preserves the export's autoregressive feedback,
    multiscale temporal convolutions, history attention, and left-padded input
    contract. It exists for artifact compatibility; new training still uses the
    direct-horizon factory above.
    """

    torch, nn = _torch_modules()
    if torch is None or nn is None:
        raise RuntimeError("PyTorch is required for restored V3 LSTM model creation.")
    module_base: type[Any] = cast(type[Any], nn.Module)
    max_horizon = max(1, int(horizon_steps))
    feature_dim = len(PREDICTION_SCHEMA)
    location_index = FEATURE_SCHEMA.index("relative_price_location")
    phase_index = FEATURE_SCHEMA.index("phase_value")

    class LegacyLSTMCandlePathModel(module_base):
        def __init__(self) -> None:
            cast(Callable[[Any], None], module_base.__init__)(self)
            self.max_horizon_steps = max_horizon
            pattern_dim = max(16, int(hidden_dim) // 2)
            self.short_pattern = nn.Conv1d(int(input_dim), pattern_dim, kernel_size=3, padding=1)
            self.medium_pattern = nn.Conv1d(int(input_dim), pattern_dim, kernel_size=7, padding=3)
            self.long_pattern = nn.Conv1d(int(input_dim), pattern_dim, kernel_size=15, padding=7)
            self.pattern_fusion = nn.Sequential(
                nn.Linear(pattern_dim * 3, int(input_dim)),
                nn.GELU(),
                nn.LayerNorm(int(input_dim)),
            )
            self.encoder = nn.LSTM(
                input_size=int(input_dim),
                hidden_size=int(hidden_dim),
                num_layers=int(num_layers),
                dropout=float(dropout) if int(num_layers) > 1 else 0.0,
                batch_first=True,
            )
            attention_heads = 4 if int(hidden_dim) % 4 == 0 else 2 if int(hidden_dim) % 2 == 0 else 1
            self.history_attention = nn.MultiheadAttention(
                int(hidden_dim),
                num_heads=attention_heads,
                dropout=float(dropout),
                batch_first=True,
            )
            self.encoder_norm = nn.LayerNorm(int(hidden_dim))
            self.step_embedding = nn.Embedding(max_horizon, 12)
            self.decoder_cell = nn.LSTMCell(int(input_dim) + 12, int(hidden_dim))
            self.decoder_dropout = nn.Dropout(float(dropout))
            self.direction_head = nn.Linear(int(hidden_dim), 2)
            self.feature_mean_head = nn.Linear(int(hidden_dim), feature_dim)
            self.feature_scale_head = nn.Linear(int(hidden_dim), feature_dim)
            self.play_head = nn.Linear(int(hidden_dim), len(PLAY_LABELS))

        def _feedback(self, previous: Any, direction_logits: Any, mean: Any) -> Any:
            direction_probabilities = torch.softmax(direction_logits, dim=-1)
            direction_value = direction_probabilities[:, 0] - direction_probabilities[:, 1]
            next_location = torch.clamp(
                previous[:, location_index] + mean[:, 4] * MAX_PRICE_DELTA,
                0.0,
                1.0,
            )
            range_ratio = torch.clamp(
                mean[:, 3] / torch.clamp(previous[:, 4], min=1e-4) / 3.0,
                0.0,
                1.0,
            )
            body_ratio = torch.clamp(
                mean[:, 0] / torch.clamp(previous[:, 0], min=1e-4) / 3.0,
                0.0,
                1.0,
            )
            values = (
                mean[:, 0],
                mean[:, 1],
                mean[:, 2],
                direction_value,
                mean[:, 3],
                next_location,
                mean[:, 4],
                range_ratio,
                body_ratio,
                direction_value,
                torch.clamp(previous[:, 10] + 0.125, 0.0, 1.0),
                torch.ones_like(direction_value),
                previous[:, phase_index],
            )
            return torch.stack(values, dim=-1)

        def forward(
            self,
            sequence: Any,
            targets: Any | None = None,
            teacher_forcing_ratio: float = 0.0,
            horizon_steps: int | None = None,
        ) -> dict[str, Any]:
            steps = min(
                self.max_horizon_steps,
                max(1, int(horizon_steps or self.max_horizon_steps)),
            )
            visual_sequence = sequence.transpose(1, 2)
            pattern_features = torch.cat(
                (
                    torch.nn.functional.gelu(self.short_pattern(visual_sequence)),
                    torch.nn.functional.gelu(self.medium_pattern(visual_sequence)),
                    torch.nn.functional.gelu(self.long_pattern(visual_sequence)),
                ),
                dim=1,
            ).transpose(1, 2)
            fused_sequence = sequence + self.pattern_fusion(pattern_features)
            encoded, (_hidden, cell) = self.encoder(fused_sequence)
            attended, _weights = self.history_attention(
                encoded[:, -1:, :],
                encoded,
                encoded,
                need_weights=False,
            )
            pooled = self.encoder_norm(encoded[:, -1, :] + attended[:, 0, :])
            decoder_hidden = pooled
            decoder_cell = cell[-1]
            decoder_input = sequence[:, -1, :]
            direction_rows: list[Any] = []
            mean_rows: list[Any] = []
            scale_rows: list[Any] = []
            for step in range(steps):
                step_ids = torch.full(
                    (sequence.shape[0],),
                    step,
                    dtype=torch.long,
                    device=sequence.device,
                )
                embedded_step = self.step_embedding(step_ids)
                decoder_hidden, decoder_cell = self.decoder_cell(
                    torch.cat((decoder_input, embedded_step), dim=-1),
                    (decoder_hidden, decoder_cell),
                )
                decoded = self.decoder_dropout(decoder_hidden)
                direction_logits = self.direction_head(decoded)
                raw_mean = self.feature_mean_head(decoded)
                mean = torch.cat(
                    (torch.sigmoid(raw_mean[:, :4]), torch.tanh(raw_mean[:, 4:5])),
                    dim=-1,
                )
                scale = 0.01 + 0.34 * torch.sigmoid(self.feature_scale_head(decoded))
                direction_rows.append(direction_logits)
                mean_rows.append(mean)
                scale_rows.append(scale)
                predicted_input = self._feedback(decoder_input, direction_logits, mean)
                if targets is not None and step < targets.shape[1] and teacher_forcing_ratio > 0.0:
                    use_teacher = (
                        torch.rand((sequence.shape[0], 1), device=sequence.device)
                        < float(teacher_forcing_ratio)
                    )
                    decoder_input = torch.where(use_teacher, targets[:, step, :], predicted_input)
                else:
                    decoder_input = predicted_input
            return {
                "direction_logits": torch.stack(direction_rows, dim=1),
                "feature_mean": torch.stack(mean_rows, dim=1),
                "feature_scale": torch.stack(scale_rows, dim=1),
                "play_logits": self.play_head(pooled),
            }

    return LegacyLSTMCandlePathModel()


def _artifact_cache_key(model_path: Path, config_path: Path, metrics_path: Path) -> tuple[str, str, str, float, float, float]:
    def mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    return (str(model_path.resolve()), str(config_path.resolve()), str(metrics_path.resolve()), mtime(model_path), mtime(config_path), mtime(metrics_path))


def _load_artifact_bundle(model_path: Path, config_path: Path, metrics_path: Path) -> dict[str, Any]:
    key = _artifact_cache_key(model_path, config_path, metrics_path)
    if key in _ARTIFACT_CACHE:
        return _ARTIFACT_CACHE[key]
    config = _read_json(config_path)
    metrics = _read_json(metrics_path)
    bundle: dict[str, Any] = {
        "config": config,
        "metrics": metrics,
        "model": None,
        "model_architecture": str(config.get("architecture") or ""),
        "legacy_restored": False,
        "model_loaded": False,
        "ready": False,
        "retrieval_bank": None,
        "risk_control": {},
        "risk_error": "",
        "error": "",
    }
    if not (model_path.exists() and config and metrics):
        _ARTIFACT_CACHE[key] = bundle
        return bundle
    if str(config.get("model_version")) != LSTM_CANDLE_SEQUENCE_VERSION:
        bundle["error"] = "incompatible LSTM artifact; PhoenixGuard V3 candle-path retraining is required."
        _ARTIFACT_CACHE[key] = bundle
        return bundle
    torch, _nn = _torch_modules()
    if torch is None:
        bundle["error"] = "PyTorch unavailable; LSTM candle-path artifact ignored."
        _ARTIFACT_CACHE[key] = bundle
        return bundle
    try:
        architecture = str(config.get("architecture") or "")
        if architecture not in {LEGACY_MULTISCALE_ARCHITECTURE, DIRECT_RAW_CV_ARCHITECTURE}:
            raise ValueError(f"unsupported V3 LSTM architecture: {architecture or 'missing'}")
        legacy_restored = architecture == LEGACY_MULTISCALE_ARCHITECTURE
        model_factory = (
            create_legacy_lstm_candle_sequence_model
            if legacy_restored
            else create_lstm_candle_sequence_model
        )
        model = model_factory(
            input_dim=int(config.get("input_dim", len(FEATURE_SCHEMA)) or len(FEATURE_SCHEMA)),
            hidden_dim=int(config.get("hidden_dim", 96) or 96),
            num_layers=int(config.get("num_layers", 2) or 2),
            dropout=float(config.get("dropout", 0.15) or 0.0),
            horizon_steps=int(config.get("horizon_steps", DEFAULT_HORIZON_STEPS) or DEFAULT_HORIZON_STEPS),
        )
        payload: Any = torch.load(model_path, map_location="cpu", weights_only=False)
        payload_mapping = _mapping(payload)
        state_dict = payload_mapping.get("state_dict", payload) if payload_mapping else payload
        model.load_state_dict(state_dict)
        model.eval()
        bundle["model"] = model
        bundle["legacy_restored"] = legacy_restored
        bundle["model_loaded"] = True

        raw_risk_control = _mapping(payload_mapping.get("risk_control"))
        if not raw_risk_control:
            # Transitional compatibility for V3 artifacts that wrote the
            # validation-fitted controls to config but not to the checkpoint.
            config_thresholds = _mapping(config.get("selective_direction_thresholds"))
            config_retrieval = _mapping(config.get("retrieval"))
            if config_thresholds or "probability_temperature" in config:
                raw_risk_control = {
                    "temperature": config.get("probability_temperature", 1.0),
                    "thresholds": config_thresholds,
                    "retrieval": config_retrieval,
                    "target_precision": config.get("target_selective_precision"),
                }
        bundle["risk_control"] = raw_risk_control

        raw_retrieval_bank = payload_mapping.get("retrieval_bank")
        if raw_retrieval_bank is not None:
            try:
                bundle["retrieval_bank"] = validate_retrieval_bank_v3(raw_retrieval_bank)
            except (TypeError, ValueError) as exc:
                # A corrupt or leakage-unsafe bank must disable retrieval and
                # selection, but it must not suppress the diagnostic model path.
                bundle["risk_error"] = f"retrieval bank rejected: {exc}"
        bundle["ready"] = bool(metrics.get("production_ready", config.get("production_ready", False)))
        if not bundle["ready"]:
            bundle["error"] = "V3 artifact loaded but held-out evaluation did not pass the production gate."
    except Exception as exc:
        bundle["error"] = f"failed to load LSTM candle-path artifact: {exc}"
    _ARTIFACT_CACHE.clear()
    _ARTIFACT_CACHE[key] = bundle
    return bundle


def _model_forecast(
    model: Any,
    features: Sequence[Mapping[str, Any]],
    *,
    sequence_length: int,
    horizon_steps: int,
    chart_image: Any | None = None,
    chart_cut_x: int | float | None = None,
    chart_context_size: tuple[int, int] = (96, 192),
    retrieval_bank: Mapping[str, Any] | None = None,
    risk_control: Mapping[str, Any] | None = None,
    production_authorized: bool = False,
    risk_error: str = "",
    legacy_restored: bool = False,
) -> dict[str, Any]:
    torch, _nn = _torch_modules()
    if torch is None or model is None or not features:
        return {}
    matrix = (
        legacy_sequence_features_to_matrix(features, sequence_length=sequence_length)
        if legacy_restored
        else sequence_features_to_matrix(features, sequence_length=sequence_length)
    )
    tensor = torch.tensor([matrix], dtype=torch.float32)
    true_length = min(len(features), max(1, int(sequence_length)))
    lengths = torch.tensor([true_length], dtype=torch.long)
    chart_context = None
    if chart_image is not None and not legacy_restored:
        chart_context = causal_chart_context_tensor(
            chart_image,
            cut_x=chart_cut_x,
            output_size=chart_context_size,
        )
        if chart_context.ndim == 3:
            chart_context = chart_context.unsqueeze(0)
    with torch.inference_mode():
        if legacy_restored:
            outputs = model(tensor, horizon_steps=horizon_steps)
        else:
            outputs = model(
                tensor,
                horizon_steps=horizon_steps,
                lengths=lengths,
                chart_context=chart_context,
            )
        natural_logits = outputs["direction_logits"].squeeze(0)
        raw_directions = torch.softmax(natural_logits, dim=-1)
        decisions = torch.softmax(outputs.get("decision_logits", outputs["direction_logits"]), dim=-1).squeeze(0)
        means = outputs["feature_mean"].squeeze(0)
        scales = outputs["feature_scale"].squeeze(0)
        play = torch.softmax(outputs["play_logits"], dim=-1).squeeze(0)
        context_embedding = outputs.get("context_embedding")

    controls = _mapping(risk_control)
    thresholds = _mapping(controls.get("thresholds"))
    retrieval_settings = _mapping(controls.get("retrieval"))
    raw_temperature = _safe_float(controls.get("temperature"), 1.0)
    temperature_valid = raw_temperature > 0.0
    probability_temperature = raw_temperature if temperature_valid else 1.0
    calibrated_rows = temperature_softmax(
        [[float(value) for value in row.tolist()] for row in natural_logits],
        probability_temperature,
    )
    threshold_values: dict[str, float] = {}
    thresholds_valid = True
    for candidate_side in ("BUY", "SELL"):
        if candidate_side not in thresholds:
            thresholds_valid = False
            continue
        threshold = _safe_float(thresholds.get(candidate_side), 1.01)
        if not 0.0 <= threshold <= 1.01:
            thresholds_valid = False
        threshold_values[candidate_side] = threshold

    retrieval_top_k = max(1, int(_safe_float(retrieval_settings.get("top_k"), 8)))
    retrieval_alpha = _clip(retrieval_settings.get("alpha"), 0.0, 0.75)
    minimum_similarity = _clip(retrieval_settings.get("minimum_similarity"), -1.0, 0.999999, 0.05)
    similarity_power = max(0.01, _safe_float(retrieval_settings.get("similarity_power"), 2.0))
    retrieval_forecast: dict[str, Any] = {}
    retrieval_status = "disabled" if retrieval_alpha <= 0.0 else "unavailable"
    retrieval_failure = str(risk_error or "")
    if retrieval_alpha > 0.0 and retrieval_bank is not None and context_embedding is not None:
        try:
            retrieved = retrieve_forecast_v3(
                retrieval_bank,
                context_embedding.squeeze(0),
                top_k=retrieval_top_k,
                minimum_similarity=minimum_similarity,
                similarity_power=similarity_power,
            )
            retrieval_forecast = retrieved[0] if retrieved else {}
            retrieval_status = str(retrieval_forecast.get("status") or "unavailable")
        except (TypeError, ValueError) as exc:
            retrieval_failure = f"retrieval failed closed: {exc}"
            retrieval_status = "rejected"
    retrieval_required = retrieval_alpha > 0.0
    retrieval_valid = not retrieval_required or retrieval_status == "ok"
    controls_valid = bool(controls) and temperature_valid and thresholds_valid and retrieval_valid

    current_location = _clip01(features[-1].get("relative_price_location"), 0.5)
    cumulative_variance = 0.0
    forecast_path: list[dict[str, Any]] = []
    for step in range(min(int(horizon_steps), int(means.shape[0]))):
        raw_buy_probability = float(raw_directions[step, 0].item())
        raw_sell_probability = float(raw_directions[step, 1].item())
        model_buy_probability = float(calibrated_rows[step][0])
        model_sell_probability = float(calibrated_rows[step][1])
        raw_balanced_buy_probability = float(decisions[step, 0].item())
        raw_balanced_sell_probability = float(decisions[step, 1].item())
        retrieval_horizons = cast(
            Sequence[Mapping[str, Any]],
            retrieval_forecast.get("horizons", []),
        )
        retrieval_row: Mapping[str, Any]
        if step < len(retrieval_horizons):
            retrieval_row = retrieval_horizons[step]
        else:
            retrieval_row = {}
        retrieval_probabilities = _mapping(retrieval_row.get("probabilities"))
        retrieval_buy_probability = _clip01(retrieval_probabilities.get("BUY"), 0.5)
        retrieval_sell_probability = _clip01(retrieval_probabilities.get("SELL"), 0.5)
        retrieval_support = _clip01(retrieval_row.get("effective_confidence"), 0.0)
        effective_alpha = retrieval_alpha * retrieval_support if retrieval_status == "ok" else 0.0
        buy_probability = (1.0 - effective_alpha) * model_buy_probability + effective_alpha * retrieval_buy_probability
        sell_probability = (1.0 - effective_alpha) * model_sell_probability + effective_alpha * retrieval_sell_probability
        balanced_buy_probability = (
            (1.0 - effective_alpha) * raw_balanced_buy_probability
            + effective_alpha * retrieval_buy_probability
        )
        balanced_sell_probability = (
            (1.0 - effective_alpha) * raw_balanced_sell_probability
            + effective_alpha * retrieval_sell_probability
        )
        continuous = [float(value) for value in means[step].tolist()]
        scale = [float(value) for value in scales[step].tolist()]
        delta = _clip(continuous[4], -1.0, 1.0) * MAX_PRICE_DELTA
        delta_scale = max(0.001, scale[4] * MAX_PRICE_DELTA)
        open_location = current_location
        close_location = _clip01(open_location + delta, open_location)
        candle_range = max(0.001, _clip01(continuous[3], 0.01))
        shape_total = max(0.001, continuous[0] + continuous[1] + continuous[2])
        upper_extension = candle_range * continuous[1] / shape_total
        lower_extension = candle_range * continuous[2] / shape_total
        high_location = _clip01(max(open_location, close_location) + upper_extension)
        low_location = _clip01(min(open_location, close_location) - lower_extension)
        cumulative_variance += delta_scale * delta_scale
        cumulative_scale = math.sqrt(cumulative_variance)
        model_direction = "BUY" if model_buy_probability >= model_sell_probability else "SELL"
        direction = "BUY" if balanced_buy_probability >= balanced_sell_probability else "SELL"
        selective_confidence = buy_probability if direction == "BUY" else sell_probability
        confidence = selective_confidence * (1.0 - 0.18 * step / max(1, horizon_steps - 1))
        selective_threshold = threshold_values.get(direction, 1.01)
        selective_authorized = bool(
            production_authorized
            and controls_valid
            and selective_confidence + 1e-12 >= selective_threshold
        )
        movement_direction = "BUY" if delta >= 0.0 else "SELL"
        forecast_path.append(
            {
                "step": step + 1,
                "event": f"CANDLE_EVENT_{step + 1}",
                "direction": direction,
                "candle_body_direction": direction,
                "model_direction": model_direction,
                "movement_direction": movement_direction,
                "buy_probability": round(buy_probability, 4),
                "sell_probability": round(sell_probability, 4),
                "raw_model_buy_probability": round(raw_buy_probability, 4),
                "raw_model_sell_probability": round(raw_sell_probability, 4),
                "calibrated_model_buy_probability": round(model_buy_probability, 4),
                "calibrated_model_sell_probability": round(model_sell_probability, 4),
                "balanced_direction": direction,
                "balanced_buy_probability": round(balanced_buy_probability, 4),
                "balanced_sell_probability": round(balanced_sell_probability, 4),
                "retrieval_buy_probability": round(retrieval_buy_probability, 4),
                "retrieval_sell_probability": round(retrieval_sell_probability, 4),
                "retrieval_effective_alpha": round(effective_alpha, 6),
                "selective_authorized": selective_authorized,
                "selective_status": "AUTHORIZED" if selective_authorized else "NO_EDGE",
                "selective_confidence": round(selective_confidence, 6),
                "selective_threshold": round(selective_threshold, 6),
                "confidence": round(_clip01(confidence), 4),
                "expected_open_norm": round(open_location, 6),
                "expected_high_norm": round(high_location, 6),
                "expected_low_norm": round(low_location, 6),
                "expected_close_norm": round(close_location, 6),
                "close_lower_90_norm": round(_clip01(close_location - 1.645 * cumulative_scale), 6),
                "close_upper_90_norm": round(_clip01(close_location + 1.645 * cumulative_scale), 6),
                "expected_delta_norm": round(delta, 6),
                "delta_scale_norm": round(delta_scale, 6),
                "expected_body_ratio": round(_clip01(continuous[0]), 6),
                "expected_upper_wick_ratio": round(_clip01(continuous[1]), 6),
                "expected_lower_wick_ratio": round(_clip01(continuous[2]), 6),
                "expected_range_norm": round(candle_range, 6),
            }
        )
        current_location = close_location
    play_probabilities = {label: round(float(play[index].item()), 4) for index, label in enumerate(PLAY_LABELS)}
    play_label = (
        max(play_probabilities, key=lambda label: play_probabilities[label])
        if play_probabilities
        else "PULLBACK"
    )
    return {
        "forecast_path": forecast_path,
        "play_label": play_label,
        "play_probabilities": play_probabilities,
        "context_embedding": (
            [round(float(value), 7) for value in context_embedding.squeeze(0).tolist()]
            if context_embedding is not None
            else []
        ),
        "chart_context_used": chart_context is not None,
        "probability_temperature": round(probability_temperature, 6),
        "risk_control_applied": controls_valid,
        "risk_control_status": (
            "READY"
            if controls_valid
            else "RETRIEVAL_UNAVAILABLE"
            if thresholds_valid and temperature_valid and not retrieval_valid
            else "CALIBRATION_UNAVAILABLE"
        ),
        "risk_control_error": retrieval_failure,
        "retrieval": {
            "status": retrieval_status,
            "top_k": retrieval_top_k,
            "alpha": round(retrieval_alpha, 6),
            "neighbor_count": int(retrieval_forecast.get("neighbor_count", 0) or 0),
            "unique_source_count": int(retrieval_forecast.get("unique_source_count", 0) or 0),
            "effective_sample_size": round(_safe_float(retrieval_forecast.get("effective_sample_size")), 6),
            "mean_similarity": round(_safe_float(retrieval_forecast.get("mean_similarity")), 6),
            "effective_confidence": round(_safe_float(retrieval_forecast.get("effective_confidence")), 6),
        },
    }


def _direction_probabilities(features: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    if not features:
        return {"BUY": 0.34, "SELL": 0.33, "HOLD": 0.33}
    recent = list(features[-min(24, len(features)) :])
    weighted_buy = weighted_sell = 0.0
    weighted_hold = total_weight = 0.18
    for offset, row in enumerate(recent):
        weight = (1.0 + offset / max(1, len(recent) - 1) * 1.4) * max(
            0.35,
            0.65 + _clip01(row.get("body_norm")) - 0.45 * max(_clip01(row.get("upper_wick_norm")), _clip01(row.get("lower_wick_norm"))),
        )
        total_weight += weight
        side = _side(row.get("direction"), "HOLD")
        if side == "BUY":
            weighted_buy += weight
        elif side == "SELL":
            weighted_sell += weight
        else:
            weighted_hold += weight
    return {"BUY": weighted_buy / total_weight, "SELL": weighted_sell / total_weight, "HOLD": weighted_hold / total_weight}


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
    chart_image: Any | None = None,
    chart_cut_x: int | float | None = None,
) -> dict[str, Any]:
    features = candle_sequence_features(candles, image_size=image_size, sequence_phase=sequence_phase)
    model_file, config_file, metrics_file = Path(model_path), Path(config_path), Path(metrics_path)
    artifact = _load_artifact_bundle(model_file, config_file, metrics_file)
    config, metrics = _mapping(artifact.get("config")), _mapping(artifact.get("metrics"))
    artifact_available = bool(model_file.exists() and config and metrics)
    artifact_model_loaded = bool(artifact.get("model_loaded"))
    artifact_ready = bool(artifact.get("ready"))
    legacy_restored = bool(artifact.get("legacy_restored"))
    horizon_steps = int(config.get("horizon_steps", DEFAULT_HORIZON_STEPS) or DEFAULT_HORIZON_STEPS)
    configured_chart_size = config.get("chart_context_size", [96, 192])
    chart_context_size = (96, 192)
    if isinstance(configured_chart_size, Sequence) and not isinstance(configured_chart_size, (str, bytes, bytearray)):
        configured_values: list[Any] = list(cast(Sequence[Any], configured_chart_size))
        if len(configured_values) >= 2:
            chart_context_size = (
                max(8, int(_safe_float(configured_values[0], 96))),
                max(8, int(_safe_float(configured_values[1], 192))),
            )
    forecast = _model_forecast(
        artifact.get("model") if artifact_model_loaded else None,
        features,
        sequence_length=int(config.get("sequence_length", DEFAULT_SEQUENCE_LENGTH) or DEFAULT_SEQUENCE_LENGTH),
        horizon_steps=horizon_steps,
        chart_image=chart_image,
        chart_cut_x=chart_cut_x,
        chart_context_size=chart_context_size,
        retrieval_bank=cast(Mapping[str, Any] | None, artifact.get("retrieval_bank")),
        risk_control=_mapping(artifact.get("risk_control")),
        production_authorized=artifact_ready,
        risk_error=str(artifact.get("risk_error") or ""),
        legacy_restored=legacy_restored,
    )
    forecast_path = cast(list[dict[str, Any]], forecast.get("forecast_path", []))
    fallback = _direction_probabilities(features)
    if forecast_path:
        directional_rows = forecast_path[: min(6, len(forecast_path))]
        buy_probability = sum(float(row["buy_probability"]) / math.sqrt(index + 1) for index, row in enumerate(directional_rows))
        sell_probability = sum(float(row["sell_probability"]) / math.sqrt(index + 1) for index, row in enumerate(directional_rows))
        divisor = sum(1.0 / math.sqrt(index + 1) for index in range(len(directional_rows)))
        buy_probability /= max(1e-9, divisor)
        sell_probability /= max(1e-9, divisor)
    else:
        buy_probability, sell_probability = fallback["BUY"], fallback["SELL"]
    side = "BUY" if buy_probability >= sell_probability else "SELL"
    primary_probability = max(buy_probability, sell_probability)
    play_probabilities = cast(dict[str, float], forecast.get("play_probabilities", {}))
    play_label = str(forecast.get("play_label") or "PULLBACK")
    if forecast_path:
        net_path_delta = float(forecast_path[-1]["expected_close_norm"]) - float(forecast_path[0]["expected_open_norm"])
        path_side = "BUY" if net_path_delta >= 0.0 else "SELL"
    else:
        net_path_delta = 0.0
        path_side = side
    continuation_probability = _clip01(play_probabilities.get("CONTINUATION"), primary_probability)
    reversal_probability = _clip01(play_probabilities.get("REVERSAL"), 1.0 - primary_probability)
    pullback_probability = _clip01(play_probabilities.get("PULLBACK"), fallback["HOLD"])
    heldout_balanced = _clip01(metrics.get("test_balanced_accuracy", metrics.get("balanced_accuracy")), 0.5)
    path_confidence = (
        sum(float(row.get("confidence", 0.0)) for row in forecast_path) / len(forecast_path)
        if forecast_path
        else 0.0
    )
    confidence = _clip01(0.58 * path_confidence + 0.27 * heldout_balanced + 0.15 * min(1.0, len(features) / 48.0))
    next_1 = forecast_path[0] if forecast_path else {}
    next_2 = forecast_path[1] if len(forecast_path) > 1 else {}
    legacy_active = bool(legacy_restored and artifact_ready and forecast_path)
    # Restoring an exported architecture proves that inference is available; it
    # does not recreate a missing, current selective-risk gate.  In particular,
    # the legacy bundle can produce a directional body classification while its
    # regression head projects price the other way.  Never promote that
    # disagreement (or any row-level NO_EDGE result) to an authorized signal.
    next_body_side = _side(next_1.get("direction"), "HOLD")
    next_movement_side = _side(next_1.get("movement_direction"), "HOLD")
    next_direction_consistent = bool(
        next_body_side in {"BUY", "SELL"}
        and next_movement_side in {"BUY", "SELL"}
        and next_body_side == next_movement_side
    )
    selective_authorized = bool(
        next_1.get("selective_authorized", False)
        and next_direction_consistent
    )
    selective_status = "AUTHORIZED" if selective_authorized else "NO_EDGE"
    selective_side = str(next_1.get("direction") or side) if selective_authorized else "NO_EDGE"
    contribution = round(0.08 * confidence, 4) if selective_authorized else 0.0
    if legacy_active and selective_authorized:
        reason = (
            "Exact exported V3 multiscale-attention LSTM restored and the current "
            "row-level selective-risk gate authorizes the next candle event."
        )
    elif legacy_active:
        reason = (
            "Exact exported V3 multiscale-attention LSTM restored for diagnostic "
            "inference, but no current row-level selective edge is authorized."
        )
    elif selective_authorized:
        reason = "V3 held-out production and class-conditional risk gates authorize the next candle event."
    elif artifact_model_loaded and forecast_path and not artifact_ready:
        reason = "V3 challenger loaded for diagnostic path display, but held-out production gates require NO_EDGE."
    elif artifact_model_loaded and forecast_path:
        reason = "V3 path is diagnostic only; calibration/retrieval thresholds did not authorize an edge."
    else:
        reason = str(artifact.get("error") or "V3 LSTM candle-path artifact/config/metrics not present; output ignored.")
    reversal_step = next(
        (int(row["step"]) for row in forecast_path if str(row.get("movement_direction")) != path_side),
        None,
    )
    size_values = list(image_size)[:2]

    return {
        "schema_version": LSTM_CONTRIBUTION_SCHEMA_VERSION,
        "stack_version": "PHOENIXGUARD_V3",
        "modality": "COMPUTER_VISION",
        "training_source": "RAW_SCREENSHOT_SUITES",
        "skill": "LSTM_CANDLE_PATH",
        "model_version": str(config.get("model_version") or LSTM_CANDLE_SEQUENCE_VERSION),
        "artifact_path": str(model_file),
        "config_path": str(config_file),
        "metrics_path": str(metrics_file),
        "artifact_available": artifact_available,
        "artifact_loaded": artifact_model_loaded,
        "architecture": str(config.get("architecture") or ""),
        "legacy_restored": legacy_restored,
        "production_authorized": artifact_ready,
        "fresh": bool(artifact_model_loaded and forecast_path),
        "blocker": False,
        "contribution": contribution,
        "side": side,
        "selective_side": selective_side,
        "selective_authorized": selective_authorized,
        "selective_status": selective_status,
        "selective_prediction": {
            "status": selective_status,
            "side": selective_side,
            "confidence": round(_safe_float(next_1.get("selective_confidence")), 6),
            "threshold": (
                0.0
                if legacy_active
                else round(_safe_float(next_1.get("selective_threshold"), 1.01), 6)
            ),
            "horizon_step": 1,
            "policy": (
                "VALIDATION_FITTED_CLASS_CONDITIONAL_ABSTENTION"
                if selective_authorized
                else "LEGACY_EXPORTED_V3_DIAGNOSTIC_ONLY"
                if legacy_active
                else "VALIDATION_FITTED_CLASS_CONDITIONAL_ABSTENTION"
            ),
            "accuracy_guarantee": False,
        },
        "path_side": path_side,
        "net_expected_path_delta_norm": round(net_path_delta, 6),
        "next_1_direction": str(next_1.get("direction") or side),
        "next_1_probability": round(float(next_1.get("confidence", primary_probability)), 4),
        "next_2_direction": str(next_2.get("direction") or side),
        "next_2_probability": round(float(next_2.get("confidence", max(0.0, primary_probability - 0.05))), 4),
        "continuation_probability": round(continuation_probability, 4),
        "reversal_probability": round(reversal_probability, 4),
        "pullback_first_probability": round(pullback_probability, 4),
        "confidence": round(confidence, 4),
        "sequence_length": len(features),
        "input_window_candles": int(config.get("sequence_length", DEFAULT_SEQUENCE_LENGTH) or DEFAULT_SEQUENCE_LENGTH),
        "horizon_steps": len(forecast_path) if forecast_path else horizon_steps,
        "horizon_unit": "CANDLE_EVENTS",
        "clock_time_assumption": "NONE",
        "forecast_available": bool(forecast_path),
        "forecast_path": forecast_path,
        "context_embedding": cast(list[float], forecast.get("context_embedding", [])),
        "chart_context_used": bool(forecast.get("chart_context_used", False)),
        "probability_temperature": forecast.get("probability_temperature", 1.0),
        "risk_control_applied": bool(forecast.get("risk_control_applied", False)),
        "risk_control_status": str(
            forecast.get("risk_control_status") or "CALIBRATION_UNAVAILABLE"
        ),
        "risk_control_error": str(forecast.get("risk_control_error") or ""),
        "retrieval": _mapping(forecast.get("retrieval")),
        "progression_play": {
            "label": play_label,
            "probabilities": play_probabilities,
            "dominant_direction": path_side,
            "candle_body_bias": side,
            "first_direction_change_step": reversal_step,
            "horizon_steps": len(forecast_path),
            "horizon_unit": "CANDLE_EVENTS",
        },
        "source_image_size": [int(_safe_float(value, 1.0)) for value in size_values],
        "timeframe": str(timeframe or "").upper(),
        "sequence_phase": str(sequence_phase or "").upper(),
        "market_play_label": str(market_play_label or "").upper(),
        "pair_profile": _mapping(pair_profile),
        "usage": {
            "default": str(config.get("default_usage", "ALL_ANALYSIS")).upper(),
            "high_frequency_enabled": bool(config.get("high_frequency_enabled", True)),
            "normal_analysis_enabled": bool(config.get("normal_analysis_enabled", True)),
            "normal_analysis_env": "PHOENIXGUARD_LSTM_NORMAL_ANALYSIS",
        },
        "metrics": {
            "test_balanced_accuracy": metrics.get("test_balanced_accuracy", metrics.get("balanced_accuracy")),
            "test_path_delta_mae": metrics.get("test_path_delta_mae"),
            "test_interval_90_coverage": metrics.get("test_interval_90_coverage"),
            "test_play_accuracy": metrics.get("test_play_accuracy"),
            "test_play_balanced_accuracy": metrics.get("test_play_balanced_accuracy"),
            "persistence_baseline_accuracy": metrics.get("test_persistence_baseline_accuracy"),
            "production_ready": bool(metrics.get("production_ready")),
        },
        "interpretation": (
            f"Restored V3 multiscale-attention LSTM projects a {len(forecast_path)}-candle-event {play_label.lower()} diagnostic path moving {path_side}, with a {side} candle-body bias; selective status is {selective_status}, and no wall-clock duration is imposed."
            if legacy_active
            else f"Causal LSTM projects a {len(forecast_path)}-candle-event {play_label.lower()} diagnostic path moving {path_side}, with a {side} candle-body bias; selective status is {selective_status}, and no wall-clock duration is imposed."
            if forecast_path
            else "V3 LSTM candle-path forecast is offline; no future progression is fabricated."
        ),
        "reason": reason,
        "features": features[-int(config.get("sequence_length", DEFAULT_SEQUENCE_LENGTH) or DEFAULT_SEQUENCE_LENGTH) :],
    }


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "DEFAULT_HORIZON_STEPS",
    "DEFAULT_METRICS_PATH",
    "DEFAULT_MODEL_PATH",
    "DEFAULT_SEQUENCE_LENGTH",
    "DIRECT_RAW_CV_ARCHITECTURE",
    "FEATURE_SCHEMA",
    "LEGACY_MULTISCALE_ARCHITECTURE",
    "LSTM_CANDLE_SEQUENCE_VERSION",
    "LSTM_CONTRIBUTION_SCHEMA_VERSION",
    "MAX_PRICE_DELTA",
    "PLAY_LABELS",
    "PREDICTION_FEATURE_INDICES",
    "PREDICTION_SCHEMA",
    "build_lstm_candle_sequence_contribution",
    "causal_chart_context_tensor",
    "candle_sequence_features",
    "create_lstm_candle_sequence_model",
    "create_legacy_lstm_candle_sequence_model",
    "feature_vector",
    "phase_value",
    "sequence_features_to_matrix",
    "legacy_sequence_features_to_matrix",
]
