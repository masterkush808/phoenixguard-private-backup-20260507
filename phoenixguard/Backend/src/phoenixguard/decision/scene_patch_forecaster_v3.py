from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Mapping

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from phoenixguard.decision.scene_forecast_features_v3 import (
    CANDLE_NUMERIC_SCHEMA,
    CONTEXT_NUMERIC_SCHEMA,
)


SCENE_FORECASTER_SCHEMA_VERSION = "PG_SCENE_PATCH_FORECASTER_V3"
DEFAULT_HORIZON = 12
QUANTILE_LEVELS = (0.10, 0.50, 0.90)
MOVEMENT_LABELS = ("SELL", "HOLD", "BUY")
SCENARIO_LABELS = ("BEAR", "BASE", "BULL")


@dataclass(frozen=True)
class ScenePatchForecasterConfig:
    candle_features: int = len(CANDLE_NUMERIC_SCHEMA)
    static_features: int = len(CONTEXT_NUMERIC_SCHEMA)
    horizon: int = DEFAULT_HORIZON
    patch_size: int = 4
    d_model: int = 64
    attention_heads: int = 4
    encoder_layers: int = 2
    feedforward_width: int = 128
    dropout: float = 0.10
    max_path_scale: float = 6.0
    quantile_gap_scale: float = 1.5
    span_scale: float = 1.0
    scenario_tail_scale: float = 0.35
    minimum_span: float = 1.0e-4

    def __post_init__(self) -> None:
        if self.candle_features <= 0 or self.static_features <= 0:
            raise ValueError("candle_features and static_features must be positive")
        if self.horizon <= 0 or self.patch_size <= 0:
            raise ValueError("horizon and patch_size must be positive")
        if self.d_model <= 0 or self.d_model % self.attention_heads:
            raise ValueError("d_model must be positive and divisible by attention_heads")
        if self.encoder_layers <= 0 or self.feedforward_width <= 0:
            raise ValueError("encoder_layers and feedforward_width must be positive")
        if not 0.0 <= self.dropout < 1.0:
            raise ValueError("dropout must be in [0, 1)")
        if min(
            self.max_path_scale,
            self.quantile_gap_scale,
            self.span_scale,
            self.scenario_tail_scale,
            self.minimum_span,
        ) <= 0.0:
            raise ValueError("all output scales must be positive")

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class SceneForecastLossWeights:
    quantile: float = 1.00
    path: float = 0.85
    movement: float = 0.35
    spans: float = 0.20
    endpoint: float = 0.20
    turning: float = 0.30
    extrema: float = 0.20
    roughness: float = 0.20
    scenario: float = 0.20
    teacher: float = 0.15

    def __post_init__(self) -> None:
        values = tuple(asdict(self).values())
        if any(value < 0.0 for value in values):
            raise ValueError("loss weights cannot be negative")
        if self.quantile <= 0.0 or self.path <= 0.0:
            raise ValueError("full-horizon quantile and path supervision cannot be disabled")


def _sinusoidal_positions(length: int, width: int, *, device: torch.device, dtype: torch.dtype) -> Tensor:
    position = torch.arange(length, device=device, dtype=dtype).unsqueeze(1)
    even_width = (width + 1) // 2
    exponent = torch.arange(even_width, device=device, dtype=dtype)
    exponent = exponent * (-math.log(10_000.0) / max(width, 1))
    angles = position * torch.exp(exponent).unsqueeze(0)
    encoding = torch.zeros((length, width), device=device, dtype=dtype)
    encoding[:, 0::2] = torch.sin(angles[:, : encoding[:, 0::2].shape[1]])
    encoding[:, 1::2] = torch.cos(angles[:, : encoding[:, 1::2].shape[1]])
    return encoding


