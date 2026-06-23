"""
PhoenixGuard SIGE-VLA 3.0 — Computer Vision Pattern Detector
=============================================================
Skills wired:
  - AI in Robotics & Computer Vision (YOLO26s + foduucom fallback, conf=0.3)
  - Design & Analysis of Algorithms (min-heap priority queue for pattern ranking)
  - Competitive Coding (O(n log n) pattern ranking, heap property)
  - Clustering (K-Means on bbox centers → consolidation cluster detection)
  - Data Structures (priority queue, deque, cluster dict)
  - Discrete Mathematics (H&S pattern scoring penalty set)
"""
from __future__ import annotations

import heapq
import io
import json
import os
from urllib import error, request
import pickle
import importlib
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence, cast

import numpy as np
from numpy.typing import NDArray
from PIL import Image
from PIL import ImageEnhance
from phoenixguard.core.utils import can_import_torchvision_safely
from phoenixguard.paths import PROJECT_ROOT
from phoenixguard.vision.cv_reasoning import (
    CVReasoningTrace,
    MarketState,
    normalize_transition_probabilities,
    validate_market_state,
)


class SklearnClassifierLike(Protocol):
    coef_: Any
    C: float

    def fit(self, X: NDArray[np.float32], y: NDArray[np.int32]) -> Any: ...
    def predict(self, X: NDArray[np.float32]) -> NDArray[Any]: ...
    def predict_proba(self, X: NDArray[np.float32]) -> NDArray[Any]: ...


class StandardScalerLike(Protocol):
    def fit_transform(self, X: NDArray[np.float32]) -> NDArray[Any]: ...
    def transform(self, X: NDArray[np.float32]) -> NDArray[Any]: ...


class LogisticRegCtor(Protocol):
    def __call__(self, *, max_iter: int, solver: str, class_weight: str, C: float) -> SklearnClassifierLike: ...


class StandardScalerCtor(Protocol):
    def __call__(self) -> StandardScalerLike: ...


class MakePipelineCallable(Protocol):
    def __call__(self, *steps: object) -> SklearnClassifierLike: ...


class TrainTestSplitCallable(Protocol):
    def __call__(
        self,
        X: NDArray[np.float32],
        y: NDArray[np.int32],
        *,
        test_size: float,
        random_state: int,
        stratify: NDArray[np.int32] | None = None,
    ) -> tuple[NDArray[np.float32], NDArray[np.float32], NDArray[np.int32], NDArray[np.int32]]: ...


class KMeansLike(Protocol):
    cluster_centers_: NDArray[Any]

    def fit_predict(self, X: NDArray[np.float32]) -> NDArray[Any]: ...


class KMeansCtor(Protocol):
    def __call__(self, *, n_clusters: int, random_state: int, n_init: int) -> KMeansLike: ...


class HfHubDownloadCallable(Protocol):
    def __call__(
        self,
        repo_id: str,
        filename: str,
        *,
        subfolder: str | None = None,
        repo_type: str | None = None,
        revision: str | None = None,
        library_name: str | None = None,
        library_version: str | None = None,
        cache_dir: str | Path | None = None,
        local_dir: str | Path | None = None,
        user_agent: Mapping[str, str] | str | None = None,
        force_download: bool = False,
        proxies: Mapping[str, str] | None = None,
        etag_timeout: float = 10.0,
        token: bool | str | None = None,
        local_files_only: bool = False,
        headers: Mapping[str, str] | None = None,
        endpoint: str | None = None,
        resume_download: bool | None = None,
        force_filename: str | None = None,
        local_dir_use_symlinks: str | bool = "auto",
    ) -> str: ...


YOLOModel: Any | None = None
try:
    from ultralytics import YOLO as _ultralytics_yolo
    YOLOModel = _ultralytics_yolo
except Exception:
    YOLOModel = None

KMeansModel: KMeansCtor | None = None
_sk_ok = False
try:
    import sklearn.cluster as _sk_cluster

    KMeansModel = cast(KMeansCtor, _sk_cluster.KMeans)
    _sk_ok = True
except Exception:
    KMeansModel = None

LogisticRegModel: LogisticRegCtor | None = None
train_test_split_fn: TrainTestSplitCallable | None = None
StandardScalerModel: StandardScalerCtor | None = None
MakePipelineFn: MakePipelineCallable | None = None
try:
    _sk_linear_model_module: Any = importlib.import_module("sklearn.linear_model")
    _sk_model_selection_module: Any = importlib.import_module("sklearn.model_selection")
    _sk_pipeline_module: Any = importlib.import_module("sklearn.pipeline")
    _sk_preprocessing_module: Any = importlib.import_module("sklearn.preprocessing")

    _logreg_obj: object = getattr(_sk_linear_model_module, "LogisticRegression", None)
    _split_obj: object = getattr(_sk_model_selection_module, "train_test_split", None)
    _pipeline_obj: object = getattr(_sk_pipeline_module, "make_pipeline", None)
    _scaler_obj: object = getattr(_sk_preprocessing_module, "StandardScaler", None)

    if callable(_logreg_obj):
        LogisticRegModel = cast(LogisticRegCtor, _logreg_obj)
    if callable(_split_obj):
        train_test_split_fn = cast(TrainTestSplitCallable, _split_obj)
    if callable(_pipeline_obj):
        MakePipelineFn = cast(MakePipelineCallable, _pipeline_obj)
    if callable(_scaler_obj):
        StandardScalerModel = cast(StandardScalerCtor, _scaler_obj)
except Exception:
    LogisticRegModel = None
    train_test_split_fn = None
    StandardScalerModel = None
    MakePipelineFn = None

HfApi: Any | None = None
InferenceClient: Any | None = None
hf_hub_download: HfHubDownloadCallable | None = None
try:
    import huggingface_hub as _huggingface_hub

    HfApi = getattr(_huggingface_hub, "HfApi", None)
    InferenceClient = getattr(_huggingface_hub, "InferenceClient", None)
    _download_obj = getattr(_huggingface_hub, "hf_hub_download", None)
    if callable(_download_obj):
        hf_hub_download = cast(HfHubDownloadCallable, _download_obj)
except Exception:
    HfApi = None
    InferenceClient = None
    hf_hub_download = None


# ── Pattern priority scores (higher = more relevant to price-action style) ────
# H&S and complex patterns are heavily penalized (80% reduction per spec)
_PATTERN_SCORES: dict[str, float] = {
    "bullish_engulfing": 1.0,
    "bearish_engulfing": 1.0,
    "hammer": 0.95,
    "shooting_star": 0.95,
    "doji": 0.85,
    "pin_bar": 0.90,
    "inside_bar": 0.85,
    "morning_star": 0.90,
    "evening_star": 0.90,
    "three_white_soldiers": 0.88,
    "three_black_crows": 0.88,
    "tweezer_top": 0.82,
    "tweezer_bottom": 0.82,
    "reversal": 1.0,
    "continuation": 0.95,
    "breakout": 0.85,
    "consolidation": 0.80,
    # Penalized patterns (complex, not matching 808FX style)
    "head_and_shoulders": 0.20,        # 80% reduction
    "inverse_head_and_shoulders": 0.20,
    "double_top": 0.40,
    "double_bottom": 0.40,
    "triple_top": 0.35,
    "triple_bottom": 0.35,
    "wedge": 0.45,
    "triangle": 0.50,
    "flag": 0.60,
    "pennant": 0.60,
    # Memory fine-tuned directional classes
    "buy_memory_bias": 1.0,
    "sell_memory_bias": 1.0,
    # Latest-candle specialist branch
    "latest_candle_buy": 1.0,
    "latest_candle_sell": 1.0,
    "next_candle_buy": 1.0,
    "next_candle_sell": 1.0,
    "wick_dominance_upper": 0.88,
    "wick_dominance_lower": 0.88,
    "next_move_small": 0.72,
    "next_move_medium": 0.78,
    "next_move_large": 0.82,
}

# Patterns that are considered reversal-type
_REVERSAL_PATTERNS = {
    "bullish_engulfing", "bearish_engulfing", "hammer", "shooting_star",
    "doji", "pin_bar", "morning_star", "evening_star",
    "tweezer_top", "tweezer_bottom", "reversal",
}

# Patterns that suggest continuation
_CONTINUATION_PATTERNS = {
    "inside_bar", "three_white_soldiers", "three_black_crows",
    "continuation", "breakout", "flag", "pennant",
    "buy_memory_bias", "sell_memory_bias",
}


@dataclass
class PatternDetection:
    pattern: str
    confidence: float
    bbox: list[float]
    priority_score: float = 0.0
    pattern_type: str = "unknown"   # "reversal" | "continuation" | "other"


class LoggerLike(Protocol):
    def info(self, msg: str, *args: Any, **kwargs: Any) -> Any: ...
    def warning(self, msg: str, *args: Any, **kwargs: Any) -> Any: ...
    def exception(self, msg: str, *args: Any, **kwargs: Any) -> Any: ...
    def error(self, msg: str, *args: Any, **kwargs: Any) -> Any: ...



