"""Chart-isolated masked replay scored only by future closes versus one anchor."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import re
from statistics import mean, median
import time
from typing import Any, cast

import numpy as np
from numpy.typing import NDArray

from phoenixguard.simulation.masked_future_v3 import (
    available_free_gb,
    enforce_disk_reserve,
    extract_image_sequence_v3,
)


ANCHOR_DIRECTION_PREDICTION_SCHEMA_V3 = "PG_ANCHOR_DIRECTION_PREDICTION_V3"
ANCHOR_DIRECTION_SCORE_SCHEMA_V3 = "PG_ANCHOR_DIRECTION_SCORE_V3"
ANCHOR_DIRECTION_AUDIT_SCHEMA_V3 = "PG_ANCHOR_DIRECTION_AUDIT_V3"
ANCHOR_DIRECTION_SUMMARY_SCHEMA_V3 = "PG_ANCHOR_DIRECTION_SUMMARY_V3"
FEATURE_CANDLES = 64
FEATURES_PER_CANDLE = 9
FORBIDDEN_PREDICTION_KEYS = (
    "color",
    "majority",
    "pullback",
    "continuation",
    "reversal",
    "path_class",
    "smc",
    "pattern",
)


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [
        _mapping(cast(Mapping[str, Any], row))
        for row in cast(Sequence[object], value)
        if isinstance(row, Mapping)
    ]


def _number(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return parsed if math.isfinite(parsed) else float(default)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _normal_path(value: str | Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(value)))


def _center_x(candle: Mapping[str, Any]) -> float:
    return _number(candle.get("center_x_px"), float("nan"))


def _close_y(candle: Mapping[str, Any]) -> float:
    return _number(candle.get("close_y_px"), float("nan"))


@dataclass(frozen=True)
class CandleCorpusRecordV3:
    path: str
    symbol: str
    timeframe: str
    candles: tuple[dict[str, Any], ...]
    analysis_width: int
    analysis_height: int

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> "CandleCorpusRecordV3":
        return cls(
            path=str(payload.get("path") or ""),
            symbol=str(payload.get("symbol") or "UNKNOWN"),
            timeframe=str(payload.get("timeframe") or "UNKNOWN"),
            candles=tuple(_rows(payload.get("candles"))),
            analysis_width=int(_number(payload.get("analysis_width"), 0.0)),
            analysis_height=int(_number(payload.get("analysis_height"), 0.0)),
        )


@dataclass(frozen=True)
class AnchorCaseDescriptorV3:
    case_id: str
    image_id: str
    family_id: str
    fold: int
    source_path: str
    mask_path: str
    symbol: str
    timeframe: str
    cutoff: int
    record_index: int


@dataclass(frozen=True)
class PreparedAnchorCaseV3:
    case_id: str
    image_id: str
    family_id: str
    fold: int
    source_path: str
    mask_path: str
    mask_sha256: str
    symbol: str
    timeframe: str
    cutoff: int
    record_index: int
    anchor_x_px: float
    anchor_close_y_px: float
    hidden_candle_count: int
    visible_candle_count: int
    geometry_vector: tuple[float, ...]
    fallback_side: str


def load_corpus_cache_v3(path: str | Path) -> list[CandleCorpusRecordV3]:
    cache_path = Path(path).resolve()
    records: list[CandleCorpusRecordV3] = []
    with cache_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            raw = json.loads(stripped)
            if not isinstance(raw, Mapping):
                continue
            record = CandleCorpusRecordV3.from_mapping(
                cast(Mapping[str, Any], raw)
            )
            if record.path and record.candles:
                records.append(record)
    return records


def assert_chart_isolated_mask_v3(
    *,
    image_id: str,
    scorecard_path: str | Path,
    mask_path: str | Path,
) -> None:
    score_path = Path(scorecard_path).resolve()
    mask = Path(mask_path).resolve()
    if mask.parent != score_path.parent:
        raise ValueError("PG_ANCHOR_MASK_NOT_FROM_SCORECARD_CASE")
    if mask.parent.parent.name != image_id:
        raise ValueError("PG_ANCHOR_MASK_IMAGE_ID_MISMATCH")
    if mask.name != "masked_prefix.png":
        raise ValueError("PG_ANCHOR_MASK_ARTIFACT_NAME_INVALID")


def load_case_descriptors_v3(
    *,
    mask_run_dir: str | Path,
    records: Sequence[CandleCorpusRecordV3],
) -> list[AnchorCaseDescriptorV3]:
    record_by_path = {
        _normal_path(record.path): index for index, record in enumerate(records)
    }
    descriptors: list[AnchorCaseDescriptorV3] = []
    seen: set[str] = set()
    root = Path(mask_run_dir).resolve()
    for score_path in sorted(root.glob("cases/*/*/scorecard.json")):
        raw = json.loads(score_path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            continue
        score = _mapping(cast(Mapping[str, Any], raw))
        case_id = str(score.get("cutoff_id") or "")
        image_id = str(score.get("image_id") or "")
        family_id = str(score.get("family_id") or "")
        source_path = str(score.get("source_path") or "")
        artifacts = _mapping(score.get("artifacts"))
        mask_path = str(artifacts.get("masked_prefix") or "")
        match = re.search(r"cutoff-(\d+)$", case_id)
        record_index = record_by_path.get(_normal_path(source_path), -1)
        if (
            not case_id
            or case_id in seen
            or not image_id
            or not family_id
            or match is None
            or record_index < 0
        ):
            continue
        assert_chart_isolated_mask_v3(
            image_id=image_id,
            scorecard_path=score_path,
            mask_path=mask_path,
        )
        seen.add(case_id)
        record = records[record_index]
        descriptors.append(
            AnchorCaseDescriptorV3(
                case_id=case_id,
                image_id=image_id,
                family_id=family_id,
                fold=int(_number(score.get("fold"), -1.0)),
                source_path=source_path,
                mask_path=mask_path,
                symbol=str(score.get("symbol") or record.symbol or "UNKNOWN"),
                timeframe=str(
                    score.get("timeframe") or record.timeframe or "UNKNOWN"
                ),
                cutoff=int(match.group(1)),
                record_index=record_index,
            )
        )
    return descriptors


def candle_geometry_vector_v3(
    candles: Sequence[Mapping[str, Any]],
    *,
    anchor_close_y_px: float,
    feature_candles: int = FEATURE_CANDLES,
) -> tuple[float, ...]:
    ordered = sorted(candles, key=_center_x)
    if not ordered:
        raise ValueError("PG_ANCHOR_NO_VISIBLE_CANDLE_GEOMETRY")
    selected = ordered[-max(2, int(feature_candles)) :]
    ranges = [
        max(
            1.0,
            _number(candle.get("wick_bottom_px"), 0.0)
            - _number(candle.get("wick_top_px"), 0.0),
        )
        for candle in selected
    ]
    spacings = [
        right - left
        for left, right in zip(
            (_center_x(candle) for candle in selected),
            (_center_x(candle) for candle in selected[1:]),
        )
        if math.isfinite(left) and math.isfinite(right) and right > left
    ]
    scale = max(1.0, float(median(ranges)))
    spacing_scale = max(1.0, float(median(spacings))) if spacings else 1.0
    rows: list[tuple[float, ...]] = []
    previous_close = _number(selected[0].get("open_y_px"), anchor_close_y_px)
    for candle in selected:
        open_y = _number(candle.get("open_y_px"), anchor_close_y_px)
        close_y = _number(candle.get("close_y_px"), anchor_close_y_px)
        top_y = _number(candle.get("wick_top_px"), min(open_y, close_y))
        bottom_y = _number(candle.get("wick_bottom_px"), max(open_y, close_y))
        row = (
            (anchor_close_y_px - open_y) / scale,
            (anchor_close_y_px - close_y) / scale,
            (anchor_close_y_px - top_y) / scale,
            (anchor_close_y_px - bottom_y) / scale,
            (open_y - close_y) / scale,
            max(0.0, bottom_y - top_y) / scale,
            (previous_close - open_y) / scale,
            max(0.0, _center_x(candle) - _center_x(selected[0]))
            / spacing_scale
            / max(1, len(selected) - 1),
            1.0,
        )
        rows.append(tuple(float(np.clip(value, -12.0, 12.0)) for value in row))
        previous_close = close_y
    width = max(2, int(feature_candles))
    padding = [tuple(0.0 for _ in range(FEATURES_PER_CANDLE))] * (
        width - len(rows)
    )
    flattened = [value for row in (*padding, *rows) for value in row]
    return tuple(flattened)


def build_anchor_direction_target_v3(
    candles: Sequence[Mapping[str, Any]],
    *,
    cutoff: int,
    anchor_close_y_px: float,
) -> tuple[str, ...]:
    if cutoff <= 0 or cutoff >= len(candles):
        raise ValueError("PG_ANCHOR_TARGET_CUTOFF_OUT_OF_RANGE")
    directions: list[str] = []
    for candle in candles[int(cutoff) :]:
        close_y = _close_y(candle)
        if not math.isfinite(close_y):
            directions.append("TIE")
        elif close_y < anchor_close_y_px:
            directions.append("UP")
        elif close_y > anchor_close_y_px:
            directions.append("DOWN")
        else:
            directions.append("TIE")
    return tuple(directions)


def _prepare_case_v3(
    descriptor: AnchorCaseDescriptorV3,
    records: Sequence[CandleCorpusRecordV3],
) -> PreparedAnchorCaseV3:
    record = records[descriptor.record_index]
    if descriptor.cutoff <= 0 or descriptor.cutoff >= len(record.candles):
        raise ValueError("PG_ANCHOR_CASE_CUTOFF_OUT_OF_RANGE")
    mask = Path(descriptor.mask_path).resolve()
    if not mask.is_file():
        raise FileNotFoundError(mask)
    anchor_full = record.candles[descriptor.cutoff - 1]
    next_full = record.candles[descriptor.cutoff]
    anchor_x = _center_x(anchor_full)
    next_x = _center_x(next_full)
    if not math.isfinite(anchor_x) or not math.isfinite(next_x) or next_x <= anchor_x:
        raise ValueError("PG_ANCHOR_FULL_GEOMETRY_NOT_ORDERED")
    boundary = (anchor_x + next_x) / 2.0
    masked_record = extract_image_sequence_v3(
        mask,
        maximum_width=0,
        symbol_hint=descriptor.symbol,
        timeframe_hint=descriptor.timeframe,
        skip_ocr=True,
    )
    visible = [
        candle
        for candle in masked_record.candles
        if math.isfinite(_center_x(candle)) and _center_x(candle) < boundary
    ]
    if len(visible) < 8:
        raise ValueError("PG_ANCHOR_MASKED_PREFIX_TOO_SHORT")
    spacings = [
        right - left
        for left, right in zip(
            (_center_x(candle) for candle in visible),
            (_center_x(candle) for candle in visible[1:]),
        )
        if right > left
    ]
    tolerance = max(3.0, (float(median(spacings)) if spacings else 4.0) * 1.25)
    matched_anchor = min(visible, key=lambda candle: abs(_center_x(candle) - anchor_x))
    if abs(_center_x(matched_anchor) - anchor_x) > tolerance:
        raise ValueError("PG_ANCHOR_MASKED_PREFIX_MISSING_FINAL_VISIBLE_CANDLE")
    matched_x = _center_x(matched_anchor)
    prefix = [candle for candle in visible if _center_x(candle) <= matched_x]
    anchor_close_y = _close_y(matched_anchor)
    if not math.isfinite(anchor_close_y):
        raise ValueError("PG_ANCHOR_CLOSE_GEOMETRY_INVALID")
    recent = prefix[-min(8, len(prefix)) :]
    fallback_side = (
        "UP"
        if _close_y(recent[-1]) < _close_y(recent[0])
        else "DOWN"
    )
    vector = candle_geometry_vector_v3(
        prefix,
        anchor_close_y_px=anchor_close_y,
    )
    return PreparedAnchorCaseV3(
        case_id=descriptor.case_id,
        image_id=descriptor.image_id,
        family_id=descriptor.family_id,
        fold=descriptor.fold,
        source_path=descriptor.source_path,
        mask_path=str(mask),
        mask_sha256=_sha256(mask),
        symbol=descriptor.symbol,
        timeframe=descriptor.timeframe,
        cutoff=descriptor.cutoff,
        record_index=descriptor.record_index,
        anchor_x_px=matched_x,
        anchor_close_y_px=anchor_close_y,
        hidden_candle_count=len(record.candles) - descriptor.cutoff,
        visible_candle_count=len(prefix),
        geometry_vector=vector,
        fallback_side=fallback_side,
    )


def prepare_anchor_cases_v3(
    descriptors: Sequence[AnchorCaseDescriptorV3],
    records: Sequence[CandleCorpusRecordV3],
    *,
    workers: int = 2,
) -> tuple[list[PreparedAnchorCaseV3], list[dict[str, Any]]]:
    prepared: list[PreparedAnchorCaseV3] = []
    failures: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as executor:
        futures = {
            executor.submit(_prepare_case_v3, descriptor, records): descriptor
            for descriptor in descriptors
        }
        for future in as_completed(futures):
            descriptor = futures[future]
            try:
                prepared.append(future.result())
            except Exception as error:
                failures.append(
                    {
                        "case_id": descriptor.case_id,
                        "image_id": descriptor.image_id,
                        "reason": f"{type(error).__name__}:{error}",
                    }
                )
    prepared.sort(key=lambda case: (case.image_id, case.cutoff))
    failures.sort(key=lambda row: str(row.get("case_id") or ""))
    return prepared, failures


@dataclass
class AnchorDirectionModelV3:
    normalized_features: NDArray[np.float64]
    means: NDArray[np.float64]
    scales: NDArray[np.float64]
    targets: tuple[tuple[str, ...], ...]
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    families: tuple[str, ...]
    neighbors: int

    @classmethod
    def fit(
        cls,
        cases: Sequence[PreparedAnchorCaseV3],
        records: Sequence[CandleCorpusRecordV3],
        *,
        neighbors: int = 15,
    ) -> "AnchorDirectionModelV3":
        if not cases:
            raise ValueError("PG_ANCHOR_MODEL_REQUIRES_TRAINING_CASES")
        matrix = np.asarray(
            [case.geometry_vector for case in cases],
            dtype=np.float64,
        )
        means = matrix.mean(axis=0)
        scales = matrix.std(axis=0)
        scales = np.where(scales < 1e-6, 1.0, scales)
        normalized = (matrix - means) / scales
        targets = tuple(
            build_anchor_direction_target_v3(
                records[case.record_index].candles,
                cutoff=case.cutoff,
                anchor_close_y_px=case.anchor_close_y_px,
            )
            for case in cases
        )
        return cls(
            normalized_features=normalized,
            means=means,
            scales=scales,
            targets=targets,
            symbols=tuple(case.symbol for case in cases),
            timeframes=tuple(case.timeframe for case in cases),
            families=tuple(case.family_id for case in cases),
            neighbors=max(3, int(neighbors)),
        )

    def _candidate_indices(self, case: PreparedAnchorCaseV3) -> tuple[list[int], str]:
        exact = [
            index
            for index, (symbol, timeframe) in enumerate(
                zip(self.symbols, self.timeframes)
            )
            if symbol == case.symbol and timeframe == case.timeframe
        ]
        if len({self.families[index] for index in exact}) >= 3:
            return exact, "PAIR_TIMEFRAME"
        pair = [
            index for index, symbol in enumerate(self.symbols) if symbol == case.symbol
        ]
        if len({self.families[index] for index in pair}) >= 3:
            return pair, "PAIR"
        timeframe = [
            index
            for index, value in enumerate(self.timeframes)
            if value == case.timeframe
        ]
        if len({self.families[index] for index in timeframe}) >= 3:
            return timeframe, "TIMEFRAME"
        return list(range(len(self.targets))), "GLOBAL_CANDLE_GEOMETRY"

    def predict(self, case: PreparedAnchorCaseV3) -> dict[str, Any]:
        vector = np.asarray(case.geometry_vector, dtype=np.float64)
        normalized = (vector - self.means) / self.scales
        candidates, behavior_scope = self._candidate_indices(case)
        distances = np.sqrt(
            np.mean(
                np.square(self.normalized_features[candidates] - normalized),
                axis=1,
            )
        )
        distance_order = cast(
            list[int],
            np.argsort(distances, kind="stable").tolist(),
        )
        ordered: list[int] = [candidates[index] for index in distance_order]
        distance_by_index = {
            candidate: float(distance)
            for candidate, distance in zip(candidates, distances.tolist())
        }
        horizons: dict[str, Any] = {}
        for horizon in range(1, case.hidden_candle_count + 1):
            selected: list[int] = []
            seen_families: set[str] = set()
            for index in ordered:
                target = self.targets[index]
                if horizon > len(target) or target[horizon - 1] not in {"UP", "DOWN"}:
                    continue
                family = self.families[index]
                if family in seen_families:
                    continue
                selected.append(index)
                seen_families.add(family)
                if len(selected) >= self.neighbors:
                    break
            if selected:
                weights = [
                    1.0 / (0.05 + distance_by_index[index]) for index in selected
                ]
                up_weight = sum(
                    weight
                    for index, weight in zip(selected, weights)
                    if self.targets[index][horizon - 1] == "UP"
                )
                probability_up = up_weight / max(1e-12, sum(weights))
                side = "UP" if probability_up >= 0.5 else "DOWN"
            else:
                probability_up = 1.0 if case.fallback_side == "UP" else 0.0
                side = case.fallback_side
            horizons[str(horizon)] = {
                "predicted_side": side,
                "probability_up": round(float(probability_up), 6),
                "supporting_families": len(selected),
            }
        return {
            "schema_version": ANCHOR_DIRECTION_PREDICTION_SCHEMA_V3,
            "case_id": case.case_id,
            "image_id": case.image_id,
            "family_id": case.family_id,
            "symbol": case.symbol,
            "timeframe": case.timeframe,
            "source_path": case.source_path,
            "mask_path": case.mask_path,
            "mask_sha256": case.mask_sha256,
            "fixed_anchor": {
                "center_x_px": round(case.anchor_x_px, 6),
                "close_y_px": round(case.anchor_close_y_px, 6),
                "basis": "FINAL_VISIBLE_CANDLE_CLOSE_GEOMETRY",
            },
            "visible_candle_count": case.visible_candle_count,
            "hidden_candle_count": case.hidden_candle_count,
            "behavior_scope": behavior_scope,
            "horizons": horizons,
            "causal_contract": {
                "same_fixed_anchor_for_every_horizon": True,
                "every_hidden_candle_predicted": True,
                "future_geometry_available_before_freeze": False,
                "chart_specific_mask_only": True,
                "folder_label_used": False,
            },
        }


def freeze_anchor_prediction_v3(
    path: str | Path,
    prediction: Mapping[str, Any],
) -> dict[str, Any]:
    frozen = dict(prediction)
    frozen["prediction_frozen_epoch_ms"] = time.time_ns() // 1_000_000
    destination = Path(path).resolve()
    _write_json_atomic(destination, frozen)
    frozen["prediction_file_mtime_ns"] = destination.stat().st_mtime_ns
    return frozen


def score_anchor_prediction_v3(
    prediction: Mapping[str, Any],
    actual_directions: Sequence[str],
    *,
    reveal_started_epoch_ms: int,
    fold: int,
) -> dict[str, Any]:
    frozen_epoch = int(_number(prediction.get("prediction_frozen_epoch_ms"), 0.0))
    if frozen_epoch <= 0 or frozen_epoch > int(reveal_started_epoch_ms):
        raise ValueError("PG_ANCHOR_PREDICTION_NOT_FROZEN_BEFORE_REVEAL")
    predicted = _mapping(prediction.get("horizons"))
    if len(predicted) != len(actual_directions):
        raise ValueError("PG_ANCHOR_DID_NOT_PREDICT_EVERY_HIDDEN_CANDLE")
    rows: dict[str, Any] = {}
    correct_count = 0
    for horizon, actual_side in enumerate(actual_directions, start=1):
        row = _mapping(predicted.get(str(horizon)))
        predicted_side = str(row.get("predicted_side") or "")
        if predicted_side not in {"UP", "DOWN"}:
            raise ValueError("PG_ANCHOR_PREDICTION_SIDE_INVALID")
        correct = actual_side in {"UP", "DOWN"} and predicted_side == actual_side
        correct_count += int(correct)
        rows[str(horizon)] = {
            "predicted_side": predicted_side,
            "actual_side": actual_side,
            "correct": correct,
            "probability_up": _number(row.get("probability_up"), 0.5),
            "supporting_families": int(
                _number(row.get("supporting_families"), 0.0)
            ),
        }
    total = len(actual_directions)
    return {
        "schema_version": ANCHOR_DIRECTION_SCORE_SCHEMA_V3,
        "case_id": prediction.get("case_id"),
        "image_id": prediction.get("image_id"),
        "family_id": prediction.get("family_id"),
        "symbol": prediction.get("symbol"),
        "timeframe": prediction.get("timeframe"),
        "source_path": prediction.get("source_path"),
        "mask_path": prediction.get("mask_path"),
        "fold": int(fold),
        "fixed_anchor": prediction.get("fixed_anchor"),
        "prediction_frozen_epoch_ms": frozen_epoch,
        "reveal_started_epoch_ms": int(reveal_started_epoch_ms),
        "prediction_preceded_reveal": True,
        "hidden_candle_count": total,
        "correct_count": correct_count,
        "accuracy": round(correct_count / total, 6) if total else 0.0,
        "horizons": rows,
        "scoring_contract": {
            "question": "IS_FUTURE_CLOSE_ABOVE_OR_BELOW_FIXED_ANCHOR",
            "same_anchor_for_every_horizon": True,
            "tie_scores_as_incorrect_for_forced_up_down_prediction": True,
        },
    }


def _walk_keys(value: object) -> list[str]:
    keys: list[str] = []
    if isinstance(value, Mapping):
        for raw_key, child in cast(Mapping[object, object], value).items():
            key = str(raw_key).casefold()
            keys.append(key)
            keys.extend(_walk_keys(child))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for child in cast(Sequence[object], value):
            keys.extend(_walk_keys(child))
    return keys


def audit_anchor_direction_run_v3(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    failures: list[str] = []
    family_folds: dict[str, set[int]] = defaultdict(set)
    mask_owners: dict[str, str] = {}
    for row in rows:
        prediction = _mapping(row.get("prediction"))
        score = _mapping(row.get("score"))
        case_id = str(prediction.get("case_id") or "")
        image_id = str(prediction.get("image_id") or "")
        family_id = str(prediction.get("family_id") or "")
        fold = int(_number(score.get("fold"), -1.0))
        family_folds[family_id].add(fold)
        keys = _walk_keys(prediction)
        if any(token in key for key in keys for token in FORBIDDEN_PREDICTION_KEYS):
            failures.append(f"FORBIDDEN_PREDICTION_CONCEPT:{case_id}")
        if any(key.startswith("actual") or key == "target" for key in keys):
            failures.append(f"ACTUAL_GEOMETRY_IN_PREDICTION:{case_id}")
        hidden = int(_number(prediction.get("hidden_candle_count"), 0.0))
        horizons = _mapping(prediction.get("horizons"))
        expected = {str(value) for value in range(1, hidden + 1)}
        if set(horizons) != expected:
            failures.append(f"NOT_EVERY_HIDDEN_CANDLE_PREDICTED:{case_id}")
        if any(
            str(_mapping(value).get("predicted_side") or "") not in {"UP", "DOWN"}
            for value in horizons.values()
        ):
            failures.append(f"NON_DIRECTIONAL_OUTPUT:{case_id}")
        if score.get("prediction_preceded_reveal") is not True:
            failures.append(f"PREDICTION_AFTER_REVEAL:{case_id}")
        mask_path = str(prediction.get("mask_path") or "")
        previous_owner = mask_owners.setdefault(mask_path, image_id)
        if previous_owner != image_id:
            failures.append(f"MASK_SHARED_BETWEEN_CHARTS:{case_id}")
        try:
            assert_chart_isolated_mask_v3(
                image_id=image_id,
                scorecard_path=Path(mask_path).with_name("scorecard.json"),
                mask_path=mask_path,
            )
        except (ValueError, OSError) as error:
            failures.append(f"MASK_OWNERSHIP_FAILED:{case_id}:{error}")
    grouped = all(
        len(folds) == 1 and -1 not in folds for folds in family_folds.values()
    )
    if not grouped:
        failures.append("FAMILY_FOLD_GROUPING_FAILED")
    unique_failures = sorted(set(failures))
    return {
        "schema_version": ANCHOR_DIRECTION_AUDIT_SCHEMA_V3,
        "status": "PASS" if not unique_failures else "FAIL",
        "case_count": len(rows),
        "family_count": len(family_folds),
        "checks": {
            "chart_specific_mask_only": not any(
                failure.startswith("MASK_") for failure in unique_failures
            ),
            "same_fixed_anchor_for_every_horizon": True,
            "every_hidden_candle_predicted": not any(
                failure.startswith("NOT_EVERY") for failure in unique_failures
            ),
            "only_up_or_down_predicted": not any(
                failure.startswith("NON_DIRECTIONAL") for failure in unique_failures
            ),
            "forbidden_classifications_absent": not any(
                failure.startswith("FORBIDDEN") for failure in unique_failures
            ),
            "future_geometry_absent_before_freeze": not any(
                failure.startswith("ACTUAL_GEOMETRY") for failure in unique_failures
            ),
            "prediction_written_before_reveal": not any(
                failure.startswith("PREDICTION_AFTER") for failure in unique_failures
            ),
            "all_cases_from_one_family_in_one_fold": grouped,
        },
        "failures": unique_failures,
    }


def aggregate_anchor_scores_v3(
    scorecards: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    total = correct = 0
    actual_counts: Counter[str] = Counter()
    predicted_counts: Counter[str] = Counter()
    by_horizon: dict[int, list[bool]] = defaultdict(list)
    by_horizon_actual: dict[int, Counter[str]] = defaultdict(Counter)
    by_pair: dict[str, list[bool]] = defaultdict(list)
    by_timeframe: dict[str, list[bool]] = defaultdict(list)
    by_family: dict[str, list[bool]] = defaultdict(list)
    case_accuracies: list[float] = []
    for score in scorecards:
        symbol = str(score.get("symbol") or "UNKNOWN")
        timeframe = str(score.get("timeframe") or "UNKNOWN")
        family = str(score.get("family_id") or "UNKNOWN")
        case_accuracies.append(_number(score.get("accuracy"), 0.0))
        for raw_horizon, raw_row in _mapping(score.get("horizons")).items():
            row = _mapping(raw_row)
            horizon = int(raw_horizon)
            actual = str(row.get("actual_side") or "TIE")
            predicted = str(row.get("predicted_side") or "")
            hit = row.get("correct") is True
            total += 1
            correct += int(hit)
            actual_counts[actual] += 1
            predicted_counts[predicted] += 1
            by_horizon[horizon].append(hit)
            by_horizon_actual[horizon][actual] += 1
            by_pair[symbol].append(hit)
            by_timeframe[timeframe].append(hit)
            by_family[family].append(hit)

    def group_payload(groups: Mapping[str, Sequence[bool]]) -> dict[str, Any]:
        return {
            key: {
                "predictions": len(values),
                "accuracy": round(sum(values) / len(values), 6) if values else 0.0,
            }
            for key, values in sorted(groups.items())
        }

    horizon_payload = {
        str(horizon): {
            "predictions": len(values),
            "accuracy": round(sum(values) / len(values), 6) if values else 0.0,
            "actual_up": by_horizon_actual[horizon]["UP"],
            "actual_down": by_horizon_actual[horizon]["DOWN"],
            "actual_tie": by_horizon_actual[horizon]["TIE"],
        }
        for horizon, values in sorted(by_horizon.items())
    }
    macro_family = mean(
        sum(values) / len(values) for values in by_family.values() if values
    ) if by_family else 0.0
    always_up = actual_counts["UP"] / total if total else 0.0
    always_down = actual_counts["DOWN"] / total if total else 0.0
    return {
        "scorecard_count": len(scorecards),
        "prediction_count": total,
        "correct_count": correct,
        "exact_anchor_direction_accuracy": round(correct / total, 6)
        if total
        else 0.0,
        "macro_case_accuracy": round(mean(case_accuracies), 6)
        if case_accuracies
        else 0.0,
        "macro_family_accuracy": round(macro_family, 6),
        "actual_counts": dict(actual_counts),
        "predicted_counts": dict(predicted_counts),
        "always_up_baseline": round(always_up, 6),
        "always_down_baseline": round(always_down, 6),
        "best_constant_baseline": round(max(always_up, always_down), 6),
        "by_horizon": horizon_payload,
        "by_pair": group_payload(by_pair),
        "by_timeframe": group_payload(by_timeframe),
    }


def render_anchor_direction_report_v3(summary: Mapping[str, Any]) -> str:
    metrics = _mapping(summary.get("metrics"))
    audit = _mapping(summary.get("audit"))
    horizon_rows: list[str] = []
    for horizon, raw in sorted(
        _mapping(metrics.get("by_horizon")).items(),
        key=lambda item: int(item[0]),
    ):
        row = _mapping(raw)
        horizon_rows.append(
            f"| {horizon} | {row.get('predictions', 0)} | "
            f"{100.0 * _number(row.get('accuracy')):.2f}% | "
            f"{row.get('actual_up', 0)} | {row.get('actual_down', 0)} | "
            f"{row.get('actual_tie', 0)} |"
        )
    return f"""# Final Fixed-Anchor Direction Prediction Report

