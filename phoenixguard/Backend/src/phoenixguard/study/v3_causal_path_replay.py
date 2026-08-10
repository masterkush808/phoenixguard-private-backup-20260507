from __future__ import annotations

import copy
import hashlib
import io
import json
import math
import re
import shutil
import statistics
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont, ImageOps


REPLAY_SCHEMA_VERSION = "PHOENIXGUARD_V3_CAUSAL_SCREENSHOT_PATH_REPLAY_V1"
DIRECT_PATH_SEMANTICS = "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR"
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})
GIB = 1024**3


class ReplayContractError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)


class DiskReserveError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReplayConfig:
    mask_ratio: float = 0.60
    min_visible_candles: int = 24
    min_future_candles: int = 4
    max_horizon_candles: int = 12
    direction_epsilon_norm: float = 0.0005
    turn_tolerance_steps: int = 1
    min_free_gb: float = 45.0
    max_output_bytes: int = 512 * 1024 * 1024
    evidence_width: int = 1440
    evidence_panel_height: int = 500
    mask_rgb: tuple[int, int, int] = (7, 9, 11)

    def __post_init__(self) -> None:
        if not 0.35 <= float(self.mask_ratio) <= 0.80:
            raise ValueError("mask_ratio must be between 0.35 and 0.80")
        if self.min_visible_candles < 4:
            raise ValueError("min_visible_candles must be at least 4")
        if self.min_future_candles < 2:
            raise ValueError("min_future_candles must be at least 2")
        if self.max_horizon_candles < self.min_future_candles:
            raise ValueError("max_horizon_candles must cover min_future_candles")
        if self.min_free_gb < 0:
            raise ValueError("min_free_gb cannot be negative")


@dataclass(frozen=True)
class FrozenPrediction:
    schema_version: str
    cut_x: int
    source_image_size: tuple[int, int]
    chart_bbox: tuple[int, int, int, int]
    mask_rgb: tuple[int, int, int]
    mask_sha256: str
    freeze_sha256: str
    forecast_path: tuple[dict[str, Any], ...]
    contribution: dict[str, Any]
    evidence: dict[str, Any]
    visible_tracks: tuple[dict[str, Any], ...]
    anchor_track: dict[str, Any]
    prediction_canvas: Image.Image = field(repr=False, compare=False)


@dataclass(frozen=True)
class RevealResult:
    metrics: dict[str, float | int | str | bool | None]
    actual_cumulative_path: tuple[float, ...]
    actual_tracks: tuple[dict[str, Any], ...]
    actual_anchor_track: dict[str, Any]
    full_chart_bbox: tuple[int, int, int, int]


@dataclass
class ReplayCaseOutcome:
    source_path: Path
    category: str
    status: str
    reason: str
    evidence_path: Path | None = None
    metrics: dict[str, float | int | str | bool | None] = field(default_factory=dict)
    prediction_sha256: str = ""
    model_version: str = ""
    trajectory_mode: str = ""
    horizon_candles: int = 0
    visible_candles: int = 0
    actual_future_candles: int = 0
    market: str = ""
    timeframe: str = ""
    identity_confirmed: bool = False
    production_gate_passed: bool = False


AdapterFactory = Callable[[Path], Any]


def default_adapter_factory(market_study_root: Path) -> Any:
    from phoenixguard.mobile_api.window_tracker import (
        PhoenixGuardWindowTrackingAdapter,
    )

    market_study_root.mkdir(parents=True, exist_ok=True)
    return PhoenixGuardWindowTrackingAdapter(market_study_root=market_study_root)


def discover_screenshot_images(corpus_root: Path) -> list[Path]:
    root = Path(corpus_root)
    if not root.is_dir():
        raise FileNotFoundError(f"Screenshot corpus does not exist: {root}")
    return sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ),
        key=lambda value: str(value).lower(),
    )


def source_category(path: Path) -> str:
    upper_parts = {part.upper() for part in Path(path).parts}
    if "BUYS" in upper_parts:
        return "BUYS"
    if "SELLS" in upper_parts:
        return "SELLS"
    return "UNLABELED"


def ensure_disk_reserve(
    anchor: Path,
    *,
    min_free_gb: float,
    anticipated_bytes: int = 0,
) -> float:
    probe = Path(anchor)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free_bytes = int(shutil.disk_usage(probe).free)
    reserve_bytes = int(float(min_free_gb) * GIB)
    if free_bytes - int(anticipated_bytes) < reserve_bytes:
        raise DiskReserveError(
            "Disk reserve contract refused the write: "
            f"free={free_bytes / GIB:.2f} GB, "
            f"required_after_write={float(min_free_gb):.2f} GB, "
            f"anticipated={int(anticipated_bytes) / (1024**2):.2f} MB"
        )
    return free_bytes / GIB


def prepare_fresh_output_root(output_root: Path, workspace_root: Path) -> Path:
    workspace = Path(workspace_root).resolve()
    runtime_root = (workspace / ".codex_runtime").resolve()
    target = Path(output_root).resolve()
    if target == runtime_root or runtime_root not in target.parents:
        raise ReplayContractError(
            "UNSAFE_OUTPUT_ROOT",
            "Replay output must be a child of the workspace .codex_runtime directory.",
        )
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    return target


def calculate_cut_x(image_width: int, mask_ratio: float) -> int:
    width = int(image_width)
    if width < 40:
        raise ReplayContractError("IMAGE_TOO_NARROW", "Image is too narrow to mask.")
    return max(16, min(width - 16, int(round(width * float(mask_ratio)))))


def mask_future_pixels(
    image: Image.Image,
    *,
    cut_x: int,
    mask_rgb: tuple[int, int, int] = (7, 9, 11),
) -> Image.Image:
    masked = image.convert("RGB").copy()
    width, height = masked.size
    if not 0 < int(cut_x) < width:
        raise ReplayContractError("INVALID_MASK_CUT", "Mask cut is outside the image.")
    ImageDraw.Draw(masked).rectangle(
        (int(cut_x), 0, width - 1, height - 1),
        fill=tuple(int(channel) for channel in mask_rgb),
    )
    assert_mask_contract(masked, cut_x=cut_x, mask_rgb=mask_rgb)
    return masked