class CVPatternDetector:
    memory_clf_meta: dict[str, Any]

    def get_structured_output(self, image: Image.Image) -> dict[str, Any]:
        """
        Returns a structured dictionary for interpreter fusion layer.
        """
        detections = self.detect(image)  # type: ignore[attr-defined]
        # Example: pick the top detection or summarize
        if detections:
            top = max(detections, key=lambda d: float(d.get("confidence", 0.0)))
            setup = top.get("pattern", "unknown")
            confidence = float(top.get("confidence", 0.0))
            notes = top.get("notes", "")
            risk = "moderate"
            projection = top.get("direction", "unknown")
        else:
            setup = "none"
            confidence = 0.0
            notes = "no pattern detected"
            risk = "moderate"
            projection = "unknown"
        return {
            "setup": setup,
            "confidence": confidence,
            "risk": risk,
            "projection": projection,
            "notes": notes,
        }
    ensemble_cv: Any = None  # Will be set by main.py for ensemble boosting

    """
    YOLO26s-primary pattern detector with:
    - Priority queue ranking for price-action pattern selection
    - K-Means consolidation cluster detection
    - H&S score reduction (80%)
    """

    @staticmethod
    def _safe_float(val: Any = None, default: float = 0.0) -> float:
        if isinstance(val, list):
            if not val:
                return default
            first: Any = val[0]  # type: ignore
            if isinstance(first, (int, float, str)):
                try:
                    return float(first)
                except (TypeError, ValueError):
                    return default
            return default
        if val is None:
            return default
        if isinstance(val, (int, float, str)):
            try:
                return float(val)
            except (TypeError, ValueError):
                return default
        return default

    def __init__(self, primary_model: str, fallback_model: str, logger: LoggerLike) -> None:
        self.logger = logger
        self.model_name = primary_model
        self.model = None
        self.strict_model_only = True
        self.use_hf_endpoint = False
        self.hf_model_id = None
        self.hf_client = None
        self.hf_remote_url = ""
        self.memory_clf = None
        self.latest_dir_clf = None
        self.next_dir_clf = None
        self.wick_dom_clf = None
        self.move_bucket_clf = None
        self.seq_dir_clf = None
        self.macro_trend_clf = None
        self.local_phase_clf = None
        self.phase_risk_clf = None
        self.intent_next_clf = None
        self.global_scaler = None
        self.latest_scaler = None
        self.seq_scaler = None
        self.local_scaler = None
        self.macro_scaler = None
        self.intent_scaler = None
        self.memory_clf_meta = {}
        self.taxonomy_label_maps = {}
        self.ensemble_cv = None  # Will be set externally

        loaded_local_model = self._try_load_hf_yolo_weights(primary_model)
        if not loaded_local_model:
            fallback_ref = str(fallback_model or "").strip()
            primary_ref = str(primary_model or "").strip()
            if fallback_ref and fallback_ref != primary_ref:
                loaded_local_model = self._try_load_hf_yolo_weights(fallback_ref)
        if loaded_local_model:
            self.logger.info("CV local YOLO backend ready (%s)", self.model_name)
            self._load_or_train_memory_classifier()
            return

        self.strict_model_only = False
        self.logger.warning(
            "CV YOLO backend unavailable for %s; continuing with degraded chart parsing until weights are available.",
            primary_model,
        )

    @staticmethod
    def _as_float32_array(value: object) -> NDArray[np.float32]:
        return np.asarray(value, dtype=np.float32)

    @staticmethod
    def _coef_feature_width(model: object | None) -> int | None:
        if model is None:
            return None
        coef = getattr(model, "coef_", None)
        if coef is None:
            return None
        coef_arr = np.asarray(coef)
        if coef_arr.ndim < 2:
            return None
        return int(coef_arr.shape[1])

    @staticmethod
    def _box_from_object(box: object | None) -> tuple[float, float, float, float] | None:
        if box is None:
            return None
        if isinstance(box, Mapping):
            box_map = cast(Mapping[str, object], box)
            return (
                CVPatternDetector._safe_float(box_map.get("xmin"), 0.0),
                CVPatternDetector._safe_float(box_map.get("ymin"), 0.0),
                CVPatternDetector._safe_float(box_map.get("xmax"), 0.0),
                CVPatternDetector._safe_float(box_map.get("ymax"), 0.0),
            )
        if all(hasattr(box, attr) for attr in ("xmin", "ymin", "xmax", "ymax")):
            return (
                CVPatternDetector._safe_float(getattr(box, "xmin", 0.0), 0.0),
                CVPatternDetector._safe_float(getattr(box, "ymin", 0.0), 0.0),
                CVPatternDetector._safe_float(getattr(box, "xmax", 0.0), 0.0),
                CVPatternDetector._safe_float(getattr(box, "ymax", 0.0), 0.0),
            )
        return None

    def _new_logistic_regression(self, *, max_iter: int, solver: str, class_weight: str, C: float) -> SklearnClassifierLike:
        ctor = LogisticRegModel
        if ctor is None:
            raise RuntimeError("scikit-learn LogisticRegression is unavailable")
        return ctor(max_iter=max_iter, solver=solver, class_weight=class_weight, C=C)

    def _new_standard_scaler(self) -> StandardScalerLike:
        ctor = StandardScalerModel
        if ctor is None:
            raise RuntimeError("scikit-learn StandardScaler is unavailable")
        return ctor()

    def _make_pipeline(self, *steps: object) -> SklearnClassifierLike:
        factory = MakePipelineFn
        if factory is None:
            raise RuntimeError("scikit-learn make_pipeline is unavailable")
        return factory(*steps)

    def _mean_accuracy(self, pred: object, truth: NDArray[np.int32]) -> float:
        pred_arr = np.asarray(pred, dtype=np.int32)
        truth_arr = np.asarray(truth, dtype=np.int32)
        matches = np.equal(pred_arr, truth_arr).astype(np.float32)
        if matches.size == 0:
            return 0.0
        return float(np.sum(matches, dtype=np.float32) / np.float32(matches.size))

    def predict_with_ensemble(self, image: Image.Image) -> dict[str, Any]:
        """Use the ensemble of fine-tuned models for boosted prediction."""
        if self.ensemble_cv is not None:
            return self.ensemble_cv.predict_ensemble(image)
        return {}

    def _extract_memory_features(self, image_rgb: Image.Image | NDArray[np.uint8]) -> NDArray[np.float32]:
        img = image_rgb.convert("RGB") if isinstance(image_rgb, Image.Image) else Image.fromarray(np.asarray(image_rgb).astype(np.uint8))
        arr = np.asarray(img.resize((64, 64), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
        means = arr.mean(axis=(0, 1))
        stds = arr.std(axis=(0, 1))
        flat = arr.reshape(-1)
        feat = np.concatenate([flat, means.astype(np.float32), stds.astype(np.float32)], axis=0)
        return feat.astype(np.float32)

    def _extract_latest_region_features(self, image_rgb: Image.Image | NDArray[np.uint8]) -> NDArray[np.float32]:
        img = image_rgb.convert("RGB") if isinstance(image_rgb, Image.Image) else Image.fromarray(np.asarray(image_rgb).astype(np.uint8))
        arr = np.asarray(img, dtype=np.uint8)
        _height, w = int(arr.shape[0]), int(arr.shape[1])
        x0 = int(w * 0.65)
        crop = arr[:, max(0, x0):w]
        if crop.size == 0:
            crop = arr
        crop_small = np.asarray(
            Image.fromarray(crop).resize((64, 64), Image.Resampling.BILINEAR),
            dtype=np.float32,
        ) / 255.0
        means = crop_small.mean(axis=(0, 1)).astype(np.float32)
        stds = crop_small.std(axis=(0, 1)).astype(np.float32)
        flat = crop_small.reshape(-1).astype(np.float32)
        geom = self._extract_candle_geometry(image_rgb)
        geom_vec = np.array(
            [
                self._safe_float(geom.get("parse_conf"), 0.0),
                self._safe_float(geom.get("body_height_pct"), 0.0),
                self._safe_float(geom.get("upper_wick_pct"), 0.0),
                self._safe_float(geom.get("lower_wick_pct"), 0.0),
                self._safe_float(geom.get("close_pos_in_range"), 0.5),
                self._safe_float(geom.get("candle_color_green"), 0.0),
            ],
            dtype=np.float32,
        )
        seq_vec = self._extract_sequence_features(image_rgb, max_count=10)
        return np.concatenate([flat, means, stds, geom_vec, seq_vec], axis=0).astype(np.float32)

    def _extract_candle_candidates(
        self,
        image_rgb: Image.Image | NDArray[np.uint8],
        max_candidates: int = 16,
    ) -> list[dict[str, float | list[float]]]:
        img = image_rgb.convert("RGB") if isinstance(image_rgb, Image.Image) else Image.fromarray(np.asarray(image_rgb).astype(np.uint8))
        if img.width > 1920 or img.height > 1200:
            img = img.copy()
            img.thumbnail((1920, 1200), Image.Resampling.BILINEAR)
        arr = np.asarray(img, dtype=np.uint8)
        if arr.ndim != 3 or arr.shape[2] < 3:
            return []

        h, w = int(arr.shape[0]), int(arr.shape[1])
        x0 = int(w * 0.08)
        x1 = int(w * 0.92)
        y0 = int(h * 0.04)
        y1 = int(h * 0.96)
        roi = arr[y0:y1, x0:x1]
        if roi.size == 0:
            return []

        r = roi[:, :, 0].astype(np.int16)
        g = roi[:, :, 1].astype(np.int16)
        b = roi[:, :, 2].astype(np.int16)
        green = (g > (r + 16)) & (g > (b + 12)) & (g > 45)
        red = (r > (g + 16)) & (r > (b + 12)) & (r > 45)
        mask = green | red

        col_strength = np.sum(mask, axis=0)
        min_col_pixels = max(4, int(roi.shape[0] * 0.010))
        active = col_strength >= min_col_pixels
        if not np.any(active):
            return []

        segments: list[tuple[int, int]] = []
        start: int | None = None
        for i, v in enumerate(active):
            if bool(v) and start is None:
                start = i
            elif (not bool(v)) and start is not None:
                if i - start >= 1:
                    segments.append((start, i - 1))
                start = None
        if start is not None and len(active) - start >= 1:
            segments.append((start, len(active) - 1))

        if not segments:
            return []

        candidates: list[dict[str, float | list[float]]] = []
        roi_h = max(1, roi.shape[0])
        roi_w = max(1, roi.shape[1])
        for sx, ex in segments:
            width_px = ex - sx + 1
            if width_px > int(roi_w * 0.08):
                continue
            strip = mask[:, sx:ex + 1]
            ys, _ = np.where(strip)
            if ys.size < 5:
                continue
            ymin = int(np.min(ys))
            ymax = int(np.max(ys))
            total_h = max(1, ymax - ymin + 1)
            if total_h > int(roi_h * 0.75):
                # Drop vertical UI/grid artifacts that span almost full height.
                continue

            row_counts = np.sum(strip, axis=1)
            peak = float(np.max(row_counts)) if row_counts.size else 0.0
            dense_rows = np.where(row_counts >= max(2.0, 0.55 * peak))[0]
            if dense_rows.size >= 2:
                body_top = int(np.min(dense_rows))
                body_bottom = int(np.max(dense_rows))
            else:
                body_top = ymin
                body_bottom = ymax

            body_h = max(1, body_bottom - body_top + 1)
            upper_wick = max(0, body_top - ymin)
            lower_wick = max(0, ymax - body_bottom)

            green_count = int(np.sum(green[:, sx:ex + 1]))
            red_count = int(np.sum(red[:, sx:ex + 1]))
            is_green = float(green_count >= red_count)
            close_pos = (
                (body_bottom - ymin) / max(1.0, float(total_h - 1))
                if is_green > 0.5
                else (ymax - body_top) / max(1.0, float(total_h - 1))
            )

            width_score = min(1.0, width_px / max(2.0, roi_w * 0.004))
            height_score = min(1.0, total_h / max(8.0, roi_h * 0.08))
            parse_conf = float(np.clip(0.55 * width_score + 0.45 * height_score, 0.0, 1.0))

            x_l = float(x0 + sx)
            x_r = float(x0 + ex)
            # Ensure candle boxes are visibly wide enough on overlay.
            min_w = max(3.0, float(roi_w) * 0.003)
            if (x_r - x_l + 1.0) < min_w:
                center_x = 0.5 * (x_l + x_r)
                half_w = 0.5 * (min_w - 1.0)
                x_l = max(float(x0), center_x - half_w)
                x_r = min(float(x1 - 1), center_x + half_w)
            y_t = float(y0 + ymin)
            y_b = float(y0 + ymax)
            candidates.append(
                {
                    "parse_conf": parse_conf,
                    "body_height_pct": float(np.clip(body_h / max(1.0, float(total_h)), 0.0, 1.0)),
                    "upper_wick_pct": float(np.clip(upper_wick / max(1.0, float(total_h)), 0.0, 1.0)),
                    "lower_wick_pct": float(np.clip(lower_wick / max(1.0, float(total_h)), 0.0, 1.0)),
                    "close_pos_in_range": float(np.clip(close_pos, 0.0, 1.0)),
                    "candle_color_green": is_green,
                    "bbox": [x_l, y_t, x_r, y_b],
                }
            )

        # Keep right-most candles, which matter most for immediate prediction.
        candidates = sorted(candidates, key=lambda c: float(cast(list[float], c["bbox"])[2]))
        if len(candidates) > max_candidates:
            candidates = candidates[-max_candidates:]
        return candidates

    def _select_recent_candles(
        self,
        candidates: list[dict[str, float | list[float]]],
        max_count: int = 10,
    ) -> list[dict[str, float | list[float]]]:
        if not candidates:
            return []

        clean: list[dict[str, float | list[float]]] = []
        for c in candidates:
            q = self._safe_float(c.get("parse_conf"), 0.0)
            bbox = cast(list[float], c.get("bbox", [0.0, 0.0, 0.0, 0.0]))
            w = float(bbox[2]) - float(bbox[0])
            h = float(bbox[3]) - float(bbox[1])
            if q < 0.10:
                continue
            if w < 2.0 or h < 6.0:
                continue
            clean.append(c)
        if not clean:
            clean = list(candidates)

        # Deduplicate near-identical x centers to avoid stacked duplicates.
        with_centers: list[tuple[float, dict[str, float | list[float]]]] = []
        for c in clean:
            bbox = cast(list[float], c.get("bbox", [0.0, 0.0, 0.0, 0.0]))
            cx = 0.5 * (float(bbox[0]) + float(bbox[2]))
            with_centers.append((cx, c))
        with_centers.sort(key=lambda x: x[0])

        dedup: list[dict[str, float | list[float]]] = []
        for cx, c in with_centers:
            if not dedup:
                dedup.append(c)
                continue
            prev = dedup[-1]
            pb = cast(list[float], prev.get("bbox", [0.0, 0.0, 0.0, 0.0]))
            pcx = 0.5 * (float(pb[0]) + float(pb[2]))
            if abs(cx - pcx) < 3.0:
                if self._safe_float(c.get("parse_conf"), 0.0) > self._safe_float(prev.get("parse_conf"), 0.0):
                    dedup[-1] = c
            else:
                dedup.append(c)

        if len(dedup) <= max_count:
            return dedup

        tail = dedup[-max_count:]
        first_b = cast(list[float], tail[0].get("bbox", [0.0, 0.0, 0.0, 0.0]))
        last_b = cast(list[float], tail[-1].get("bbox", [0.0, 0.0, 0.0, 0.0]))
        span = float(last_b[2]) - float(first_b[0])
        if span < 36.0:
            tail = dedup[-min(len(dedup), max_count + 4):]
        return tail[-max_count:]

    def _extract_sequence_features(self, image_rgb: Image.Image | NDArray[np.uint8], max_count: int = 10) -> NDArray[np.float32]:
        seq = self._select_recent_candles(self._extract_candle_candidates(image_rgb, max_candidates=28), max_count=max_count)
        return self._sequence_stats_from_candles(seq, max_count=max_count)

    def _sequence_stats_from_candles(
        self,
        seq: list[dict[str, float | list[float]]],
        max_count: int = 10,
    ) -> NDArray[np.float32]:
        if not seq:
            return np.zeros((14,), dtype=np.float32)

        body = np.array([self._safe_float(c.get("body_height_pct"), 0.0) for c in seq], dtype=np.float32)
        upw = np.array([self._safe_float(c.get("upper_wick_pct"), 0.0) for c in seq], dtype=np.float32)
        loww = np.array([self._safe_float(c.get("lower_wick_pct"), 0.0) for c in seq], dtype=np.float32)
        closep = np.array([self._safe_float(c.get("close_pos_in_range"), 0.5) for c in seq], dtype=np.float32)
        green = np.array([self._safe_float(c.get("candle_color_green"), 0.0) for c in seq], dtype=np.float32)

        feat = np.array(
            [
                float(body.mean()), float(body.std()),
                float(upw.mean()), float(upw.std()),
                float(loww.mean()), float(loww.std()),
                float(closep.mean()), float(closep.std()),
                float(green.mean()),
                float(np.mean(np.abs(np.diff(green))) if green.size > 1 else 0.0),
                float(np.mean(np.abs(np.diff(body))) if body.size > 1 else 0.0),
                float(np.mean(np.abs(np.diff(closep))) if closep.size > 1 else 0.0),
                float(np.mean(upw - loww)),
                float(len(seq)) / max(1.0, float(max_count)),
            ],
            dtype=np.float32,
        )
        return feat

    def _extract_multiscale_sequence_features(
        self,
        image_rgb: Image.Image | NDArray[np.uint8],
        max_count: int = 10,
    ) -> tuple[NDArray[np.float32], NDArray[np.float32], NDArray[np.float32]]:
        img = image_rgb.convert("RGB") if isinstance(image_rgb, Image.Image) else Image.fromarray(np.asarray(image_rgb).astype(np.uint8))
        w, h = int(img.width), int(img.height)

        # Local scale-in views: progressively tighter right-edge crops.
        local_fracs = (0.55, 0.40, 0.28)
        local_vecs: list[NDArray[np.float32]] = []
        for frac in local_fracs:
            cw = max(24, int(w * frac))
            crop = img.crop((max(0, w - cw), 0, w, h))
            seq = self._select_recent_candles(self._extract_candle_candidates(crop, max_candidates=24), max_count=max_count)
            local_vecs.append(self._sequence_stats_from_candles(seq, max_count=max_count))

        # Macro scale-out views: full + wider context centered crops.
        macro_fracs = (1.00, 0.84, 0.68)
        macro_vecs: list[NDArray[np.float32]] = []
        for frac in macro_fracs:
            cw = max(24, int(w * frac))
            x0 = max(0, (w - cw) // 2)
            crop = img.crop((x0, 0, min(w, x0 + cw), h))
            seq = self._select_recent_candles(self._extract_candle_candidates(crop, max_candidates=30), max_count=max_count)
            macro_vecs.append(self._sequence_stats_from_candles(seq, max_count=max_count))

        local_vec = np.mean(np.stack(local_vecs, axis=0), axis=0).astype(np.float32)
        macro_vec = np.mean(np.stack(macro_vecs, axis=0), axis=0).astype(np.float32)

        # Fuse local and macro behaviors into one directional sequence vector.
        delta = np.abs(local_vec - macro_vec).astype(np.float32)
        fused = np.concatenate(
            [
                local_vec,
                macro_vec,
                delta[[0, 2, 4, 8, 9, 10]],
            ],
            axis=0,
        ).astype(np.float32)
        return local_vec, macro_vec, fused

    def _normalize_memory_rel_path(self, path_value: str) -> str:
        return str(path_value).replace("\\", "/").strip().lower()

    def _taxonomy_from_memory_semantics(
        self,
        *,
        label: str,
        chart_state: dict[str, Any],
        local_vec: NDArray[np.float32],
        macro_vec: NDArray[np.float32],
    ) -> tuple[str, str, str, str]:
        lbl = str(label).strip().upper()
        entry_type = str(chart_state.get("entry_type", "")).strip().lower()
        continuation_signal = str(chart_state.get("continuation_signal", "none")).strip().lower()
        reversal_signal = str(chart_state.get("reversal_signal", "none")).strip().lower()
        momentum_bias = str(chart_state.get("momentum_bias", "neutral")).strip().lower()

        if momentum_bias == "bullish":
            macro_trend = "BULL"
        elif momentum_bias == "bearish":
            macro_trend = "BEAR"
        else:
            macro_trend = "BULL" if lbl == "BUY" else "BEAR"

        with_trend = (macro_trend == "BULL" and lbl == "BUY") or (macro_trend == "BEAR" and lbl == "SELL")
        local_body = float(local_vec[0]) if int(local_vec.size) > 0 else 0.0
        local_chop = float(local_vec[9]) if int(local_vec.size) > 9 else 0.0
        local_vol = float(local_vec[10]) if int(local_vec.size) > 10 else 0.0
        wick_imbalance = abs(float(local_vec[2] - local_vec[4])) if int(local_vec.size) > 4 else 0.0
        macro_flip = float(macro_vec[9]) if int(macro_vec.size) > 9 else 0.0

        continuation_like = (entry_type == "continuation") or (continuation_signal in {"impulse_pause", "breakout"})
        reversal_like = (entry_type == "reversal") or (reversal_signal in {"wick_rejection", "engulfing"})

        if continuation_like and with_trend:
            local_phase = "with_trend_push" if local_body >= 0.26 and local_vol >= 0.08 else "with_trend_pause"
        elif continuation_like and (not with_trend):
            local_phase = "counter_trend_spike" if local_body >= 0.30 else "counter_trend_pullback"
        elif reversal_like and (not with_trend):
            local_phase = "reversal_base" if wick_imbalance >= 0.07 else "counter_trend_spike"
        elif reversal_like and with_trend:
            local_phase = "counter_trend_pullback" if macro_flip >= 0.16 else "continuation_base"
        elif with_trend:
            local_phase = "with_trend_pause" if local_chop >= 0.22 else "continuation_base"
        else:
            local_phase = "counter_trend_pullback"

        if local_phase in {"counter_trend_spike", "reversal_base"}:
            phase_risk = "exhaustion_risk"
            intent_next = "reversal_attempt"
        elif local_phase in {"with_trend_push", "continuation_base"}:
            phase_risk = "breakout_risk"
            intent_next = "continue"
        elif local_phase == "counter_trend_pullback":
            phase_risk = "chop_risk"
            intent_next = "pullback"
        else:
            phase_risk = "chop_risk"
            intent_next = "fakeout"

        return macro_trend, local_phase, phase_risk, intent_next

    def _extract_candle_geometry(self, image_rgb: Image.Image | NDArray[np.uint8]) -> dict[str, float | list[float]]:
        candidates = self._select_recent_candles(
            self._extract_candle_candidates(image_rgb, max_candidates=20),
            max_count=10,
        )
        if not candidates:
            return {
                "parse_conf": 0.0,
                "body_height_pct": 0.0,
                "upper_wick_pct": 0.0,
                "lower_wick_pct": 0.0,
                "close_pos_in_range": 0.5,
                "candle_color_green": 0.0,
                "bbox": [0.0, 0.0, 0.0, 0.0],
            }
        return candidates[-1]

    def _memory_model_path(self) -> Path:
        return PROJECT_ROOT / "models" / "cv_memory_direction.pkl"

    def _iter_image_files(self, root: Path) -> list[Path]:
        valid = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}
        return sorted([p for p in root.rglob("*") if p.is_file() and p.suffix.lower() in valid])

    def _augment_training_views(self, img: Image.Image) -> list[Image.Image]:
        # Keep augmentations deterministic and geometry-safe for candle charts.
        base = img.convert("RGB")
        views: list[Image.Image] = [base]
        arr = np.asarray(base, dtype=np.uint8)
        h, w = int(arr.shape[0]), int(arr.shape[1])
        if h > 40 and w > 40:
            x0 = int(w * 0.12)
            x1 = int(w * 0.96)
            if x1 - x0 > 24:
                views.append(base.crop((x0, 0, x1, h)).resize((w, h), Image.Resampling.BILINEAR))
        views.append(ImageEnhance.Contrast(base).enhance(1.08))
        return views[:3]

    def _fit_best_logistic(
        self,
        X_train: NDArray[np.float32],
        y_train: NDArray[np.int32],
        X_val: NDArray[np.float32],
        y_val: NDArray[np.int32],
        max_iter: int = 1200,
    ) -> tuple[SklearnClassifierLike, float]:
        c_grid = [0.5, 1.0, 2.0]
        best_acc = -1.0
        best_clf: SklearnClassifierLike | None = None
        for c_val in c_grid:
            clf = self._new_logistic_regression(max_iter=max_iter, solver="liblinear", class_weight="balanced", C=float(c_val))
            clf.fit(X_train, y_train)
            acc = self._mean_accuracy(clf.predict(X_val), y_val)
            if acc > best_acc:
                best_acc = acc
                best_clf = clf
        if best_clf is None:
            best_clf = self._new_logistic_regression(max_iter=max_iter, solver="liblinear", class_weight="balanced", C=1.0)
            best_clf.fit(X_train, y_train)
            best_acc = self._mean_accuracy(best_clf.predict(X_val), y_val)
        return best_clf, best_acc

    def _fit_multiclass_logistic(
        self,
        X_train: NDArray[np.float32],
        y_train: NDArray[np.int32],
        X_val: NDArray[np.float32],
        y_val: NDArray[np.int32],
        max_iter: int = 1200,
        use_resample: bool = True,
    ) -> tuple[SklearnClassifierLike | None, float]:
        if int(np.unique(y_train).size) < 2:
            return None, 1.0
        Xb, yb = (X_train, y_train)
        if use_resample:
            Xb, yb = self._balanced_multiclass_resample(X_train, y_train, random_state=808)
        c_grid = [0.35, 0.7, 1.0, 1.6, 2.4]
        best_acc = -1.0
        best_clf: SklearnClassifierLike | None = None
        for c_val in c_grid:
            clf = self._new_logistic_regression(max_iter=max_iter + 400, solver="lbfgs", class_weight="balanced", C=float(c_val))
            clf.fit(Xb, yb)
            acc = self._mean_accuracy(clf.predict(X_val), y_val)
            if acc > best_acc:
                best_acc = acc
                best_clf = clf
        return best_clf, max(0.0, best_acc)

    def _balanced_multiclass_resample(
        self,
        X: NDArray[np.float32],
        y: NDArray[np.int32],
        random_state: int = 808,
    ) -> tuple[NDArray[np.float32], NDArray[np.int32]]:
        uniq, counts = np.unique(y, return_counts=True)
        if int(uniq.size) < 2:
            return X, y
        if int(np.min(counts)) == int(np.max(counts)):
            return X, y

        rng = np.random.default_rng(random_state)
        median_count = float(np.median(counts))
        max_count = int(np.max(counts))
        target = int(min(max_count, max(72, int(round(median_count * 3.0)))))

        x_parts: list[NDArray[np.float32]] = []
        y_parts: list[NDArray[np.int32]] = []
        for cls in uniq:
            idx = np.where(y == int(cls))[0]
            if idx.size == 0:
                continue
            if int(idx.size) >= target:
                chosen = rng.choice(idx, size=target, replace=False)
            else:
                chosen = rng.choice(idx, size=target, replace=True)
            x_parts.append(X[chosen])
            y_parts.append(y[chosen])

        if not x_parts:
            return X, y

        X_out = np.concatenate(x_parts, axis=0).astype(np.float32)
        y_out = np.concatenate(y_parts, axis=0).astype(np.int32)
        perm = rng.permutation(X_out.shape[0])
        return X_out[perm], y_out[perm]

    def _safe_train_val_split(
        self,
        X: NDArray[np.float32],
        y: NDArray[np.int32],
        test_size: float = 0.2,
        random_state: int = 808,
    ) -> tuple[NDArray[np.float32], NDArray[np.float32], NDArray[np.int32], NDArray[np.int32]]:
        if train_test_split_fn is None:
            n = int(X.shape[0])
            cut = max(1, int(n * (1.0 - test_size)))
            return X[:cut], X[cut:], y[:cut], y[cut:]
        uniq, counts = np.unique(y, return_counts=True)
        can_stratify = bool(uniq.size >= 2 and counts.size > 0 and int(np.min(counts)) >= 2)
        if can_stratify:
            return train_test_split_fn(X, y, test_size=test_size, random_state=random_state, stratify=y)
        return train_test_split_fn(X, y, test_size=test_size, random_state=random_state, stratify=None)

    def _encode_labels(self, values: list[str]) -> tuple[NDArray[np.int32], dict[str, int], dict[int, str]]:
        uniq = sorted(set(values))
        to_idx = {v: i for i, v in enumerate(uniq)}
        inv = {i: v for v, i in to_idx.items()}
        y = np.array([to_idx[v] for v in values], dtype=np.int32)
        return y, to_idx, inv

    def _pseudo_taxonomy_from_geom(
        self,
        geom: dict[str, float | list[float]],
        seq_feat: NDArray[np.float32],
        cls_label: int,
    ) -> tuple[str, str, str, str]:
        macro_trend = "BULL" if int(cls_label) == 1 else "BEAR"
        body = self._safe_float(geom.get("body_height_pct"), 0.0)
        upper = self._safe_float(geom.get("upper_wick_pct"), 0.0)
        lower = self._safe_float(geom.get("lower_wick_pct"), 0.0)
        parse_q = self._safe_float(geom.get("parse_conf"), 0.0)
        seq_chop = float(seq_feat[9]) if int(seq_feat.size) > 9 else 0.0
        seq_vol = float(seq_feat[10]) if int(seq_feat.size) > 10 else 0.0

        wick_imbalance = abs(upper - lower)
        strong_body = body >= 0.32
        pause_body = body <= 0.20
        reversal_shape = wick_imbalance >= 0.28 and body <= 0.34

        if strong_body and parse_q >= 0.45 and seq_vol >= 0.12:
            local_phase = "with_trend_push"
        elif pause_body and seq_chop >= 0.22:
            local_phase = "with_trend_pause"
        elif reversal_shape and parse_q >= 0.35:
            local_phase = "reversal_base"
        elif (not strong_body) and seq_chop < 0.18:
            local_phase = "counter_trend_pullback"
        elif strong_body and seq_chop < 0.16:
            local_phase = "counter_trend_spike"
        else:
            local_phase = "continuation_base"

        if local_phase in {"counter_trend_spike", "reversal_base"}:
            phase_risk = "exhaustion_risk"
        elif local_phase in {"with_trend_push", "continuation_base"}:
            phase_risk = "breakout_risk"
        else:
            phase_risk = "chop_risk"

        if local_phase in {"with_trend_push", "continuation_base"}:
            intent_next = "continue"
        elif local_phase == "counter_trend_pullback":
            intent_next = "pullback"
        elif local_phase in {"reversal_base", "counter_trend_spike"}:
            intent_next = "reversal_attempt"
        else:
            intent_next = "fakeout"

        return macro_trend, local_phase, phase_risk, intent_next

    def _clear_memory_classifier_state(self) -> None:
        self.memory_clf = None
        self.latest_dir_clf = None
        self.next_dir_clf = None
        self.wick_dom_clf = None
        self.move_bucket_clf = None
        self.seq_dir_clf = None
        self.macro_trend_clf = None
        self.local_phase_clf = None
        self.phase_risk_clf = None
        self.intent_next_clf = None
        self.global_scaler = None
        self.latest_scaler = None
        self.seq_scaler = None
        self.local_scaler = None
        self.macro_scaler = None
        self.intent_scaler = None

    def _load_or_train_memory_classifier(self) -> None:
        if (
            LogisticRegModel is None
            or train_test_split_fn is None
            or StandardScalerModel is None
            or MakePipelineFn is None
        ):
            self.logger.warning("scikit-learn unavailable; memory CV fine-tune skipped")
            return

        model_path = self._memory_model_path()
        model_path.parent.mkdir(parents=True, exist_ok=True)

        runtime_train_allowed = os.getenv("PHOENIXGUARD_CV_ALLOW_RUNTIME_TRAIN", "0").strip() == "1"
        force_retrain = runtime_train_allowed and os.getenv("PHOENIXGUARD_CV_RETRAIN_ON_START", "0").strip() == "1"
        if model_path.exists() and not force_retrain:
            try:
                with open(model_path, "rb") as f:
                    payload = pickle.load(f)
                self.memory_clf = payload.get("clf")
                self.latest_dir_clf = payload.get("latest_dir_clf")
                self.next_dir_clf = payload.get("next_dir_clf")
                self.wick_dom_clf = payload.get("wick_dom_clf")
                self.move_bucket_clf = payload.get("move_bucket_clf")
                self.seq_dir_clf = payload.get("seq_dir_clf")
                self.macro_trend_clf = payload.get("macro_trend_clf")
                self.local_phase_clf = payload.get("local_phase_clf")
                self.phase_risk_clf = payload.get("phase_risk_clf")
                self.intent_next_clf = payload.get("intent_next_clf")
                self.global_scaler = payload.get("global_scaler")
                self.latest_scaler = payload.get("latest_scaler")
                self.seq_scaler = payload.get("seq_scaler")
                self.local_scaler = payload.get("local_scaler")
                self.macro_scaler = payload.get("macro_scaler")
                self.intent_scaler = payload.get("intent_scaler")
                payload_map = cast(Mapping[str, object], payload)
                self.memory_clf_meta = dict(cast(Mapping[str, Any], payload_map.get("meta", {})))
                self.taxonomy_label_maps = dict(cast(Mapping[str, dict[int, str]], payload_map.get("taxonomy_label_maps", {})))

                # Re-train automatically if feature schema changed.
                try:
                    probe_img = Image.new("RGB", (256, 256), color=(0, 0, 0))
                    probe_feat_dim = int(self._extract_latest_region_features(probe_img).shape[0])
                    probe_local, probe_macro, probe_seq = self._extract_multiscale_sequence_features(probe_img, max_count=10)
                    probe_seq_dim = int(probe_seq.shape[0])
                    probe_local_dim = int(probe_local.shape[0])
                    probe_macro_dim = int(probe_macro.shape[0] + probe_seq.shape[0])
                    probe_intent_dim = int(probe_local.shape[0] + probe_seq.shape[0])
                    latest_width = self._coef_feature_width(self.latest_dir_clf)
                    if latest_width is not None:
                        seq_width = self._coef_feature_width(self.seq_dir_clf)
                        local_width = self._coef_feature_width(self.local_phase_clf)
                        macro_width = self._coef_feature_width(self.macro_trend_clf)
                        intent_width = self._coef_feature_width(self.intent_next_clf)
                        seq_bad = seq_width is not None and seq_width != probe_seq_dim
                        local_bad = local_width is not None and local_width != probe_local_dim
                        macro_bad = macro_width is not None and macro_width != probe_macro_dim
                        intent_bad = intent_width is not None and intent_width != probe_intent_dim

                        if latest_width != probe_feat_dim or seq_bad or local_bad or macro_bad or intent_bad:
                            self._clear_memory_classifier_state()
                            if not runtime_train_allowed:
                                self.logger.warning(
                                    "Loaded CV specialist feature dim mismatch detected (latest=%d/%d seq=%d local=%d macro=%d intent=%d). Runtime CV retrain is disabled; continuing with the saved YOLO path only.",
                                    latest_width,
                                    probe_feat_dim,
                                    probe_seq_dim,
                                    probe_local_dim,
                                    probe_macro_dim,
                                    probe_intent_dim,
                                )
                                return
                            self.logger.warning(
                                "Loaded CV specialist feature dim mismatch detected (latest=%d/%d seq=%d local=%d macro=%d intent=%d). Retraining.",
                                latest_width,
                                probe_feat_dim,
                                probe_seq_dim,
                                probe_local_dim,
                                probe_macro_dim,
                                probe_intent_dim,
                            )
                        else:
                            self.logger.info("Loaded memory CV classifier from %s", model_path)
                            return
                    else:
                        self.logger.info("Loaded memory CV classifier from %s", model_path)
                        return
                except Exception:
                    self.logger.info("Loaded memory CV classifier from %s", model_path)
                    return
            except Exception as e:
                if not runtime_train_allowed:
                    self.logger.warning(
                        "Loading memory CV classifier failed (%s); runtime retrain is disabled, so the app will continue without the saved specialist classifier.",
                        e,
                    )
                    self._clear_memory_classifier_state()
                    return
                self.logger.warning("Loading memory CV classifier failed (%s); retraining", e)

        if not runtime_train_allowed:
            if not model_path.exists():
                self.logger.warning(
                    "Runtime CV fine-tune is disabled and no saved classifier exists at %s; using the YOLO-only path.",
                    model_path,
                )
            return

        try:
            from phoenixguard.core.config import MEMORY_BANK, RUNTIME
            buy_dir = RUNTIME.project_root / MEMORY_BANK.buys_dir
            sell_dir = RUNTIME.project_root / MEMORY_BANK.sells_dir
            buy_files = self._iter_image_files(buy_dir)
            sell_files = self._iter_image_files(sell_dir)
            if not buy_files or not sell_files:
                self.logger.warning("Memory CV fine-tune skipped: missing BUY/SELL files")
                return

            X_global_list: list[NDArray[np.float32]] = []
            X_latest_list: list[NDArray[np.float32]] = []
            X_seq_list: list[NDArray[np.float32]] = []
            X_local_list: list[NDArray[np.float32]] = []
            X_macro_list: list[NDArray[np.float32]] = []
            y_list: list[int] = []
            y_wick: list[int] = []
            y_move: list[int] = []
            y_macro_s: list[str] = []
            y_local_s: list[str] = []
            y_risk_s: list[str] = []
            y_intent_s: list[str] = []
            parse_conf_buy: list[float] = []
            parse_conf_sell: list[float] = []

            metadata_path = RUNTIME.memory_bank_dir / "metadata.json"
            if not metadata_path.exists():
                self.logger.warning("Memory CV fine-tune skipped: metadata file missing at %s", metadata_path)
                return
            with open(metadata_path, "r", encoding="utf-8") as mf:
                metadata_raw = cast(list[dict[str, Any]], json.load(mf))
            metadata_map: dict[str, dict[str, Any]] = {}
            for item in metadata_raw:
                raw_path = str(item.get("image_path", ""))
                key = self._normalize_memory_rel_path(raw_path)
                if key:
                    metadata_map[key] = item

            skipped_missing_meta = 0

            def _pseudo_labels_from_geom(geom: dict[str, float | list[float]], cls_label: int) -> tuple[int, int]:
                upper = CVPatternDetector._safe_float(geom.get("upper_wick_pct"), 0.0)
                lower = CVPatternDetector._safe_float(geom.get("lower_wick_pct"), 0.0)
                body = CVPatternDetector._safe_float(geom.get("body_height_pct"), 0.0)
                wick_label = 1 if lower >= upper else 0
                if upper == 0.0 and lower == 0.0:
                    wick_label = 1 if cls_label == 1 else 0
                if body < 0.18:
                    move_bucket = 0
                elif body < 0.40:
                    move_bucket = 1
                else:
                    move_bucket = 2
                return wick_label, move_bucket

            for p in buy_files:
                try:
                    rel_key = self._normalize_memory_rel_path(str(p.relative_to(RUNTIME.project_root)))
                    md = metadata_map.get(rel_key)
                    if md is None:
                        skipped_missing_meta += 1
                        continue
                    chart_state = cast(dict[str, Any], md.get("chart_state") or md.get("vlm_json", {}))
                    img = Image.open(p).convert("RGB")
                    for v in self._augment_training_views(img):
                        geom = self._extract_candle_geometry(v)
                        local_vec, macro_vec, seq_vec = self._extract_multiscale_sequence_features(v, max_count=10)
                        X_global_list.append(self._extract_memory_features(v))
                        X_latest_list.append(self._extract_latest_region_features(v))
                        X_seq_list.append(seq_vec)
                        X_local_list.append(local_vec)
                        X_macro_list.append(macro_vec)
                        y_list.append(1)
                        w_lbl, m_lbl = _pseudo_labels_from_geom(geom, cls_label=1)
                        y_wick.append(w_lbl)
                        y_move.append(m_lbl)
                        macro_s, local_s, risk_s, intent_s = self._taxonomy_from_memory_semantics(
                            label="BUY",
                            chart_state=chart_state,
                            local_vec=local_vec,
                            macro_vec=macro_vec,
                        )
                        y_macro_s.append(macro_s)
                        y_local_s.append(local_s)
                        y_risk_s.append(risk_s)
                        y_intent_s.append(intent_s)
                        parse_conf_buy.append(CVPatternDetector._safe_float(geom.get("parse_conf"), 0.0))
                except Exception:
                    continue
            for p in sell_files:
                try:
                    rel_key = self._normalize_memory_rel_path(str(p.relative_to(RUNTIME.project_root)))
                    md = metadata_map.get(rel_key)
                    if md is None:
                        skipped_missing_meta += 1
                        continue
                    chart_state = cast(dict[str, Any], md.get("chart_state") or md.get("vlm_json", {}))
                    img = Image.open(p).convert("RGB")
                    for v in self._augment_training_views(img):
                        geom = self._extract_candle_geometry(v)
                        local_vec, macro_vec, seq_vec = self._extract_multiscale_sequence_features(v, max_count=10)
                        X_global_list.append(self._extract_memory_features(v))
                        X_latest_list.append(self._extract_latest_region_features(v))
                        X_seq_list.append(seq_vec)
                        X_local_list.append(local_vec)
                        X_macro_list.append(macro_vec)
                        y_list.append(0)
                        w_lbl, m_lbl = _pseudo_labels_from_geom(geom, cls_label=0)
                        y_wick.append(w_lbl)
                        y_move.append(m_lbl)
                        macro_s, local_s, risk_s, intent_s = self._taxonomy_from_memory_semantics(
                            label="SELL",
                            chart_state=chart_state,
                            local_vec=local_vec,
                            macro_vec=macro_vec,
                        )
                        y_macro_s.append(macro_s)
                        y_local_s.append(local_s)
                        y_risk_s.append(risk_s)
                        y_intent_s.append(intent_s)
                        parse_conf_sell.append(CVPatternDetector._safe_float(geom.get("parse_conf"), 0.0))
                except Exception:
                    continue

            if len(X_global_list) < 20:
                self.logger.warning("Memory CV fine-tune skipped: insufficient samples (%d)", len(X_global_list))
                return

            X_global = np.stack(X_global_list, axis=0).astype(np.float32)
            X_latest = np.stack(X_latest_list, axis=0).astype(np.float32)
            X_seq = np.stack(X_seq_list, axis=0).astype(np.float32)
            X_local = np.stack(X_local_list, axis=0).astype(np.float32)
            X_macro = np.stack(X_macro_list, axis=0).astype(np.float32)
            X_macro_feat = np.concatenate([X_macro, X_seq], axis=1).astype(np.float32)
            X_intent_feat = np.concatenate([X_local, X_seq], axis=1).astype(np.float32)
            y = np.array(y_list, dtype=np.int32)
            y_w = np.array(y_wick, dtype=np.int32)
            y_m = np.array(y_move, dtype=np.int32)
            y_macro, _macro_map, inv_macro = self._encode_labels(y_macro_s)
            y_local, _local_map, inv_local = self._encode_labels(y_local_s)
            y_risk, _risk_map, inv_risk = self._encode_labels(y_risk_s)
            y_intent, _intent_map, inv_intent = self._encode_labels(y_intent_s)

            Xg_train, Xg_val, y_train, y_val = self._safe_train_val_split(X_global, y, test_size=0.2, random_state=808)
            Xl_train, Xl_val, _y_train_2, _y_val_2 = self._safe_train_val_split(X_latest, y, test_size=0.2, random_state=808)
            Xs_train, Xs_val, _ys_train_2, _ys_val_2 = self._safe_train_val_split(X_seq, y, test_size=0.2, random_state=808)
            Xl_train_w, Xl_val_w, y_w_train, y_w_val = self._safe_train_val_split(X_latest, y_w, test_size=0.2, random_state=808)
            Xl_train_m, Xl_val_m, y_m_train, y_m_val = self._safe_train_val_split(X_latest, y_m, test_size=0.2, random_state=808)
            Xma_train, Xma_val, y_ma_train, y_ma_val = self._safe_train_val_split(X_macro_feat, y_macro, test_size=0.2, random_state=808)
            Xlo_train, Xlo_val, y_lo_train, y_lo_val = self._safe_train_val_split(X_local, y_local, test_size=0.2, random_state=808)
            Xlo_train_ri, Xlo_val_ri, y_ri_train, y_ri_val = self._safe_train_val_split(X_local, y_risk, test_size=0.2, random_state=808)
            Xin_train, Xin_val, y_in_train, y_in_val = self._safe_train_val_split(X_intent_feat, y_intent, test_size=0.2, random_state=808)

            global_scaler = self._new_standard_scaler()
            latest_scaler = self._new_standard_scaler()
            seq_scaler = self._new_standard_scaler()
            local_scaler = self._new_standard_scaler()
            macro_scaler = self._new_standard_scaler()
            intent_scaler = self._new_standard_scaler()
            Xg_train_s = global_scaler.fit_transform(Xg_train).astype(np.float32)
            Xg_val_s = global_scaler.transform(Xg_val).astype(np.float32)
            Xl_train_s = latest_scaler.fit_transform(Xl_train).astype(np.float32)
            Xl_val_s = latest_scaler.transform(Xl_val).astype(np.float32)
            Xl_train_w_s = latest_scaler.transform(Xl_train_w).astype(np.float32)
            Xl_val_w_s = latest_scaler.transform(Xl_val_w).astype(np.float32)
            Xl_train_m_s = latest_scaler.transform(Xl_train_m).astype(np.float32)
            Xl_val_m_s = latest_scaler.transform(Xl_val_m).astype(np.float32)
            Xs_train_s = seq_scaler.fit_transform(Xs_train).astype(np.float32)
            Xs_val_s = seq_scaler.transform(Xs_val).astype(np.float32)
            Xma_train_s = macro_scaler.fit_transform(Xma_train).astype(np.float32)
            Xma_val_s = macro_scaler.transform(Xma_val).astype(np.float32)
            Xlo_train_s = local_scaler.fit_transform(Xlo_train).astype(np.float32)
            Xlo_val_s = local_scaler.transform(Xlo_val).astype(np.float32)
            Xlo_train_ri_s = local_scaler.transform(Xlo_train_ri).astype(np.float32)
            Xlo_val_ri_s = local_scaler.transform(Xlo_val_ri).astype(np.float32)
            Xin_train_s = intent_scaler.fit_transform(Xin_train).astype(np.float32)
            Xin_val_s = intent_scaler.transform(Xin_val).astype(np.float32)

            clf_core, val_acc = self._fit_best_logistic(Xg_train_s, y_train, Xg_val_s, y_val, max_iter=1200)
            latest_clf, latest_acc = self._fit_best_logistic(Xl_train_s, y_train, Xl_val_s, y_val, max_iter=1200)
            next_dir_clf, next_acc = self._fit_best_logistic(Xl_train_s, y_train, Xl_val_s, y_val, max_iter=1200)
            seq_dir_clf, seq_acc = self._fit_best_logistic(Xs_train_s, y_train, Xs_val_s, y_val, max_iter=1200)
            macro_clf, macro_acc = self._fit_multiclass_logistic(Xma_train_s, y_ma_train, Xma_val_s, y_ma_val, max_iter=1200, use_resample=False)
            local_clf, local_acc = self._fit_multiclass_logistic(Xlo_train_s, y_lo_train, Xlo_val_s, y_lo_val, max_iter=1200)
            risk_clf, risk_acc = self._fit_multiclass_logistic(Xlo_train_ri_s, y_ri_train, Xlo_val_ri_s, y_ri_val, max_iter=1200)
            intent_clf, intent_acc = self._fit_multiclass_logistic(Xin_train_s, y_in_train, Xin_val_s, y_in_val, max_iter=1200)
            wick_clf = self._new_logistic_regression(max_iter=1200, solver="liblinear", class_weight="balanced", C=1.0)
            wick_clf.fit(Xl_train_w_s, y_w_train)
            move_clf = self._new_logistic_regression(max_iter=1200, solver="lbfgs", class_weight="balanced", C=1.2)
            move_clf.fit(Xl_train_m_s, y_m_train)

            global_c = float(getattr(clf_core, "C", 1.0))
            clf = self._make_pipeline(
                self._new_standard_scaler(),
                self._new_logistic_regression(max_iter=1200, solver="liblinear", class_weight="balanced", C=global_c),
            )
            clf.fit(Xg_train, y_train)

            wick_acc = self._mean_accuracy(wick_clf.predict(Xl_val_w_s), y_w_val)
            move_acc = self._mean_accuracy(move_clf.predict(Xl_val_m_s), y_m_val)

            latest_val_proba = self._as_float32_array(latest_clf.predict_proba(Xl_val_s))
            seq_val_proba = self._as_float32_array(seq_dir_clf.predict_proba(Xs_val_s))
            latest_margin = np.abs(latest_val_proba[:, 1] - latest_val_proba[:, 0])
            seq_margin = np.abs(seq_val_proba[:, 1] - seq_val_proba[:, 0])
            margin_min = float(np.clip(np.quantile(0.6 * latest_margin + 0.4 * seq_margin, 0.20), 0.10, 0.35))
            prob_min = float(np.clip(np.quantile(np.maximum(latest_val_proba[:, 0], latest_val_proba[:, 1]), 0.20), 0.56, 0.72))

            self.memory_clf = clf
            self.latest_dir_clf = latest_clf
            self.next_dir_clf = next_dir_clf
            self.wick_dom_clf = wick_clf
            self.move_bucket_clf = move_clf
            self.seq_dir_clf = seq_dir_clf
            self.macro_trend_clf = macro_clf
            self.local_phase_clf = local_clf
            self.phase_risk_clf = risk_clf
            self.intent_next_clf = intent_clf
            self.global_scaler = global_scaler
            self.latest_scaler = latest_scaler
            self.seq_scaler = seq_scaler
            self.local_scaler = local_scaler
            self.macro_scaler = macro_scaler
            self.intent_scaler = intent_scaler
            self.taxonomy_label_maps = {
                "macro_trend": inv_macro,
                "local_phase": inv_local,
                "phase_risk": inv_risk,
                "intent_next": inv_intent,
            }
            self.memory_clf_meta = {
                "val_acc": val_acc,
                "latest_dir_val_acc": latest_acc,
                "next_dir_val_acc": next_acc,
                "seq_dir_val_acc": seq_acc,
                "macro_trend_val_acc": macro_acc,
                "local_phase_val_acc": local_acc,
                "phase_risk_val_acc": risk_acc,
                "intent_next_val_acc": intent_acc,
                "wick_val_acc": wick_acc,
                "move_bucket_val_acc": move_acc,
                "latest_margin_min": margin_min,
                "latest_prob_min": prob_min,
                "buy_parse_mean": float(np.mean(np.array(parse_conf_buy, dtype=np.float32))) if parse_conf_buy else 0.0,
                "sell_parse_mean": float(np.mean(np.array(parse_conf_sell, dtype=np.float32))) if parse_conf_sell else 0.0,
                "n_train": int(Xg_train.shape[0]),
                "n_val": int(Xg_val.shape[0]),
                "n_total": int(X_global.shape[0]),
                "n_buy_files": int(len(buy_files)),
                "n_sell_files": int(len(sell_files)),
                "n_missing_metadata": int(skipped_missing_meta),
            }

            with open(model_path, "wb") as f:
                pickle.dump(
                    {
                        "clf": clf,
                        "latest_dir_clf": latest_clf,
                        "next_dir_clf": next_dir_clf,
                        "wick_dom_clf": wick_clf,
                        "move_bucket_clf": move_clf,
                        "seq_dir_clf": seq_dir_clf,
                        "macro_trend_clf": macro_clf,
                        "local_phase_clf": local_clf,
                        "phase_risk_clf": risk_clf,
                        "intent_next_clf": intent_clf,
                        "global_scaler": global_scaler,
                        "latest_scaler": latest_scaler,
                        "seq_scaler": seq_scaler,
                        "local_scaler": local_scaler,
                        "macro_scaler": macro_scaler,
                        "intent_scaler": intent_scaler,
                        "taxonomy_label_maps": self.taxonomy_label_maps,
                        "meta": self.memory_clf_meta,
                    },
                    f,
                )

            self.logger.info(
                "Memory CV fine-tuned: global=%.3f latest=%.3f next=%.3f seq=%.3f macro=%.3f local=%.3f risk=%.3f intent=%.3f wick=%.3f move=%.3f samples=%d buy_files=%d sell_files=%d missing_meta=%d margin_min=%.3f prob_min=%.3f saved=%s",
                val_acc,
                latest_acc,
                next_acc,
                seq_acc,
                macro_acc,
                local_acc,
                risk_acc,
                intent_acc,
                wick_acc,
                move_acc,
                int(X_global.shape[0]),
                int(len(buy_files)),
                int(len(sell_files)),
                int(skipped_missing_meta),
                margin_min,
                prob_min,
                model_path,
            )
        except Exception as e:
            self.logger.warning("Memory CV fine-tune failed: %s", e)

    def _try_load_hf_yolo_weights(self, model_ref: str) -> bool:
        """
        Preferred path for hf:// YOLO references:
        load cached .pt weights first and only touch the network when explicitly enabled.
        """
        if not model_ref.startswith("hf://"):
            return False
        if YOLOModel is None:
            self.logger.warning("Ultralytics not available; cannot run local CV inference")
            return False
        if not can_import_torchvision_safely():
            self.logger.warning("torchvision runtime probe failed; skipping local YOLO backend")
            return False
        if hf_hub_download is None:
            self.logger.warning("huggingface_hub.hf_hub_download unavailable; cannot pull HF YOLO weights")
            return False

        model_id = model_ref.replace("hf://", "", 1)
        token = os.getenv("HF_TOKEN", "").strip() or None
        weight_file = os.getenv("PHOENIXGUARD_CV_HF_WEIGHT_FILE", "model.pt").strip() or "model.pt"
        force_dl = os.getenv("PHOENIXGUARD_CV_FORCE_DOWNLOAD", "0").strip() == "1"
        allow_remote_bootstrap = force_dl or (
            str(os.getenv("PHOENIXGUARD_CV_ALLOW_REMOTE_BOOTSTRAP", "") or "").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        try:
            etag_timeout = float(
                str(os.getenv("PHOENIXGUARD_CV_HF_ETAG_TIMEOUT_SEC", "2.0") or "2.0").strip()
            )
        except Exception:
            etag_timeout = 2.0
        download_fn = hf_hub_download
        yolo_model_cls = YOLOModel
        if yolo_model_cls is None:
            self.logger.warning("ultralytics.YOLO unavailable after dependency guard")
            return False

        def _load_from_downloaded_path(*, local_files_only: bool) -> bool:
            local_path = download_fn(
                repo_id=model_id,
                filename=weight_file,
                token=token,
                force_download=force_dl and (not local_files_only),
                etag_timeout=max(0.1, float(etag_timeout)),
                local_files_only=local_files_only,
            )
            self.model = yolo_model_cls(local_path)
            self.model_name = model_ref
            self.use_hf_endpoint = False
            self.hf_model_id = model_id
            return True

        try:
            if _load_from_downloaded_path(local_files_only=True):
                self.logger.info("Loaded cached CV model weights from HF cache: %s (%s)", model_id, weight_file)
                return True
        except Exception as e:
            if not allow_remote_bootstrap:
                self.logger.warning(
                    "HF YOLO cache miss for %s (%s). Remote bootstrap is disabled; set PHOENIXGUARD_CV_ALLOW_REMOTE_BOOTSTRAP=1 or PHOENIXGUARD_CV_FORCE_DOWNLOAD=1 to fetch weights.",
                    model_id,
                    e,
                )
                return False
            self.logger.info("Cached HF YOLO weights unavailable for %s; attempting remote bootstrap.", model_id)

        try:
            if _load_from_downloaded_path(local_files_only=False):
                self.logger.info("Loaded CV model weights from HF: %s (%s)", model_id, weight_file)
                return True
        except Exception as e:
            self.logger.warning("HF YOLO weight download/load failed for %s (%s)", model_id, e)
            return False
        return False

    def _try_enable_hf_endpoint(self, model_ref: str) -> bool:
        if not model_ref.startswith("hf://"):
            return False
        model_id = model_ref.replace("hf://", "", 1)
        token = os.getenv("HF_TOKEN", "").strip() or None
        custom_url = os.getenv("PHOENIXGUARD_CV_REMOTE_URL", "").strip()
        try:
            if HfApi is not None:
                HfApi(token=token).model_info(model_id)
            self.hf_remote_url = custom_url or f"https://api-inference.huggingface.co/models/{model_id}"
            self.use_hf_endpoint = True
            self.hf_model_id = model_id
            self.model_name = model_ref
            self.logger.info("Using remote CV inference API: model=%s url=%s", model_id, self.hf_remote_url)
            return True
        except Exception as e:
            self.logger.warning("Remote CV init failed (%s)", e)
            return False

    def _raw_detect_hf(self, image_rgb: Image.Image | NDArray[np.uint8]) -> list[dict[str, Any]]:
        if not self.use_hf_endpoint or not self.hf_model_id or not self.hf_remote_url:
            return []
        try:
            if isinstance(image_rgb, Image.Image):
                buffer = io.BytesIO()
                image_rgb.save(buffer, format="PNG")
                image_bytes = buffer.getvalue()
            else:
                arr = np.asarray(image_rgb)
                buffer = io.BytesIO()
                Image.fromarray(arr.astype(np.uint8)).save(buffer, format="PNG")
                image_bytes = buffer.getvalue()

            headers = {"Content-Type": "image/png"}
            token = os.getenv("HF_TOKEN", "").strip()
            if token:
                headers["Authorization"] = f"Bearer {token}"

            req = request.Request(self.hf_remote_url, data=image_bytes, headers=headers, method="POST")
            with request.urlopen(req, timeout=120) as resp:
                raw = resp.read().decode("utf-8", errors="replace")

            parsed_obj: object = json.loads(raw)
            preds_obj: list[Any] = parsed_obj if isinstance(parsed_obj, list) else []  # type: ignore
            preds: list[Mapping[str, object]] = [
                cast(Mapping[str, object], item) for item in preds_obj if isinstance(item, dict)
            ]
            out: list[dict[str, Any]] = []
            for pred in preds:
                label = str(pred.get("label", "unknown"))
                score = self._safe_float(pred.get("score"), 0.0)
                box_tuple = self._box_from_object(pred.get("box"))
                if box_tuple is None:
                    continue
                xmin, ymin, xmax, ymax = box_tuple
                out.append({
                    "pattern": label,
                    "confidence": score,
                    "bbox": [xmin, ymin, xmax, ymax],
                })
            return out
        except error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            self.logger.warning("Remote CV HTTP error for model=%s: %s | %s", self.hf_model_id, e, body)
            if int(getattr(e, "code", 0)) in (404, 410):
                self.logger.warning(
                    "Remote endpoint unavailable. Set PHOENIXGUARD_CV_REMOTE_URL to your hosted inference endpoint."
                )
            return []
        except Exception as e:
            self.logger.warning("Remote CV detection failed for model=%s: %s", self.hf_model_id, e)
            return []

    # ── raw YOLO detection ────────────────────────────────────────────────────
    def _raw_detect(self, image_rgb: Image.Image | NDArray[np.uint8]) -> list[dict[str, Any]]:
        if self.model is None:
            return []
        if not can_import_torchvision_safely():
            self.logger.warning("Skipping local YOLO inference because torchvision runtime probe failed")
            return []
        try:
            res = cast(Sequence[Any], self.model.predict(source=np.asarray(image_rgb), verbose=False, conf=0.3))
            out: list[dict[str, Any]] = []
            for r in res:
                names = cast(dict[int, str], getattr(r, "names", {}))
                boxes = getattr(r, "boxes", None)
                if boxes is None:
                    continue
                for b in cast(Sequence[Any], boxes):
                    cls_idx = int(b.cls.item()) if hasattr(b.cls, "item") else int(b.cls)
                    conf = float(b.conf.item()) if hasattr(b.conf, "item") else float(b.conf)
                    xyxy = b.xyxy[0].tolist() if hasattr(b.xyxy, "tolist") else list(b.xyxy[0])
                    out.append({
                        "pattern": names.get(cls_idx, f"class_{cls_idx}"),
                        "confidence": conf,
                        "bbox": [float(v) for v in xyxy],
                    })
            # Fuse fine-tuned BUY/SELL directional classifier result.
            if self.memory_clf is not None:
                try:
                    feat = self._extract_memory_features(image_rgb).reshape(1, -1)
                    proba = cast(NDArray[np.float32], self.memory_clf.predict_proba(feat))[0]
                    class_to_prob: dict[str, float] = {}
                    raw_classes = getattr(self.memory_clf, "classes_", None)
                    if raw_classes is not None:
                        for idx, label in enumerate(cast(Sequence[Any], raw_classes)):
                            label_name = str(label).strip().upper()
                            if idx < len(proba):
                                class_to_prob[label_name] = float(proba[idx])
                    sell_p = float(class_to_prob.get("SELL", proba[0] if len(proba) > 0 else 0.0))
                    if "BUY" in class_to_prob:
                        buy_p = float(class_to_prob["BUY"])
                    elif len(proba) > 1:
                        buy_p = float(proba[1])
                    else:
                        buy_p = float(1.0 - sell_p)
                    h = getattr(image_rgb, "height", None) or int(np.asarray(image_rgb).shape[0])
                    w = getattr(image_rgb, "width", None) or int(np.asarray(image_rgb).shape[1])
                    if buy_p >= sell_p:
                        out.append(
                            {
                                "pattern": "buy_memory_bias",
                                "confidence": buy_p,
                                "bbox": [0.0, 0.0, float(w), float(h)],
                            }
                        )
                    else:
                        out.append(
                            {
                                "pattern": "sell_memory_bias",
                                "confidence": sell_p,
                                "bbox": [0.0, 0.0, float(w), float(h)],
                            }
                        )
                except Exception as e:
                    self.logger.warning("Memory CV inference failed: %s", e)

            # Branch B: latest-candle specialist (right-edge crop + geometry heads).
            try:
                latest_feat = self._extract_latest_region_features(image_rgb).reshape(1, -1)
                _local_probe, _macro_probe, seq_probe = self._extract_multiscale_sequence_features(image_rgb, max_count=10)
                seq_feat = seq_probe.reshape(1, -1)
                latest_feat_model = latest_feat
                seq_feat_model = seq_feat
                if self.latest_scaler is not None:
                    latest_feat_model = self.latest_scaler.transform(latest_feat).astype(np.float32)
                if self.seq_scaler is not None:
                    seq_feat_model = self.seq_scaler.transform(seq_feat).astype(np.float32)
                geom = self._extract_candle_geometry(image_rgb)
                bbox = cast(list[float], geom.get("bbox", [0.0, 0.0, 0.0, 0.0]))
                recent_candles = self._select_recent_candles(
                    self._extract_candle_candidates(image_rgb, max_candidates=18),
                    max_count=10,
                )
                if not recent_candles:
                    recent_candles = [geom]

                if self.latest_dir_clf is not None:
                    p_latest = cast(NDArray[np.float32], self.latest_dir_clf.predict_proba(latest_feat_model))[0]
                    sell_p_l = float(p_latest[0])
                    buy_p_l = float(p_latest[1]) if p_latest.shape[0] > 1 else float(1.0 - sell_p_l)
                    sell_p_s = sell_p_l
                    buy_p_s = buy_p_l
                    if self.seq_dir_clf is not None:
                        p_seq = cast(NDArray[np.float32], self.seq_dir_clf.predict_proba(seq_feat_model))[0]
                        sell_p_s = float(p_seq[0])
                        buy_p_s = float(p_seq[1]) if p_seq.shape[0] > 1 else float(1.0 - sell_p_s)

                    parse_q0 = float(np.clip(CVPatternDetector._safe_float(geom.get("parse_conf"), 0.0), 0.0, 1.0))
                    w_latest = 0.55 * (0.45 + 0.55 * parse_q0)
                    w_seq = 0.30 * (0.70 + 0.30 * parse_q0)
                    w_global = 0.15
                    w_sum = max(1e-6, w_latest + w_seq + w_global)
                    buy_p = float((w_latest * buy_p_l + w_seq * buy_p_s + w_global * buy_p_l) / w_sum)
                    sell_p = float((w_latest * sell_p_l + w_seq * sell_p_s + w_global * sell_p_l) / w_sum)

                    margin = float(abs(buy_p - sell_p))
                    max_prob = float(max(buy_p, sell_p))
                    margin_min = float(self.memory_clf_meta.get("latest_margin_min", 0.16) or 0.16)
                    prob_min = float(self.memory_clf_meta.get("latest_prob_min", 0.60) or 0.60)
                    strong_signal = bool(margin >= margin_min and max_prob >= prob_min)

                    recent_tail = recent_candles[-6:]
                    n_tail = max(1, len(recent_tail))
                    if strong_signal:
                        latest_pattern = "latest_candle_buy" if buy_p >= sell_p else "latest_candle_sell"
                        latest_prob = buy_p if buy_p >= sell_p else sell_p
                        branch_candidates: list[tuple[int, float, list[float]]] = []
                        for i, c in enumerate(recent_tail):
                            cbox = cast(list[float], c.get("bbox", bbox))
                            parse_q = np.clip(float(CVPatternDetector._safe_float(c.get("parse_conf"), 0.0)), 0.0, 1.0)
                            recency_pos = float(i / max(1.0, float(n_tail - 1))) if n_tail > 1 else 1.0
                            recency_w = 0.72 + 0.28 * recency_pos
                            conf_val = float(np.clip(latest_prob * (0.62 + 0.38 * parse_q) * recency_w, 0.0, 1.0))
                            if conf_val >= max(0.38, prob_min - 0.08) or i >= max(0, n_tail - 2):
                                branch_candidates.append((i, conf_val, cbox))
                        if not branch_candidates:
                            branch_candidates.append((n_tail - 1, float(np.clip(latest_prob, 0.0, 1.0)), bbox))
                        branch_candidates = sorted(branch_candidates, key=lambda item: (item[1], item[0]), reverse=True)[:3]
                        branch_candidates.sort(key=lambda item: item[0])
                        for rank, (_i, conf_val, cbox) in enumerate(branch_candidates):
                            damp = 1.0 - 0.08 * float(rank)
                            out.append({
                                "pattern": latest_pattern,
                                "confidence": float(np.clip(conf_val * damp, 0.0, 1.0)),
                                "bbox": cbox,
                            })

                if self.next_dir_clf is not None:
                    p_next = cast(NDArray[np.float32], self.next_dir_clf.predict_proba(latest_feat_model))[0]
                    sell_next = float(p_next[0])
                    buy_next = float(p_next[1]) if p_next.shape[0] > 1 else float(1.0 - sell_next)
                    next_margin = abs(buy_next - sell_next)
                    if max(buy_next, sell_next) >= 0.57 and next_margin >= 0.12:
                        if buy_next >= sell_next:
                            out.append({"pattern": "next_candle_buy", "confidence": buy_next, "bbox": bbox})
                        else:
                            out.append({"pattern": "next_candle_sell", "confidence": sell_next, "bbox": bbox})

                if self.wick_dom_clf is not None:
                    p_w = cast(NDArray[np.float32], self.wick_dom_clf.predict_proba(latest_feat_model))[0]
                    upper_p = float(p_w[0])
                    lower_p = float(p_w[1]) if p_w.shape[0] > 1 else float(1.0 - upper_p)
                    if lower_p >= upper_p:
                        out.append({"pattern": "wick_dominance_lower", "confidence": lower_p, "bbox": bbox})
                    else:
                        out.append({"pattern": "wick_dominance_upper", "confidence": upper_p, "bbox": bbox})

                if self.move_bucket_clf is not None:
                    p_m = cast(NDArray[np.float32], self.move_bucket_clf.predict_proba(latest_feat_model))[0]
                    move_idx = int(np.argmax(p_m))
                    move_name = "next_move_small" if move_idx == 0 else ("next_move_medium" if move_idx == 1 else "next_move_large")
                    out.append({"pattern": move_name, "confidence": float(p_m[move_idx]), "bbox": bbox})

                # Always expose parse quality so strict gating can fail-closed if needed.
                out.append(
                    {
                        "pattern": "latest_parse_quality",
                        "confidence": np.clip(float(CVPatternDetector._safe_float(geom.get("parse_conf"), 0.0)), 0.0, 1.0),
                        "bbox": bbox,
                    }
                )
                # Add parse quality boxes over recent candles for visual debugging density.
                for c in recent_candles[-8:]:
                    cbox = cast(list[float], c.get("bbox", bbox))
                    out.append(
                        {
                            "pattern": "latest_parse_quality",
                            "confidence": np.clip(float(CVPatternDetector._safe_float(c.get("parse_conf"), 0.0)), 0.0, 1.0),
                            "bbox": cbox,
                        }
                    )
            except Exception as e:
                self.logger.warning("Latest-candle specialist inference failed: %s", e)
            return out
        except Exception as e:
            self.logger.exception("CV detection failed: %s", e)
            return []

    def _heuristic_candle_detect(self, image_rgb: Image.Image | NDArray[np.uint8]) -> list[dict[str, Any]]:
        """
        Lightweight fallback detector for chart screenshots.
        Extracts color-coded candle blobs and derives simple continuation/reversal
        signals so CV remains operational when model endpoints are unavailable.
        """
        arr = np.asarray(image_rgb.convert("RGB") if isinstance(image_rgb, Image.Image) else image_rgb)
        if arr.ndim != 3 or arr.shape[2] < 3:
            return []

        h, w = int(arr.shape[0]), int(arr.shape[1])
        if h < 32 or w < 32:
            return []

        # Focus on the center chart pane to avoid side panels and headers.
        x0 = int(w * 0.08)
        x1 = int(w * 0.92)
        y0 = int(h * 0.08)
        y1 = int(h * 0.92)
        roi = arr[y0:y1, x0:x1]
        if roi.size == 0:
            return []

        r = roi[:, :, 0].astype(np.int16)
        g = roi[:, :, 1].astype(np.int16)
        b = roi[:, :, 2].astype(np.int16)

        green = (g > (r + 16)) & (g > (b + 12)) & (g > 50)
        red = (r > (g + 16)) & (r > (b + 12)) & (r > 50)
        mask = green | red

        col_strength = np.sum(mask, axis=0)
        min_col_pixels = max(6, int((y1 - y0) * 0.02))
        active = col_strength >= min_col_pixels
        if not np.any(active):
            return []

        # Group adjacent active columns into candle candidates.
        segments: list[tuple[int, int]] = []
        start: int | None = None
        for i, v in enumerate(active):
            if bool(v) and start is None:
                start = i
            elif (not bool(v)) and start is not None:
                if i - start >= 2:
                    segments.append((start, i - 1))
                start = None
        if start is not None and len(active) - start >= 2:
            segments.append((start, len(active) - 1))

        if not segments:
            return []

        candles: list[dict[str, Any]] = []
        for sx, ex in segments[:80]:
            strip = mask[:, sx:ex + 1]
            ys, _ = np.where(strip)
            if ys.size < 6:
                continue
            ymin = int(np.min(ys))
            ymax = int(np.max(ys))
            if ymax - ymin < 3:
                continue

            green_count = int(np.sum(green[:, sx:ex + 1]))
            red_count = int(np.sum(red[:, sx:ex + 1]))
            color = "green" if green_count >= red_count else "red"

            row_counts = np.sum(strip, axis=1)
            peak = float(np.max(row_counts))
            dense_rows = np.where(row_counts >= max(2.0, 0.55 * peak))[0]
            if dense_rows.size >= 2:
                body_top = int(np.min(dense_rows))
                body_bottom = int(np.max(dense_rows))
            else:
                body_top = ymin
                body_bottom = ymax

            body_h = max(1, body_bottom - body_top + 1)
            upper_wick = max(0, body_top - ymin)
            lower_wick = max(0, ymax - body_bottom)
            wick_ratio = float(max(upper_wick, lower_wick) / max(1, body_h))

            candles.append(
                {
                    "sx": sx,
                    "ex": ex,
                    "ymin": ymin,
                    "ymax": ymax,
                    "color": color,
                    "body_h": body_h,
                    "upper_wick": upper_wick,
                    "lower_wick": lower_wick,
                    "wick_ratio": wick_ratio,
                }
            )

        if len(candles) < 3:
            return []

        # Sort left-to-right and keep a much wider visible slice so box-history
        # logic can reason over the full path, not just the last few bars.
        candles.sort(key=lambda c: (int(c["sx"]) + int(c["ex"])) / 2.0)
        tail = candles[-min(len(candles), 28):]

        up_count = sum(1 for c in tail if c["color"] == "green")
        down_count = sum(1 for c in tail if c["color"] == "red")

        def _streak(target: str) -> int:
            n = 0
            for c in reversed(tail):
                if c["color"] == target:
                    n += 1
                else:
                    break
            return n

        up_streak = _streak("green")
        down_streak = _streak("red")
        last = tail[-1]

        out: list[dict[str, Any]] = []

        # Base pattern from streaks (continuation or consolidation).
        if up_streak >= 3:
            conf = min(0.92, 0.58 + 0.06 * up_streak)
            out.append(
                {
                    "pattern": "continuation",
                    "confidence": float(conf),
                    "bbox": [
                        float(x0 + int(last["sx"])),
                        float(y0 + int(last["ymin"])),
                        float(x0 + int(last["ex"])),
                        float(y0 + int(last["ymax"])),
                    ],
                    "source": "heuristic",
                }
            )
        elif down_streak >= 3:
            conf = min(0.92, 0.58 + 0.06 * down_streak)
            out.append(
                {
                    "pattern": "continuation",
                    "confidence": float(conf),
                    "bbox": [
                        float(x0 + int(last["sx"])),
                        float(y0 + int(last["ymin"])),
                        float(x0 + int(last["ex"])),
                        float(y0 + int(last["ymax"])),
                    ],
                    "source": "heuristic",
                }
            )

        # Reversal cue: opposite last candle after a streak + dominant wick.
        if len(tail) >= 2:
            prev = tail[-2]
            if prev["color"] != last["color"] and float(last["wick_ratio"]) >= 0.6:
                conf = min(0.88, 0.54 + 0.10 * float(last["wick_ratio"]))
                out.append(
                    {
                        "pattern": "reversal",
                        "confidence": float(conf),
                        "bbox": [
                            float(x0 + int(last["sx"])),
                            float(y0 + int(last["ymin"])),
                            float(x0 + int(last["ex"])),
                            float(y0 + int(last["ymax"])),
                        ],
                        "source": "heuristic",
                    }
                )

        # Consolidation cue if colors are balanced and streaks are weak.
        balance = abs(up_count - down_count)
        if balance <= 2 and max(up_streak, down_streak) <= 2:
            c0 = tail[0]
            c1 = tail[-1]
            out.append(
                {
                    "pattern": "consolidation",
                    "confidence": 0.62,
                    "bbox": [
                        float(x0 + int(c0["sx"])),
                        float(y0 + int(min(c["ymin"] for c in tail))),
                        float(x0 + int(c1["ex"])),
                        float(y0 + int(max(c["ymax"] for c in tail))),
                    ],
                    "source": "heuristic",
                }
            )

        return out[:6]

    def heuristic_candle_detect(self, image_rgb: Image.Image | NDArray[np.uint8]) -> list[dict[str, Any]]:
        return self._heuristic_candle_detect(image_rgb)

    # ── priority queue ranking (Design & Analysis of Algorithms) ─────────────
    def _priority_queue_rank(
        self, raw: list[dict[str, Any]], top_n: int = 15
    ) -> list[PatternDetection]:
        """
        Min-heap priority queue: rank detections by combined score.
        score = confidence × pattern_base_score
        H&S patterns receive 80% score reduction.
        Returns top_n highest-scoring patterns (max-heap via negation).
        """
        heap: list[tuple[float, int, PatternDetection]] = []

        for idx, d in enumerate(raw):
            name = d["pattern"].lower().replace(" ", "_")
            base_score = _PATTERN_SCORES.get(name, 0.55)
            combined = float(d["confidence"]) * base_score

            # Determine pattern type
            if name in _REVERSAL_PATTERNS:
                p_type = "reversal"
            elif name in _CONTINUATION_PATTERNS:
                p_type = "continuation"
            else:
                p_type = "other"

            pd = PatternDetection(
                pattern=d["pattern"],
                confidence=d["confidence"],
                bbox=d["bbox"],
                priority_score=combined,
                pattern_type=p_type,
            )
            # Use negative score for max-heap behavior with min-heap
            heapq.heappush(heap, (-combined, idx, pd))

        results: list[PatternDetection] = []
        for _ in range(min(top_n, len(heap))):
            _, _, pd = heapq.heappop(heap)
            results.append(pd)

        return results

    # ── K-Means consolidation cluster detection (Clustering) ──────────────────
    def _kmeans_consolidation(
        self, detections: list[PatternDetection], image_width: int, image_height: int
    ) -> dict[str, Any]:
        """
        K-Means on bbox centers to identify candle grouping / consolidation zones.
        Returns cluster info: {n_clusters, consolidation_zones, dominant_cluster}.
        """
        if not _sk_ok or KMeansModel is None or len(detections) < 3:
            return {"n_clusters": 0, "consolidation_zones": [], "dominant_cluster": None}

        centers = np.array(
            [
                [
                    (d.bbox[0] + d.bbox[2]) / 2.0 / max(image_width, 1),
                    (d.bbox[1] + d.bbox[3]) / 2.0 / max(image_height, 1),
                ]
                for d in detections
            ],
            dtype=np.float32,
        )

        n_k = min(max(2, len(detections) // 3), 5)
        try:
            km = KMeansModel(n_clusters=n_k, random_state=808, n_init=5)
            labels = km.fit_predict(centers)
            cluster_counts = np.bincount(labels)
            dominant = int(np.argmax(cluster_counts))

            # Consolidation zones = clusters with >=2 patterns in a tight x-range.
            zones: list[dict[str, Any]] = []
            for c_id in range(n_k):
                members_x = centers[labels == c_id, 0]
                if len(members_x) >= 2 and float(np.std(members_x)) < 0.12:
                    zones.append(
                        {
                            "cluster_id": int(c_id),
                            "x_center": float(km.cluster_centers_[c_id, 0]),
                            "y_center": float(km.cluster_centers_[c_id, 1]),
                            "count": int(cluster_counts[c_id]),
                        }
                    )
            return {
                "n_clusters": n_k,
                "consolidation_zones": zones,
                "dominant_cluster": dominant,
            }
        except Exception as e:
            self.logger.warning("K-Means consolidation failed: %s", e)
            return {"n_clusters": 0, "consolidation_zones": [], "dominant_cluster": None}

    def _confidence_by_pattern(self, detections: list[dict[str, Any]], name: str) -> float:
        name_norm = name.lower().strip().replace(" ", "_")
        best = 0.0
        for d in detections:
            p = str(d.get("pattern", "")).lower().strip().replace(" ", "_")
            if p != name_norm:
                continue
            conf = float(d.get("confidence", 0.0) or 0.0)
            if conf > best:
                best = conf
        return best

    def build_reasoning_trace(
        self,
        detections: list[dict[str, Any]],
        image_rgb: Image.Image | NDArray[np.uint8] | None = None,
    ) -> dict[str, Any]:
        """
        Build CV-only market-state reasoning from detector outputs.
        This method is additive and does not alter the existing detect API contract.
        """
        buy_mem = self._confidence_by_pattern(detections, "buy_memory_bias")
        sell_mem = self._confidence_by_pattern(detections, "sell_memory_bias")
        latest_buy = self._confidence_by_pattern(detections, "latest_candle_buy")
        latest_sell = self._confidence_by_pattern(detections, "latest_candle_sell")
        next_buy = self._confidence_by_pattern(detections, "next_candle_buy")
        next_sell = self._confidence_by_pattern(detections, "next_candle_sell")
        wick_upper = self._confidence_by_pattern(detections, "wick_dominance_upper")
        wick_lower = self._confidence_by_pattern(detections, "wick_dominance_lower")
        move_small = self._confidence_by_pattern(detections, "next_move_small")
        move_medium = self._confidence_by_pattern(detections, "next_move_medium")
        move_large = self._confidence_by_pattern(detections, "next_move_large")

        local_buy_impulse = 0.45 * latest_buy + 0.55 * next_buy
        local_sell_impulse = 0.45 * latest_sell + 0.55 * next_sell
        local_dir = "BUY" if local_buy_impulse >= local_sell_impulse else "SELL"

        aligned_buy_support = 0.50 * buy_mem + 0.30 * next_buy + 0.20 * latest_buy
        aligned_sell_support = 0.50 * sell_mem + 0.30 * next_sell + 0.20 * latest_sell
        buy_cont_override = 0.12 if (
            next_buy >= 0.78
            and latest_buy >= 0.60
            and buy_mem >= max(0.55, sell_mem + 0.06)
            and move_large >= 0.55
        ) else 0.0
        sell_cont_override = 0.12 if (
            next_sell >= 0.78
            and latest_sell >= 0.60
            and sell_mem >= max(0.55, buy_mem + 0.06)
            and move_large >= 0.55
        ) else 0.0
        macro_sell_score = 0.58 * sell_mem + 0.22 * max(latest_sell, next_sell) + 0.20 * local_sell_impulse + sell_cont_override
        macro_buy_score = 0.58 * buy_mem + 0.22 * max(latest_buy, next_buy) + 0.20 * local_buy_impulse + buy_cont_override
        macro_gap = float(abs(macro_buy_score - macro_sell_score))
        if macro_buy_score > macro_sell_score + 0.04:
            macro_trend = "BULL"
        elif macro_sell_score > macro_buy_score + 0.04:
            macro_trend = "BEAR"
        else:
            macro_trend = "BULL" if local_dir == "BUY" else "BEAR"

        with_trend = (macro_trend == "BULL" and local_dir == "BUY") or (macro_trend == "BEAR" and local_dir == "SELL")
        strong_counter_trend_continuation = (
            (not with_trend)
            and move_large >= 0.55
            and max(aligned_buy_support, aligned_sell_support) >= 0.70
            and abs(local_buy_impulse - local_sell_impulse) >= 0.14
        )

        if strong_counter_trend_continuation and macro_gap <= 0.14:
            macro_trend = "BULL" if local_dir == "BUY" else "BEAR"
            with_trend = True

        continuation_override_active = bool(
            strong_counter_trend_continuation
            and max(aligned_buy_support, aligned_sell_support) >= 0.74
            and max(next_buy, next_sell) >= 0.72
        )

        if with_trend and move_large >= 0.65:
            local_phase = "with_trend_push"
        elif with_trend and (move_small >= 0.60 or move_medium >= 0.55):
            local_phase = "with_trend_pause"
        elif (not with_trend) and max(wick_upper, wick_lower) >= 0.60:
            local_phase = "reversal_base"
        elif strong_counter_trend_continuation:
            local_phase = "continuation_base"
        elif (not with_trend) and move_large >= 0.68:
            local_phase = "counter_trend_spike"
        elif with_trend:
            local_phase = "continuation_base"
        else:
            local_phase = "counter_trend_pullback"

        if image_rgb is not None and self.seq_scaler is not None:
            try:
                local_vec, macro_vec, seq_vec = self._extract_multiscale_sequence_features(image_rgb, max_count=10)
                seq_feat = seq_vec.reshape(1, -1)
                seq_feat_s = self.seq_scaler.transform(seq_feat).astype(np.float32)
                local_feat_s = seq_feat_s
                macro_feat_s = seq_feat_s
                intent_feat_s = seq_feat_s
                if self.local_scaler is not None:
                    local_feat_s = self.local_scaler.transform(local_vec.reshape(1, -1)).astype(np.float32)
                if self.macro_scaler is not None:
                    macro_input = np.concatenate([macro_vec, seq_vec], axis=0).reshape(1, -1)
                    macro_feat_s = self.macro_scaler.transform(macro_input).astype(np.float32)
                if self.intent_scaler is not None:
                    intent_input = np.concatenate([local_vec, seq_vec], axis=0).reshape(1, -1)
                    intent_feat_s = self.intent_scaler.transform(intent_input).astype(np.float32)

                def _decode_head(clf: Any | None, key: str, default: str, feat_arr: NDArray[np.float32]) -> str:
                    if clf is None:
                        return default
                    pred = cast(NDArray[np.int32], clf.predict(feat_arr))
                    idx = int(pred[0])
                    inv = self.taxonomy_label_maps.get(key, {})
                    return str(inv.get(idx, default))
                    inv = self.taxonomy_label_maps.get(key, {})
                    return str(inv.get(idx, default))

                macro_trend = _decode_head(self.macro_trend_clf, "macro_trend", macro_trend, macro_feat_s)
                local_phase = _decode_head(self.local_phase_clf, "local_phase", local_phase, local_feat_s)
                phase_risk = _decode_head(self.phase_risk_clf, "phase_risk", "chop_risk", local_feat_s)
                predicted_intent = _decode_head(self.intent_next_clf, "intent_next", "continue", intent_feat_s)
                if continuation_override_active:
                    macro_trend = "BULL" if local_dir == "BUY" else "BEAR"
                    local_phase = "continuation_base"
                    predicted_intent = "continue"
            except Exception:
                phase_risk = "chop_risk"
                predicted_intent = "continue"
        else:
            predicted_intent = "continue"

        continuation_pressure = float(max(aligned_buy_support, aligned_sell_support))
        if local_phase in {"counter_trend_spike", "reversal_base"}:
            phase_risk = "exhaustion_risk"
        elif continuation_override_active:
            phase_risk = "managed_counter_trend"
        elif local_phase in {"with_trend_push", "continuation_base"} or strong_counter_trend_continuation:
            phase_risk = "breakout_risk"
        else:
            phase_risk = "chop_risk"

        raw_transition = {
            "continue": float(
                0.56 * max(next_buy, next_sell)
                + 0.18 * (1.0 if with_trend else 0.0)
                + 0.12 * move_large
                + 0.12 * continuation_pressure
                + 0.10 * (1.0 if strong_counter_trend_continuation else 0.0)
                + 0.08 * (1.0 if continuation_override_active else 0.0)
            ),
            "pullback": float(
                0.40 * move_small
                + 0.24 * (1.0 if local_phase == "counter_trend_pullback" else 0.0)
                + 0.15 * max(wick_upper, wick_lower)
                - 0.12 * (1.0 if strong_counter_trend_continuation else 0.0)
                - 0.06 * (1.0 if continuation_override_active else 0.0)
            ),
            "reversal_attempt": float(
                0.38 * max(wick_upper, wick_lower)
                + 0.33 * (1.0 if local_phase in {"counter_trend_spike", "reversal_base"} else 0.0)
                + 0.14 * (1.0 - max(next_buy, next_sell))
                - 0.10 * (1.0 if strong_counter_trend_continuation else 0.0)
            ),
            "fakeout": float(
                0.28 * move_large
                + 0.26 * (1.0 if (not with_trend) else 0.0)
                + 0.18 * abs(local_buy_impulse - local_sell_impulse)
                - 0.08 * continuation_pressure
            ),
        }
        # Keep classifier intent as a soft prior, but keep final intent driven by
        # normalized transition probabilities to avoid contradictory debug outputs.
        if predicted_intent in raw_transition:
            raw_transition[predicted_intent] = float(raw_transition[predicted_intent] + 0.20)
        transitions = normalize_transition_probabilities(raw_transition)

        transition_to_intent = {
            "continue_prob": "continue",
            "pullback_prob": "pullback",
            "reversal_attempt_prob": "reversal_attempt",
            "fakeout_prob": "fakeout",
        }
        best_transition_key = max(transition_to_intent.keys(), key=lambda k: float(transitions.get(k, 0.0)))
        intent_next = transition_to_intent[best_transition_key]

        control_state = "with_trend" if with_trend else "counter_trend"
        if macro_gap < 0.08 or strong_counter_trend_continuation:
            control_state = "transition"

        control_delta = float(abs(max(macro_buy_score, macro_sell_score) - max(local_buy_impulse, local_sell_impulse)))
        if with_trend:
            conflict_type = "none"
        elif strong_counter_trend_continuation:
            conflict_type = "healthy_pullback"
        elif local_phase == "counter_trend_pullback":
            conflict_type = "healthy_pullback"
        elif local_phase in {"counter_trend_spike", "reversal_base"}:
            conflict_type = "possible_reversal"
        else:
            conflict_type = "noise_conflict"

        ttl = 1
        if intent_next in {"pullback", "fakeout"}:
            ttl = 2
        if intent_next == "reversal_attempt":
            ttl = 3

        market_state = MarketState(
            macro_trend=cast(Any, macro_trend),
            local_phase=cast(Any, local_phase),
            phase_risk=cast(Any, phase_risk),
            intent_next=cast(Any, intent_next),
            control_state=cast(Any, control_state),
            control_strength_delta=control_delta,
            conflict_type=cast(Any, conflict_type),
            time_to_resolution_candles=ttl,
        )
        validate_market_state(market_state)

        trace = CVReasoningTrace(
            market_state=market_state,
            transition_probabilities=transitions,
            episode_matches=[],
            final_trade_bias="BUY" if macro_trend == "BULL" else "SELL",
            explanation=(
                f"macro={macro_trend} local={local_phase} intent={intent_next} "
                f"conflict={conflict_type} control_delta={control_delta:.3f}"
            ),
        )
        return trace.to_dict()

    # ── public detect API ─────────────────────────────────────────────────────
    def detect(self, image_rgb: Image.Image | NDArray[np.uint8]) -> list[dict[str, Any]]:
        """
        Full detection pipeline:
        1. YOLO26s inference (conf=0.3)
        2. Priority queue ranking with H&S reduction
        3. K-Means consolidation detection
        Returns enriched detection dicts.
        """
        raw = self._raw_detect(image_rgb)

        if not raw:
            raw = self._heuristic_candle_detect(image_rgb)
        if not raw:
            return []

        # Keep high-value chart patterns only; drop noisy/unknown classes.
        allowed = {
            "reversal", "continuation", "consolidation", "breakout",
            "bullish_engulfing", "bearish_engulfing", "hammer", "shooting_star",
            "doji", "pin_bar", "inside_bar", "morning_star", "evening_star",
            "three_white_soldiers", "three_black_crows",
            "buy_memory_bias", "sell_memory_bias",
            "latest_candle_buy", "latest_candle_sell",
            "next_candle_buy", "next_candle_sell",
            "wick_dominance_upper", "wick_dominance_lower",
            "next_move_small", "next_move_medium", "next_move_large",
            "latest_parse_quality",
        }
        filtered: list[dict[str, Any]] = []
        for d in raw:
            name = str(d.get("pattern", "")).strip().lower().replace(" ", "_")
            conf = float(d.get("confidence", 0.0) or 0.0)
            if name == "latest_parse_quality" and name in allowed:
                filtered.append(d)
                continue
            if name in allowed and conf >= 0.35:
                filtered.append(d)
        raw = filtered if filtered else raw

        # Priority queue ranking
        ranked = self._priority_queue_rank(raw, top_n=15)

        # K-Means consolidation
        if isinstance(image_rgb, Image.Image):
            img_h = int(image_rgb.height)
            img_w = int(image_rgb.width)
        else:
            img_arr = np.asarray(image_rgb)
            img_h = int(img_arr.shape[0]) if img_arr.ndim >= 2 else 1024
            img_w = int(img_arr.shape[1]) if img_arr.ndim >= 2 else 1024
        cluster_info = self._kmeans_consolidation(ranked, img_w, img_h)

        # Build output dicts
        out: list[dict[str, Any]] = []
        for d in ranked:
            out.append({
                "pattern": d.pattern,
                "confidence": d.confidence,
                "bbox": d.bbox,
                "priority_score": d.priority_score,
                "pattern_type": d.pattern_type,
                "consolidation_zones": cluster_info["consolidation_zones"],
            })
        return out