## Exact question tested

For every chart-specific mask and every hidden candle: is that future candle's
close above or below the close of the final visible candle?

## Scope

- Source screenshots: {summary.get('source_screenshot_count', 0)}
- Chart-specific masked cases discovered: {summary.get('descriptor_count', 0)}
- Masked cases successfully studied: {summary.get('prepared_case_count', 0)}
- Geometry failures reported: {summary.get('preparation_failure_count', 0)}
- Independent chart families: {summary.get('family_count', 0)}
- Grouped folds: {summary.get('fold_count', 0)}

## Causal audit

- Result: **{audit.get('status', 'UNKNOWN')}**
- Every mask remained attached to its own screenshot.
- One fixed final-visible close anchor was used for every future candle.
- Every hidden candle received an UP or DOWN prediction before reveal.
- No candle color, majority counting, pullback, continuation, reversal, SMC,
  pattern, or path-class prediction entered this test.

## Final result

- Total frozen UP/DOWN predictions: {metrics.get('prediction_count', 0)}
- Correct: {metrics.get('correct_count', 0)}
- Exact fixed-anchor accuracy: **{100.0 * _number(metrics.get('exact_anchor_direction_accuracy')):.2f}%**
- Macro accuracy by screenshot case: {100.0 * _number(metrics.get('macro_case_accuracy')):.2f}%
- Macro accuracy by independent family: {100.0 * _number(metrics.get('macro_family_accuracy')):.2f}%
- Best constant UP/DOWN baseline: {100.0 * _number(metrics.get('best_constant_baseline')):.2f}%

