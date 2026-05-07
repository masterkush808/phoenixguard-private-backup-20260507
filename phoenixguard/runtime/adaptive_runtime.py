from __future__ import annotations

import io
import json
from dataclasses import dataclass
import os
from pathlib import Path
from threading import RLock
import tempfile
from typing import Any, Mapping, Sequence, cast

import numpy as np
from PIL import Image

from phoenixguard.core.config import RUNTIME
from phoenixguard.vision.preprocess import apply_clahe, auto_crop_price_area


_grounded_parser_cache: Any | None = None


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clip01(value: Any, default: float = 0.0) -> float:
    return float(np.clip(_safe_float(value, default), 0.0, 1.0))


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    _atomic_write_bytes(
        path,
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8"),
    )


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=str(path.parent),
            prefix=f"{path.name}.",
            suffix=".tmp",
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            tmp_path = Path(handle.name)
        tmp_path.replace(path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    payload = "".join(json.dumps(dict(row), ensure_ascii=True) + "\n" for row in rows)
    _atomic_write_bytes(path, payload.encode("utf-8"))


def _get_grounded_parser(logger: Any | None = None) -> Any | None:
    global _grounded_parser_cache
    if _grounded_parser_cache is not None:
        return _grounded_parser_cache
    try:
        from phoenixguard.vision.grounded_backends import OptionalGroundedParser

        _grounded_parser_cache = OptionalGroundedParser(logger)
        return _grounded_parser_cache
    except Exception:
        return None


def build_style_signature(
    image_rgb: Image.Image,
    *,
    chart_geometry: Mapping[str, Any] | None = None,
    sequence_state: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    chart_geometry = chart_geometry or {}
    sequence_state = sequence_state or {}
    arr = np.asarray(image_rgb.convert("RGB"), dtype=np.float32) / 255.0
    gray = arr.mean(axis=2)
    saturation = cast(np.ndarray, arr.max(axis=2) - arr.min(axis=2))
    width = max(int(image_rgb.width), 1)
    height = max(int(image_rgb.height), 1)
    return {
        "mean_luma": float(np.clip(gray.mean(), 0.0, 1.0)),
        "contrast": float(np.clip(gray.std() * 2.0, 0.0, 1.0)),
        "saturation": float(np.clip(saturation.mean() * 2.2, 0.0, 1.0)),
        "dark_theme": float(gray.mean() < 0.45),
        "aspect_ratio": float(np.clip(width / height, 0.25, 4.0)),
        "width_bucket": float(np.clip(width / 1920.0, 0.0, 2.0)),
        "height_bucket": float(np.clip(height / 1080.0, 0.0, 2.0)),
        "geometry_confidence": _clip01(chart_geometry.get("geometry_confidence", chart_geometry.get("parse_conf", 0.0))),
        "spacing_consistency": _clip01(sequence_state.get("spacing_consistency", 0.0)),
        "candle_density": _clip01(_safe_float(sequence_state.get("visible_candle_count", 0), 0.0) / 96.0),
        "color_flip_rate": _clip01(sequence_state.get("color_flip_rate", 0.0)),
        "body_balance": _clip01(sequence_state.get("body_mean_pct", 0.0)),
    }


def build_artifact_summary(
    image_rgb: Image.Image,
    *,
    chart_geometry: Mapping[str, Any] | None = None,
) -> dict[str, float]:
    chart_geometry = chart_geometry or {}
    arr = np.asarray(image_rgb.convert("RGB"), dtype=np.float32) / 255.0
    gray = arr.mean(axis=2)
    grad_x = np.abs(np.diff(gray, axis=1))
    grad_y = np.abs(np.diff(gray, axis=0))
    vert_energy = float(grad_x.mean()) if grad_x.size else 0.0
    horiz_energy = float(grad_y.mean()) if grad_y.size else 0.0
    vertical_artifact_ratio = float(np.clip(vert_energy / max(horiz_energy, 1e-6), 0.0, 4.0))
    second_x = np.abs(np.diff(gray, n=2, axis=1))
    second_y = np.abs(np.diff(gray, n=2, axis=0))
    sharpness = float(np.clip((second_x.mean() if second_x.size else 0.0) + (second_y.mean() if second_y.size else 0.0), 0.0, 1.0))
    blur_risk = float(np.clip(1.0 - 8.0 * sharpness, 0.0, 1.0))
    blockiness = 0.0
    for axis in (0, 1):
        length = gray.shape[axis]
        if length <= 8:
            continue
        diffs: list[float] = []
        for boundary in range(8, length, 8):
            if axis == 0 and boundary < gray.shape[0]:
                diffs.append(float(np.abs(gray[boundary - 1, :] - gray[boundary, :]).mean()))
            elif axis == 1 and boundary < gray.shape[1]:
                diffs.append(float(np.abs(gray[:, boundary - 1] - gray[:, boundary]).mean()))
        if diffs:
            blockiness += float(np.mean(np.asarray(diffs, dtype=np.float32)))
    blockiness = float(np.clip(blockiness * 4.0, 0.0, 1.0))
    parse_penalty = 1.0 - _clip01(chart_geometry.get("geometry_confidence", chart_geometry.get("parse_conf", 0.0)))
    artifact_score = float(
        np.clip(
            0.34 * blockiness
            + 0.22 * max(0.0, vertical_artifact_ratio - 1.25) / 2.75
            + 0.24 * blur_risk
            + 0.20 * parse_penalty,
            0.0,
            1.0,
        )
    )
    return {
        "blockiness": blockiness,
        "vertical_artifact_ratio": float(np.clip(vertical_artifact_ratio / 4.0, 0.0, 1.0)),
        "blur_risk": blur_risk,
        "artifact_score": artifact_score,
    }


def _build_heuristic_grounded_chart(
    image_rgb: Image.Image,
    *,
    detections: Sequence[Mapping[str, Any]],
    chart_geometry: Mapping[str, Any],
    sequence_state: Mapping[str, Any],
) -> dict[str, Any]:
    visible_candles = cast(list[dict[str, Any]], sequence_state.get("all_visible_candles", []))
    box_history = cast(list[dict[str, Any]], sequence_state.get("box_history", []))
    current_box = cast(dict[str, Any], sequence_state.get("current_box", {}))
    next_boxes = cast(list[dict[str, Any]], sequence_state.get("next_box_hypotheses", []))
    objects = [
        {
            "id": f"candle_{index}",
            "kind": "candle",
            "bbox": cast(list[float], candle.get("bbox", [0.0, 0.0, 0.0, 0.0])),
            "direction": "BUY" if _clip01(candle.get("candle_color_green", 0.0)) >= 0.5 else "SELL",
            "body_pct": _clip01(candle.get("body_height_pct", 0.0)),
            "upper_wick_pct": _clip01(candle.get("upper_wick_pct", 0.0)),
            "lower_wick_pct": _clip01(candle.get("lower_wick_pct", 0.0)),
        }
        for index, candle in enumerate(visible_candles[-12:])
    ]
    zones: list[dict[str, Any]] = []
    for detection in detections:
        pattern = str(detection.get("pattern", "")).strip().lower().replace(" ", "_")
        if pattern in {"buy_memory_bias", "sell_memory_bias", "reversal", "continuation", "breakout"}:
            zones.append(
                {
                    "kind": "pattern_zone",
                    "pattern": pattern,
                    "bbox": cast(list[float], detection.get("bbox", [0.0, 0.0, 0.0, 0.0])),
                    "confidence": _clip01(detection.get("confidence", 0.0)),
                }
            )
    for box in box_history[-3:]:
        zones.append(
            {
                "kind": "sequence_box",
                "box_type": str(box.get("box_type", "balance")),
                "direction": str(box.get("direction", "HOLD")).upper(),
                "bbox": cast(list[float], box.get("bbox", [0.0, 0.0, 0.0, 0.0])),
                "confidence": _clip01(box.get("confidence", 0.0)),
                "consolidation_score": _clip01(box.get("consolidation_score", 0.0)),
            }
        )
    style_signature = build_style_signature(image_rgb, chart_geometry=chart_geometry, sequence_state=sequence_state)
    artifact_summary = build_artifact_summary(image_rgb, chart_geometry=chart_geometry)
    grounded_confidence = float(
        np.clip(
            0.42 * _clip01(chart_geometry.get("geometry_confidence", 0.0))
            + 0.28 * _clip01(sequence_state.get("spacing_consistency", 0.0))
            + 0.18 * _clip01(sequence_state.get("box_sequence_agreement", 0.0))
            + 0.12 * (1.0 - artifact_summary["artifact_score"]),
            0.0,
            1.0,
        )
    )
    structure_summary = _summarize_grounded_structure(
        objects=objects,
        zones=zones,
        current_box=current_box,
        next_boxes=next_boxes,
        sequence_state=sequence_state,
        chart_geometry=chart_geometry,
    )
    return {
        "grounded_confidence": grounded_confidence,
        "objects": objects,
        "zones": zones,
        "current_box": dict(current_box),
        "next_boxes": [dict(item) for item in next_boxes[:3]],
        "structure_summary": structure_summary,
        "style_signature": style_signature,
        "artifact_summary": artifact_summary,
        "backend": {
            "available": False,
            "used_backends": [],
            "caption": "",
            "errors": {},
            "detections": [],
            "masks": [],
        },
    }


def build_heuristic_grounded_chart(
    image_rgb: Image.Image,
    *,
    detections: Sequence[Mapping[str, Any]],
    chart_geometry: Mapping[str, Any],
    sequence_state: Mapping[str, Any],
) -> dict[str, Any]:
    return _build_heuristic_grounded_chart(
        image_rgb,
        detections=detections,
        chart_geometry=chart_geometry,
        sequence_state=sequence_state,
    )


def build_grounded_chart(
    image_rgb: Image.Image,
    *,
    detections: Sequence[Mapping[str, Any]],
    chart_geometry: Mapping[str, Any],
    sequence_state: Mapping[str, Any],
    backend_parser: Any | None = None,
) -> dict[str, Any]:
    base = _build_heuristic_grounded_chart(
        image_rgb,
        detections=detections,
        chart_geometry=chart_geometry,
        sequence_state=sequence_state,
    )
    if backend_parser is None and not bool(getattr(RUNTIME, "prefer_foundation_grounding", True)):
        return base
    parser = backend_parser if backend_parser is not None else _get_grounded_parser()
    if parser is None:
        return base
    try:
        backend = parser.parse(image_rgb)
    except Exception:
        return base

    objects = list(cast(list[dict[str, Any]], base.get("objects", [])))
    zones = list(cast(list[dict[str, Any]], base.get("zones", [])))
    artifact_summary = dict(cast(dict[str, float], base.get("artifact_summary", {})))
    ui_artifact_score = 0.0

    for index, detection in enumerate(cast(list[dict[str, Any]], getattr(backend, "detections", []))):
        label = str(detection.get("label", "")).strip().lower()
        bbox = cast(list[float], detection.get("bbox", [0.0, 0.0, 0.0, 0.0]))
        score = _clip01(detection.get("score", 0.0))
        entry = {
            "id": f"backend_{index}",
            "kind": "grounded_region",
            "label": label,
            "bbox": bbox,
            "confidence": score,
            "source": str(detection.get("source", "backend")),
        }
        if any(token in label for token in ("zone", "support", "resistance", "box", "breakout", "pullback", "consolidation")):
            zones.append(
                {
                    "kind": "grounded_zone",
                    "pattern": label or "zone",
                    "bbox": bbox,
                    "confidence": score,
                    "source": str(detection.get("source", "backend")),
                }
            )
        else:
            objects.append(entry)
        if any(token in label for token in ("ui", "cursor", "watermark", "artifact", "toolbar", "button", "broker")):
            ui_artifact_score = max(ui_artifact_score, score)

    artifact_summary["ui_artifact_score"] = float(np.clip(ui_artifact_score, 0.0, 1.0))
    artifact_summary["artifact_score"] = float(
        np.clip(
            max(
                _clip01(artifact_summary.get("artifact_score", 0.0)),
                ui_artifact_score,
                0.60 * _clip01(artifact_summary.get("artifact_score", 0.0)) + 0.40 * ui_artifact_score,
            ),
            0.0,
            1.0,
        )
    )

    grounded_confidence = float(
        np.clip(
            max(
                _clip01(base.get("grounded_confidence", 0.0)),
                0.72 * _clip01(base.get("grounded_confidence", 0.0)) + 0.28 * float(getattr(backend, "confidence", 0.0)),
            ),
            0.0,
            1.0,
        )
    )

    merged = dict(base)
    merged["grounded_confidence"] = grounded_confidence
    merged["objects"] = objects
    merged["zones"] = zones
    merged["artifact_summary"] = artifact_summary
    merged["structure_summary"] = _summarize_grounded_structure(
        objects=objects,
        zones=zones,
        current_box=cast(dict[str, Any], merged.get("current_box", {})),
        next_boxes=cast(list[dict[str, Any]], merged.get("next_boxes", [])),
        sequence_state=sequence_state,
        chart_geometry=chart_geometry,
    )
    merged["backend"] = {
        "available": bool(getattr(backend, "used_backends", [])),
        "used_backends": list(getattr(backend, "used_backends", [])),
        "caption": str(getattr(backend, "caption", "")),
        "errors": dict(getattr(backend, "errors", {})),
        "detections": cast(list[dict[str, Any]], getattr(backend, "detections", [])),
        "masks": cast(list[dict[str, Any]], getattr(backend, "masks", [])),
    }
    return merged


def _zone_bucket(zone: Mapping[str, Any]) -> str:
    label = " ".join(
        str(zone.get(key, "")).strip().lower()
        for key in ("kind", "pattern", "label", "box_type", "source")
    )
    direction = str(zone.get("direction", "")).strip().upper()
    if "support" in label:
        return "support"
    if "resistance" in label:
        return "resistance"
    if "breakout" in label or ("impulse" in label and direction in {"BUY", "SELL"}):
        return "breakout"
    if "pullback" in label:
        return "pullback"
    if "consolidation" in label or "balance" in label:
        return "consolidation"
    if "reversal" in label:
        return "reversal"
    if direction in {"BUY", "SELL"}:
        return "directional_box"
    return "other"


def _summarize_grounded_structure(
    *,
    objects: Sequence[Mapping[str, Any]],
    zones: Sequence[Mapping[str, Any]],
    current_box: Mapping[str, Any],
    next_boxes: Sequence[Mapping[str, Any]],
    sequence_state: Mapping[str, Any],
    chart_geometry: Mapping[str, Any],
) -> dict[str, Any]:
    counts = {
        "support": 0,
        "resistance": 0,
        "breakout": 0,
        "pullback": 0,
        "consolidation": 0,
        "reversal": 0,
        "directional_box": 0,
    }
    strengths = {key: 0.0 for key in counts}
    buy_pressure = 0.0
    sell_pressure = 0.0

    for zone in zones:
        bucket = _zone_bucket(zone)
        if bucket not in counts:
            continue
        confidence = _clip01(zone.get("confidence", 0.0))
        counts[bucket] += 1
        strengths[bucket] += confidence
        direction = str(zone.get("direction", "")).strip().upper()
        if bucket == "support":
            buy_pressure += confidence
        elif bucket == "resistance":
            sell_pressure += confidence
        elif bucket == "breakout":
            if direction == "BUY":
                buy_pressure += confidence
            elif direction == "SELL":
                sell_pressure += confidence
        elif bucket == "pullback":
            if direction == "BUY":
                buy_pressure += confidence * 0.60
            elif direction == "SELL":
                sell_pressure += confidence * 0.60
        elif bucket == "reversal":
            if direction == "BUY":
                buy_pressure += confidence
            elif direction == "SELL":
                sell_pressure += confidence
        elif bucket == "directional_box":
            if direction == "BUY":
                buy_pressure += confidence * 0.50
            elif direction == "SELL":
                sell_pressure += confidence * 0.50

    current_direction = str(current_box.get("direction", "HOLD")).upper()
    current_confidence = _clip01(current_box.get("confidence", 0.0))
    current_consolidation = _clip01(current_box.get("consolidation_score", 0.0))
    if current_direction == "BUY":
        buy_pressure += 0.30 * current_confidence
    elif current_direction == "SELL":
        sell_pressure += 0.30 * current_confidence

    next_breakout_strength = 0.0
    next_pullback_strength = 0.0
    for box in next_boxes[:3]:
        confidence = _clip01(box.get("confidence", 0.0))
        direction = str(box.get("direction", "HOLD")).upper()
        box_type = str(box.get("box_type", "balance")).lower()
        if box_type == "impulse":
            next_breakout_strength = max(next_breakout_strength, confidence)
            if direction == "BUY":
                buy_pressure += 0.26 * confidence
            elif direction == "SELL":
                sell_pressure += 0.26 * confidence
        elif box_type == "pullback":
            next_pullback_strength = max(next_pullback_strength, confidence)
            if direction == "BUY":
                buy_pressure += 0.14 * confidence
            elif direction == "SELL":
                sell_pressure += 0.14 * confidence

    spacing_consistency = _clip01(sequence_state.get("spacing_consistency", 0.0))
    sequence_agreement = _clip01(sequence_state.get("box_sequence_agreement", 0.0))
    path_clarity = _clip01(sequence_state.get("path_clarity", 0.0))
    geometry_confidence = _clip01(chart_geometry.get("geometry_confidence", chart_geometry.get("parse_conf", 0.0)))
    continuation_probability = _clip01(sequence_state.get("continuation_probability", 0.0))
    reversal_probability = _clip01(sequence_state.get("reversal_probability", 0.0))
    fakeout_probability = _clip01(sequence_state.get("fakeout_probability", 0.0))

    if continuation_probability >= reversal_probability:
        if current_direction == "BUY":
            buy_pressure += 0.18 * continuation_probability
        elif current_direction == "SELL":
            sell_pressure += 0.18 * continuation_probability
    else:
        if current_direction == "BUY":
            sell_pressure += 0.12 * reversal_probability
        elif current_direction == "SELL":
            buy_pressure += 0.12 * reversal_probability

    breakout_strength = float(
        np.clip(
            0.54 * _clip01(strengths["breakout"], 0.0)
            + 0.28 * next_breakout_strength
            + 0.18 * continuation_probability,
            0.0,
            1.0,
        )
    )
    pullback_strength = float(
        np.clip(
            0.62 * _clip01(strengths["pullback"], 0.0)
            + 0.20 * next_pullback_strength
            + 0.18 * fakeout_probability,
            0.0,
            1.0,
        )
    )
    consolidation_strength = float(
        np.clip(
            0.52 * _clip01(strengths["consolidation"], 0.0)
            + 0.28 * current_consolidation
            + 0.20 * (1.0 - abs(continuation_probability - reversal_probability)),
            0.0,
            1.0,
        )
    )

    total_pressure = max(buy_pressure + sell_pressure, 1e-6)
    structure_bias_direction = "HOLD"
    if buy_pressure > sell_pressure * 1.04:
        structure_bias_direction = "BUY"
    elif sell_pressure > buy_pressure * 1.04:
        structure_bias_direction = "SELL"
    structure_bias_confidence = float(np.clip(abs(buy_pressure - sell_pressure) / total_pressure, 0.0, 1.0))
    structure_readiness = float(
        np.clip(
            0.24 * geometry_confidence
            + 0.18 * spacing_consistency
            + 0.18 * sequence_agreement
            + 0.18 * path_clarity
            + 0.12 * breakout_strength
            + 0.10 * (1.0 - fakeout_probability),
            0.0,
            1.0,
        )
    )
    return {
        "object_count": int(len(objects)),
        "zone_count": int(len(zones)),
        "support_count": int(counts["support"]),
        "resistance_count": int(counts["resistance"]),
        "breakout_count": int(counts["breakout"]),
        "pullback_count": int(counts["pullback"]),
        "consolidation_count": int(counts["consolidation"]),
        "support_strength": float(np.clip(strengths["support"], 0.0, 1.0)),
        "resistance_strength": float(np.clip(strengths["resistance"], 0.0, 1.0)),
        "breakout_strength": breakout_strength,
        "pullback_strength": pullback_strength,
        "consolidation_strength": consolidation_strength,
        "reversal_strength": float(np.clip(strengths["reversal"], 0.0, 1.0)),
        "buy_pressure": float(np.clip(buy_pressure, 0.0, 1.0)),
        "sell_pressure": float(np.clip(sell_pressure, 0.0, 1.0)),
        "structure_bias_direction": structure_bias_direction,
        "structure_bias_confidence": structure_bias_confidence,
        "structure_readiness": structure_readiness,
        "geometry_confidence": geometry_confidence,
        "current_box_direction": current_direction,
    }


@dataclass(slots=True)
class TestTimeView:
    name: str
    image: Image.Image
    score: float
    style_signature: dict[str, float]
    artifact_summary: dict[str, float]
    parse_confidence: float
    visible_candle_count: int


class TestTimeAdaptationManager:
    def __init__(self, logger: Any) -> None:
        self.logger = logger

    def _candidate_views(self, image_rgb: Image.Image) -> list[tuple[str, Image.Image]]:
        original = image_rgb.convert("RGB")
        views: list[tuple[str, Image.Image]] = [("raw", original)]
        views.append(("clahe", apply_clahe(original, clip_limit=3).convert("RGB")))
        cropped = auto_crop_price_area(original).convert("RGB")
        if cropped.size != original.size:
            cropped = cropped.resize(original.size, Image.Resampling.BICUBIC)
        views.append(("crop_clahe", apply_clahe(cropped, clip_limit=3).convert("RGB")))
        return views

    def select_view(self, image_rgb: Image.Image, cv_engine: Any) -> dict[str, Any]:
        candidates: list[TestTimeView] = []
        for name, candidate in self._candidate_views(image_rgb):
            try:
                geom = cast(dict[str, Any], cv_engine._extract_candle_geometry(candidate))
                visible = cast(
                    list[dict[str, Any]],
                    cv_engine._select_recent_candles(
                        cv_engine._extract_candle_candidates(candidate, max_candidates=48),
                        max_count=48,
                    ),
                )
            except Exception:
                geom = {"parse_conf": 0.0}
                visible = []
            artifact_summary = build_artifact_summary(candidate, chart_geometry=geom)
            style_signature = build_style_signature(
                candidate,
                chart_geometry={"geometry_confidence": geom.get("parse_conf", 0.0)},
                sequence_state={"visible_candle_count": len(visible)},
            )
            score = float(
                np.clip(
                    0.58 * _clip01(geom.get("parse_conf", 0.0))
                    + 0.20 * _clip01(len(visible) / 48.0)
                    + 0.12 * _clip01(style_signature.get("contrast", 0.0))
                    - 0.30 * _clip01(artifact_summary.get("artifact_score", 0.0)),
                    0.0,
                    1.0,
                )
            )
            candidates.append(
                TestTimeView(
                    name=name,
                    image=candidate,
                    score=score,
                    style_signature=style_signature,
                    artifact_summary=artifact_summary,
                    parse_confidence=_clip01(geom.get("parse_conf", 0.0)),
                    visible_candle_count=int(len(visible)),
                )
            )
        best = max(candidates, key=lambda item: item.score) if candidates else None
        if best is None:
            return {
                "selected_view": "raw",
                "selected_image": image_rgb.convert("RGB"),
                "style_signature": build_style_signature(image_rgb),
                "artifact_summary": build_artifact_summary(image_rgb),
                "candidates": [],
            }
        return {
            "selected_view": best.name,
            "selected_image": best.image,
            "style_signature": dict(best.style_signature),
            "artifact_summary": dict(best.artifact_summary),
            "candidates": [
                {
                    "name": item.name,
                    "score": float(item.score),
                    "parse_confidence": float(item.parse_confidence),
                    "visible_candle_count": int(item.visible_candle_count),
                    "artifact_score": float(item.artifact_summary.get("artifact_score", 0.0)),
                }
                for item in candidates
            ],
        }


class OpenSetDetector:
    def __init__(self, logger: Any) -> None:
        self.logger = logger

    def assess(
        self,
        *,
        style_signature: Mapping[str, float],
        artifact_summary: Mapping[str, float],
        chart_geometry: Mapping[str, Any],
        sequence_state: Mapping[str, Any],
        local_ensemble: Mapping[str, Any] | None = None,
        memory_reference: Mapping[str, Any] | None = None,
        memory_summary: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        reference_mean = cast(dict[str, float], (memory_reference or {}).get("mean", {}))
        reference_std = cast(dict[str, float], (memory_reference or {}).get("std", {}))
        style_deltas: list[float] = []
        for key, value in style_signature.items():
            mean = _safe_float(reference_mean.get(key, value), value)
            std = max(_safe_float(reference_std.get(key, 0.12), 0.12), 0.05)
            style_deltas.append(abs(_safe_float(value, 0.0) - mean) / std)
        style_novelty = float(np.clip(np.mean(np.asarray(style_deltas, dtype=np.float32)) / 3.0, 0.0, 1.0)) if style_deltas else 0.0
        ensemble_view = cast(dict[str, Any], (local_ensemble or {}).get("ensemble", {}))
        disagreement = _clip01(ensemble_view.get("disagreement", 0.0))
        entropy = _clip01(ensemble_view.get("entropy", 0.0))
        parse_penalty = 1.0 - _clip01(chart_geometry.get("geometry_confidence", 0.0))
        structure_penalty = float(
            np.clip(
                0.55 * (1.0 - _clip01(sequence_state.get("box_sequence_agreement", 0.0)))
                + 0.45 * _clip01(sequence_state.get("color_flip_rate", 0.0)),
                0.0,
                1.0,
            )
        )
        ambiguity = _clip01((memory_summary or {}).get("ambiguity", 0.0))
        artifact_score = _clip01(artifact_summary.get("artifact_score", 0.0))
        ood_score = float(
            np.clip(
                0.30 * style_novelty
                + 0.26 * artifact_score
                + 0.18 * parse_penalty
                + 0.14 * disagreement
                + 0.07 * entropy
                + 0.05 * ambiguity
                + 0.10 * structure_penalty,
                0.0,
                1.0,
            )
        )
        flags: list[str] = []
        if style_novelty >= 0.60:
            flags.append("style_shift")
        if artifact_score >= 0.60:
            flags.append("artifact_heavy")
        if parse_penalty >= 0.65:
            flags.append("parser_unreliable")
        if disagreement >= 0.35:
            flags.append("ensemble_disagreement")
        if structure_penalty >= 0.60:
            flags.append("regime_unfamiliar")
        return {
            "ood_score": ood_score,
            "style_novelty": style_novelty,
            "artifact_score": artifact_score,
            "parse_penalty": parse_penalty,
            "structure_penalty": structure_penalty,
            "disagreement": disagreement,
            "entropy": entropy,
            "flags": flags,
            "force_hold": bool(ood_score >= 0.72 or artifact_score >= 0.82),
        }


class ContinualLearningManager:
    def __init__(
        self,
        data_dir: Path,
        logger: Any,
        *,
        replay_buffer_size: int = 1500,
        ewc_lambda: float = 0.35,
        lwf_temperature: float = 2.0,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.logger = logger
        self._lock = RLock()
        self.replay_buffer_size = int(max(replay_buffer_size, 50))
        self.ewc_lambda = float(max(ewc_lambda, 0.0))
        self.lwf_temperature = float(max(lwf_temperature, 1.0))
        self.replay_buffer_path = self.data_dir / "replay_buffer.jsonl"
        self.adapter_bank_path = self.data_dir / "adapter_bank.json"
        self.pending_context_path = self.data_dir / "pending_contexts.json"
        self.replay_snapshot_dir = self.data_dir / "replay_snapshots"
        self.adapter_bank = cast(dict[str, dict[str, Any]], _read_json(self.adapter_bank_path, {}))
        self.pending_contexts = cast(dict[str, dict[str, Any]], _read_json(self.pending_context_path, {}))

    def derive_context_key(
        self,
        style_signature: Mapping[str, float],
        *,
        chart_state: Mapping[str, Any] | None = None,
        sequence_state: Mapping[str, Any] | None = None,
    ) -> str:
        chart_state = chart_state or {}
        sequence_state = sequence_state or {}
        theme = "dark" if _clip01(style_signature.get("dark_theme", 0.0)) >= 0.5 else "light"
        density_raw = _clip01(style_signature.get("candle_density", sequence_state.get("visible_candle_count", 0) / 96.0))
        density = "dense" if density_raw >= 0.66 else ("mid" if density_raw >= 0.33 else "sparse")
        width_bucket = "wide" if _safe_float(style_signature.get("aspect_ratio", 1.0), 1.0) >= 1.55 else "standard"
        timeframe = str(chart_state.get("timeframe", "M5")).upper()
        structure = str(
            chart_state.get(
                "structure_setup",
                chart_state.get("entry_type", chart_state.get("continuation_signal", "unknown")),
            )
        ).lower()
        pair_identity = ""
        for key in ("pair_key", "pair_name", "symbol", "instrument", "asset", "ticker", "source_image_hash"):
            value = chart_state.get(key)
            if value:
                pair_identity = str(value).strip().lower()
                break
        context_parts = [theme, width_bucket, density, timeframe, structure]
        if pair_identity:
            context_parts.append(pair_identity)
        return "|".join(context_parts)

    def adapter_profile_for_context(self, context_key: str) -> dict[str, Any]:
        with self._lock:
            profile = dict(self.adapter_bank.get(context_key, {}))
        return {
            "context_key": context_key,
            "confidence_scale": float(np.clip(_safe_float(profile.get("confidence_scale", 1.0), 1.0), 0.75, 1.25)),
            "direction_bias": dict(profile.get("direction_bias", {"BUY": 0.0, "SELL": 0.0})),
            "model_weight_biases": dict(profile.get("model_weight_biases", {})),
            "success_rate": float(np.clip(_safe_float(profile.get("success_rate", 0.5), 0.5), 0.0, 1.0)),
            "count": int(max(_safe_float(profile.get("count", 0), 0.0), 0.0)),
            "lora_adapter_name": str(profile.get("lora_adapter_name", "")),
            "lora_adapter_file": str(profile.get("lora_adapter_file", "")),
        }

    def _persist_replay_snapshot(self, image_hash: str, image_rgb: Image.Image | None) -> str:
        if image_rgb is None:
            return ""
        try:
            self.replay_snapshot_dir.mkdir(parents=True, exist_ok=True)
            snapshot_path = self.replay_snapshot_dir / f"{str(image_hash)}.png"
            buffer = io.BytesIO()
            image_rgb.save(buffer, format="PNG")
            _atomic_write_bytes(snapshot_path, buffer.getvalue())
            return str(snapshot_path)
        except Exception:
            return ""

    def record_inference_context(
        self,
        *,
        image_hash: str,
        context_id: str = "",
        context_key: str,
        context_descriptor: str,
        local_ensemble: Mapping[str, Any],
        predicted_action: str,
        confidence: float,
        style_signature: Mapping[str, float],
        ood_summary: Mapping[str, Any] | None = None,
        source_path: str = "",
        selected_view: str = "",
        snapshot_image: Image.Image | None = None,
    ) -> None:
        pending_key = str(context_id or image_hash).strip()
        if not pending_key:
            pending_key = str(image_hash)
        ensemble_view = cast(dict[str, Any], local_ensemble.get("ensemble", {}))
        snapshot_path = self._persist_replay_snapshot(image_hash, snapshot_image)
        with self._lock:
            self.pending_contexts[pending_key] = {
                "context_id": pending_key,
                "image_hash": str(image_hash),
                "context_key": str(context_key),
                "context_descriptor": str(context_descriptor),
                "predicted_action": str(predicted_action).upper(),
                "confidence": float(np.clip(confidence, 0.0, 1.0)),
                "champion_model": str(ensemble_view.get("champion_model", "")),
                "confirmer_model": str(ensemble_view.get("confirmer_model", "")),
                "style_signature": dict(style_signature),
                "ood_summary": dict(ood_summary or {}),
                "source_path": str(source_path),
                "selected_view": str(selected_view),
                "snapshot_path": snapshot_path,
            }
            if len(self.pending_contexts) > 512:
                keys = list(self.pending_contexts.keys())[-512:]
                self.pending_contexts = {key: self.pending_contexts[key] for key in keys}
            _write_json(self.pending_context_path, self.pending_contexts)

    def register_context_adapter(
        self,
        context_key: str,
        adapter_name: str,
        *,
        adapter_file: str = "",
    ) -> None:
        with self._lock:
            profile = dict(self.adapter_bank.get(str(context_key), {}))
            profile["lora_adapter_name"] = str(adapter_name)
            if adapter_file:
                profile["lora_adapter_file"] = str(adapter_file)
            self.adapter_bank[str(context_key)] = profile
            _write_json(self.adapter_bank_path, self.adapter_bank)

    def record_feedback(
        self,
        image_hash: str,
        verdict: str,
        reason: str,
        *,
        context_id: str = "",
        submission_id: str = "",
        operator_confidence: float = 1.0,
        feedback_meta: Mapping[str, Any] | None = None,
        feedback_image_path: str = "",
        feedback_image_sha256: str = "",
        feedback_image_meta: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        image_key = str(image_hash)
        pending_key = str(context_id or image_key).strip() or image_key
        with self._lock:
            submission_key = str(submission_id or "").strip()
            if submission_key and self.replay_buffer_path.exists():
                try:
                    with self.replay_buffer_path.open("r", encoding="utf-8") as handle:
                        for line in handle:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                existing = cast(dict[str, Any], json.loads(line))
                            except Exception:
                                continue
                            if str(existing.get("submission_id", "")).strip() == submission_key:
                                return existing
                except Exception:
                    pass
            context = dict(self.pending_contexts.get(pending_key, {}))
            if not context and pending_key != image_key:
                context = dict(self.pending_contexts.get(image_key, {}))
            if not context:
                return {}
            context_key = str(context.get("context_key", "default"))
            predicted_action = str(context.get("predicted_action", "HOLD")).upper()
            verdict_upper = str(verdict).upper()
            success = predicted_action == verdict_upper
            profile = dict(self.adapter_bank.get(context_key, {}))
            count = int(profile.get("count", 0) or 0) + 1
            old_success = float(profile.get("success_rate", 0.5) or 0.5)
            success_rate = 0.85 * old_success + 0.15 * float(1.0 if success else 0.0)
            confidence_scale = float(profile.get("confidence_scale", 1.0) or 1.0)
            confidence_scale = float(np.clip(confidence_scale * (1.02 if success else 0.97), 0.75, 1.25))
            direction_bias = dict(profile.get("direction_bias", {"BUY": 0.0, "SELL": 0.0}))
            if verdict_upper in {"BUY", "SELL"}:
                direction_bias.setdefault("BUY", 0.0)
                direction_bias.setdefault("SELL", 0.0)
                direction_bias[verdict_upper] = float(np.clip(_safe_float(direction_bias.get(verdict_upper, 0.0), 0.0) + 0.02, -0.25, 0.25))
                other = "SELL" if verdict_upper == "BUY" else "BUY"
                direction_bias[other] = float(np.clip(_safe_float(direction_bias.get(other, 0.0), 0.0) - 0.01, -0.25, 0.25))
            if predicted_action in {"BUY", "SELL"} and not success:
                direction_bias[predicted_action] = float(np.clip(_safe_float(direction_bias.get(predicted_action, 0.0), 0.0) - 0.03, -0.25, 0.25))
            model_weight_biases = dict(profile.get("model_weight_biases", {}))
            for field_name, delta in (("champion_model", 0.05 if success else -0.06), ("confirmer_model", 0.02 if success else -0.03)):
                model_name = str(context.get(field_name, "")).strip()
                if not model_name:
                    continue
                current = _safe_float(model_weight_biases.get(model_name, 0.0), 0.0)
                model_weight_biases[model_name] = float(np.clip(current + delta, -0.25, 0.25))
            self.adapter_bank[context_key] = {
                "count": count,
                "success_rate": success_rate,
                "confidence_scale": confidence_scale,
                "direction_bias": direction_bias,
                "model_weight_biases": model_weight_biases,
                "last_reason": str(reason),
                "lora_adapter_name": str(profile.get("lora_adapter_name", "")),
                "lora_adapter_file": str(profile.get("lora_adapter_file", "")),
            }
            _write_json(self.adapter_bank_path, self.adapter_bank)
            inference_snapshot_path = str(context.get("snapshot_path", ""))
            saved_feedback_image_path = str(feedback_image_path or "").strip()
            learning_snapshot_path = saved_feedback_image_path or inference_snapshot_path
            replay_item = {
                "submission_id": submission_key,
                "image_hash": image_key,
                "context_id": str(context.get("context_id", pending_key or image_key)),
                "context_key": context_key,
                "context_descriptor": str(context.get("context_descriptor", "")),
                "predicted_action": predicted_action,
                "verdict": verdict_upper,
                "reason": str(reason),
                "success": bool(success),
                "operator_confidence": float(np.clip(operator_confidence, 0.05, 1.0)),
                "feedback_meta": dict(feedback_meta or {}),
                "confidence": float(np.clip(_safe_float(context.get("confidence", 0.0), 0.0), 0.0, 1.0)),
                "champion_model": str(context.get("champion_model", "")),
                "confirmer_model": str(context.get("confirmer_model", "")),
                "style_signature": dict(context.get("style_signature", {})),
                "ood_summary": dict(context.get("ood_summary", {})),
                "source_path": str(context.get("source_path", "")),
                "selected_view": str(context.get("selected_view", "")),
                "snapshot_path": learning_snapshot_path,
                "inference_snapshot_path": inference_snapshot_path,
                "feedback_image_path": saved_feedback_image_path,
                "feedback_image_sha256": str(feedback_image_sha256 or "").strip(),
                "feedback_image_meta": dict(feedback_image_meta or {}),
                "lora_adapter_name": str(profile.get("lora_adapter_name", "")),
                "lora_adapter_file": str(profile.get("lora_adapter_file", "")),
                "ewc_lambda": self.ewc_lambda,
                "lwf_temperature": self.lwf_temperature,
            }
            rows: list[dict[str, Any]] = []
            if self.replay_buffer_path.exists():
                with self.replay_buffer_path.open("r", encoding="utf-8") as handle:
                    for line in handle:
                        line = line.strip()
                        if line:
                            try:
                                rows.append(cast(dict[str, Any], json.loads(line)))
                            except Exception:
                                continue
            rows.append(replay_item)
            rows = rows[-self.replay_buffer_size :]
            _write_jsonl(self.replay_buffer_path, rows)
            self.pending_contexts.pop(pending_key, None)
            if pending_key != image_key:
                legacy_context = self.pending_contexts.get(image_key)
                if isinstance(legacy_context, dict) and str(legacy_context.get("context_id", "")).strip() == pending_key:
                    self.pending_contexts.pop(image_key, None)
            _write_json(self.pending_context_path, self.pending_contexts)
            return replay_item
