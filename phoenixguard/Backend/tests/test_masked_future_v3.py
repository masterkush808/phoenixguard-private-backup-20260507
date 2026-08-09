from __future__ import annotations

from pathlib import Path

import pytest

from phoenixguard.simulation.masked_future_v3 import (
    DiskReserveError,
    assign_grouped_folds_v3,
    build_masked_future_target_v3,
    enforce_disk_reserve,
    parse_instrument_text_v3,
)
from phoenixguard.study.behavioral_sequence_v3 import measure_market_behavior_v3
from phoenixguard.study.candle_intelligence_v3 import analyze_candle_sequence_v3
from phoenixguard.study.masked_future_behavior_v3 import (
    MaskedFutureBehaviorModelV3,
    apply_masked_future_evidence_v3,
    build_masked_future_context_v3,
    finalize_masked_future_model_v3,
    new_masked_future_model_artifact_v3,
    update_masked_future_model_v3,
)


def _candles(deltas: list[float], *, start: float = 100.0) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    price = start
    for index, delta in enumerate(deltas):
        close = price + delta
        rows.append(
            {
                "candle_id": f"c{index}",
                "timestamp": index * 300,
                "is_closed": True,
                "open": price,
                "high": max(price, close) + 0.4,
                "low": min(price, close) - 0.4,
                "close": close,
            }
        )
        price = close
    return rows


def _context(rows: list[dict[str, object]]) -> dict[str, object]:
    study = analyze_candle_sequence_v3(rows, require_closed=True)
    behavior = measure_market_behavior_v3(study, timeframe_seconds=300)
    return build_masked_future_context_v3(study["candles"], behavior, symbol="EURUSD", timeframe="M5")


def test_context_is_identical_when_only_withheld_suffix_changes() -> None:
    prefix = _candles(([1.0, -0.4, 0.8, 0.3] * 8)[:24])
    context_a = _context(prefix)
    context_b = _context(prefix)
    assert context_a["feature_digest"] == context_b["feature_digest"]
    assert context_a["future_fields_present"] is False


def test_whole_swing_labels_two_candle_sell_as_pullback_before_buy() -> None:
    prefix = _candles([0.6] * 24)
    future = _candles([-0.5, -0.4] + [0.8] * 19, start=float(prefix[-1]["close"]))
    target = build_masked_future_target_v3(prefix + future, cutoff=len(prefix), horizons=(3, 5, 8, 13, 21))
    assert target["whole_swing"]["side"] == "BUY"
    assert target["whole_swing"]["pullback_candles"] == 2
    assert target["pullback"] is True


def test_empirical_model_learns_majority_direction_and_swing_length() -> None:
    context = _context(_candles([0.7] * 24))
    artifact = new_masked_future_model_artifact_v3((13, 21))
    target = {
        "horizons": {"13": "BUY", "21": "BUY"},
        "endpoint_horizons": {"13": "BUY", "21": "BUY"},
        "whole_swing": {"side": "BUY", "candles": 18},
        "pullback": False,
    }
    for _ in range(20):
        update_masked_future_model_v3(artifact, context, target)
    artifact["promotion"] = {"eligible": True, "reason": "TEST"}
    model = MaskedFutureBehaviorModelV3(finalize_masked_future_model_v3(artifact))
    prediction = model.predict_context(context)
    assert prediction["whole_swing"]["predicted_side"] == "BUY"
    assert prediction["whole_swing"]["probabilities"]["BUY"] > 0.8
    assert prediction["whole_swing"]["expected_candles"] == 18.0


def test_promoted_evidence_updates_state_control_without_execution_authority() -> None:
    evidence = {
        "status": "ACTIVE",
        "promotion_eligible": True,
        "whole_swing": {
            "predicted_side": "SELL",
            "probabilities": {"BUY": 0.2, "SELL": 0.75, "REST": 0.05},
            "support": 40,
            "expected_candles": 15.0,
            "candle_interval_80": [10, 22],
            "pullback_before_swing_probability": 0.3,
        },
    }
    hidden = {"timeframe": "H4", "control": {"side": "UNRESOLVED"}, "directional_components": {}}
    merged = apply_masked_future_evidence_v3(hidden, evidence)
    assert merged["control"]["side"] == "SELL"
    assert merged["state_cycle_horizon"]["expected_candles"] == 15.0
    assert merged["state_cycle_horizon"]["duration"]["hours"] == 60.0
    assert merged["execution_authority"] is False
    assert merged["grants_entry_permission"] is False


def test_grouped_folds_never_split_one_family() -> None:
    groups = ["a", "a", "b", "c", "c", "d"]
    folds = assign_grouped_folds_v3(groups, folds=3)
    assert folds[0] == folds[1]
    assert folds[3] == folds[4]


def test_disk_reserve_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("phoenixguard.simulation.masked_future_v3.available_free_gb", lambda _path: 44.9)
    with pytest.raises(DiskReserveError, match="PG_DISK_RESERVE_BLOCKED"):
        enforce_disk_reserve(tmp_path, minimum_free_gb=45.0)


def test_ocr_title_text_resolves_pair_and_timeframe_without_direction_label() -> None:
    symbol, timeframe = parse_instrument_text_v3(
        "245498723: Trade245-Live - Trade245 (Pty) Ltd - [GBPNZD,M30]"
    )
    assert symbol == "GBPNZD"
    assert timeframe == "M30"


def test_context_exposes_visible_scale_conflict_without_future_fields() -> None:
    context = _context(_candles([0.8] * 22 + [-0.8, -0.8]))
    features = context["features"]
    assert features["state_side"] == "SELL"
    assert features["long_side"] == "BUY"
    assert "COUNTER_TO_BUY" in features["scale_conflict"]
    assert context["future_fields_present"] is False