def assert_mask_contract(
    masked_image: Image.Image,
    *,
    cut_x: int,
    mask_rgb: tuple[int, int, int],
) -> None:
    rgb = masked_image.convert("RGB")
    future = rgb.crop((int(cut_x), 0, rgb.width, rgb.height))
    extrema = future.getextrema()
    expected = tuple((int(value), int(value)) for value in mask_rgb)
    if extrema != expected:
        raise ReplayContractError(
            "FUTURE_PIXEL_LEAK",
            "Pixels to the right of the anchor are not uniformly withheld.",
        )


def _image_sha256(image: Image.Image) -> str:
    rgb = image.convert("RGB")
    digest = hashlib.sha256()
    digest.update(f"{rgb.width}x{rgb.height}:RGB".encode("ascii"))
    digest.update(rgb.tobytes())
    return digest.hexdigest()


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return number if math.isfinite(number) else float(default)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _chart_bbox(study: Any, summary: Mapping[str, Any], image_size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = image_size
    candidates = [
        getattr(study, "chart_region", None),
        summary.get("chart_region"),
    ]
    raw_bbox: Sequence[Any] | None = None
    for candidate in candidates:
        if isinstance(candidate, Mapping):
            possible = candidate.get("pixel_bbox")
            if isinstance(possible, Sequence) and len(possible) == 4:
                raw_bbox = possible
                break
    if raw_bbox is None:
        return (0, 0, width, height)
    x0, y0, x1, y1 = (_as_int(value) for value in raw_bbox)
    x0 = max(0, min(width - 1, x0))
    y0 = max(0, min(height - 1, y0))
    x1 = max(x0 + 1, min(width, x1))
    y1 = max(y0 + 1, min(height, y1))
    return (x0, y0, x1, y1)


def _track_x(track: Mapping[str, Any]) -> float:
    return _as_float(track.get("center_x_px", track.get("center_x")), float("nan"))


def _track_close_y(track: Mapping[str, Any]) -> float:
    return _as_float(track.get("close_y_px"), float("nan"))


def _clean_tracks(
    summary: Mapping[str, Any],
    *,
    before_x: float | None = None,
) -> list[dict[str, Any]]:
    raw_tracks = summary.get("tracked_candles")
    if not isinstance(raw_tracks, list):
        return []
    candidates: list[dict[str, Any]] = []
    for raw in raw_tracks:
        if not isinstance(raw, Mapping):
            continue
        track = dict(raw)
        x_value = _track_x(track)
        close_y = _track_close_y(track)
        if not math.isfinite(x_value) or not math.isfinite(close_y):
            continue
        if before_x is not None and x_value >= float(before_x):
            continue
        candidates.append(track)
    candidates.sort(key=_track_x)
    deduped: list[dict[str, Any]] = []
    for track in candidates:
        if deduped and abs(_track_x(track) - _track_x(deduped[-1])) < 1.0:
            previous_confidence = _as_float(deduped[-1].get("parse_confidence"))
            current_confidence = _as_float(track.get("parse_confidence"))
            if current_confidence > previous_confidence:
                deduped[-1] = track
            continue
        deduped.append(track)
    return deduped


def _prediction_canvas(study: Any, masked_image: Image.Image) -> Image.Image:
    overlay = getattr(study, "overlay_image", None)
    if not isinstance(overlay, Image.Image) or overlay.size != masked_image.size:
        return masked_image.convert("RGB").copy()
    if overlay.mode == "RGBA":
        base = masked_image.convert("RGBA")
        return Image.alpha_composite(base, overlay).convert("RGB")
    return overlay.convert("RGB").copy()


def _trajectory_rows(contribution: Mapping[str, Any], max_horizon: int) -> tuple[dict[str, Any], ...]:
    raw_path = contribution.get("forecast_path")
    if not isinstance(raw_path, list) or not raw_path:
        raise ReplayContractError(
            "V3_FORECAST_PATH_MISSING",
            "The production V3 LSTM contribution did not publish a path.",
        )
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_path[: int(max_horizon)], start=1):
        if not isinstance(raw, Mapping):
            raise ReplayContractError(
                "V3_FORECAST_PATH_INVALID",
                f"Forecast step {index} is not an object.",
            )
        row = copy.deepcopy(dict(raw))
        cumulative = _as_float(row.get("expected_cumulative_delta_norm"), float("nan"))
        delta = _as_float(row.get("expected_delta_norm"), float("nan"))
        if not math.isfinite(cumulative) or not math.isfinite(delta):
            raise ReplayContractError(
                "V3_FORECAST_PATH_INVALID",
                f"Forecast step {index} lacks direct cumulative and movement geometry.",
            )
        row["step"] = _as_int(row.get("step"), index)
        rows.append(row)
    if len(rows) < 2:
        raise ReplayContractError(
            "V3_FORECAST_PATH_TOO_SHORT",
            "The production path contains fewer than two candle events.",
        )
    return tuple(rows)


