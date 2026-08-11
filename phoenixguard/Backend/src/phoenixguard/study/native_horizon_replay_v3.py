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
from typing import Any, Callable, Mapping, Sequence

from PIL import Image, ImageDraw, ImageFont, ImageOps

from phoenixguard.decision.scene_forecast_contributor_v3 import (
    FORECAST_HORIZON_STEPS_V3,
    build_scene_forecast_contribution_v3,
)


SCHEMA_VERSION = "PHOENIXGUARD_NATIVE_HORIZON_REPLAY_V3"
PATH_SEMANTICS = "DIRECT_72_EVENT_COHERENT_TRAJECTORY"
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".bmp"})
GIB = 1024**3
SIDES = {"BUY", "SELL", "HOLD"}
TIMEFRAMES = (
    "MN6", "MN3", "MN1", "H12", "H8", "H6", "H4", "H3", "H2", "H1",
    "M45", "M30", "M20", "M15", "M10", "M5", "M4", "M3", "M2", "M1",
    "D3", "D2", "D1", "W2", "W1",
)
CURRENCY_CODES = frozenset(
    {
        "AUD", "BTC", "CAD", "CHF", "ETH", "EUR", "GBP", "JPY", "NZD",
        "USD", "XAG", "XAU", "ZAR",
    }
)


class NativeReplayError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = str(code)


class DiskReserveError(RuntimeError):
    pass


@dataclass(frozen=True)
class NativeReplayConfig:
    max_horizon: int = 72
    min_visible_candles: int = 24
    min_future_candles: int = 4
    direction_epsilon: float = 0.0005
    mask_rgb: tuple[int, int, int] = (7, 9, 11)
    min_free_gb: float = 45.0
    max_output_bytes: int = 512 * 1024 * 1024
    evidence_width: int = 1920

    def __post_init__(self) -> None:
        if self.max_horizon != FORECAST_HORIZON_STEPS_V3 or self.max_horizon != 72:
            raise ValueError("PhoenixGuard V3 native horizon must be exactly 72")
        if self.min_visible_candles < 16:
            raise ValueError("At least 16 visible candles are required")
        if self.min_future_candles < 2:
            raise ValueError("At least two revealed candles are required")


@dataclass(frozen=True)
class ChartIdentity:
    pair: str
    timeframe: str
    confidence: float
    source: str
    ocr_text: tuple[str, ...] = ()


@dataclass(frozen=True)
class GeometryPlan:
    image_size: tuple[int, int]
    chart_bbox: tuple[int, int, int, int]
    cut_x: int
    spacing_px: float
    available_future_candles: int
    visible_tracks: tuple[dict[str, Any], ...]
    anchor_track: dict[str, Any]
    planner_contract: str = "ISOLATED_X_BOUNDARY_AND_VISIBLE_PREFIX_ONLY"


@dataclass(frozen=True)
class FrozenNativeForecast:
    identity: ChartIdentity
    plan: GeometryPlan
    mask_sha256: str
    freeze_sha256: str
    forecast_candles: tuple[dict[str, Any], ...]
    forecast_path: tuple[dict[str, Any], ...]
    forecast_scenarios: tuple[dict[str, Any], ...]
    contribution: dict[str, Any]
    evidence: dict[str, Any]
    prediction_canvas: Image.Image = field(repr=False, compare=False)


@dataclass(frozen=True)
class RevealResult:
    metrics: dict[str, Any]
    actual_tracks: tuple[dict[str, Any], ...]
    actual_anchor: dict[str, Any]
    actual_cumulative_path: tuple[float, ...]


@dataclass
class CaseOutcome:
    source_path: Path
    category: str
    status: str
    reason: str
    evidence_path: Path | None = None
    metrics: dict[str, Any] = field(default_factory=dict)
    pair: str = ""
    timeframe: str = ""
    horizon: int = 0
    available_future: int = 0
    freeze_sha256: str = ""


AdapterFactory = Callable[[Path], Any]


def default_adapter_factory(root: Path) -> Any:
    from phoenixguard.mobile_api.window_tracker import PhoenixGuardWindowTrackingAdapter

    root.mkdir(parents=True, exist_ok=True)
    return PhoenixGuardWindowTrackingAdapter(market_study_root=root)


def discover_images(root: Path) -> list[Path]:
    corpus = Path(root)
    if not corpus.is_dir():
        raise FileNotFoundError(f"Screenshot corpus does not exist: {corpus}")
    return sorted(
        (
            path for path in corpus.rglob("*")
            if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
        ),
        key=lambda path: str(path).lower(),
    )


def source_category(path: Path) -> str:
    parts = {part.upper() for part in path.parts}
    if "BUYS" in parts:
        return "BUYS"
    if "SELLS" in parts:
        return "SELLS"
    return "UNLABELED"


def ensure_disk_reserve(anchor: Path, *, minimum_gb: float, anticipated_bytes: int = 0) -> float:
    probe = Path(anchor)
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = int(shutil.disk_usage(probe).free)
    if free - int(anticipated_bytes) < int(float(minimum_gb) * GIB):
        raise DiskReserveError(
            f"Disk reserve refused write: free={free / GIB:.2f} GB, "
            f"required={minimum_gb:.2f} GB"
        )
    return free / GIB


def prepare_output_root(output_root: Path, workspace_root: Path) -> Path:
    workspace = Path(workspace_root).resolve()
    runtime = (workspace / ".codex_runtime").resolve()
    target = Path(output_root).resolve()
    if target == runtime or runtime not in target.parents:
        raise NativeReplayError("UNSAFE_OUTPUT_ROOT", "Output must be below workspace .codex_runtime")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    return target


