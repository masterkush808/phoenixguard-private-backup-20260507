from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from phoenixguard.study.anchor_direction_replay_v3 import (
    ANCHOR_DIRECTION_PREDICTION_SCHEMA_V3,
    AnchorDirectionModelV3,
    CandleCorpusRecordV3,
    PreparedAnchorCaseV3,
    assert_chart_isolated_mask_v3,
    audit_anchor_direction_run_v3,
    build_anchor_direction_target_v3,
    candle_geometry_vector_v3,
    freeze_anchor_prediction_v3,
    score_anchor_prediction_v3,
)


def _candle(x: float, close_y: float, open_y: float | None = None) -> dict[str, Any]:
    opening = close_y if open_y is None else open_y
    return {
        "center_x_px": x,
        "open_y_px": opening,
        "close_y_px": close_y,
        "wick_top_px": min(opening, close_y) - 1.0,
        "wick_bottom_px": max(opening, close_y) + 1.0,
        "direction": "DELIBERATELY_IGNORED",
        "palette": "DELIBERATELY_IGNORED",
    }


def _case(
    case_id: str,
    family: str,
    fold: int,
    vector: tuple[float, ...],
    hidden: int,
    *,
    symbol: str = "EURUSD",
    timeframe: str = "M5",
) -> PreparedAnchorCaseV3:
    return PreparedAnchorCaseV3(
        case_id=case_id,
        image_id=case_id.split("-cutoff")[0],
        family_id=family,
        fold=fold,
        source_path=f"C:/memory/{case_id}.png",
        mask_path=f"C:/run/cases/{case_id.split('-cutoff')[0]}/cutoff-0010/masked_prefix.png",
        mask_sha256="a" * 64,
        symbol=symbol,
        timeframe=timeframe,
        cutoff=10,
        record_index=0,
        anchor_x_px=50.0,
        anchor_close_y_px=100.0,
        hidden_candle_count=hidden,
        visible_candle_count=10,
        geometry_vector=vector,
        fallback_side="UP",
    )


def _prediction(hidden: int = 3) -> dict[str, Any]:
    return {
        "schema_version": ANCHOR_DIRECTION_PREDICTION_SCHEMA_V3,
        "case_id": "image-a-cutoff-0010",
        "image_id": "image-a",
        "family_id": "family-a",
        "symbol": "EURUSD",
        "timeframe": "M5",
        "source_path": "C:/memory/image-a.png",
        "mask_path": "C:/run/cases/image-a/cutoff-0010/masked_prefix.png",
        "fixed_anchor": {"close_y_px": 100.0},
        "hidden_candle_count": hidden,
        "horizons": {
            str(index): {
                "predicted_side": "UP" if index % 2 else "DOWN",
                "probability_up": 0.7 if index % 2 else 0.3,
                "supporting_families": 3,
            }
            for index in range(1, hidden + 1)
        },
        "causal_contract": {
            "same_fixed_anchor_for_every_horizon": True,
            "every_hidden_candle_predicted": True,
        },
    }


def test_anchor_direction_uses_close_geometry_not_color() -> None:
    candles = [
        _candle(1, 100),
        _candle(2, 90),
        _candle(3, 110),
    ]
    candles[1]["direction"] = "SELL"
    candles[2]["direction"] = "BUY"
    assert build_anchor_direction_target_v3(
        candles,
        cutoff=1,
        anchor_close_y_px=100.0,
    ) == ("UP", "DOWN")


def test_fixed_anchor_is_used_for_every_hidden_candle() -> None:
    candles = [
        _candle(1, 100),
        _candle(2, 90),
        _candle(3, 95),
        _candle(4, 101),
    ]
    assert build_anchor_direction_target_v3(
        candles,
        cutoff=1,
        anchor_close_y_px=100.0,
    ) == ("UP", "UP", "DOWN")


def test_every_hidden_candle_receives_a_prediction(tmp_path: Path) -> None:
    frozen = freeze_anchor_prediction_v3(
        tmp_path / "prediction_frozen.json",
        _prediction(hidden=5),
    )
    score = score_anchor_prediction_v3(
        frozen,
        ("UP", "DOWN", "UP", "DOWN", "UP"),
        reveal_started_epoch_ms=max(
            int(frozen["prediction_frozen_epoch_ms"]), int(time.time() * 1000)
        ),
        fold=0,
    )
    assert list(score["horizons"]) == ["1", "2", "3", "4", "5"]
    assert score["accuracy"] == 1.0


