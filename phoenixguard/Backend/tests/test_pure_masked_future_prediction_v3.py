from __future__ import annotations

import ast
import json
import time
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from phoenixguard.simulation.masked_future_v3 import assign_grouped_folds_v3
from phoenixguard.study.masked_future_scoring_v3 import (
    build_revealed_target_v3,
    score_frozen_prediction_v3,
)
from phoenixguard.study.masked_image_region_v3 import (
    MaskRectangleV3,
    automatic_mask_rectangle_v3,
    create_masked_image_v3,
    mask_proof_passes_v3,
)
from phoenixguard.study.pure_masked_future_gallery_v3 import (
    render_pure_masked_future_gallery_v3,
)
from phoenixguard.study.pure_masked_future_replay_v3 import freeze_prediction_v3


def _source_image(path: Path, width: int = 120, height: int = 60) -> None:
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    assert pixels is not None
    for y in range(height):
        for x in range(width):
            pixels[x, y] = (x % 251, y % 251, (x + y) % 251)
    image.save(path)


def _candles(closes: list[float]) -> list[dict[str, float]]:
    rows: list[dict[str, float]] = []
    previous = closes[0]
    for close in closes:
        rows.append(
            {
                "open": previous,
                "high": max(previous, close) + 0.2,
                "low": min(previous, close) - 0.2,
                "close": close,
            }
        )
        previous = close
    return rows


def _prediction(side: str = "BUY") -> dict[str, Any]:
    opposite = "SELL" if side == "BUY" else "BUY"
    return {
        "schema": "phoenixguard.pure_masked_future_prediction.v3",
        "horizons": {
            str(horizon): {
                "predicted_side": side,
                "probabilities": {side: 0.7, opposite: 0.2, "REST": 0.1},
                "confidence": 0.7,
                "candle_token": {
                    "direction": side,
                    "body": "MEDIUM",
                    "range": "MEDIUM",
                    "upper_wick": "SHORT",
                    "lower_wick": "SHORT",
                },
            }
            for horizon in (1, 2, 3)
        },
        "predicted_path_class": "CONTINUATION",
        "predicted_path": [0.2, 0.4, 0.6],
    }


def _frozen_prediction(path: Path, side: str = "BUY") -> dict[str, Any]:
    return freeze_prediction_v3(path, _prediction(side))