def _number(value: Any, default: float = 0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return float(default)
    return result if math.isfinite(result) else float(default)


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _side(value: Any) -> str:
    candidate = str(value or "HOLD").strip().upper()
    return candidate if candidate in SIDES else "HOLD"


def _image_hash(image: Image.Image) -> str:
    rgb = image.convert("RGB")
    digest = hashlib.sha256(f"{rgb.width}x{rgb.height}:RGB".encode("ascii"))
    digest.update(rgb.tobytes())
    return digest.hexdigest()


def _chart_bbox(study: Any, image_size: tuple[int, int]) -> tuple[int, int, int, int]:
    width, height = image_size
    region = getattr(study, "chart_region", None)
    raw = region.get("pixel_bbox") if isinstance(region, Mapping) else None
    if not isinstance(raw, Sequence) or len(raw) != 4:
        return (0, 0, width, height)
    x0, y0, x1, y1 = (_integer(value) for value in raw)
    return (
        max(0, min(width - 1, x0)),
        max(0, min(height - 1, y0)),
        max(x0 + 1, min(width, x1)),
        max(y0 + 1, min(height, y1)),
    )


def _track_x(track: Mapping[str, Any]) -> float:
    return _number(track.get("center_x_px", track.get("center_x")), float("nan"))


def _track_close_y(track: Mapping[str, Any]) -> float:
    return _number(track.get("close_y_px"), float("nan"))


def _clean_tracks(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = summary.get("tracked_candles")
    tracks: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return tracks
    for value in raw:
        if not isinstance(value, Mapping):
            continue
        track = dict(value)
        if math.isfinite(_track_x(track)) and math.isfinite(_track_close_y(track)):
            tracks.append(track)
    tracks.sort(key=_track_x)
    deduped: list[dict[str, Any]] = []
    for track in tracks:
        if deduped and abs(_track_x(track) - _track_x(deduped[-1])) < 1.0:
            if _number(track.get("parse_confidence")) > _number(deduped[-1].get("parse_confidence")):
                deduped[-1] = track
        else:
            deduped.append(track)
    return deduped


def _spacing(tracks: Sequence[Mapping[str, Any]]) -> float:
    values = [_track_x(track) for track in tracks]
    gaps = [right - left for left, right in zip(values, values[1:]) if right - left >= 1.0]
    return statistics.median(gaps) if gaps else 8.0


def _normalize_visible_track(track: Mapping[str, Any], image_size: tuple[int, int], index: int) -> dict[str, Any]:
    width, height = image_size
    result = copy.deepcopy(dict(track))
    x_value = _track_x(result)
    y_value = _number(result.get("center_y_px", result.get("center_y")))
    result.update(
        {
            "center_x_px": x_value,
            "center_y_px": y_value,
            "center_x": x_value,
            "center_y": y_value,
            "normalized_x": x_value / max(1.0, float(width)),
            "normalized_y": y_value / max(1.0, float(height)),
            "is_closed": True,
            "closed": True,
            "candle_closed": True,
            "forming": False,
            "candle_index": index,
        }
    )
    return result


class RapidChartIdentityResolver:
    def __init__(self) -> None:
        self._engine: Any = None

    def _get_engine(self) -> Any:
        if self._engine is None:
            from rapidocr_onnxruntime import RapidOCR

            self._engine = RapidOCR()
        return self._engine

    @staticmethod
    def _filename_rows(path: Path) -> list[tuple[str, float, float, float, str]]:
        return [(path.stem, 0.72, -1.0, -1.0, "filename")]

    def _ocr_crop(
        self,
        image: Image.Image,
        *,
        box: tuple[int, int, int, int],
        upscale: int,
        region: str,
    ) -> list[tuple[str, float, float, float, str]]:
        import numpy as np

        left, top, right, bottom = box
        crop = image.crop((left, top, right, bottom)).convert("RGB")
        factor = max(1, int(upscale))
        if factor > 1:
            crop = crop.resize(
                (crop.width * factor, crop.height * factor),
                Image.Resampling.BICUBIC,
            )
        result, _ = self._get_engine()(np.asarray(crop))
        rows: list[tuple[str, float, float, float, str]] = []
        for row in result or []:
            if not isinstance(row, Sequence) or len(row) < 3 or not str(row[1]).strip():
                continue
            points = row[0] if isinstance(row[0], Sequence) else []
            xs = [_number(point[0], float("nan")) for point in points if isinstance(point, Sequence) and len(point) >= 2]
            ys = [_number(point[1], float("nan")) for point in points if isinstance(point, Sequence) and len(point) >= 2]
            xs = [value for value in xs if math.isfinite(value)]
            ys = [value for value in ys if math.isfinite(value)]
            if xs and ys:
                center_x = (left + statistics.fmean(xs) / factor) / max(1.0, float(image.width))
                center_y = (top + statistics.fmean(ys) / factor) / max(1.0, float(image.height))
            else:
                center_x = center_y = -1.0
            rows.append((str(row[1]), _number(row[2]), center_x, center_y, region))
        return rows

    def _ocr_rows(self, image: Image.Image, *, full: bool) -> list[tuple[str, float, float, float, str]]:
        if full:
            return self._ocr_crop(
                image,
                box=(0, 0, image.width, image.height),
                upscale=1,
                region="full",
            )
        bottom = min(image.height, max(260, int(image.height * 0.34)))
        return self._ocr_crop(
            image,
            box=(0, 0, image.width, bottom),
            upscale=2 if image.width <= 2200 else 1,
            region="header",
        )

    def _selector_rows(self, image: Image.Image) -> list[tuple[str, float, float, float, str]]:
        width, height = image.size
        top_left = self._ocr_crop(
            image,
            box=(0, 0, max(1, int(width * 0.48)), max(1, int(height * 0.36))),
            upscale=3 if width <= 2400 else 2,
            region="top_left_selector",
        )
        lower_left = self._ocr_crop(
            image,
            box=(0, max(0, int(height * 0.45)), max(1, int(width * 0.42)), height),
            upscale=3 if width <= 2400 else 2,
            region="lower_left_selector",
        )
        return top_left + lower_left

    @staticmethod
    def _parse(
        rows: Sequence[tuple[str, float, float, float, str]],
    ) -> tuple[str, str, float]:
        pair = ""
        pair_confidence = 0.0
        timeframe_pattern = "|".join(
            re.escape(value)
            for value in sorted(TIMEFRAMES, key=lambda value: (-len(value), value))
        )
        same_line: list[tuple[float, float, str, str, float]] = []
        standalone: list[tuple[int, str, float, float, float, str]] = []
        pair_positions: list[tuple[float, float]] = []
        for row_index, (text, confidence, center_x, center_y, region) in enumerate(rows):
            upper = str(text).upper().replace("DAILY", "D1")
            pair_matches = re.finditer(r"(?<![A-Z])([A-Z]{3})\s*[/._-]?\s*([A-Z]{3})(?:\s*(OTC))?", upper)
            for match in pair_matches:
                base, quote, otc = match.group(1), match.group(2), match.group(3)
                if base not in CURRENCY_CODES or quote not in CURRENCY_CODES or base == quote:
                    continue
                candidate = f"{base}/{quote}" + (" OTC" if otc else "")
                if confidence > pair_confidence:
                    pair, pair_confidence = candidate, confidence
                if center_x >= 0.0 and center_y >= 0.0:
                    pair_positions.append((center_x, center_y))
                tail = upper[match.end() : match.end() + 24]
                tf_match = re.search(rf"(?:[,\s])({timeframe_pattern})", tail)
                if tf_match:
                    title_priority = 2.0
                    if center_x >= 0.0 and center_y >= 0.0:
                        if center_x <= 0.48 and center_y <= 0.45:
                            title_priority = 4.0
                        elif center_x <= 0.48:
                            title_priority = 3.0
                    same_line.append(
                        (
                            title_priority,
                            confidence,
                            candidate,
                            tf_match.group(1),
                            confidence,
                        )
                    )
            stripped = re.sub(r"[^A-Z0-9]", "", upper)
            canonical_timeframe = stripped
            suffix_match = re.fullmatch(r"(\d+)([MHDW])", stripped)
            if suffix_match:
                canonical_timeframe = f"{suffix_match.group(2)}{suffix_match.group(1)}"
            if canonical_timeframe in TIMEFRAMES:
                standalone.append(
                    (
                        row_index,
                        canonical_timeframe,
                        confidence,
                        center_x,
                        center_y,
                        region,
                    )
                )

        if same_line:
            _, _, title_pair, timeframe, timeframe_confidence = max(
                same_line,
                key=lambda row: (row[0], row[1]),
            )
            return title_pair, timeframe, timeframe_confidence

        toolbar_rows: set[int] = set()
        for row_index, _, _, _, center_y, region in standalone:
            if region not in {"header", "full", "top_left_selector"} or center_y < 0.0:
                continue
            peers = [
                other_index
                for other_index, _, _, _, other_y, other_region in standalone
                if other_region == region and other_y >= 0.0 and abs(other_y - center_y) <= 0.025
            ]
            if len(peers) >= 3:
                toolbar_rows.update(peers)

        candidates: list[tuple[float, float, str, float]] = []
        for row_index, candidate, confidence, center_x, center_y, region in standalone:
            if row_index in toolbar_rows or region == "filename":
                continue
            spatial_priority = 1.0
            if center_x >= 0.0 and center_y >= 0.0:
                if center_x <= 0.42 and center_y >= 0.55:
                    spatial_priority = 3.0 + 0.25 * center_y
                elif any(
                    math.hypot(center_x - pair_x, center_y - pair_y) <= 0.24
                    for pair_x, pair_y in pair_positions
                ):
                    spatial_priority = 2.75
                elif center_x <= 0.48 and center_y <= 0.40:
                    spatial_priority = 2.0
            candidates.append((spatial_priority, confidence, candidate, confidence))
        if not candidates:
            return pair, "", 0.0
        _, _, timeframe, timeframe_confidence = max(
            candidates,
            key=lambda row: (row[0], row[1]),
        )
        return pair, timeframe, min(pair_confidence, timeframe_confidence)

    def resolve(self, image: Image.Image, source_path: Path) -> ChartIdentity:
        filename_rows = self._filename_rows(source_path)
        top_rows = self._ocr_rows(image, full=False)
        pair, timeframe, confidence = self._parse(top_rows + filename_rows)
        source = "rapidocr_chart_header"
        rows = top_rows
        if not pair or not timeframe:
            selector_rows = self._selector_rows(image)
            pair, timeframe, confidence = self._parse(
                selector_rows + top_rows + filename_rows
            )
            rows = selector_rows + top_rows
            source = "rapidocr_active_selector"
        if not pair or not timeframe:
            full_rows = self._ocr_rows(image, full=True)
            pair, timeframe, confidence = self._parse(
                full_rows + rows + filename_rows
            )
            rows = full_rows + rows
            source = "rapidocr_full_chart"
        if not pair or not timeframe:
            raise NativeReplayError(
                "IDENTITY_OCR_FAILED",
                f"Pair/timeframe not confirmed: pair={pair or 'EMPTY'} timeframe={timeframe or 'EMPTY'}",
            )
        return ChartIdentity(
            pair=pair,
            timeframe=timeframe,
            confidence=round(max(0.0, min(1.0, confidence)), 4),
            source=source,
            ocr_text=tuple(text for text, *_ in rows[:80]),
        )


def plan_geometry(
    full_image: Image.Image,
    *,
    root: Path,
    config: NativeReplayConfig,
    adapter_factory: AdapterFactory = default_adapter_factory,
) -> GeometryPlan:
    study = adapter_factory(root).study(full_image.convert("RGB").copy())
    summary = getattr(study, "tracking_summary", None)
    if not isinstance(summary, Mapping):
        raise NativeReplayError("PLANNER_SUMMARY_MISSING", "V3 planner returned no tracking summary")
    tracks = _clean_tracks(summary)
    future_count = min(config.max_horizon, len(tracks) - config.min_visible_candles)
    if future_count < config.min_future_candles:
        raise NativeReplayError(
            "INSUFFICIENT_CAUSAL_SPLIT",
            f"V3 extracted {len(tracks)} candles; cannot preserve visible history and future",
        )
    anchor_index = len(tracks) - future_count - 1
    if anchor_index < config.min_visible_candles - 1:
        anchor_index = config.min_visible_candles - 1
        future_count = len(tracks) - anchor_index - 1
    anchor_x = _track_x(tracks[anchor_index])
    next_x = _track_x(tracks[anchor_index + 1])
    cut_x = int(round((anchor_x + next_x) / 2.0))
    visible = tuple(
        _normalize_visible_track(track, full_image.size, index)
        for index, track in enumerate(tracks[: anchor_index + 1])
    )
    return GeometryPlan(
        image_size=tuple(full_image.size),
        chart_bbox=_chart_bbox(study, full_image.size),
        cut_x=cut_x,
        spacing_px=max(3.0, min(32.0, _spacing(visible))),
        available_future_candles=future_count,
        visible_tracks=visible,
        anchor_track=copy.deepcopy(visible[-1]),
    )


def mask_chart_future(image: Image.Image, plan: GeometryPlan, mask_rgb: tuple[int, int, int]) -> Image.Image:
    masked = image.convert("RGB").copy()
    _, y0, x1, y1 = plan.chart_bbox
    ImageDraw.Draw(masked).rectangle(
        (plan.cut_x, y0, max(plan.cut_x, x1 - 1), max(y0, y1 - 1)),
        fill=tuple(mask_rgb),
    )
    region = masked.crop((plan.cut_x, y0, x1, y1))
    expected = tuple((value, value) for value in mask_rgb)
    if region.getextrema() != expected:
        raise NativeReplayError("FUTURE_PIXEL_LEAK", "Masked chart future is not uniform")
    return masked


def _overlay_canvas(study: Any, masked: Image.Image) -> Image.Image:
    overlay = getattr(study, "overlay_image", None)
    if not isinstance(overlay, Image.Image) or overlay.size != masked.size:
        return masked.convert("RGB").copy()
    if overlay.mode == "RGBA":
        return Image.alpha_composite(masked.convert("RGBA"), overlay).convert("RGB")
    return overlay.convert("RGB").copy()


def _forecast_digest_payload(frozen: FrozenNativeForecast) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "identity": {
            "pair": frozen.identity.pair,
            "timeframe": frozen.identity.timeframe,
            "confidence": frozen.identity.confidence,
            "source": frozen.identity.source,
        },
        "mask_sha256": frozen.mask_sha256,
        "cut_x": frozen.plan.cut_x,
        "anchor": frozen.plan.anchor_track,
        "planner_contract": frozen.plan.planner_contract,
        "provider": frozen.contribution.get("provider"),
        "path_semantics": frozen.contribution.get("path_target_semantics"),
        "forecast_candles": list(frozen.forecast_candles),
        "forecast_path": list(frozen.forecast_path),
    }


def _digest(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def recompute_freeze_hash(frozen: FrozenNativeForecast) -> str:
    return _digest(_forecast_digest_payload(frozen))


def freeze_native_forecast(
    masked_image: Image.Image,
    *,
    identity: ChartIdentity,
    plan: GeometryPlan,
    root: Path,
    config: NativeReplayConfig,
    adapter_factory: AdapterFactory = default_adapter_factory,
) -> FrozenNativeForecast:
    mask_sha256 = _image_hash(masked_image)
    study = adapter_factory(root).study(masked_image.convert("RGB").copy())
    summary = getattr(study, "tracking_summary", None)
    if not isinstance(summary, Mapping):
        raise NativeReplayError("MASKED_SUMMARY_MISSING", "Masked V3 study returned no summary")
    event_key = hashlib.sha256(
        f"{identity.pair}|{identity.timeframe}|{mask_sha256}|{plan.cut_x}".encode("utf-8")
    ).hexdigest()
    contribution = build_scene_forecast_contribution_v3(
        candles=plan.visible_tracks,
        image_size=masked_image.size,
        timeframe=identity.timeframe,
        pair=identity.pair,
        projection=summary.get("projection") if isinstance(summary.get("projection"), Mapping) else {},
        candle_statistics=summary.get("candle_statistics") if isinstance(summary.get("candle_statistics"), Mapping) else {},
        behavior_payload=summary.get("behavior") if isinstance(summary.get("behavior"), Mapping) else {},
        decision_kernel=summary.get("decision_kernel") if isinstance(summary.get("decision_kernel"), Mapping) else {},
        smart_money_context=summary.get("smart_money_context") if isinstance(summary.get("smart_money_context"), Mapping) else {},
        support_resistance_context=summary.get("support_resistance_context") if isinstance(summary.get("support_resistance_context"), Mapping) else {},
        support_resistance_zones=summary.get("support_resistance_zones") if isinstance(summary.get("support_resistance_zones"), list) else [],
        trendlines=summary.get("trendlines_v3") if isinstance(summary.get("trendlines_v3"), list) else [],
        trend_slopes={
            "global": summary.get("global_slope"),
            "local": summary.get("local_slope"),
            "current": summary.get("current_slope"),
        },
        trend_directions={
            "global": summary.get("global_direction"),
            "local": summary.get("local_direction"),
            "impulse": summary.get("impulse_direction"),
            "major": summary.get("major_trend_direction"),
        },
        book_strategy=summary.get("book_strategy") if isinstance(summary.get("book_strategy"), Mapping) else {},
        playbook_ai_intelligence=summary.get("playbook_ai_intelligence") if isinstance(summary.get("playbook_ai_intelligence"), Mapping) else {},
        session_context=summary.get("session_payload") if isinstance(summary.get("session_payload"), Mapping) else summary.get("session_context") if isinstance(summary.get("session_context"), Mapping) else {},
        news_context=summary.get("news_context") if isinstance(summary.get("news_context"), Mapping) else {},
        pair_dna_context=summary.get("pair_dna") if isinstance(summary.get("pair_dna"), Mapping) else summary.get("pair_profile") if isinstance(summary.get("pair_profile"), Mapping) else {},
        higher_timeframe_context=summary.get("major_trend_context") if isinstance(summary.get("major_trend_context"), Mapping) else {},
        allow_foundation_model=False,
        event_key_override=event_key,
    )
    provider = str(contribution.get("provider") or "").upper()
    if "LSTM" in provider or "LSTM" in str(contribution.get("model_version") or "").upper():
        raise NativeReplayError("FORBIDDEN_LSTM_PROVIDER", "LSTM reached the native PhoenixGuard replay")
    candles = contribution.get("forecast_candles")
    path = contribution.get("forecast_path")
    scenarios = contribution.get("forecast_scenarios")
    if not isinstance(candles, list) or len(candles) != config.max_horizon:
        raise NativeReplayError(
            "NATIVE_HORIZON_INCOMPLETE",
            f"Native scene forecast published {len(candles) if isinstance(candles, list) else 0}/72 candles",
        )
    if not isinstance(path, list) or len(path) != config.max_horizon:
        raise NativeReplayError("NATIVE_PATH_INCOMPLETE", "Native forecast path does not contain 72 events")
    if str(contribution.get("path_target_semantics") or "") != PATH_SEMANTICS:
        raise NativeReplayError("NATIVE_PATH_SEMANTICS_INVALID", "Native path semantics are not 72-event coherent")
    anchor_y_norm = _track_close_y(plan.anchor_track) / max(1.0, float(masked_image.height))
    first_open = _number(candles[0].get("open_y_norm"), float("nan"))
    if not math.isfinite(first_open) or abs(first_open - anchor_y_norm) > 0.012:
        raise NativeReplayError(
            "FORECAST_ANCHOR_MISMATCH",
            f"First forecast candle is {abs(first_open - anchor_y_norm):.5f} away from anchor close",
        )
    evidence = {
        "accepted_trendlines": sum(
            bool(row.get("geometry_contract_accepted"))
            for row in summary.get("trendlines_v3", [])
            if isinstance(row, Mapping)
        ),
        "zone_count": len(summary.get("support_resistance_zones", []))
        if isinstance(summary.get("support_resistance_zones"), list) else 0,
        "behavior_state": str(
            (summary.get("behavior") or {}).get("current_state") or "UNKNOWN"
        ) if isinstance(summary.get("behavior"), Mapping) else "UNKNOWN",
        "smart_money_side": str(
            (summary.get("smart_money_context") or {}).get("dominant_side") or "HOLD"
        ) if isinstance(summary.get("smart_money_context"), Mapping) else "HOLD",
        "global_direction": str(summary.get("global_direction") or "HOLD"),
        "local_direction": str(summary.get("local_direction") or "HOLD"),
        "major_direction": str(summary.get("major_trend_direction") or "HOLD"),
    }
    provisional = FrozenNativeForecast(
        identity=identity,
        plan=plan,
        mask_sha256=mask_sha256,
        freeze_sha256="",
        forecast_candles=tuple(copy.deepcopy(candles)),
        forecast_path=tuple(copy.deepcopy(path)),
        forecast_scenarios=tuple(copy.deepcopy(scenarios if isinstance(scenarios, list) else [])),
        contribution=copy.deepcopy(dict(contribution)),
        evidence=evidence,
        prediction_canvas=_overlay_canvas(study, masked_image),
    )
    freeze_hash = _digest(_forecast_digest_payload(provisional))
    frozen = FrozenNativeForecast(
        **{**provisional.__dict__, "freeze_sha256": freeze_hash}
    )
    if recompute_freeze_hash(frozen) != freeze_hash:
        raise NativeReplayError("FREEZE_HASH_FAILED", "Native forecast changed during freeze")
    return frozen


def _direction(value: float, epsilon: float) -> str:
    if value > epsilon:
        return "BUY"
    if value < -epsilon:
        return "SELL"
    return "HOLD"


def _movements(path: Sequence[float], epsilon: float) -> list[str]:
    previous = 0.0
    output: list[str] = []
    for value in path:
        output.append(_direction(float(value) - previous, epsilon))
        previous = float(value)
    return output


def _turns(path: Sequence[float], epsilon: float) -> list[int]:
    result: list[int] = []
    previous = ""
    for step, side in enumerate(_movements(path, epsilon), start=1):
        if side == "HOLD":
            continue
        if previous and side != previous:
            result.append(step)
        previous = side
    return result


def _turn_f1(predicted: Sequence[int], actual: Sequence[int]) -> float:
    if not predicted and not actual:
        return 1.0
    if not predicted or not actual:
        return 0.0
    remaining = list(actual)
    hits = 0
    for step in predicted:
        choices = [(abs(step - candidate), index) for index, candidate in enumerate(remaining) if abs(step - candidate) <= 1]
        if choices:
            _, index = min(choices)
            remaining.pop(index)
            hits += 1
    precision = hits / len(predicted)
    recall = hits / len(actual)
    return 0.0 if precision + recall == 0 else 2.0 * precision * recall / (precision + recall)


def _correlation(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) < 2 or len(left) != len(right):
        return 0.0
    left_mean, right_mean = statistics.fmean(left), statistics.fmean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    denominator = math.sqrt(
        sum((value - left_mean) ** 2 for value in left)
        * sum((value - right_mean) ** 2 for value in right)
    )
    return 0.0 if denominator <= 1e-12 else max(-1.0, min(1.0, numerator / denominator))


def _score(predicted: Sequence[float], actual: Sequence[float], epsilon: float) -> dict[str, Any]:
    count = min(len(predicted), len(actual))
    predicted_values = list(predicted[:count])
    actual_values = list(actual[:count])
    if count < 2:
        raise NativeReplayError("SCORING_HORIZON_SHORT", "Fewer than two aligned horizons")
    predicted_sides = [_direction(value, epsilon) for value in predicted_values]
    actual_sides = [_direction(value, epsilon) for value in actual_values]
    movement_predicted = _movements(predicted_values, epsilon)
    movement_actual = _movements(actual_values, epsilon)
    rmse = math.sqrt(statistics.fmean((a - b) ** 2 for a, b in zip(predicted_values, actual_values)))
    scale = max(
        max(abs(value) for value in predicted_values),
        max(abs(value) for value in actual_values),
        epsilon * 4.0,
    )
    predicted_majority = _direction(
        sum(side == "BUY" for side in predicted_sides) - sum(side == "SELL" for side in predicted_sides),
        0.0,
    )
    actual_majority = _direction(
        sum(side == "BUY" for side in actual_sides) - sum(side == "SELL" for side in actual_sides),
        0.0,
    )
    return {
        "aligned_horizons": count,
        "predicted_terminal_direction": predicted_sides[-1],
        "actual_terminal_direction": actual_sides[-1],
        "terminal_direction_hit": predicted_sides[-1] == actual_sides[-1],
        "predicted_majority_direction": predicted_majority,
        "actual_majority_direction": actual_majority,
        "majority_direction_hit": predicted_majority == actual_majority,
        "horizon_direction_accuracy": statistics.fmean(a == b for a, b in zip(predicted_sides, actual_sides)),
        "fluctuation_accuracy": statistics.fmean(a == b for a, b in zip(movement_predicted, movement_actual)),
        "turning_point_f1": _turn_f1(_turns(predicted_values, epsilon), _turns(actual_values, epsilon)),
        "path_correlation": _correlation(predicted_values, actual_values),
        "path_similarity": max(0.0, min(1.0, 1.0 - rmse / scale)),
        "path_rmse_norm": rmse,
        "prediction_frozen_before_reveal": True,
        "future_ohlc_passed_to_forecaster": False,
        "candle_colors_used_for_scoring": False,
    }


def reveal_and_score(
    full_image: Image.Image,
    frozen: FrozenNativeForecast,
    *,
    root: Path,
    config: NativeReplayConfig,
    adapter_factory: AdapterFactory = default_adapter_factory,
) -> RevealResult:
    if recompute_freeze_hash(frozen) != frozen.freeze_sha256:
        raise NativeReplayError("FORECAST_MUTATED_BEFORE_REVEAL", "Frozen forecast hash changed")
    study = adapter_factory(root).study(full_image.convert("RGB").copy())
    summary = getattr(study, "tracking_summary", None)
    if not isinstance(summary, Mapping):
        raise NativeReplayError("REVEAL_SUMMARY_MISSING", "Reveal V3 study returned no summary")
    tracks = _clean_tracks(summary)
    anchor_x = _track_x(frozen.plan.anchor_track)
    nearest = min(range(len(tracks)), key=lambda index: abs(_track_x(tracks[index]) - anchor_x))
    actual_anchor = tracks[nearest]
    if abs(_track_x(actual_anchor) - anchor_x) > max(3.0, frozen.plan.spacing_px * 0.55):
        raise NativeReplayError("ANCHOR_REVEAL_MISMATCH", "Reveal anchor does not match frozen candle")
    future = tracks[nearest + 1 : nearest + 1 + config.max_horizon]
    if len(future) < config.min_future_candles:
        raise NativeReplayError("REVEALED_FUTURE_SHORT", f"Only {len(future)} future candles available")
    height = max(1.0, float(full_image.height))
    anchor_y = _track_close_y(actual_anchor)
    actual_path = tuple((anchor_y - _track_close_y(track)) / height for track in future)
    frozen_anchor_y = _track_close_y(frozen.plan.anchor_track) / height
    predicted_path = tuple(
        frozen_anchor_y - _number(candle.get("close_y_norm"))
        for candle in frozen.forecast_candles[: len(actual_path)]
    )
    metrics = _score(predicted_path, actual_path, config.direction_epsilon)
    if recompute_freeze_hash(frozen) != frozen.freeze_sha256:
        raise NativeReplayError("FORECAST_MUTATED_AFTER_REVEAL", "Frozen forecast changed after reveal")
    return RevealResult(
        metrics=metrics,
        actual_tracks=tuple(copy.deepcopy(future)),
        actual_anchor=copy.deepcopy(actual_anchor),
        actual_cumulative_path=actual_path,
    )


def _extended_canvas(image: Image.Image, frozen: FrozenNativeForecast) -> Image.Image:
    width = max(
        image.width,
        int(math.ceil(_track_x(frozen.plan.anchor_track) + (len(frozen.forecast_candles) + 3) * frozen.plan.spacing_px)),
    )
    canvas = Image.new("RGB", (width, image.height), (7, 9, 11))
    canvas.paste(image.convert("RGB"), (0, 0))
    return canvas


def _draw_anchor(draw: ImageDraw.ImageDraw, frozen: FrozenNativeForecast, height: int) -> None:
    x_value = _track_x(frozen.plan.anchor_track)
    y_value = _track_close_y(frozen.plan.anchor_track)
    draw.line((x_value, frozen.plan.chart_bbox[1], x_value, frozen.plan.chart_bbox[3]), fill=(255, 204, 70, 230), width=2)
    draw.ellipse((x_value - 5, y_value - 5, x_value + 5, y_value + 5), fill=(255, 220, 92, 255))
    draw.text((x_value + 8, max(4, y_value - 16)), "ANCHOR", fill=(255, 222, 112, 255))


def draw_native_forecast(image: Image.Image, frozen: FrozenNativeForecast) -> Image.Image:
    canvas = _extended_canvas(image, frozen)
    draw = ImageDraw.Draw(canvas, "RGBA")
    _draw_anchor(draw, frozen, canvas.height)
    anchor_x = _track_x(frozen.plan.anchor_track)
    spacing = frozen.plan.spacing_px
    body_width = max(2.0, min(9.0, spacing * 0.58))
    close_points = [(anchor_x, _track_close_y(frozen.plan.anchor_track))]
    for index, candle in enumerate(frozen.forecast_candles, start=1):
        x_value = anchor_x + spacing * index
        open_y = _number(candle.get("open_y_norm")) * frozen.plan.image_size[1]
        high_y = _number(candle.get("high_y_norm")) * frozen.plan.image_size[1]
        low_y = _number(candle.get("low_y_norm")) * frozen.plan.image_size[1]
        close_y = _number(candle.get("close_y_norm")) * frozen.plan.image_size[1]
        draw.line((x_value, high_y, x_value, low_y), fill=(63, 226, 255, 255), width=2)
        top, bottom = min(open_y, close_y), max(open_y, close_y)
        if bottom - top < 2:
            bottom = top + 2
        draw.rectangle(
            (x_value - body_width / 2, top, x_value + body_width / 2, bottom),
            fill=(20, 183, 226, 150),
            outline=(178, 247, 255, 255),
            width=1,
        )
        close_points.append((x_value, close_y))
        if index == 1 or index % 8 == 0 or index == 72:
            draw.text((x_value - 5, max(2, high_y - 15)), str(index), fill=(208, 249, 255, 255))
    draw.line(close_points, fill=(36, 210, 247, 210), width=2, joint="curve")
    return canvas


def draw_comparison(full_image: Image.Image, frozen: FrozenNativeForecast, reveal: RevealResult) -> Image.Image:
    canvas = draw_native_forecast(full_image, frozen)
    draw = ImageDraw.Draw(canvas, "RGBA")
    points = [(_track_x(reveal.actual_anchor), _track_close_y(reveal.actual_anchor))]
    for track in reveal.actual_tracks:
        x_value = _track_x(track)
        open_y = _number(track.get("open_y_px"), _track_close_y(track))
        close_y = _track_close_y(track)
        high_y = _number(track.get("wick_top_px"), min(open_y, close_y))
        low_y = _number(track.get("wick_bottom_px"), max(open_y, close_y))
        width = max(2.0, min(9.0, _number(track.get("width"), frozen.plan.spacing_px * 0.5)))
        draw.line((x_value, high_y, x_value, low_y), fill=(255, 153, 46, 255), width=2)
        draw.rectangle(
            (x_value - width / 2, min(open_y, close_y), x_value + width / 2, max(open_y, close_y) + 1),
            outline=(255, 190, 87, 255),
            width=2,
        )
        points.append((x_value, close_y))
    if len(points) > 1:
        draw.line(points, fill=(255, 137, 35, 230), width=3, joint="curve")
    draw.rectangle((12, 12, 410, 58), fill=(4, 8, 12, 215))
    draw.text((22, 20), "CYAN: FROZEN PHOENIXGUARD CANDLES", fill=(119, 240, 255, 255))
    draw.text((22, 39), "ORANGE: REVEALED CANDLE GEOMETRY", fill=(255, 183, 79, 255))
    return canvas


def _panel(image: Image.Image, label: str, target_width: int) -> Image.Image:
    ratio = min(1.0, target_width / max(1, image.width))
    resized = image.resize(
        (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
        Image.Resampling.LANCZOS,
    )
    panel = Image.new("RGB", (target_width, resized.height + 34), (5, 7, 10))
    panel.paste(resized, ((target_width - resized.width) // 2, 34))
    ImageDraw.Draw(panel).text((12, 11), label, fill=(239, 222, 165), font=ImageFont.load_default())
    return panel


def render_evidence(
    *,
    source_label: str,
    masked: Image.Image,
    full: Image.Image,
    frozen: FrozenNativeForecast | None,
    reveal: RevealResult | None,
    status: str,
    reason: str,
    config: NativeReplayConfig,
) -> Image.Image:
    if frozen is None:
        forecast = masked.copy()
        comparison = full.copy()
        ImageDraw.Draw(forecast).text((18, 18), reason[:180], fill=(255, 120, 110))
    else:
        forecast = draw_native_forecast(frozen.prediction_canvas, frozen)
        comparison = draw_comparison(full, frozen, reveal) if reveal is not None else draw_native_forecast(full, frozen)
    panels = [
        _panel(masked, "1. CHART FUTURE WITHHELD - UI IDENTITY PRESERVED", config.evidence_width),
        _panel(forecast, "2. PHOENIXGUARD 72-CANDLE FORECAST FROZEN FROM ANCHOR", config.evidence_width),
        _panel(comparison, "3. FROZEN CANDLE BOXES VS REVEALED GEOMETRY", config.evidence_width),
    ]
    title_height = 74
    sheet = Image.new("RGB", (config.evidence_width, title_height + sum(panel.height for panel in panels)), (4, 6, 9))
    draw = ImageDraw.Draw(sheet)
    draw.text((14, 11), f"{source_label[:145]} | {status}", fill=(247, 219, 130))
    if frozen is not None:
        line = (
            f"{frozen.identity.pair} {frozen.identity.timeframe} | provider={frozen.contribution.get('provider')} | "
            f"anchor={_track_x(frozen.plan.anchor_track):.1f}px | horizon=72 | freeze={frozen.freeze_sha256[:16]}"
        )
        if reveal is not None:
            line += (
                f" | terminal={'HIT' if reveal.metrics.get('terminal_direction_hit') else 'MISS'}"
                f" | H-accuracy={100 * _number(reveal.metrics.get('horizon_direction_accuracy')):.1f}%"
                f" | shape={100 * _number(reveal.metrics.get('path_similarity')):.1f}%"
            )
        draw.text((14, 34), line[:230], fill=(168, 218, 232))
    else:
        draw.text((14, 34), reason[:220], fill=(255, 135, 122))
    draw.text((14, 55), "No LSTM. No folder labels. No candle colors. Future OHLC revealed only after freeze.", fill=(139, 158, 169))
    y_value = title_height
    for panel in panels:
        sheet.paste(panel, (0, y_value))
        y_value += panel.height
    return sheet


def _encode_png(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=True, compress_level=8)
    return stream.getvalue()


def save_evidence(
    image: Image.Image,
    destination: Path,
    *,
    output_root: Path,
    bytes_written: int,
    config: NativeReplayConfig,
) -> int:
    encoded = _encode_png(image)
    total = bytes_written + len(encoded)
    if total > config.max_output_bytes:
        raise DiskReserveError("Native replay evidence exceeded bounded output contract")
    ensure_disk_reserve(output_root, minimum_gb=config.min_free_gb, anticipated_bytes=len(encoded))
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".png.tmp")
    temporary.write_bytes(encoded)
    temporary.replace(destination)
    return total


def evidence_name(index: int, source: Path) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", source.stem).strip("._-")[:68] or "chart"
    digest = hashlib.sha256(str(source).encode("utf-8")).hexdigest()[:10]
    return f"case_{index:04d}_{stem}_{digest}.png"


def run_case(
    source: Path,
    *,
    source_label: str,
    index: int,
    state_root: Path,
    evidence_root: Path,
    bytes_written: int,
    identity_resolver: RapidChartIdentityResolver,
    config: NativeReplayConfig,
    adapter_factory: AdapterFactory = default_adapter_factory,
) -> tuple[CaseOutcome, int]:
    category = source_category(source)
    ensure_disk_reserve(state_root, minimum_gb=config.min_free_gb)
    frozen: FrozenNativeForecast | None = None
    reveal: RevealResult | None = None
    status = "REJECTED"
    reason = ""
    with tempfile.TemporaryDirectory(prefix="case_", dir=state_root) as temporary:
        root = Path(temporary)
        with Image.open(source) as raw:
            full = ImageOps.exif_transpose(raw).convert("RGB")
        try:
            identity = identity_resolver.resolve(full, source)
            plan = plan_geometry(full, root=root / "planner", config=config, adapter_factory=adapter_factory)
            masked = mask_chart_future(full, plan, config.mask_rgb)
            del full
            frozen = freeze_native_forecast(
                masked,
                identity=identity,
                plan=plan,
                root=root / "predictor",
                config=config,
                adapter_factory=adapter_factory,
            )
            with Image.open(source) as raw:
                full = ImageOps.exif_transpose(raw).convert("RGB")
            reveal = reveal_and_score(
                full,
                frozen,
                root=root / "reveal",
                config=config,
                adapter_factory=adapter_factory,
            )
            status = "SCORED_NATIVE_V3"
            reason = str(frozen.contribution.get("interpretation") or "Native PhoenixGuard path scored")
        except NativeReplayError as exc:
            reason = f"{exc.code}: {exc}"
            if "full" not in locals():
                with Image.open(source) as raw:
                    full = ImageOps.exif_transpose(raw).convert("RGB")
            if "masked" not in locals():
                masked = full.copy()
        sheet = render_evidence(
            source_label=source_label,
            masked=masked,
            full=full,
            frozen=frozen,
            reveal=reveal,
            status=status,
            reason=reason,
            config=config,
        )
        destination = evidence_root / evidence_name(index, source)
        bytes_written = save_evidence(
            sheet,
            destination,
            output_root=evidence_root,
            bytes_written=bytes_written,
            config=config,
        )
    outcome = CaseOutcome(
        source_path=source,
        category=category,
        status=status,
        reason=reason,
        evidence_path=destination,
        metrics=dict(reveal.metrics) if reveal is not None else {},
        pair=frozen.identity.pair if frozen is not None else "",
        timeframe=frozen.identity.timeframe if frozen is not None else "",
        horizon=len(frozen.forecast_candles) if frozen is not None else 0,
        available_future=len(reveal.actual_tracks) if reveal is not None else 0,
        freeze_sha256=frozen.freeze_sha256 if frozen is not None else "",
    )
    return outcome, bytes_written


def _mean(outcomes: Sequence[CaseOutcome], key: str) -> float:
    values = [_number(outcome.metrics.get(key), float("nan")) for outcome in outcomes if key in outcome.metrics]
    values = [value for value in values if math.isfinite(value)]
    return statistics.fmean(values) if values else 0.0


def summarize(outcomes: Sequence[CaseOutcome]) -> dict[str, Any]:
    scored = [outcome for outcome in outcomes if outcome.metrics]
    rejected = [outcome for outcome in outcomes if not outcome.metrics]
    reasons: dict[str, int] = {}
    for outcome in rejected:
        code = outcome.reason.split(":", 1)[0] if outcome.reason else "UNKNOWN"
        reasons[code] = reasons.get(code, 0) + 1
    terminal = statistics.fmean(bool(outcome.metrics.get("terminal_direction_hit")) for outcome in scored) if scored else 0.0
    return {
        "attempted": len(outcomes),
        "scored": len(scored),
        "rejected": len(rejected),
        "terminal_direction_accuracy": terminal,
        "majority_direction_accuracy": statistics.fmean(bool(outcome.metrics.get("majority_direction_hit")) for outcome in scored) if scored else 0.0,
        "horizon_direction_accuracy": _mean(scored, "horizon_direction_accuracy"),
        "fluctuation_accuracy": _mean(scored, "fluctuation_accuracy"),
        "turning_point_f1": _mean(scored, "turning_point_f1"),
        "path_correlation": _mean(scored, "path_correlation"),
        "path_similarity": _mean(scored, "path_similarity"),
        "target_65_met": bool(scored) and terminal >= 0.65,
        "rejection_reasons": dict(sorted(reasons.items())),
    }


def format_summary(summary: Mapping[str, Any]) -> str:
    lines = [
        "PHOENIXGUARD NATIVE 72-HORIZON CAUSAL REPLAY",
        f"Attempted: {_integer(summary.get('attempted'))}",
        f"Scored: {_integer(summary.get('scored'))}",
        f"Rejected: {_integer(summary.get('rejected'))}",
        "Predictor: PHOENIXGUARD_NATIVE_SCENE_TRAJECTORY_V3",
        "LSTM used: NO",
        "Prediction geometry: 72 OHLC candle boxes from one verified anchor",
        f"Terminal direction accuracy: {100 * _number(summary.get('terminal_direction_accuracy')):.2f}%",
        f"Majority direction accuracy: {100 * _number(summary.get('majority_direction_accuracy')):.2f}%",
        f"All-horizon direction accuracy: {100 * _number(summary.get('horizon_direction_accuracy')):.2f}%",
        f"Fluctuation accuracy: {100 * _number(summary.get('fluctuation_accuracy')):.2f}%",
        f"Turning-point F1: {100 * _number(summary.get('turning_point_f1')):.2f}%",
        f"Path correlation: {_number(summary.get('path_correlation')):.4f}",
        f"Path similarity: {100 * _number(summary.get('path_similarity')):.2f}%",
        f"65% terminal target met: {'YES' if summary.get('target_65_met') else 'NO'}",
    ]
    reasons = summary.get("rejection_reasons")
    if isinstance(reasons, Mapping) and reasons:
        lines.append("Rejections: " + ", ".join(f"{key}={value}" for key, value in reasons.items()))
    return "\n".join(lines)


def output_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in Path(root).rglob("*") if path.is_file()) if Path(root).exists() else 0