def test_missing_hidden_candle_prediction_is_rejected(tmp_path: Path) -> None:
    prediction = _prediction(hidden=3)
    del prediction["horizons"]["3"]
    frozen = freeze_anchor_prediction_v3(
        tmp_path / "prediction_frozen.json",
        prediction,
    )
    with pytest.raises(ValueError, match="EVERY_HIDDEN_CANDLE"):
        score_anchor_prediction_v3(
            frozen,
            ("UP", "DOWN", "UP"),
            reveal_started_epoch_ms=max(
                int(frozen["prediction_frozen_epoch_ms"]),
                int(time.time() * 1000),
            ),
            fold=0,
        )


def test_mask_must_belong_to_the_same_chart(tmp_path: Path) -> None:
    case_dir = tmp_path / "cases" / "image-a" / "cutoff-0010"
    case_dir.mkdir(parents=True)
    mask = case_dir / "masked_prefix.png"
    score = case_dir / "scorecard.json"
    mask.write_bytes(b"mask")
    score.write_text("{}", encoding="utf-8")
    assert_chart_isolated_mask_v3(
        image_id="image-a",
        scorecard_path=score,
        mask_path=mask,
    )
    with pytest.raises(ValueError, match="IMAGE_ID_MISMATCH"):
        assert_chart_isolated_mask_v3(
            image_id="image-b",
            scorecard_path=score,
            mask_path=mask,
        )


def test_geometry_vector_ignores_direction_and_palette() -> None:
    first = [_candle(float(index), 100.0 - index) for index in range(1, 12)]
    second = [dict(candle) for candle in first]
    for candle in second:
        candle["direction"] = "OPPOSITE"
        candle["palette"] = "OPPOSITE"
    assert candle_geometry_vector_v3(
        first,
        anchor_close_y_px=89.0,
    ) == candle_geometry_vector_v3(second, anchor_close_y_px=89.0)


def test_equal_close_is_not_fabricated_as_up_or_down(tmp_path: Path) -> None:
    candles = [_candle(1, 100), _candle(2, 100)]
    actual = build_anchor_direction_target_v3(
        candles,
        cutoff=1,
        anchor_close_y_px=100.0,
    )
    frozen = freeze_anchor_prediction_v3(
        tmp_path / "prediction_frozen.json",
        _prediction(hidden=1),
    )
    score = score_anchor_prediction_v3(
        frozen,
        actual,
        reveal_started_epoch_ms=max(
            int(frozen["prediction_frozen_epoch_ms"]), int(time.time() * 1000)
        ),
        fold=0,
    )
    assert actual == ("TIE",)
    assert score["horizons"]["1"]["correct"] is False


def test_model_predicts_all_horizons_with_pair_timeframe_scope() -> None:
    vector = tuple(float(value) for value in np.zeros(64 * 9))
    cases = [
        _case(f"image-{index}-cutoff-0010", f"family-{index}", 0, vector, 3)
        for index in range(3)
    ]
    records = [
        CandleCorpusRecordV3(
            path="C:/memory/source.png",
            symbol="EURUSD",
            timeframe="M5",
            candles=tuple(
                [_candle(float(index), 100.0) for index in range(10)]
                + [_candle(10.0, 90.0), _candle(11.0, 89.0), _candle(12.0, 88.0)]
            ),
            analysis_width=100,
            analysis_height=100,
        )
    ]
    model = AnchorDirectionModelV3.fit(cases, records, neighbors=3)
    prediction = model.predict(cases[0])
    assert prediction["behavior_scope"] == "PAIR_TIMEFRAME"
    assert list(prediction["horizons"]) == ["1", "2", "3"]


def test_audit_rejects_forbidden_classification_key() -> None:
    prediction = _prediction(hidden=1)
    prediction["pullback"] = True
    prediction["prediction_frozen_epoch_ms"] = 100
    score = {
        "fold": 0,
        "prediction_preceded_reveal": True,
    }
    audit = audit_anchor_direction_run_v3(
        [{"prediction": prediction, "score": score}]
    )
    assert audit["status"] == "FAIL"
    assert any(
        str(failure).startswith("FORBIDDEN_PREDICTION_CONCEPT")
        for failure in audit["failures"]
    )


def test_prediction_file_contains_no_actual_future(tmp_path: Path) -> None:
    path = tmp_path / "prediction_frozen.json"
    freeze_anchor_prediction_v3(path, _prediction(hidden=3))
    payload = json.loads(path.read_text(encoding="utf-8"))
    serialized = json.dumps(payload).casefold()
    assert "actual_side" not in serialized
    assert "target" not in serialized