class ScenePatchForecasterV3(nn.Module):
    """Causal 12-event scene forecaster over closed candles and as-of suite context.

    Every predicted close is an anchor-relative cumulative path value. The decoder is
    direct across the complete horizon, so it may retain reversals and local fluctuations;
    it is not constrained to extrapolate one repeated increment.
    """

    def __init__(self, config: ScenePatchForecasterConfig | None = None) -> None:
        super().__init__()
        self.config = config or ScenePatchForecasterConfig()
        config = self.config

        patch_width = config.patch_size * (config.candle_features + 1)
        self.patch_projection = nn.Sequential(
            nn.Linear(patch_width, config.d_model),
            nn.LayerNorm(config.d_model),
            nn.GELU(),
        )
        layer = nn.TransformerEncoderLayer(
            d_model=config.d_model,
            nhead=config.attention_heads,
            dim_feedforward=config.feedforward_width,
            dropout=config.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(
            layer,
            num_layers=config.encoder_layers,
            norm=nn.LayerNorm(config.d_model),
            enable_nested_tensor=False,
        )

        self.static_encoder = nn.Sequential(
            nn.Linear(config.static_features * 2, config.feedforward_width),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.feedforward_width, config.d_model),
            nn.LayerNorm(config.d_model),
        )
        self.static_gate = nn.Sequential(
            nn.Linear(config.d_model * 2, config.d_model),
            nn.Sigmoid(),
        )
        self.fusion_norm = nn.LayerNorm(config.d_model)
        self.decoder = nn.Sequential(
            nn.Linear(config.d_model, config.feedforward_width),
            nn.GELU(),
            nn.Dropout(config.dropout),
        )

        self.close_head = nn.Linear(config.feedforward_width, config.horizon * 3)
        self.span_head = nn.Linear(config.feedforward_width, config.horizon * 3 * 2)
        self.movement_head = nn.Linear(config.feedforward_width, config.horizon * 3)
        self.scenario_path_head = nn.Linear(config.feedforward_width, config.horizon * 3)
        self.scenario_probability_head = nn.Linear(config.feedforward_width, 3)

    def _patch_tokens(self, candles: Tensor, candle_mask: Tensor) -> tuple[Tensor, Tensor]:
        batch, steps, features = candles.shape
        patch_size = self.config.patch_size
        left_padding = (-steps) % patch_size
        if left_padding:
            candles = F.pad(candles, (0, 0, left_padding, 0))
            candle_mask = F.pad(candle_mask, (left_padding, 0), value=False)

        validity = candle_mask.to(dtype=candles.dtype).unsqueeze(-1)
        marked_candles = torch.cat((candles * validity, validity), dim=-1)
        patch_count = marked_candles.shape[1] // patch_size
        marked_candles = marked_candles.reshape(batch, patch_count, patch_size * (features + 1))
        patch_mask = candle_mask.reshape(batch, patch_count, patch_size).any(dim=-1)
        return self.patch_projection(marked_candles), patch_mask

    def forward(
        self,
        candles: Tensor,
        candle_mask: Tensor | None = None,
        static_values: Tensor | None = None,
        static_missing_mask: Tensor | None = None,
    ) -> dict[str, Tensor]:
        if candles.ndim != 3 or candles.shape[-1] != self.config.candle_features:
            raise ValueError(
                "candles must have shape [batch, steps, "
                f"{self.config.candle_features}]"
            )
        batch, steps, _ = candles.shape
        if steps <= 0:
            raise ValueError("candles must contain at least one time step")
        candles = torch.nan_to_num(candles)

        if candle_mask is None:
            candle_mask = torch.ones((batch, steps), device=candles.device, dtype=torch.bool)
        else:
            candle_mask = candle_mask.to(device=candles.device, dtype=torch.bool)
            if candle_mask.shape != (batch, steps):
                raise ValueError("candle_mask must have shape [batch, steps]")
        if not bool(candle_mask.any(dim=1).all()):
            raise ValueError("every sample must contain at least one observed candle")

        if static_values is None:
            static_values = candles.new_zeros((batch, self.config.static_features))
        if static_missing_mask is None:
            static_missing_mask = torch.ones(
                (batch, self.config.static_features),
                device=candles.device,
                dtype=torch.bool,
            )
        static_values = torch.nan_to_num(static_values.to(device=candles.device, dtype=candles.dtype))
        static_missing_mask = static_missing_mask.to(device=candles.device, dtype=torch.bool)
        expected_static = (batch, self.config.static_features)
        if static_values.shape != expected_static or static_missing_mask.shape != expected_static:
            raise ValueError(f"static tensors must have shape {expected_static}")

        tokens, patch_mask = self._patch_tokens(candles, candle_mask)
        positions = _sinusoidal_positions(
            tokens.shape[1],
            tokens.shape[2],
            device=tokens.device,
            dtype=tokens.dtype,
        )
        tokens = tokens + positions.unsqueeze(0)
        causal_mask = torch.triu(
            torch.full(
                (tokens.shape[1], tokens.shape[1]),
                float("-inf"),
                device=tokens.device,
                dtype=tokens.dtype,
            ),
            diagonal=1,
        )
        encoded = self.encoder(tokens, mask=causal_mask)
        sequence_state = encoded[:, -1]

        static_observed = ~static_missing_mask
        static_input = torch.cat(
            (
                static_values * static_observed.to(static_values.dtype),
                static_observed.to(static_values.dtype),
            ),
            dim=-1,
        )
        static_state = self.static_encoder(static_input)
        gate = self.static_gate(torch.cat((sequence_state, static_state), dim=-1))
        fused = self.fusion_norm(sequence_state + gate * static_state)
        decoded = self.decoder(fused)

        horizon = self.config.horizon
        close_raw = self.close_head(decoded).reshape(batch, horizon, 3)
        p50 = self.config.max_path_scale * torch.tanh(close_raw[..., 0])
        lower_gap = self.config.quantile_gap_scale * F.softplus(close_raw[..., 1])
        upper_gap = self.config.quantile_gap_scale * F.softplus(close_raw[..., 2])
        close_quantiles = torch.stack((p50 - lower_gap, p50, p50 + upper_gap), dim=-1)

        span_raw = self.span_head(decoded).reshape(batch, horizon, 3, 2)
        spans = self.config.minimum_span + self.config.span_scale * F.softplus(span_raw)
        upper_spans = spans[..., 0]
        lower_spans = spans[..., 1]
        anchor = torch.zeros_like(close_quantiles[:, :1])
        predicted_open = torch.cat((anchor, close_quantiles[:, :-1]), dim=1)
        predicted_high = torch.maximum(predicted_open, close_quantiles) + upper_spans
        predicted_low = torch.minimum(predicted_open, close_quantiles) - lower_spans
        ohlc_quantiles = torch.stack(
            (predicted_open, predicted_high, predicted_low, close_quantiles),
            dim=-1,
        )

        movement_logits = self.movement_head(decoded).reshape(batch, horizon, 3)
        scenario_raw = self.scenario_path_head(decoded).reshape(batch, horizon, 3)
        base = close_quantiles[..., 0] + torch.sigmoid(scenario_raw[..., 0]) * (
            close_quantiles[..., 2] - close_quantiles[..., 0]
        )
        bear = close_quantiles[..., 0] - self.config.scenario_tail_scale * F.softplus(
            scenario_raw[..., 1]
        )
        bull = close_quantiles[..., 2] + self.config.scenario_tail_scale * F.softplus(
            scenario_raw[..., 2]
        )
        scenario_trajectories = torch.stack((bear, base, bull), dim=1)
        scenario_logits = self.scenario_probability_head(decoded)

        return {
            "close_quantiles": close_quantiles,
            "upper_spans": upper_spans,
            "lower_spans": lower_spans,
            "ohlc_quantiles": ohlc_quantiles,
            "movement_logits": movement_logits,
            "movement_probabilities": torch.softmax(movement_logits, dim=-1),
            "scenario_trajectories": scenario_trajectories,
            "scenario_logits": scenario_logits,
            "scenario_probabilities": torch.softmax(scenario_logits, dim=-1),
            "patch_mask": patch_mask,
        }


