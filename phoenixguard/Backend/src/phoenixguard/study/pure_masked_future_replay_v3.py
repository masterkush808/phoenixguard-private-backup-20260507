"""Offline screenshot-in, masked-prefix prediction, reveal-and-score replay."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
import shutil
import time
from typing import Any, Mapping, Sequence, cast

from PIL import Image, ImageDraw

from phoenixguard.simulation.masked_future_v3 import (
    DEFAULT_RESERVE_GB,
    ExtractedSequenceV3,
    assign_grouped_folds_v3,
    discover_corpus_images,
    enforce_disk_reserve,
    extract_corpus_v3,
    extract_image_sequence_v3,
    group_sequence_families_v3,
)
from phoenixguard.study.masked_future_behavior_v3 import (
    load_default_masked_future_model_v3,
)
from phoenixguard.study.masked_future_scoring_v3 import (
    aggregate_scorecards_v3,
    build_revealed_target_v3,
    score_frozen_prediction_v3,
)
from phoenixguard.study.masked_image_region_v3 import (
    MaskRectangleV3,
    automatic_mask_rectangle_v3,
    create_masked_image_v3,
    load_analysis_image_v3,
)
from phoenixguard.study.prefix_vision_prediction_v3 import (
    PrefixVisionPredictionModelV3,
    build_prefix_vision_study_v3,
)
from phoenixguard.study.pure_masked_future_leakage_audit_v3 import (
    assert_pure_masked_future_leakage_v3,
    audit_pure_masked_future_v3,
)


PURE_REPLAY_SCHEMA_VERSION = "PG_PURE_MASKED_FUTURE_REPLAY_V3"
DEFAULT_PURE_HORIZONS: tuple[int, ...] = (1, 2, 3, 5, 8, 13, 21, 34)


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return float(default)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    encoded = json.dumps(
        payload,
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    ).encode("utf-8")
    with temporary.open("wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def freeze_prediction_v3(path: str | Path, prediction: Mapping[str, Any]) -> dict[str, Any]:
    frozen = dict(prediction)
    frozen["prediction_frozen_epoch_ms"] = time.time_ns() // 1_000_000
    destination = Path(path)
    _write_json_atomic(destination, frozen)
    stat = destination.stat()
    frozen["prediction_file_mtime_ns"] = stat.st_mtime_ns
    return frozen


def select_causal_cutoffs_v3(
    candle_count: int,
    *,
    minimum_prefix_candles: int,
    minimum_hidden_candles: int,
    cutoff_stride: int,
    maximum_cutoffs: int,
) -> list[int]:
    count = int(candle_count)
    lower = max(int(minimum_prefix_candles), int(math.ceil(count * 0.35)))
    upper = min(count - int(minimum_hidden_candles), int(math.floor(count * 0.85)))
    if upper < lower:
        return []
    candidates = list(range(lower, upper + 1, max(1, int(cutoff_stride))))
    limit = max(1, int(maximum_cutoffs))
    if len(candidates) <= limit:
        return candidates
    selected: list[int] = []
    for index in range(limit):
        position = round(index * (len(candidates) - 1) / max(1, limit - 1))
        selected.append(candidates[position])
    return list(dict.fromkeys(selected))


@dataclass
class PreparedMaskedCaseV3:
    case_id: str
    record_index: int
    image_id: str
    image_hash: str
    family_id: str
    fold: int
    cutoff: int
    cutoff_id: str
    hidden_future_candles: int
    symbol: str
    timeframe: str
    source_path: str
    case_dir: str
    masked_path: str
    revealed_path: str
    prediction_path: str
    scorecard_path: str
    mask_proof: dict[str, Any]
    prefix_study: dict[str, Any]


def _link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()
    try:
        os.link(source, destination)
    except OSError:
        shutil.copyfile(source, destination)


def _predicted_points(
    prediction: Mapping[str, Any],
    case: PreparedMaskedCaseV3,
    *,
    width: int,
    height: int,
) -> list[tuple[int, int]]:
    rectangle = _mapping(case.mask_proof.get("rectangle"))
    x1 = int(rectangle.get("x1", 0) or 0)
    x2 = int(rectangle.get("x2", width) or width)
    anchor_y = _number(case.prefix_study.get("anchor_y_px"), height / 2.0)
    scale = max(2.0, _number(case.prefix_study.get("baseline_range_px"), 8.0))
    horizons = _mapping(prediction.get("horizons"))
    numeric = sorted((int(key), _mapping(value)) for key, value in horizons.items())
    maximum = max((row[0] for row in numeric), default=1)
    points = [(max(0, x1 - 2), max(0, min(height - 1, int(round(anchor_y)))))]
    cumulative = 0.0
    for horizon, row in numeric:
        side = str(row.get("predicted_side") or "REST")
        sign = -1.0 if side == "BUY" else 1.0 if side == "SELL" else 0.0
        cumulative += sign * scale * max(0.7, math.sqrt(horizon) * 0.55)
        x = x1 + int(round((x2 - x1 - 4) * horizon / maximum))
        y = int(round(anchor_y + cumulative))
        points.append((max(0, min(width - 1, x)), max(0, min(height - 1, y))))
    return points


def _actual_points(
    record: ExtractedSequenceV3,
    case: PreparedMaskedCaseV3,
    *,
    width: int,
    height: int,
    maximum_horizon: int,
) -> list[tuple[int, int]]:
    output: list[tuple[int, int]] = []
    anchor = record.candles[case.cutoff - 1]
    output.append(
        (
            int(_number(anchor.get("center_x_px"), 0.0)),
            int(_number(anchor.get("close_y_px"), height / 2.0)),
        )
    )
    for candle in record.candles[case.cutoff : case.cutoff + maximum_horizon]:
        output.append(
            (
                max(0, min(width - 1, int(_number(candle.get("center_x_px"), 0.0)))),
                max(0, min(height - 1, int(_number(candle.get("close_y_px"), height / 2.0)))),
            )
        )
    return output


def _render_before_reveal(
    case: PreparedMaskedCaseV3,
    prediction: Mapping[str, Any],
) -> Path:
    path = Path(case.case_dir) / "prediction_overlay_before_reveal.png"
    with Image.open(case.masked_path) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    points = _predicted_points(prediction, case, width=image.width, height=image.height)
    if len(points) >= 2:
        draw.line(points, fill=(0, 178, 255), width=max(2, image.width // 600))
    for index, point in enumerate(points[1:], start=1):
        radius = max(3, image.width // 450)
        draw.ellipse(
            (point[0] - radius, point[1] - radius, point[0] + radius, point[1] + radius),
            fill=(0, 178, 255),
        )
        draw.text((point[0] + radius, point[1] - radius), str(index), fill=(225, 245, 255))
    image.save(path, format="PNG", optimize=True, compress_level=9)
    return path


def _render_comparison(
    record: ExtractedSequenceV3,
    case: PreparedMaskedCaseV3,
    prediction: Mapping[str, Any],
    scorecard: Mapping[str, Any],
) -> Path:
    path = Path(case.case_dir) / "prediction_vs_actual_overlay.png"
    with Image.open(case.revealed_path) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    predicted = _predicted_points(prediction, case, width=image.width, height=image.height)
    maximum = max((int(key) for key in _mapping(prediction.get("horizons"))), default=1)
    actual = _actual_points(
        record,
        case,
        width=image.width,
        height=image.height,
        maximum_horizon=maximum,
    )
    line_width = max(2, image.width // 600)
    if len(predicted) >= 2:
        draw.line(predicted, fill=(0, 178, 255), width=line_width)
    if len(actual) >= 2:
        draw.line(actual, fill=(255, 157, 52), width=line_width)
    horizon_scores = _mapping(scorecard.get("horizons"))
    rectangle = _mapping(case.mask_proof.get("rectangle"))
    x1 = int(rectangle.get("x1", 0) or 0)
    x2 = int(rectangle.get("x2", image.width) or image.width)
    for key, row_value in horizon_scores.items():
        horizon = int(key)
        row = _mapping(row_value)
        x = x1 + int(round((x2 - x1 - 4) * horizon / maximum))
        y = max(8, min(image.height - 8, actual[min(horizon, len(actual) - 1)][1]))
        color = (38, 220, 126) if row.get("majority_correct") else (255, 73, 88)
        radius = max(4, image.width // 400)
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)
    draw.rectangle((10, 10, min(image.width - 10, 390), 68), fill=(7, 10, 12))
    draw.text((20, 18), "BLUE: FROZEN PREDICTION", fill=(0, 178, 255))
    draw.text((20, 40), "ORANGE: REVEALED ACTUAL", fill=(255, 157, 52))
    image.save(path, format="PNG", optimize=True, compress_level=9)
    return path


def _market_phase(study: Mapping[str, Any]) -> str:
    context = _mapping(study.get("context"))
    features = _mapping(context.get("features"))
    state = str(features.get("state") or "UNKNOWN")
    relationship = str(
        _mapping(_mapping(study.get("skill_evidence")).get("pullback_context")).get(
            "relationship"
        )
        or "UNKNOWN"
    )
    return f"{state}|{relationship}"


def _prepare_cases(
    records: Sequence[ExtractedSequenceV3],
    families: Sequence[str],
    folds: Sequence[int],
    *,
    output_dir: Path,
    minimum_prefix_candles: int,
    minimum_hidden_candles: int,
    cutoff_stride: int,
    maximum_cutoffs_per_image: int,
    maximum_width: int,
    minimum_free_gb: float,
) -> tuple[list[PreparedMaskedCaseV3], list[dict[str, Any]]]:
    prepared: list[PreparedMaskedCaseV3] = []
    failures: list[dict[str, Any]] = []
    for record_index, record in enumerate(records):
        if record.extraction_status != "EXTRACTED":
            failures.append(
                {
                    "path": record.path,
                    "status": record.extraction_status,
                    "reason": record.extraction_reason,
                }
            )
            continue
        cutoffs = select_causal_cutoffs_v3(
            len(record.candles),
            minimum_prefix_candles=minimum_prefix_candles,
            minimum_hidden_candles=minimum_hidden_candles,
            cutoff_stride=cutoff_stride,
            maximum_cutoffs=maximum_cutoffs_per_image,
        )
        if not cutoffs:
            failures.append(
                {
                    "path": record.path,
                    "status": "NO_VALID_CAUSAL_CUTOFF",
                    "reason": f"candles={len(record.candles)}",
                }
            )
            continue
        image_id = f"image-{record_index:04d}-{record.image_hash[:12]}"
        image_dir = output_dir / "images" / image_id
        image_dir.mkdir(parents=True, exist_ok=True)
        revealed_source = image_dir / "revealed_source.png"
        if not revealed_source.is_file():
            load_analysis_image_v3(record.path, maximum_width=maximum_width).save(
                revealed_source,
                format="PNG",
                optimize=True,
                compress_level=9,
            )
        for cutoff in cutoffs:
            cutoff_id = f"{image_id}-cutoff-{cutoff:04d}"
            case_dir = output_dir / "cases" / image_id / f"cutoff-{cutoff:04d}"
            case_dir.mkdir(parents=True, exist_ok=True)
            masked_path = case_dir / "masked_prefix.png"
            revealed_path = case_dir / "revealed_actual.png"
            try:
                rectangle = automatic_mask_rectangle_v3(
                    record.candles,
                    cutoff=cutoff,
                    width=record.analysis_width,
                    height=record.analysis_height,
                )
                proof = create_masked_image_v3(
                    record.path,
                    masked_path,
                    rectangle=rectangle,
                    maximum_width=maximum_width,
                )
                _link_or_copy(revealed_source, revealed_path)
                study = build_prefix_vision_study_v3(
                    masked_path,
                    rectangle=rectangle,
                    image_id=image_id,
                    symbol=record.symbol,
                    timeframe=record.timeframe,
                    minimum_prefix_candles=minimum_prefix_candles,
                )
            except (OSError, ValueError, ArithmeticError) as exc:
                failures.append(
                    {
                        "path": record.path,
                        "cutoff": cutoff,
                        "status": "MASKED_PREFIX_PREPARATION_FAILED",
                        "reason": f"{type(exc).__name__}: {exc}",
                    }
                )
                continue
            case = PreparedMaskedCaseV3(
                case_id=cutoff_id,
                record_index=record_index,
                image_id=image_id,
                image_hash=record.image_hash,
                family_id=str(families[record_index]),
                fold=int(folds[record_index]),
                cutoff=cutoff,
                cutoff_id=cutoff_id,
                hidden_future_candles=len(record.candles) - cutoff,
                symbol=record.symbol,
                timeframe=record.timeframe,
                source_path=record.path,
                case_dir=str(case_dir.resolve()),
                masked_path=str(masked_path.resolve()),
                revealed_path=str(revealed_path.resolve()),
                prediction_path=str((case_dir / "prediction_frozen.json").resolve()),
                scorecard_path=str((case_dir / "scorecard.json").resolve()),
                mask_proof=proof,
                prefix_study=study,
            )
            _write_json_atomic(
                case_dir / "case_manifest.json",
                {
                    "schema_version": PURE_REPLAY_SCHEMA_VERSION,
                    "case_id": case.case_id,
                    "image_id": image_id,
                    "family_id": case.family_id,
                    "fold": case.fold,
                    "cutoff": cutoff,
                    "mask_proof": proof,
                    "feature_digest": study.get("feature_digest"),
                    "visible_prefix_candle_count": study.get(
                        "visible_prefix_candle_count"
                    ),
                    "hidden_future_candle_count": case.hidden_future_candles,
                    "folder_label_used_as_target": False,
                    "future_revealed": False,
                },
            )
            prepared.append(case)
            if len(prepared) % 25 == 0:
                enforce_disk_reserve(
                    output_dir,
                    minimum_free_gb=minimum_free_gb,
                    required_bytes=32 * 1024 * 1024,
                )
    return prepared, failures


def _score_grouped_cases(
    cases: Sequence[PreparedMaskedCaseV3],
    records: Sequence[ExtractedSequenceV3],
    *,
    folds: int,
    horizons: Sequence[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    scorecards: list[dict[str, Any]] = []
    audit_rows: list[dict[str, Any]] = []
    for fold in range(max(2, int(folds))):
        training_rows: list[dict[str, Any]] = []
        for case in cases:
            if case.fold == fold:
                continue
            record = records[case.record_index]
            context = _mapping(case.prefix_study.get("context"))
            target = build_revealed_target_v3(
                record.candles,
                cutoff=case.cutoff,
                horizons=horizons,
                context=context,
            )
            training_rows.append({"context": context, "target": target})
        model = PrefixVisionPredictionModelV3.fit(
            training_rows,
            horizons=horizons,
        )
        test_cases = [case for case in cases if case.fold == fold]
        for case in test_cases:
            prediction = model.predict(
                case.prefix_study,
                image_id=case.image_id,
                family_id=case.family_id,
                cutoff_id=case.cutoff_id,
                anchor_index=case.cutoff - 1,
                hidden_future_candles=case.hidden_future_candles,
            )
            frozen = freeze_prediction_v3(case.prediction_path, prediction)
            _render_before_reveal(case, frozen)
        reveal_started = time.time_ns() // 1_000_000
        for case in test_cases:
            frozen_path = Path(case.prediction_path)
            frozen_payload = _mapping(
                json.loads(frozen_path.read_text(encoding="utf-8"))
            )
            record = records[case.record_index]
            context = _mapping(case.prefix_study.get("context"))
            target = build_revealed_target_v3(
                record.candles,
                cutoff=case.cutoff,
                horizons=horizons,
                context=context,
            )
            scorecard = score_frozen_prediction_v3(
                frozen_payload,
                target,
                reveal_started_epoch_ms=max(
                    reveal_started,
                    int(frozen_payload.get("prediction_frozen_epoch_ms", 0) or 0),
                ),
                fold=fold,
                source_path=case.source_path,
                market_phase=_market_phase(case.prefix_study),
            )
            scorecard["artifacts"] = {
                "masked_prefix": case.masked_path,
                "prediction_before_reveal": str(
                    Path(case.case_dir) / "prediction_overlay_before_reveal.png"
                ),
                "revealed_actual": case.revealed_path,
                "prediction_vs_actual": str(
                    Path(case.case_dir) / "prediction_vs_actual_overlay.png"
                ),
                "prediction_json": case.prediction_path,
            }
            _render_comparison(record, case, frozen_payload, scorecard)
            _write_json_atomic(Path(case.scorecard_path), scorecard)
            scorecards.append(scorecard)
            audit_rows.append(
                {
                    "family_id": case.family_id,
                    "image_hash": case.image_hash,
                    "fold": case.fold,
                    "cutoff_id": case.cutoff_id,
                    "mask_proof": case.mask_proof,
                    "prediction_context": case.prefix_study.get("context"),
                    "prediction": frozen_payload,
                    "prediction_frozen_epoch_ms": frozen_payload.get(
                        "prediction_frozen_epoch_ms"
                    ),
                    "reveal_started_epoch_ms": scorecard.get(
                        "reveal_started_epoch_ms"
                    ),
                    "masked_path": case.masked_path,
                    "prediction_path": case.prediction_path,
                    "scorecard_path": case.scorecard_path,
                }
            )
    return scorecards, audit_rows


def render_pure_masked_future_report_v3(summary: Mapping[str, Any]) -> str:
    metrics = _mapping(summary.get("metrics"))
    by_horizon = _mapping(metrics.get("by_horizon"))
    horizon_rows: list[str] = []
    for horizon, value in sorted(by_horizon.items(), key=lambda item: int(item[0])):
        row = _mapping(value)
        horizon_rows.append(
            f"| {horizon} | {row.get('scored', 0)} | "
            f"{100.0 * _number(row.get('majority_direction_accuracy')):.2f}% | "
            f"{100.0 * _number(row.get('endpoint_direction_accuracy')):.2f}% | "
            f"{100.0 * _number(row.get('step_direction_accuracy')):.2f}% | "
            f"{100.0 * _number(row.get('candle_token_similarity')):.2f}% |"
        )
    whole = _mapping(metrics.get("whole_path"))
    return f"""# Final Pure Masked-Future Prediction Report

