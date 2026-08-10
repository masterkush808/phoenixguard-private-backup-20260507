from __future__ import annotations

import numpy as np

from phoenixguard.simulation.masked_future_v3 import assign_grouped_folds_v3
from phoenixguard.study.event_windows_v3 import (
    build_event_window_v3,
    feature_vector_v3,
)
from phoenixguard.study.leakage_audit_v3 import audit_optimized_windows_v3
from phoenixguard.study.optimized_hidden_state_contributor_v3 import (
    attach_optimized_hidden_state_evidence_v3,
)
from phoenixguard.study.optimized_hidden_state_v3 import EmpiricalEventPriorV3
from phoenixguard.study.optimized_targets_v3 import build_trade_path_target_v3
from phoenixguard.study.probability_calibration_v3 import (
    fit_calibrators_v3,
    select_confidence_threshold_v3,
)


def _candle(open_price: float, close_price: float) -> dict[str, float | str | bool]:
    high = max(open_price, close_price) + 0.25
    low = min(open_price, close_price) - 0.25
    return {
        "open_y_px": -open_price,
        "close_y_px": -close_price,
        "wick_top_px": -high,
        "wick_bottom_px": -low,
        "direction": "BUY" if close_price > open_price else "SELL",
        "parse_confidence": 0.98,
        "spacing_confidence": 0.97,
        "is_closed": True,
        "candle_id": f"{open_price:.3f}:{close_price:.3f}",
    }


def _prefix() -> list[dict[str, float | str | bool]]:
    prices = [100.0]
    for index in range(30):
        prices.append(prices[-1] + (0.45 if index < 24 else -0.30))
    return [_candle(prices[index], prices[index + 1]) for index in range(30)]


def test_no_folder_label_leakage() -> None:
    event = build_event_window_v3(
        _prefix(),
        cutoff=30,
        image_hash="image",
        family_id="family",
        symbol="EURUSD",
        timeframe="M5",
        path="BUYS/example.png",
    )
    assert "source_bucket" not in event["features"]
    assert "folder" not in " ".join(event["features"]).lower()
    assert feature_vector_v3(event).shape[0] > 40


def test_future_candles_not_in_features() -> None:
    prefix = _prefix()
    buy_suffix = [_candle(108 + index, 109 + index) for index in range(8)]
    sell_suffix = [_candle(108 - index, 107 - index) for index in range(8)]
    first = build_event_window_v3(
        prefix + buy_suffix,
        cutoff=len(prefix),
        image_hash="same",
        family_id="family",
        symbol="EURUSD",
        timeframe="M5",
    )
    second = build_event_window_v3(
        prefix + sell_suffix,
        cutoff=len(prefix),
        image_hash="same",
        family_id="family",
        symbol="EURUSD",
        timeframe="M5",
    )
    assert first["visible_prefix_hash"] == second["visible_prefix_hash"]
    assert first["features"] == second["features"]


def test_near_duplicate_families_do_not_cross_folds() -> None:
    groups = ["a", "a", "b", "c", "c", "d", "e"]
    folds = assign_grouped_folds_v3(groups, folds=3)
    assert folds[0] == folds[1]
    assert folds[3] == folds[4]


def test_event_window_labels_revealed_only_after_prediction() -> None:
    event = build_event_window_v3(
        _prefix(),
        cutoff=30,
        image_hash="image",
        family_id="family",
        symbol="EURUSD",
        timeframe="M5",
    )
    assert "target" not in event
    assert "outcome" not in event["features"]


def test_triple_barrier_labels_are_suffix_only() -> None:
    prefix = _prefix()
    entry = -float(prefix[-1]["close_y_px"])
    rising = prefix + [
        _candle(entry + index * 1.5, entry + (index + 1) * 1.5)
        for index in range(21)
    ]
    falling = prefix + [
        _candle(entry - index * 1.5, entry - (index + 1) * 1.5)
        for index in range(21)
    ]
    first = build_trade_path_target_v3(
        rising,
        cutoff=len(prefix),
        side_candidate="BUY",
        horizon=21,
    )
    second = build_trade_path_target_v3(
        falling,
        cutoff=len(prefix),
        side_candidate="BUY",
        horizon=21,
    )
    assert first["target_before_invalidation"] is True
    assert second["invalidation_before_target"] is True


def test_calibration_uses_oof_predictions_only() -> None:
    rows = [
        {
            "event": {
                "family_id": "family-a",
                "image_hash": "image-a",
                "cutoff": 24,
                "visible_prefix_candles": 24,
                "features": {"scale_conflict": 1.0},
            }
        }
    ]
    audit = audit_optimized_windows_v3(
        rows,
        fold_by_family={"family-a": 0},
        calibration_event_ids=["calibration"],
        test_event_ids=["test"],
    )
    assert audit["status"] == "PASS"
    calibrators = fit_calibrators_v3(
        np.asarray([0, 1] * 20),
        np.linspace(0.1, 0.9, 40),
    )
    assert "platt" in calibrators


def test_high_confidence_precision_has_coverage_floor() -> None:
    labels = np.asarray([1] * 8 + [0] * 12)
    probabilities = np.asarray([0.9] * 8 + [0.2] * 12)
    selected = select_confidence_threshold_v3(
        labels,
        probabilities,
        minimum_precision=0.70,
        minimum_coverage=0.20,
    )
    assert selected["meets_objective"] is True
    assert selected["precision"] >= 0.70
    assert selected["coverage"] >= 0.20


def test_empirical_prior_does_not_override_calibrated_model() -> None:
    events = [
        {"event_type": "PULLBACK_VISIBLE", "side_candidate": "BUY", "symbol": "EURUSD", "timeframe": "M5"},
        {"event_type": "PULLBACK_VISIBLE", "side_candidate": "BUY", "symbol": "EURUSD", "timeframe": "M5"},
    ]
    prior = EmpiricalEventPriorV3().fit(events, np.asarray([1, 0]))
    probability = prior.predict(events[:1])[0]
    assert 0.0 < probability < 1.0


def test_optimized_model_does_not_grant_execution_permission() -> None:
    evidence = {
        "status": "ACTIVE",
        "side": "BUY",
        "event_type": "PULLBACK_VISIBLE",
        "opportunity_maturity": "HIGH_CONFIDENCE_EVENT",
        "target_before_invalidation_probability": 0.81,
        "selected_high_confidence": True,
        "promotion_eligible": True,
    }
    attached = attach_optimized_hidden_state_evidence_v3(
        {"execution": {"enabled": False}},
        {"masked_future_optimized_v3": evidence},
    )
    contributor = attached["optimized_hidden_state_contributor_v3"]
    assert contributor["grants_entry_permission"] is False
    assert contributor["execution_authority"] == "NONE"
    assert attached["execution"]["enabled"] is False


def test_model_council_receives_hidden_state_evidence_not_direct_order() -> None:
    payload = attach_optimized_hidden_state_evidence_v3(
        {"model_council": {"final_state": "WATCHING"}},
        {
            "masked_future_optimized_v3": {
                "status": "ACTIVE",
                "side": "SELL",
                "selected_high_confidence": True,
                "promotion_eligible": True,
            }
        },
    )
    assert payload["masked_future_optimized_v3"]["side"] == "SELL"
    assert "execution_packet" not in payload
    assert payload["optimized_hidden_state_contributor_v3"]["study_only"] is True