def _stack_evidence(summary: Mapping[str, Any], contribution: Mapping[str, Any]) -> dict[str, Any]:
    trendlines = summary.get("trendlines_v3")
    zones = summary.get("support_resistance_zones")
    smart_money = summary.get("smart_money_context")
    behavior = summary.get("behavior")
    market_study = summary.get("market_study_v3")
    geometry_contract = summary.get("trendline_geometry_contract_v3")
    accepted_trendlines = [
        item
        for item in trendlines if isinstance(item, Mapping) and bool(item.get("geometry_contract_accepted"))
    ] if isinstance(trendlines, list) else []
    return {
        "market": str(summary.get("detected_market") or contribution.get("pair") or "UNKNOWN"),
        "timeframe": str(summary.get("detected_timeframe") or contribution.get("timeframe") or "UNKNOWN"),
        "market_identity_confirmed": bool(summary.get("market_identity_confirmed")),
        "timeframe_identity_confirmed": bool(summary.get("timeframe_identity_confirmed")),
        "control_direction": str(summary.get("control_direction") or "HOLD"),
        "global_direction": str(summary.get("global_direction") or "HOLD"),
        "local_direction": str(summary.get("local_direction") or "HOLD"),
        "impulse_direction": str(summary.get("impulse_direction") or "HOLD"),
        "major_trend_direction": str(summary.get("major_trend_direction") or "HOLD"),
        "behavior_state": str(behavior.get("current_state") or "UNKNOWN") if isinstance(behavior, Mapping) else "UNKNOWN",
        "behavior_next_state": str(behavior.get("next_most_likely_state") or "UNKNOWN") if isinstance(behavior, Mapping) else "UNKNOWN",
        "smart_money_side": str(smart_money.get("dominant_side") or "HOLD") if isinstance(smart_money, Mapping) else "HOLD",
        "smart_money_buy_score": _as_float(smart_money.get("buy_score")) if isinstance(smart_money, Mapping) else 0.0,
        "smart_money_sell_score": _as_float(smart_money.get("sell_score")) if isinstance(smart_money, Mapping) else 0.0,
        "zone_count": len(zones) if isinstance(zones, list) else 0,
        "accepted_trendline_count": len(accepted_trendlines),
        "trendline_contract_status": str(geometry_contract.get("status") or "UNKNOWN") if isinstance(geometry_contract, Mapping) else "UNKNOWN",
        "market_study_status": str(market_study.get("status") or "UNKNOWN") if isinstance(market_study, Mapping) else "UNKNOWN",
        "market_study_direction": str(market_study.get("directional_read") or "HOLD") if isinstance(market_study, Mapping) else "HOLD",
        "consolidation_score": _as_float(summary.get("consolidation_score")),
        "continuation_score": _as_float(summary.get("continuation_score")),
        "reversal_score": _as_float(summary.get("reversal_score")),
    }


def _prediction_digest_payload(
    *,
    cut_x: int,
    image_size: tuple[int, int],
    mask_sha256: str,
    forecast_path: Sequence[Mapping[str, Any]],
    contribution: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "cut_x": int(cut_x),
        "source_image_size": list(image_size),
        "mask_sha256": str(mask_sha256),
        "model_version": str(contribution.get("model_version") or ""),
        "artifact_path": str(contribution.get("artifact_path") or ""),
        "path_target_semantics": str(contribution.get("path_target_semantics") or ""),
        "trajectory_mode": str(contribution.get("trajectory_mode") or ""),
        "trajectory_decoder_status": str(contribution.get("trajectory_decoder_status") or ""),
        "forecast_path": list(forecast_path),
        "stack_evidence": dict(evidence),
    }