def _masked_mean(values: Tensor, mask: Tensor) -> Tensor:
    mask = mask.to(device=values.device, dtype=values.dtype)
    while mask.ndim < values.ndim:
        mask = mask.unsqueeze(-1)
    expanded = mask.expand_as(values)
    denominator = expanded.sum().clamp_min(1.0)
    return (values * expanded).sum() / denominator


def _turning_point_loss(prediction: Tensor, target: Tensor, mask: Tensor) -> Tensor:
    if prediction.shape[1] < 3:
        return prediction.sum() * 0.0
    prediction_slopes = prediction[:, 1:] - prediction[:, :-1]
    target_slopes = target[:, 1:] - target[:, :-1]
    valid_slopes = mask[:, 1:] & mask[:, :-1]
    valid_turns = valid_slopes[:, 1:] & valid_slopes[:, :-1]
    target_turns = (target_slopes[:, 1:] * target_slopes[:, :-1] < 0.0).to(prediction.dtype)
    turn_logits = -4.0 * prediction_slopes[:, 1:] * prediction_slopes[:, :-1]
    loss = F.binary_cross_entropy_with_logits(turn_logits, target_turns, reduction="none")
    return _masked_mean(loss, valid_turns)


def _extrema_loss(prediction: Tensor, target: Tensor, mask: Tensor) -> Tensor:
    negative_fill = torch.finfo(prediction.dtype).min
    positive_fill = torch.finfo(prediction.dtype).max
    predicted_max = prediction.masked_fill(~mask, negative_fill).max(dim=1).values
    predicted_min = prediction.masked_fill(~mask, positive_fill).min(dim=1).values
    target_max = target.masked_fill(~mask, negative_fill).max(dim=1).values
    target_min = target.masked_fill(~mask, positive_fill).min(dim=1).values
    value_loss = F.smooth_l1_loss(predicted_max, target_max) + F.smooth_l1_loss(
        predicted_min, target_min
    )

    max_indices = target.masked_fill(~mask, negative_fill).argmax(dim=1)
    min_indices = target.masked_fill(~mask, positive_fill).argmin(dim=1)
    max_timing = F.cross_entropy(prediction.masked_fill(~mask, negative_fill), max_indices)
    min_timing = F.cross_entropy((-prediction).masked_fill(~mask, negative_fill), min_indices)
    return value_loss + 0.25 * (max_timing + min_timing)