## 1. Images processed

- Discovered screenshots: {summary.get("images_discovered", 0)}
- Successfully extracted screenshots: {summary.get("images_extracted", 0)}
- Screenshots with frozen predictions: {summary.get("images_with_predictions", 0)}

## 2. Independent families

- Near-duplicate families: {summary.get("independent_family_count", 0)}
- Grouped folds: {summary.get("folds", 0)}

## 3. Masked cutoffs

- Prepared causal cutoffs: {summary.get("masked_cutoff_count", 0)}
- Preparation failures: {summary.get("preparation_failure_count", 0)}

## 4. Frozen predictions

- Frozen before reveal: {summary.get("frozen_prediction_count", 0)}
- Every test-family prediction was flushed before its suffix was scored.

## 5. Leakage audit

- Result: **{_mapping(summary.get("leakage_audit")).get("status", "UNKNOWN")}**
- Future pixels were physically obscured in predictor input.
- BUY/SELL folder provenance was never a target or feature.

## 6. Accuracy by horizon

| Horizon | Scored | Majority | Endpoint | Exact step | Candle token |
|---:|---:|---:|---:|---:|---:|
{chr(10).join(horizon_rows)}

## 7. Accuracy by pair

```json
{json.dumps(metrics.get("by_pair", {}), indent=2, sort_keys=True)}
```