def test_masked_region_is_hidden_before_prediction(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    masked = tmp_path / "masked.png"
    _source_image(source)
    proof = create_masked_image_v3(
        source,
        masked,
        rectangle=MaskRectangleV3(80, 0, 120, 60),
        maximum_width=0,
        mask_color=(3, 5, 7),
    )
    assert mask_proof_passes_v3(proof)
    with Image.open(masked) as image:
        assert image.getpixel((80, 0)) == (3, 5, 7)
        assert image.getpixel((119, 59)) == (3, 5, 7)


def test_future_pixels_not_visible_to_predictor(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    masked = tmp_path / "masked.png"
    _source_image(source)
    create_masked_image_v3(
        source,
        masked,
        rectangle=MaskRectangleV3(75, 0, 120, 60),
        maximum_width=0,
        mask_color=(11, 13, 17),
    )
    with Image.open(source) as original, Image.open(masked) as prefix:
        assert prefix.crop((0, 0, 75, 60)).tobytes() == original.crop((0, 0, 75, 60)).tobytes()
        assert prefix.crop((75, 0, 120, 60)).tobytes() == bytes((11, 13, 17)) * (45 * 60)
        assert prefix.crop((75, 0, 120, 60)).tobytes() != original.crop((75, 0, 120, 60)).tobytes()


def test_prediction_written_before_reveal(tmp_path: Path) -> None:
    prediction_path = tmp_path / "prediction_frozen.json"
    frozen = _frozen_prediction(prediction_path)
    frozen_mtime = prediction_path.stat().st_mtime_ns
    time.sleep(0.002)
    reveal_path = tmp_path / "revealed_actual.png"
    Image.new("RGB", (10, 10), "white").save(reveal_path)
    assert prediction_path.exists()
    assert int(frozen["prediction_frozen_epoch_ms"]) > 0
    assert frozen_mtime <= reveal_path.stat().st_mtime_ns


def test_future_suffix_revealed_only_to_scorer(tmp_path: Path) -> None:
    target = build_revealed_target_v3(
        _candles([1.0, 1.1, 1.2, 1.3, 1.5, 1.7, 1.9]),
        cutoff=4,
        horizons=(1, 2, 3),
        context={},
    )
    with pytest.raises(ValueError, match="PG_PREDICTION_WAS_NOT_FROZEN"):
        score_frozen_prediction_v3(
            _prediction(),
            target,
            reveal_started_epoch_ms=int(time.time() * 1000),
            fold=0,
            source_path="image.png",
            market_phase="TREND",
        )
    frozen = _frozen_prediction(tmp_path / "prediction_frozen.json")
    score = score_frozen_prediction_v3(
        frozen,
        target,
        reveal_started_epoch_ms=max(
            int(frozen["prediction_frozen_epoch_ms"]), int(time.time() * 1000)
        ),
        fold=0,
        source_path="image.png",
        market_phase="TREND",
    )
    assert score["horizons"]


def test_folder_label_not_used_as_target(tmp_path: Path) -> None:
    target = build_revealed_target_v3(
        _candles([1.0, 1.1, 1.2, 1.3, 1.5, 1.6, 1.8]),
        cutoff=4,
        horizons=(1, 2, 3),
        context={},
    )
    frozen = _frozen_prediction(tmp_path / "prediction_frozen.json")
    reveal_time = max(int(frozen["prediction_frozen_epoch_ms"]), int(time.time() * 1000))
    buy_path = score_frozen_prediction_v3(
        frozen,
        target,
        reveal_started_epoch_ms=reveal_time,
        fold=1,
        source_path="memory/BUYS/example.png",
        market_phase="TREND",
    )
    sell_path = score_frozen_prediction_v3(
        frozen,
        target,
        reveal_started_epoch_ms=reveal_time,
        fold=1,
        source_path="memory/SELLS/example.png",
        market_phase="TREND",
    )
    assert buy_path["horizons"] == sell_path["horizons"]


def test_all_cutoffs_from_one_family_in_one_fold() -> None:
    families = ["a", "a", "a", "b", "b", "c", "c", "c"]
    folds = assign_grouped_folds_v3(families, folds=3)
    for family in set(families):
        assert len({fold for item, fold in zip(families, folds) if item == family}) == 1


def test_hidden_region_score_uses_actual_reveal_only(tmp_path: Path) -> None:
    frozen = _frozen_prediction(tmp_path / "prediction_frozen.json", "BUY")
    reveal_time = max(int(frozen["prediction_frozen_epoch_ms"]), int(time.time() * 1000))
    prefix = [1.0, 1.1, 1.2, 1.3]
    up = build_revealed_target_v3(
        _candles(prefix + [1.5, 1.7, 1.9]), cutoff=4, horizons=(1, 2, 3), context={}
    )
    down = build_revealed_target_v3(
        _candles(prefix + [1.1, 0.9, 0.7]), cutoff=4, horizons=(1, 2, 3), context={}
    )
    up_score = score_frozen_prediction_v3(
        frozen,
        up,
        reveal_started_epoch_ms=reveal_time,
        fold=0,
        source_path="same.png",
        market_phase="TREND",
    )
    down_score = score_frozen_prediction_v3(
        frozen,
        down,
        reveal_started_epoch_ms=reveal_time,
        fold=0,
        source_path="same.png",
        market_phase="TREND",
    )
    assert up_score["horizons"]["3"]["majority_correct"] is True
    assert down_score["horizons"]["3"]["majority_correct"] is False


def test_manual_mask_rect_respected(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    masked = tmp_path / "masked.png"
    _source_image(source, 100, 50)
    create_masked_image_v3(
        source,
        masked,
        rectangle=MaskRectangleV3(40, 10, 90, 45),
        maximum_width=0,
        mask_color=(19, 23, 29),
    )
    with Image.open(source) as original, Image.open(masked) as result:
        assert result.getpixel((40, 10)) == (19, 23, 29)
        assert result.getpixel((89, 44)) == (19, 23, 29)
        assert result.getpixel((39, 10)) == original.getpixel((39, 10))
        assert result.getpixel((90, 44)) == original.getpixel((90, 44))


def test_auto_cutoff_generates_valid_prefix_and_suffix() -> None:
    candles = [{"center_x_px": float(value)} for value in range(10, 100, 10)]
    rectangle = automatic_mask_rectangle_v3(
        candles, cutoff=5, width=120, height=60
    )
    assert 50 < rectangle.x1 < 60
    assert rectangle.y1 == 0
    assert rectangle.x2 == 120
    assert rectangle.y2 == 60


def test_gallery_contains_masked_prediction_and_revealed_actual(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    case_dir = run_dir / "cases" / "image-a" / "cutoff-0004"
    case_dir.mkdir(parents=True)
    artifact_names = {
        "masked_prefix": "masked_prefix.png",
        "prediction_before_reveal": "prediction_overlay_before_reveal.png",
        "revealed_actual": "revealed_actual.png",
        "prediction_vs_actual": "prediction_vs_actual_overlay.png",
    }
    artifacts: dict[str, str] = {}
    for key, name in artifact_names.items():
        path = case_dir / name
        Image.new("RGB", (20, 10), "black").save(path)
        artifacts[key] = str(path)
    scorecard = {
        "case_id": "image-a-cutoff-0004",
        "source_path": "memory/example.png",
        "market_phase": "TREND",
        "artifacts": artifacts,
        "horizons": {
            "1": {
                "predicted_side": "BUY",
                "actual_majority_side": "BUY",
                "majority_correct": True,
            }
        },
    }
    (case_dir / "scorecard.json").write_text(json.dumps(scorecard), encoding="utf-8")
    output = run_dir / "gallery" / "index.html"
    render_pure_masked_future_gallery_v3(run_dir, output)
    rendered = output.read_text(encoding="utf-8")
    assert "Masked prefix" in rendered
    assert "Prediction before reveal" in rendered
    assert "Revealed actual" in rendered
    assert "Prediction vs actual" in rendered
    assert "../cases/image-a/cutoff-0004/masked_prefix.png" in rendered


def _new_replay_sources() -> list[Path]:
    root = Path(__file__).resolve().parents[1]
    return [
        *sorted((root / "src" / "phoenixguard" / "study").glob("*masked_future*v3.py")),
        root / "src" / "phoenixguard" / "study" / "masked_image_region_v3.py",
        root / "src" / "phoenixguard" / "study" / "prefix_vision_prediction_v3.py",
        root / "tools" / "run_pure_masked_future_prediction_v3.py",
    ]


def test_no_pg_execution_packet_imported_or_created() -> None:
    for path in _new_replay_sources():
        assert "PG_EXECUTION_PACKET_V3" not in path.read_text(encoding="utf-8")


def test_no_mt4_bridge_called() -> None:
    for path in _new_replay_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = [
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        ]
        imported.extend(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        )
        assert not any("mt4" in module.casefold() for module in imported)