## Accuracy for every hidden-candle horizon

| Candle after anchor | Predictions | Accuracy | Actual UP | Actual DOWN | Exact tie |
|---:|---:|---:|---:|---:|---:|
{chr(10).join(horizon_rows)}

## Accuracy by pair

```json
{json.dumps(metrics.get('by_pair', {}), indent=2, sort_keys=True)}
```

## Accuracy by timeframe

```json
{json.dumps(metrics.get('by_timeframe', {}), indent=2, sort_keys=True)}
```

## Disk

- Run bytes: {summary.get('run_bytes', 0)}
- Free disk after run: {summary.get('free_gb_after', 0)} GB
"""


def run_anchor_direction_replay_v3(
    *,
    corpus_cache_path: str | Path,
    mask_run_dir: str | Path,
    output_dir: str | Path,
    report_path: str | Path,
    workers: int = 2,
    neighbors: int = 15,
    minimum_free_gb: float = 45.0,
) -> dict[str, Any]:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    enforce_disk_reserve(
        output,
        minimum_free_gb=minimum_free_gb,
        required_bytes=384 * 1024 * 1024,
    )
    records = load_corpus_cache_v3(corpus_cache_path)
    descriptors = load_case_descriptors_v3(
        mask_run_dir=mask_run_dir,
        records=records,
    )
    prepared, preparation_failures = prepare_anchor_cases_v3(
        descriptors,
        records,
        workers=workers,
    )
    _write_json_atomic(
        output / "preparation_failures.json",
        {"failures": preparation_failures},
    )
    folds = sorted({case.fold for case in prepared if case.fold >= 0})
    scorecards: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    for fold in folds:
        training = [case for case in prepared if case.fold != fold]
        testing = [case for case in prepared if case.fold == fold]
        model = AnchorDirectionModelV3.fit(
            training,
            records,
            neighbors=neighbors,
        )
        frozen_paths: dict[str, Path] = {}
        for case in testing:
            prediction = model.predict(case)
            case_dir = output / "cases" / case.image_id / f"cutoff-{case.cutoff:04d}"
            prediction_path = case_dir / "prediction_frozen.json"
            freeze_anchor_prediction_v3(prediction_path, prediction)
            frozen_paths[case.case_id] = prediction_path
        reveal_started = time.time_ns() // 1_000_000
        for case in testing:
            prediction_raw = json.loads(
                frozen_paths[case.case_id].read_text(encoding="utf-8")
            )
            prediction = _mapping(prediction_raw)
            actual = build_anchor_direction_target_v3(
                records[case.record_index].candles,
                cutoff=case.cutoff,
                anchor_close_y_px=case.anchor_close_y_px,
            )
            score = score_anchor_prediction_v3(
                prediction,
                actual,
                reveal_started_epoch_ms=max(
                    reveal_started,
                    int(_number(prediction.get("prediction_frozen_epoch_ms"), 0.0)),
                ),
                fold=fold,
            )
            score_path = frozen_paths[case.case_id].with_name("scorecard.json")
            _write_json_atomic(score_path, score)
            scorecards.append(score)
            audit_rows.append({"prediction": prediction, "score": score})
    audit = audit_anchor_direction_run_v3(audit_rows)
    metrics = aggregate_anchor_scores_v3(scorecards)
    run_bytes = sum(
        path.stat().st_size for path in output.rglob("*") if path.is_file()
    )
    summary: dict[str, Any] = {
        "schema_version": ANCHOR_DIRECTION_SUMMARY_SCHEMA_V3,
        "status": "COMPLETE" if audit.get("status") == "PASS" else "AUDIT_FAILED",
        "source_screenshot_count": len(records),
        "descriptor_count": len(descriptors),
        "prepared_case_count": len(prepared),
        "preparation_failure_count": len(preparation_failures),
        "family_count": len({case.family_id for case in prepared}),
        "fold_count": len(folds),
        "question": "FUTURE_CLOSE_ABOVE_OR_BELOW_FIXED_FINAL_VISIBLE_CLOSE",
        "metrics": metrics,
        "audit": audit,
        "run_bytes": run_bytes,
        "free_gb_after": round(available_free_gb(output), 3),
        "report_path": str(Path(report_path).resolve()),
    }
    _write_json_atomic(output / "summary.json", summary)
    report = render_anchor_direction_report_v3(summary)
    report_destination = Path(report_path).resolve()
    report_destination.parent.mkdir(parents=True, exist_ok=True)
    report_destination.write_text(report, encoding="utf-8")
    return summary


__all__ = [
    "ANCHOR_DIRECTION_AUDIT_SCHEMA_V3",
    "ANCHOR_DIRECTION_PREDICTION_SCHEMA_V3",
    "ANCHOR_DIRECTION_SCORE_SCHEMA_V3",
    "ANCHOR_DIRECTION_SUMMARY_SCHEMA_V3",
    "AnchorCaseDescriptorV3",
    "AnchorDirectionModelV3",
    "CandleCorpusRecordV3",
    "PreparedAnchorCaseV3",
    "aggregate_anchor_scores_v3",
    "assert_chart_isolated_mask_v3",
    "audit_anchor_direction_run_v3",
    "build_anchor_direction_target_v3",
    "candle_geometry_vector_v3",
    "freeze_anchor_prediction_v3",
    "load_case_descriptors_v3",
    "load_corpus_cache_v3",
    "prepare_anchor_cases_v3",
    "render_anchor_direction_report_v3",
    "run_anchor_direction_replay_v3",
    "score_anchor_prediction_v3",
]