## 8. Accuracy by timeframe

```json
{json.dumps(metrics.get("by_timeframe", {}), indent=2, sort_keys=True)}
```

## 9. Accuracy by market phase

```json
{json.dumps(metrics.get("by_market_phase", {}), indent=2, sort_keys=True)}
```

## 10. Pullback, retest, and continuation conditions

Market-phase rows combine the visible hidden state with prefix-only movement relationship.

## 11. SMC and supply-demand context

Each frozen prediction contains prefix-only trendline, supply/demand, SMC, liquidity,
pullback, continuation, candle-intelligence, and hidden-state evidence.

## 12. Confidence calibration

- Expected calibration error: {_number(_mapping(metrics.get("calibration")).get("expected_calibration_error")):.6f}

## 13. Best examples

```json
{json.dumps(metrics.get("best_examples", []), indent=2, sort_keys=True)}
```

## 14. Worst examples

```json
{json.dumps(metrics.get("worst_examples", []), indent=2, sort_keys=True)}
```

## 15. Visual gallery

- Gallery: {summary.get("gallery_path", "")}

## 16. Disk usage and cleanup

- Run bytes: {summary.get("run_bytes", 0)}
- Free disk after run: {_number(summary.get("free_gb_after")):.3f} GB
- Revealed screenshots are hard-linked per cutoff when supported.