def _last_valid(values: Tensor, mask: Tensor) -> Tensor:
    indices = torch.arange(values.shape[1], device=values.device).unsqueeze(0)
    last_indices = indices.masked_fill(~mask, -1).max(dim=1).values
    if bool((last_indices < 0).any()):
        raise ValueError("every loss sample must have at least one valid target")
    return values.gather(1, last_indices.unsqueeze(1)).squeeze(1)


def scene_forecast_loss(
    outputs: Mapping[str, Tensor],
    target_close_path: Tensor,
    target_movement: Tensor,
    *,
    target_mask: Tensor | None = None,
    target_upper_spans: Tensor | None = None,
    target_lower_spans: Tensor | None = None,
    teacher_quantiles: Tensor | None = None,
    teacher_mask: Tensor | None = None,
    weights: SceneForecastLossWeights | None = None,
) -> tuple[Tensor, dict[str, Tensor]]:
    """Fully supervise the direct path; endpoint loss is an additive detail, never a branch."""

    weights = weights or SceneForecastLossWeights()
    close_quantiles = outputs["close_quantiles"]
    if close_quantiles.ndim != 3 or close_quantiles.shape[-1] != 3:
        raise ValueError("close_quantiles must have shape [batch, horizon, 3]")
    batch, horizon, _ = close_quantiles.shape
    expected = (batch, horizon)
    target_close_path = target_close_path.to(close_quantiles)
    target_movement = target_movement.to(device=close_quantiles.device, dtype=torch.long)
    if target_close_path.shape != expected or target_movement.shape != expected:
        raise ValueError(f"target paths and movements must have shape {expected}")
    if target_mask is None:
        target_mask = torch.isfinite(target_close_path)
    else:
        target_mask = target_mask.to(device=close_quantiles.device, dtype=torch.bool)
    if target_mask.shape != expected or not bool(target_mask.any(dim=1).all()):
        raise ValueError("target_mask must cover at least one event in every sample")
    target_close_path = torch.nan_to_num(target_close_path)

    levels = close_quantiles.new_tensor(QUANTILE_LEVELS).view(1, 1, 3)
    quantile_error = target_close_path.unsqueeze(-1) - close_quantiles
    pinball = torch.maximum(levels * quantile_error, (levels - 1.0) * quantile_error)
    quantile_loss = _masked_mean(pinball, target_mask)

    p50 = close_quantiles[..., 1]
    path_values = F.smooth_l1_loss(p50, target_close_path, reduction="none")
    path_loss = _masked_mean(path_values, target_mask)

    movement_values = F.cross_entropy(
        outputs["movement_logits"].reshape(-1, 3),
        target_movement.reshape(-1),
        reduction="none",
    ).reshape(batch, horizon)
    movement_loss = _masked_mean(movement_values, target_mask)

    span_loss = close_quantiles.sum() * 0.0
    if target_upper_spans is not None and target_lower_spans is not None:
        target_upper_spans = torch.nan_to_num(target_upper_spans.to(close_quantiles)).clamp_min(0.0)
        target_lower_spans = torch.nan_to_num(target_lower_spans.to(close_quantiles)).clamp_min(0.0)
        if target_upper_spans.shape != expected or target_lower_spans.shape != expected:
            raise ValueError(f"target spans must have shape {expected}")
        predicted_upper = outputs["upper_spans"][..., 1]
        predicted_lower = outputs["lower_spans"][..., 1]
        span_values = F.smooth_l1_loss(
            predicted_upper, target_upper_spans, reduction="none"
        ) + F.smooth_l1_loss(predicted_lower, target_lower_spans, reduction="none")
        span_loss = _masked_mean(span_values, target_mask)

    endpoint_loss = F.smooth_l1_loss(
        _last_valid(p50, target_mask),
        _last_valid(target_close_path, target_mask),
    )
    turning_loss = _turning_point_loss(p50, target_close_path, target_mask)
    extrema_loss = _extrema_loss(p50, target_close_path, target_mask)

    if horizon >= 3:
        predicted_second = p50[:, 2:] - 2.0 * p50[:, 1:-1] + p50[:, :-2]
        target_second = (
            target_close_path[:, 2:]
            - 2.0 * target_close_path[:, 1:-1]
            + target_close_path[:, :-2]
        )
        roughness_mask = target_mask[:, 2:] & target_mask[:, 1:-1] & target_mask[:, :-2]
        roughness_values = F.smooth_l1_loss(
            predicted_second, target_second, reduction="none"
        )
        roughness_loss = _masked_mean(roughness_values, roughness_mask)
    else:
        roughness_loss = p50.sum() * 0.0

    endpoint_target = _last_valid(target_close_path, target_mask)
    scenario_target = torch.where(
        endpoint_target < -0.05,
        torch.zeros_like(target_movement[:, 0]),
        torch.where(
            endpoint_target > 0.05,
            torch.full_like(target_movement[:, 0], 2),
            torch.ones_like(target_movement[:, 0]),
        ),
    )
    scenario_paths = outputs["scenario_trajectories"]
    selected_paths = scenario_paths.gather(
        1,
        scenario_target.view(batch, 1, 1).expand(-1, 1, horizon),
    ).squeeze(1)
    scenario_path_loss = _masked_mean(
        F.smooth_l1_loss(selected_paths, target_close_path, reduction="none"),
        target_mask,
    )
    scenario_loss = scenario_path_loss + 0.25 * F.cross_entropy(
        outputs["scenario_logits"], scenario_target
    )

    teacher_loss = close_quantiles.sum() * 0.0
    if teacher_quantiles is not None:
        teacher_quantiles = teacher_quantiles.to(close_quantiles)
        if teacher_quantiles.shape != close_quantiles.shape:
            raise ValueError("teacher_quantiles must match close_quantiles")
        if teacher_mask is None:
            teacher_mask = torch.isfinite(teacher_quantiles).all(dim=-1)
        else:
            teacher_mask = teacher_mask.to(device=close_quantiles.device, dtype=torch.bool)
        if teacher_mask.shape != expected:
            raise ValueError(f"teacher_mask must have shape {expected}")
        teacher_values = F.smooth_l1_loss(
            close_quantiles,
            torch.nan_to_num(teacher_quantiles),
            reduction="none",
        )
        teacher_loss = _masked_mean(teacher_values, teacher_mask)

    components = {
        "quantile": quantile_loss,
        "path": path_loss,
        "movement": movement_loss,
        "spans": span_loss,
        "endpoint": endpoint_loss,
        "turning": turning_loss,
        "extrema": extrema_loss,
        "roughness": roughness_loss,
        "scenario": scenario_loss,
        "teacher": teacher_loss,
    }
    total = (
        weights.quantile * quantile_loss
        + weights.path * path_loss
        + weights.movement * movement_loss
        + weights.spans * span_loss
        + weights.endpoint * endpoint_loss
        + weights.turning * turning_loss
        + weights.extrema * extrema_loss
        + weights.roughness * roughness_loss
        + weights.scenario * scenario_loss
        + weights.teacher * teacher_loss
    )
    components["total"] = total
    return total, components


__all__ = [
    "DEFAULT_HORIZON",
    "MOVEMENT_LABELS",
    "QUANTILE_LEVELS",
    "SCENARIO_LABELS",
    "SCENE_FORECASTER_SCHEMA_VERSION",
    "SceneForecastLossWeights",
    "ScenePatchForecasterConfig",
    "ScenePatchForecasterV3",
    "scene_forecast_loss",
]