def _digest_payload(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def recompute_freeze_sha256(frozen: FrozenPrediction) -> str:
    return _digest_payload(
        _prediction_digest_payload(
            cut_x=frozen.cut_x,
            image_size=frozen.source_image_size,
            mask_sha256=frozen.mask_sha256,
            forecast_path=frozen.forecast_path,
            contribution=frozen.contribution,
            evidence=frozen.evidence,
        )
    )


def freeze_v3_prediction(
    masked_image: Image.Image,
    *,
    cut_x: int,
    market_study_root: Path,
    config: ReplayConfig,
    adapter_factory: AdapterFactory = default_adapter_factory,
) -> FrozenPrediction:
    assert_mask_contract(masked_image, cut_x=cut_x, mask_rgb=config.mask_rgb)
    mask_sha256 = _image_sha256(masked_image)
    adapter = adapter_factory(Path(market_study_root))
    study = adapter.study(masked_image.convert("RGB").copy())
    summary = getattr(study, "tracking_summary", None)
    if not isinstance(summary, Mapping):
        raise ReplayContractError(
            "V3_TRACKING_SUMMARY_MISSING",
            "The production V3 adapter did not publish a tracking summary.",
        )
    contribution = summary.get("lstm_contribution")
    if not isinstance(contribution, Mapping):
        raise ReplayContractError(
            "V3_LSTM_CONTRIBUTION_MISSING",
            "The production V3 adapter did not publish its LSTM contribution.",
        )
    contribution_copy = copy.deepcopy(dict(contribution))
    if not bool(contribution_copy.get("artifact_loaded")):
        raise ReplayContractError(
            "V3_MODEL_NOT_LOADED",
            str(contribution_copy.get("reason") or "The production trajectory artifact was not loaded."),
        )
    if not bool(contribution_copy.get("forecast_available")):
        raise ReplayContractError(
            "V3_FORECAST_UNAVAILABLE",
            str(contribution_copy.get("reason") or "The production trajectory forecast is unavailable."),
        )
    semantics = str(contribution_copy.get("path_target_semantics") or "")
    if semantics != DIRECT_PATH_SEMANTICS:
        raise ReplayContractError(
            "V3_PATH_SEMANTICS_MISMATCH",
            f"Expected {DIRECT_PATH_SEMANTICS}, received {semantics or 'EMPTY'}.",
        )
    if str(contribution_copy.get("trajectory_decoder_status") or "").upper() != "AVAILABLE":
        raise ReplayContractError(
            "V3_TRAJECTORY_DECODER_UNAVAILABLE",
            str(contribution_copy.get("reason") or "The V3 trajectory decoder is unavailable."),
        )
    forecast_path = _trajectory_rows(contribution_copy, config.max_horizon_candles)
    visible_tracks = _clean_tracks(summary, before_x=float(cut_x))
    if len(visible_tracks) < config.min_visible_candles:
        raise ReplayContractError(
            "VISIBLE_HISTORY_TOO_SHORT",
            f"V3 extracted {len(visible_tracks)} visible candles; {config.min_visible_candles} are required.",
        )
    evidence = _stack_evidence(summary, contribution_copy)
    payload = _prediction_digest_payload(
        cut_x=cut_x,
        image_size=masked_image.size,
        mask_sha256=mask_sha256,
        forecast_path=forecast_path,
        contribution=contribution_copy,
        evidence=evidence,
    )
    freeze_sha256 = _digest_payload(payload)
    chart_bbox = _chart_bbox(study, summary, masked_image.size)
    frozen = FrozenPrediction(
        schema_version=REPLAY_SCHEMA_VERSION,
        cut_x=int(cut_x),
        source_image_size=tuple(masked_image.size),
        chart_bbox=chart_bbox,
        mask_rgb=tuple(config.mask_rgb),
        mask_sha256=mask_sha256,
        freeze_sha256=freeze_sha256,
        forecast_path=forecast_path,
        contribution=contribution_copy,
        evidence=evidence,
        visible_tracks=tuple(copy.deepcopy(visible_tracks)),
        anchor_track=copy.deepcopy(visible_tracks[-1]),
        prediction_canvas=_prediction_canvas(study, masked_image),
    )
    if recompute_freeze_sha256(frozen) != freeze_sha256:
        raise ReplayContractError(
            "PREDICTION_FREEZE_FAILED",
            "The prediction changed while its freeze contract was being created.",
        )
    return frozen


def _median_spacing(tracks: Sequence[Mapping[str, Any]]) -> float:
    values = [_track_x(item) for item in tracks]
    differences = [
        right - left
        for left, right in zip(values, values[1:])
        if math.isfinite(left) and math.isfinite(right) and right - left >= 1.0
    ]
    return statistics.median(differences) if differences else 8.0


def _direction(value: float, epsilon: float) -> str:
    if value > epsilon:
        return "BUY"
    if value < -epsilon:
        return "SELL"
    return "HOLD"


def _movement_signs(path: Sequence[float], epsilon: float) -> list[str]:
    previous = 0.0
    signs: list[str] = []
    for value in path:
        signs.append(_direction(float(value) - previous, epsilon))
        previous = float(value)
    return signs


def _turn_steps(path: Sequence[float], epsilon: float) -> list[int]:
    turns: list[int] = []
    previous_non_hold = ""
    for step, direction in enumerate(_movement_signs(path, epsilon), start=1):
        if direction == "HOLD":
            continue
        if previous_non_hold and direction != previous_non_hold:
            turns.append(step)
        previous_non_hold = direction
    return turns


def _turn_f1(predicted: Sequence[int], actual: Sequence[int], tolerance: int) -> float:
    if not predicted and not actual:
        return 1.0
    if not predicted or not actual:
        return 0.0
    remaining = list(actual)
    matched = 0
    for predicted_step in predicted:
        candidates = [
            (abs(predicted_step - actual_step), index)
            for index, actual_step in enumerate(remaining)
            if abs(predicted_step - actual_step) <= tolerance
        ]
        if not candidates:
            continue
        _, match_index = min(candidates)
        remaining.pop(match_index)
        matched += 1
    precision = matched / len(predicted)
    recall = matched / len(actual)
    return 0.0 if precision + recall == 0.0 else 2.0 * precision * recall / (precision + recall)


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_energy = sum((value - left_mean) ** 2 for value in left)
    right_energy = sum((value - right_mean) ** 2 for value in right)
    denominator = math.sqrt(left_energy * right_energy)
    return 0.0 if denominator <= 1e-12 else max(-1.0, min(1.0, numerator / denominator))


def _score_paths(
    predicted: Sequence[float],
    actual: Sequence[float],
    *,
    epsilon: float,
    turn_tolerance: int,
) -> dict[str, float | int | str | bool | None]:
    count = min(len(predicted), len(actual))
    if count < 2:
        raise ReplayContractError("SCORING_HORIZON_TOO_SHORT", "Fewer than two aligned path steps are available.")
    predicted_values = [float(value) for value in predicted[:count]]
    actual_values = [float(value) for value in actual[:count]]
    predicted_terminal = _direction(predicted_values[-1], epsilon)
    actual_terminal = _direction(actual_values[-1], epsilon)
    terminal_hit = predicted_terminal == actual_terminal
    predicted_anchor_directions = [_direction(value, epsilon) for value in predicted_values]
    actual_anchor_directions = [_direction(value, epsilon) for value in actual_values]
    anchor_hits = [left == right for left, right in zip(predicted_anchor_directions, actual_anchor_directions)]
    predicted_movements = _movement_signs(predicted_values, epsilon)
    actual_movements = _movement_signs(actual_values, epsilon)
    movement_hits = [left == right for left, right in zip(predicted_movements, actual_movements)]
    predicted_majority = _direction(
        float(sum(direction == "BUY" for direction in predicted_anchor_directions))
        - float(sum(direction == "SELL" for direction in predicted_anchor_directions)),
        0.0,
    )
    actual_majority = _direction(
        float(sum(direction == "BUY" for direction in actual_anchor_directions))
        - float(sum(direction == "SELL" for direction in actual_anchor_directions)),
        0.0,
    )
    predicted_turns = _turn_steps(predicted_values, epsilon)
    actual_turns = _turn_steps(actual_values, epsilon)
    squared_error = statistics.fmean(
        (left - right) ** 2 for left, right in zip(predicted_values, actual_values)
    )
    rmse = math.sqrt(squared_error)
    mae = statistics.fmean(abs(left - right) for left, right in zip(predicted_values, actual_values))
    amplitude_scale = max(
        max(abs(value) for value in predicted_values),
        max(abs(value) for value in actual_values),
        epsilon * 4.0,
    )
    path_similarity = max(0.0, min(1.0, 1.0 - rmse / amplitude_scale))
    return {
        "aligned_horizon_candles": count,
        "predicted_terminal_direction": predicted_terminal,
        "actual_terminal_direction": actual_terminal,
        "terminal_direction_hit": terminal_hit,
        "predicted_majority_direction": predicted_majority,
        "actual_majority_direction": actual_majority,
        "majority_direction_hit": predicted_majority == actual_majority,
        "anchor_direction_accuracy": statistics.fmean(anchor_hits),
        "candle_to_candle_fluctuation_accuracy": statistics.fmean(movement_hits),
        "predicted_turn_count": len(predicted_turns),
        "actual_turn_count": len(actual_turns),
        "predicted_first_turn_step": predicted_turns[0] if predicted_turns else None,
        "actual_first_turn_step": actual_turns[0] if actual_turns else None,
        "turning_point_f1": _turn_f1(predicted_turns, actual_turns, turn_tolerance),
        "path_correlation": _pearson(predicted_values, actual_values),
        "path_similarity": path_similarity,
        "path_mae_norm": mae,
        "path_rmse_norm": rmse,
        "terminal_amplitude_error_norm": abs(predicted_values[-1] - actual_values[-1]),
        "predicted_terminal_delta_norm": predicted_values[-1],
        "actual_terminal_delta_norm": actual_values[-1],
    }


def reveal_and_score_v3_prediction(
    full_image: Image.Image,
    frozen: FrozenPrediction,
    *,
    market_study_root: Path,
    config: ReplayConfig,
    adapter_factory: AdapterFactory = default_adapter_factory,
) -> RevealResult:
    if tuple(full_image.size) != frozen.source_image_size:
        raise ReplayContractError(
            "REVEAL_SIZE_MISMATCH",
            "The revealed screenshot dimensions differ from the frozen prediction input.",
        )
    before_reveal_hash = recompute_freeze_sha256(frozen)
    if before_reveal_hash != frozen.freeze_sha256:
        raise ReplayContractError(
            "PREDICTION_MUTATED_BEFORE_REVEAL",
            "The frozen production prediction changed before the future was revealed.",
        )
    adapter = adapter_factory(Path(market_study_root))
    study = adapter.study(full_image.convert("RGB").copy())
    summary = getattr(study, "tracking_summary", None)
    if not isinstance(summary, Mapping):
        raise ReplayContractError(
            "REVEAL_TRACKING_SUMMARY_MISSING",
            "V3 could not extract candle geometry from the revealed screenshot.",
        )
    full_tracks = _clean_tracks(summary)
    if not full_tracks:
        raise ReplayContractError(
            "REVEAL_CANDLES_MISSING",
            "V3 found no candle tracks after the future was revealed.",
        )
    spacing = max(1.0, _median_spacing(frozen.visible_tracks))
    anchor_x = _track_x(frozen.anchor_track)
    nearest_index = min(
        range(len(full_tracks)),
        key=lambda index: abs(_track_x(full_tracks[index]) - anchor_x),
    )
    actual_anchor = full_tracks[nearest_index]
    anchor_distance = abs(_track_x(actual_anchor) - anchor_x)
    if anchor_distance > max(25.0, spacing * 2.5):
        raise ReplayContractError(
            "ANCHOR_GEOMETRY_MISMATCH",
            f"Masked and revealed anchor tracks differ by {anchor_distance:.1f}px.",
        )
    future_tracks = full_tracks[nearest_index + 1 :]
    horizon = min(
        len(future_tracks),
        len(frozen.forecast_path),
        config.max_horizon_candles,
    )
    if horizon < config.min_future_candles:
        raise ReplayContractError(
            "REVEALED_FUTURE_TOO_SHORT",
            f"Only {horizon} future candles were available; {config.min_future_candles} are required.",
        )
    aligned_tracks = tuple(copy.deepcopy(future_tracks[:horizon]))
    full_bbox = _chart_bbox(study, summary, full_image.size)
    chart_height = max(1.0, float(full_bbox[3] - full_bbox[1]))
    anchor_close_y = _track_close_y(actual_anchor)
    actual_path = tuple(
        (anchor_close_y - _track_close_y(track)) / chart_height
        for track in aligned_tracks
    )
    predicted_path = tuple(
        _as_float(row.get("expected_cumulative_delta_norm"))
        for row in frozen.forecast_path[:horizon]
    )
    metrics = _score_paths(
        predicted_path,
        actual_path,
        epsilon=config.direction_epsilon_norm,
        turn_tolerance=config.turn_tolerance_steps,
    )
    metrics["prediction_frozen_before_reveal"] = True
    metrics["future_pixels_passed_to_predictor"] = False
    metrics["actual_geometry_uses_color_labels"] = False
    if recompute_freeze_sha256(frozen) != frozen.freeze_sha256:
        raise ReplayContractError(
            "PREDICTION_MUTATED_AFTER_REVEAL",
            "The frozen production prediction changed after the future was revealed.",
        )
    return RevealResult(
        metrics=metrics,
        actual_cumulative_path=actual_path,
        actual_tracks=aligned_tracks,
        actual_anchor_track=copy.deepcopy(actual_anchor),
        full_chart_bbox=full_bbox,
    )


def _clamp_y(value: float, bbox: tuple[int, int, int, int]) -> float:
    return max(float(bbox[1]), min(float(bbox[3] - 1), float(value)))


def _draw_frozen_path(
    image: Image.Image,
    frozen: FrozenPrediction,
    *,
    x_positions: Sequence[float] | None = None,
    anchor_y: float | None = None,
    chart_bbox: tuple[int, int, int, int] | None = None,
) -> Image.Image:
    canvas = image.convert("RGB").copy()
    draw = ImageDraw.Draw(canvas, "RGBA")
    bbox = chart_bbox or frozen.chart_bbox
    chart_height = max(1.0, float(bbox[3] - bbox[1]))
    anchor_x = _track_x(frozen.anchor_track)
    anchor_close = _track_close_y(frozen.anchor_track) if anchor_y is None else float(anchor_y)
    spacing = max(3.0, min(28.0, _median_spacing(frozen.visible_tracks)))
    points: list[tuple[float, float]] = [(anchor_x, anchor_close)]
    interval_upper: list[tuple[float, float]] = []
    interval_lower: list[tuple[float, float]] = []
    for index, row in enumerate(frozen.forecast_path):
        x_value = (
            float(x_positions[index])
            if x_positions is not None and index < len(x_positions)
            else anchor_x + spacing * float(index + 1)
        )
        cumulative = _as_float(row.get("expected_cumulative_delta_norm"))
        delta = _as_float(row.get("expected_delta_norm"))
        close_y = _clamp_y(anchor_close - cumulative * chart_height, bbox)
        open_y = _clamp_y(close_y + delta * chart_height, bbox)
        expected_open = _as_float(row.get("expected_open_norm"))
        expected_close = _as_float(row.get("expected_close_norm"))
        expected_high = _as_float(row.get("expected_high_norm"), max(expected_open, expected_close))
        expected_low = _as_float(row.get("expected_low_norm"), min(expected_open, expected_close))
        upper_wick = max(0.0, expected_high - max(expected_open, expected_close)) * chart_height
        lower_wick = max(0.0, min(expected_open, expected_close) - expected_low) * chart_height
        high_y = _clamp_y(min(open_y, close_y) - upper_wick, bbox)
        low_y = _clamp_y(max(open_y, close_y) + lower_wick, bbox)
        body_half_width = max(2.0, min(6.0, spacing * 0.28))
        draw.line((x_value, high_y, x_value, low_y), fill=(0, 210, 255, 235), width=2)
        top = min(open_y, close_y)
        bottom = max(open_y, close_y)
        if bottom - top < 2.0:
            bottom = top + 2.0
        body_fill = (0, 210, 255, 185) if delta >= 0.0 else (0, 126, 255, 185)
        draw.rectangle(
            (x_value - body_half_width, top, x_value + body_half_width, bottom),
            fill=body_fill,
            outline=(180, 245, 255, 245),
            width=1,
        )
        points.append((x_value, close_y))
        scale = abs(_as_float(row.get("cumulative_scale_norm"))) * chart_height
        interval_upper.append((x_value, _clamp_y(close_y - scale, bbox)))
        interval_lower.append((x_value, _clamp_y(close_y + scale, bbox)))
        draw.ellipse((x_value - 2, close_y - 2, x_value + 2, close_y + 2), fill=(220, 252, 255, 255))
    if interval_upper and interval_lower:
        draw.polygon(interval_upper + list(reversed(interval_lower)), fill=(0, 174, 239, 30))
    if len(points) >= 2:
        draw.line(points, fill=(0, 225, 255, 255), width=3, joint="curve")
    draw.line((frozen.cut_x, bbox[1], frozen.cut_x, bbox[3]), fill=(255, 206, 76, 230), width=2)
    return canvas


def _draw_actual_path(
    image: Image.Image,
    reveal: RevealResult,
) -> Image.Image:
    canvas = image.convert("RGB").copy()
    draw = ImageDraw.Draw(canvas, "RGBA")
    anchor_x = _track_x(reveal.actual_anchor_track)
    anchor_y = _track_close_y(reveal.actual_anchor_track)
    points = [(anchor_x, anchor_y)] + [
        (_track_x(track), _track_close_y(track)) for track in reveal.actual_tracks
    ]
    if len(points) >= 2:
        draw.line(points, fill=(255, 142, 42, 255), width=4, joint="curve")
    for x_value, y_value in points[1:]:
        draw.ellipse((x_value - 3, y_value - 3, x_value + 3, y_value + 3), fill=(255, 188, 70, 255))
    return canvas


def _comparison_canvas(
    full_image: Image.Image,
    frozen: FrozenPrediction,
    reveal: RevealResult,
) -> Image.Image:
    x_positions = [_track_x(track) for track in reveal.actual_tracks]
    compared = _draw_frozen_path(
        full_image,
        frozen,
        x_positions=x_positions,
        anchor_y=_track_close_y(reveal.actual_anchor_track),
        chart_bbox=reveal.full_chart_bbox,
    )
    compared = _draw_actual_path(compared, reveal)
    draw = ImageDraw.Draw(compared, "RGBA")
    x0, y0, _, _ = reveal.full_chart_bbox
    draw.rectangle((x0 + 8, y0 + 8, x0 + 330, y0 + 54), fill=(5, 8, 12, 220))
    draw.text((x0 + 18, y0 + 16), "CYAN: V3 FROZEN PREDICTION", fill=(95, 235, 255, 255))
    draw.text((x0 + 18, y0 + 34), "ORANGE: REVEALED CLOSE PATH", fill=(255, 182, 74, 255))
    return compared


def _fit_panel(image: Image.Image, width: int, height: int) -> Image.Image:
    contained = ImageOps.contain(image.convert("RGB"), (width, height), Image.Resampling.LANCZOS)
    panel = Image.new("RGB", (width, height), (5, 7, 10))
    x_value = (width - contained.width) // 2
    y_value = (height - contained.height) // 2
    panel.paste(contained, (x_value, y_value))
    return panel


def _panel_with_label(image: Image.Image, label: str, width: int, height: int) -> Image.Image:
    label_height = 34
    panel = Image.new("RGB", (width, height + label_height), (10, 13, 17))
    panel.paste(_fit_panel(image, width, height), (0, label_height))
    draw = ImageDraw.Draw(panel)
    draw.text((12, 11), label, fill=(235, 226, 194), font=ImageFont.load_default())
    return panel


def render_evidence_sheet(
    *,
    source_label: str,
    category: str,
    masked_image: Image.Image,
    full_image: Image.Image,
    frozen: FrozenPrediction | None,
    reveal: RevealResult | None,
    status: str,
    reason: str,
    config: ReplayConfig,
) -> Image.Image:
    width = max(800, int(config.evidence_width))
    panel_width = width // 2
    panel_height = int(config.evidence_panel_height)
    masked_panel = masked_image.convert("RGB")
    if frozen is None:
        prediction_panel = masked_panel.copy()
    else:
        prediction_panel = _draw_frozen_path(frozen.prediction_canvas, frozen)
    revealed_panel = full_image.convert("RGB").copy()
    if frozen is not None:
        revealed_draw = ImageDraw.Draw(revealed_panel, "RGBA")
        revealed_draw.line(
            (frozen.cut_x, 0, frozen.cut_x, revealed_panel.height),
            fill=(255, 206, 76, 235),
            width=3,
        )
    if frozen is not None and reveal is not None:
        comparison_panel = _comparison_canvas(full_image, frozen, reveal)
    else:
        comparison_panel = revealed_panel.copy()
        draw = ImageDraw.Draw(comparison_panel, "RGBA")
        draw.rectangle((16, 16, comparison_panel.width - 16, 84), fill=(20, 4, 5, 220))
        draw.text((28, 28), f"NOT SCORED: {reason[:180]}", fill=(255, 138, 126, 255))
    panels = [
        _panel_with_label(masked_panel, "1. FUTURE WITHHELD - INPUT TO V3", panel_width, panel_height),
        _panel_with_label(prediction_panel, "2. V3 PREDICTION FROZEN BEFORE REVEAL", panel_width, panel_height),
        _panel_with_label(revealed_panel, "3. FUTURE REVEALED AFTER FREEZE", panel_width, panel_height),
        _panel_with_label(comparison_panel, "4. FROZEN PATH VS ACTUAL GEOMETRY", panel_width, panel_height),
    ]
    title_height = 72
    sheet = Image.new("RGB", (width, title_height + 2 * panels[0].height), (6, 8, 11))
    sheet.paste(panels[0], (0, title_height))
    sheet.paste(panels[1], (panel_width, title_height))
    sheet.paste(panels[2], (0, title_height + panels[0].height))
    sheet.paste(panels[3], (panel_width, title_height + panels[0].height))
    draw = ImageDraw.Draw(sheet)
    safe_label = source_label.replace("\n", " ")[:145]
    draw.text((16, 12), f"{safe_label} | {category} | {status}", fill=(246, 220, 143))
    if frozen is not None:
        model = str(frozen.contribution.get("model_version") or "UNKNOWN")
        mode = str(frozen.contribution.get("trajectory_mode") or "UNKNOWN")
        gate = bool(frozen.contribution.get("artifact_production_gate_passed"))
        summary = (
            f"freeze={frozen.freeze_sha256[:16]}  model={model}  mode={mode}  "
            f"gate={'PASS' if gate else 'NO_EDGE'}  horizon={len(frozen.forecast_path)}"
        )
        if reveal is not None:
            metrics = reveal.metrics
            summary += (
                f"  terminal={'HIT' if metrics.get('terminal_direction_hit') else 'MISS'}"
                f"  anchor-steps={100.0 * _as_float(metrics.get('anchor_direction_accuracy')):.1f}%"
                f"  fluctuations={100.0 * _as_float(metrics.get('candle_to_candle_fluctuation_accuracy')):.1f}%"
            )
        draw.text((16, 34), summary[:210], fill=(185, 213, 224))
    else:
        draw.text((16, 34), reason[:210], fill=(255, 142, 132))
    draw.text((16, 54), "Color labels are ignored. Scoring uses sequential close geometry only.", fill=(142, 157, 167))
    return sheet


def encode_png(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True, compress_level=8)
    return output.getvalue()


def save_png_with_contract(
    image: Image.Image,
    destination: Path,
    *,
    output_root: Path,
    existing_output_bytes: int,
    config: ReplayConfig,
) -> int:
    encoded = encode_png(image)
    next_total = int(existing_output_bytes) + len(encoded)
    if next_total > int(config.max_output_bytes):
        raise DiskReserveError(
            f"Evidence output cap would be exceeded: {next_total / (1024**2):.2f} MB "
            f"> {config.max_output_bytes / (1024**2):.2f} MB"
        )
    ensure_disk_reserve(
        output_root,
        min_free_gb=config.min_free_gb,
        anticipated_bytes=len(encoded),
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_bytes(encoded)
    temporary.replace(destination)
    return next_total


def evidence_filename(index: int, source_path: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source_path.stem).strip("._-") or "chart"
    stem = stem[:72]
    identity = hashlib.sha256(str(source_path).encode("utf-8")).hexdigest()[:10]
    return f"case_{int(index):04d}_{stem}_{identity}.png"


def run_replay_case(
    source_path: Path,
    *,
    source_label: str,
    case_index: int,
    state_parent: Path,
    evidence_root: Path,
    existing_output_bytes: int,
    config: ReplayConfig,
    adapter_factory: AdapterFactory = default_adapter_factory,
) -> tuple[ReplayCaseOutcome, int]:
    source = Path(source_path)
    category = source_category(source)
    ensure_disk_reserve(state_parent, min_free_gb=config.min_free_gb)
    with Image.open(source) as raw:
        visible_source = ImageOps.exif_transpose(raw).convert("RGB")
        cut_x = calculate_cut_x(visible_source.width, config.mask_ratio)
        masked = mask_future_pixels(
            visible_source,
            cut_x=cut_x,
            mask_rgb=config.mask_rgb,
        )
    del visible_source
    frozen: FrozenPrediction | None = None
    reveal: RevealResult | None = None
    status = "REJECTED"
    reason = ""
    with tempfile.TemporaryDirectory(prefix="case_", dir=state_parent) as temporary_root:
        temporary_path = Path(temporary_root)
        try:
            frozen = freeze_v3_prediction(
                masked,
                cut_x=cut_x,
                market_study_root=temporary_path / "prediction",
                config=config,
                adapter_factory=adapter_factory,
            )
            with Image.open(source) as raw:
                full_image = ImageOps.exif_transpose(raw).convert("RGB")
            reveal = reveal_and_score_v3_prediction(
                full_image,
                frozen,
                market_study_root=temporary_path / "reveal",
                config=config,
                adapter_factory=adapter_factory,
            )
            gate_passed = bool(frozen.contribution.get("artifact_production_gate_passed"))
            status = "SCORED_PRODUCTION" if gate_passed else "SCORED_DIAGNOSTIC_NO_EDGE"
            reason = str(frozen.contribution.get("reason") or "Production V3 path scored.")
        except ReplayContractError as exc:
            reason = f"{exc.code}: {exc}"
            with Image.open(source) as raw:
                full_image = ImageOps.exif_transpose(raw).convert("RGB")
        sheet = render_evidence_sheet(
            source_label=source_label,
            category=category,
            masked_image=masked,
            full_image=full_image,
            frozen=frozen,
            reveal=reveal,
            status=status,
            reason=reason,
            config=config,
        )
        destination = evidence_root / evidence_filename(case_index, source)
        output_bytes = save_png_with_contract(
            sheet,
            destination,
            output_root=evidence_root,
            existing_output_bytes=existing_output_bytes,
            config=config,
        )
    evidence = frozen.evidence if frozen is not None else {}
    contribution = frozen.contribution if frozen is not None else {}
    outcome = ReplayCaseOutcome(
        source_path=source,
        category=category,
        status=status,
        reason=reason,
        evidence_path=destination,
        metrics=dict(reveal.metrics) if reveal is not None else {},
        prediction_sha256=frozen.freeze_sha256 if frozen is not None else "",
        model_version=str(contribution.get("model_version") or ""),
        trajectory_mode=str(contribution.get("trajectory_mode") or ""),
        horizon_candles=len(frozen.forecast_path) if frozen is not None else 0,
        visible_candles=len(frozen.visible_tracks) if frozen is not None else 0,
        actual_future_candles=len(reveal.actual_tracks) if reveal is not None else 0,
        market=str(evidence.get("market") or "UNKNOWN"),
        timeframe=str(evidence.get("timeframe") or "UNKNOWN"),
        identity_confirmed=bool(
            evidence.get("market_identity_confirmed")
            and evidence.get("timeframe_identity_confirmed")
        ),
        production_gate_passed=bool(contribution.get("artifact_production_gate_passed")),
    )
    return outcome, output_bytes


def _mean_metric(outcomes: Sequence[ReplayCaseOutcome], key: str) -> float:
    values = [
        _as_float(outcome.metrics.get(key), float("nan"))
        for outcome in outcomes
        if key in outcome.metrics
    ]
    finite = [value for value in values if math.isfinite(value)]
    return statistics.fmean(finite) if finite else 0.0


def summarize_replay(outcomes: Sequence[ReplayCaseOutcome]) -> dict[str, Any]:
    scored = [outcome for outcome in outcomes if outcome.metrics]
    rejected = [outcome for outcome in outcomes if not outcome.metrics]
    terminal_hits = sum(bool(outcome.metrics.get("terminal_direction_hit")) for outcome in scored)
    majority_hits = sum(bool(outcome.metrics.get("majority_direction_hit")) for outcome in scored)
    categories: dict[str, dict[str, int | float]] = {}
    for category in sorted({outcome.category for outcome in outcomes}):
        category_scored = [outcome for outcome in scored if outcome.category == category]
        category_hits = sum(bool(outcome.metrics.get("terminal_direction_hit")) for outcome in category_scored)
        categories[category] = {
            "attempted": sum(outcome.category == category for outcome in outcomes),
            "scored": len(category_scored),
            "terminal_direction_accuracy": category_hits / len(category_scored) if category_scored else 0.0,
        }
    rejection_reasons: dict[str, int] = {}
    for outcome in rejected:
        code = outcome.reason.split(":", 1)[0] if outcome.reason else "UNKNOWN"
        rejection_reasons[code] = rejection_reasons.get(code, 0) + 1
    terminal_accuracy = terminal_hits / len(scored) if scored else 0.0
    return {
        "schema_version": REPLAY_SCHEMA_VERSION,
        "attempted": len(outcomes),
        "scored": len(scored),
        "rejected": len(rejected),
        "terminal_direction_accuracy": terminal_accuracy,
        "majority_direction_accuracy": majority_hits / len(scored) if scored else 0.0,
        "mean_anchor_direction_accuracy": _mean_metric(scored, "anchor_direction_accuracy"),
        "mean_candle_to_candle_fluctuation_accuracy": _mean_metric(scored, "candle_to_candle_fluctuation_accuracy"),
        "mean_turning_point_f1": _mean_metric(scored, "turning_point_f1"),
        "mean_path_correlation": _mean_metric(scored, "path_correlation"),
        "mean_path_similarity": _mean_metric(scored, "path_similarity"),
        "mean_path_mae_norm": _mean_metric(scored, "path_mae_norm"),
        "identity_confirmed": sum(outcome.identity_confirmed for outcome in scored),
        "production_gate_passed": sum(outcome.production_gate_passed for outcome in scored),
        "target_65_percent_met": bool(scored) and terminal_accuracy >= 0.65,
        "categories": categories,
        "rejection_reasons": dict(sorted(rejection_reasons.items())),
    }


def format_replay_summary(summary: Mapping[str, Any]) -> str:
    scored = _as_int(summary.get("scored"))
    lines = [
        "PHOENIXGUARD V3 CAUSAL SCREENSHOT PATH REPLAY",
        f"Attempted: {_as_int(summary.get('attempted'))}",
        f"Scored: {scored}",
        f"Rejected: {_as_int(summary.get('rejected'))}",
        "Prediction order: MASK -> V3 STUDY -> HASH FREEZE -> REVEAL -> SCORE",
        "Future pixels passed to predictor: NO",
        "Folder labels or candle colors used for scoring: NO",
        f"Terminal direction accuracy: {100.0 * _as_float(summary.get('terminal_direction_accuracy')):.2f}% ({scored} scored cases)",
        f"Majority-of-horizon direction accuracy: {100.0 * _as_float(summary.get('majority_direction_accuracy')):.2f}%",
        f"Anchor-relative step accuracy: {100.0 * _as_float(summary.get('mean_anchor_direction_accuracy')):.2f}%",
        f"Candle-to-candle fluctuation accuracy: {100.0 * _as_float(summary.get('mean_candle_to_candle_fluctuation_accuracy')):.2f}%",
        f"Turning-point F1: {100.0 * _as_float(summary.get('mean_turning_point_f1')):.2f}%",
        f"Mean path correlation: {_as_float(summary.get('mean_path_correlation')):.4f}",
        f"Mean path similarity: {100.0 * _as_float(summary.get('mean_path_similarity')):.2f}%",
        f"65% terminal-direction target met: {'YES' if summary.get('target_65_percent_met') else 'NO'}",
        f"Identity confirmed: {_as_int(summary.get('identity_confirmed'))}/{scored}",
        f"Production gate passed: {_as_int(summary.get('production_gate_passed'))}/{scored}",
    ]
    categories = summary.get("categories")
    if isinstance(categories, Mapping):
        for category, values in categories.items():
            if not isinstance(values, Mapping):
                continue
            lines.append(
                f"{category}: attempted={_as_int(values.get('attempted'))}, "
                f"scored={_as_int(values.get('scored'))}, "
                f"terminal={100.0 * _as_float(values.get('terminal_direction_accuracy')):.2f}%"
            )
    rejection_reasons = summary.get("rejection_reasons")
    if isinstance(rejection_reasons, Mapping) and rejection_reasons:
        lines.append(
            "Rejections: "
            + ", ".join(f"{key}={_as_int(value)}" for key, value in rejection_reasons.items())
        )
    return "\n".join(lines)


def output_size_bytes(path: Path) -> int:
    root = Path(path)
    if not root.exists():
        return 0
    return sum(item.stat().st_size for item in root.rglob("*") if item.is_file())


def close_images(images: Iterable[Image.Image | None]) -> None:
    for image in images:
        if isinstance(image, Image.Image):
            image.close()