## 17. Scope confirmation

- Screenshot pixels were the sole market input.
- No broker price-history import was used.
- No live transaction, authorization, or broker bridge artifact was created.
- Whole-path dominant accuracy: {100.0 * _number(whole.get("dominant_direction_accuracy")):.2f}%
- Path-class accuracy: {100.0 * _number(whole.get("path_class_accuracy")):.2f}%
- Swing-length MAE: {_number(whole.get("swing_length_mae_candles")):.3f} candles
"""


def run_pure_masked_future_replay_v3(
    *,
    roots: Sequence[str | Path],
    output_dir: str | Path,
    report_path: str | Path,
    horizons: Sequence[int] = DEFAULT_PURE_HORIZONS,
    minimum_prefix_candles: int = 32,
    minimum_hidden_candles: int = 8,
    cutoff_stride: int = 2,
    maximum_cutoffs_per_image: int = 4,
    folds: int = 5,
    workers: int = 2,
    maximum_width: int = 1200,
    minimum_free_gb: float = DEFAULT_RESERVE_GB,
    render_gallery: bool = True,
) -> dict[str, Any]:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    images = discover_corpus_images(roots)
    if not images:
        raise ValueError("PG_PURE_MASKED_FUTURE_NO_SCREENSHOTS_DISCOVERED")
    estimated_bytes = len(images) * max(1, maximum_cutoffs_per_image) * 350_000
    enforce_disk_reserve(
        output,
        minimum_free_gb=minimum_free_gb,
        required_bytes=estimated_bytes,
    )
    records = extract_corpus_v3(
        images,
        cache_path=output / "corpus_cache.jsonl",
        minimum_free_gb=minimum_free_gb,
        workers=workers,
        maximum_width=maximum_width,
    )
    families = group_sequence_families_v3(records)
    fold_rows = assign_grouped_folds_v3(families, folds=folds)
    cases, failures = _prepare_cases(
        records,
        families,
        fold_rows,
        output_dir=output,
        minimum_prefix_candles=minimum_prefix_candles,
        minimum_hidden_candles=minimum_hidden_candles,
        cutoff_stride=cutoff_stride,
        maximum_cutoffs_per_image=maximum_cutoffs_per_image,
        maximum_width=maximum_width,
        minimum_free_gb=minimum_free_gb,
    )
    if not cases:
        raise ValueError("PG_PURE_MASKED_FUTURE_NO_VALID_MASKED_CASES")
    scorecards, audit_rows = _score_grouped_cases(
        cases,
        records,
        folds=folds,
        horizons=horizons,
    )
    audit = audit_pure_masked_future_v3(audit_rows, run_dir=output)
    assert_pure_masked_future_leakage_v3(audit)
    metrics = aggregate_scorecards_v3(scorecards, horizons=horizons)
    gallery_path = output / "gallery" / "index.html"
    if render_gallery:
        from phoenixguard.study.pure_masked_future_gallery_v3 import (
            render_pure_masked_future_gallery_v3,
        )

        render_pure_masked_future_gallery_v3(output, gallery_path)
    run_bytes = sum(
        path.stat().st_size for path in output.rglob("*") if path.is_file()
    )
    free_after = enforce_disk_reserve(output, minimum_free_gb=minimum_free_gb)
    summary: dict[str, Any] = {
        "schema_version": PURE_REPLAY_SCHEMA_VERSION,
        "status": "COMPLETE",
        "images_discovered": len(images),
        "images_extracted": sum(record.extraction_status == "EXTRACTED" for record in records),
        "images_with_predictions": len({case.image_id for case in cases}),
        "independent_family_count": len(set(families)),
        "folds": max(2, int(folds)),
        "masked_cutoff_count": len(cases),
        "frozen_prediction_count": len(scorecards),
        "preparation_failure_count": len(failures),
        "horizons": list(sorted({int(value) for value in horizons})),
        "leakage_audit": audit,
        "metrics": metrics,
        "gallery_path": str(gallery_path),
        "run_bytes": run_bytes,
        "free_gb_after": round(free_after, 3),
        "scope": {
            "screenshot_only": True,
            "physical_future_mask": True,
            "grouped_out_of_family": True,
            "broker_price_history": False,
            "live_transaction_logic": False,
        },
        "failures": failures,
    }
    _write_json_atomic(output / "summary.json", summary)
    _write_json_atomic(output / "leakage_audit.json", audit)
    _write_json_atomic(output / "preparation_failures.json", {"failures": failures})
    report = Path(report_path)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_pure_masked_future_report_v3(summary), encoding="utf-8")
    return summary


def run_manual_masked_future_replay_v3(
    *,
    image_path: str | Path,
    mask_rectangle: MaskRectangleV3,
    output_dir: str | Path,
    report_path: str | Path,
    horizons: Sequence[int] = DEFAULT_PURE_HORIZONS,
    minimum_prefix_candles: int = 16,
    minimum_free_gb: float = DEFAULT_RESERVE_GB,
    render_gallery: bool = True,
) -> dict[str, Any]:
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    enforce_disk_reserve(output, minimum_free_gb=minimum_free_gb, required_bytes=64 * 1024 * 1024)
    record = extract_image_sequence_v3(image_path, maximum_width=0)
    centers = [_number(candle.get("center_x_px"), -1.0) for candle in record.candles]
    cutoff = sum(center < mask_rectangle.x1 for center in centers if center >= 0.0)
    if cutoff < minimum_prefix_candles or len(record.candles) - cutoff < 1:
        raise ValueError("PG_MANUAL_MASK_DOES_NOT_SPLIT_VALID_PREFIX_AND_SUFFIX")
    case_dir = output / "cases" / "manual-image" / f"cutoff-{cutoff:04d}"
    masked_path = case_dir / "masked_prefix.png"
    proof = create_masked_image_v3(
        image_path,
        masked_path,
        rectangle=mask_rectangle,
        maximum_width=0,
    )
    revealed_path = case_dir / "revealed_actual.png"
    case_dir.mkdir(parents=True, exist_ok=True)
    load_analysis_image_v3(image_path, maximum_width=0).save(
        revealed_path,
        format="PNG",
        optimize=True,
        compress_level=9,
    )
    study = build_prefix_vision_study_v3(
        masked_path,
        rectangle=mask_rectangle,
        image_id="manual-image",
        symbol=record.symbol,
        timeframe=record.timeframe,
        minimum_prefix_candles=minimum_prefix_candles,
    )
    behavior_model = load_default_masked_future_model_v3()
    if behavior_model is None:
        raise ValueError("PG_MANUAL_MASK_REQUIRES_EXISTING_V3_BEHAVIOR_MODEL")
    model = PrefixVisionPredictionModelV3.from_behavior_model(
        behavior_model,
        horizons=horizons,
    )
    case = PreparedMaskedCaseV3(
        case_id="manual-cutoff",
        record_index=0,
        image_id="manual-image",
        image_hash=record.image_hash,
        family_id="MANUAL",
        fold=0,
        cutoff=cutoff,
        cutoff_id="manual-cutoff",
        hidden_future_candles=len(record.candles) - cutoff,
        symbol=record.symbol,
        timeframe=record.timeframe,
        source_path=str(Path(image_path).resolve()),
        case_dir=str(case_dir.resolve()),
        masked_path=str(masked_path.resolve()),
        revealed_path=str(revealed_path.resolve()),
        prediction_path=str((case_dir / "prediction_frozen.json").resolve()),
        scorecard_path=str((case_dir / "scorecard.json").resolve()),
        mask_proof=proof,
        prefix_study=study,
    )
    prediction = model.predict(
        study,
        image_id=case.image_id,
        family_id=case.family_id,
        cutoff_id=case.cutoff_id,
        anchor_index=cutoff - 1,
        hidden_future_candles=case.hidden_future_candles,
    )
    frozen = freeze_prediction_v3(case.prediction_path, prediction)
    _render_before_reveal(case, frozen)
    reveal_started = time.time_ns() // 1_000_000
    target = build_revealed_target_v3(
        record.candles,
        cutoff=cutoff,
        horizons=horizons,
        context=_mapping(study.get("context")),
    )
    scorecard = score_frozen_prediction_v3(
        frozen,
        target,
        reveal_started_epoch_ms=max(
            reveal_started,
            int(frozen.get("prediction_frozen_epoch_ms", 0) or 0),
        ),
        fold=0,
        source_path=case.source_path,
        market_phase=_market_phase(study),
    )
    _render_comparison(record, case, frozen, scorecard)
    _write_json_atomic(Path(case.scorecard_path), scorecard)
    audit_row = {
        "family_id": case.family_id,
        "image_hash": case.image_hash,
        "fold": 0,
        "cutoff_id": case.cutoff_id,
        "mask_proof": proof,
        "prediction_context": study.get("context"),
        "prediction": frozen,
        "prediction_frozen_epoch_ms": frozen.get("prediction_frozen_epoch_ms"),
        "reveal_started_epoch_ms": scorecard.get("reveal_started_epoch_ms"),
        "masked_path": case.masked_path,
        "prediction_path": case.prediction_path,
        "scorecard_path": case.scorecard_path,
    }
    audit = audit_pure_masked_future_v3([audit_row], run_dir=output)
    assert_pure_masked_future_leakage_v3(audit)
    metrics = aggregate_scorecards_v3([scorecard], horizons=horizons)
    gallery_path = output / "gallery" / "index.html"
    if render_gallery:
        from phoenixguard.study.pure_masked_future_gallery_v3 import (
            render_pure_masked_future_gallery_v3,
        )

        render_pure_masked_future_gallery_v3(output, gallery_path)
    free_after = enforce_disk_reserve(output, minimum_free_gb=minimum_free_gb)
    summary = {
        "schema_version": PURE_REPLAY_SCHEMA_VERSION,
        "status": "COMPLETE",
        "mode": "MANUAL_MASK",
        "images_discovered": 1,
        "images_extracted": 1,
        "images_with_predictions": 1,
        "independent_family_count": 1,
        "folds": 1,
        "masked_cutoff_count": 1,
        "frozen_prediction_count": 1,
        "preparation_failure_count": 0,
        "leakage_audit": audit,
        "metrics": metrics,
        "gallery_path": str(gallery_path),
        "run_bytes": sum(path.stat().st_size for path in output.rglob("*") if path.is_file()),
        "free_gb_after": round(free_after, 3),
    }
    _write_json_atomic(output / "summary.json", summary)
    report = Path(report_path)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(render_pure_masked_future_report_v3(summary), encoding="utf-8")
    return summary


__all__ = [
    "DEFAULT_PURE_HORIZONS",
    "PURE_REPLAY_SCHEMA_VERSION",
    "PreparedMaskedCaseV3",
    "freeze_prediction_v3",
    "render_pure_masked_future_report_v3",
    "run_manual_masked_future_replay_v3",
    "run_pure_masked_future_replay_v3",
    "select_causal_cutoffs_v3",
]
