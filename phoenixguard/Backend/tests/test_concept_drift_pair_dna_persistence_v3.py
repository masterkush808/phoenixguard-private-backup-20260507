from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from phoenixguard.study import pair_dna_v3
from phoenixguard.study.behavioral_sequence_v3 import measure_market_behavior_v3
from phoenixguard.study.candle_intelligence_v3 import analyze_candle_sequence_v3
from phoenixguard.study.concept_drift_v3 import (
    ConceptDriftValidationError,
    OnlineConceptDriftDetectorV3,
)
from phoenixguard.study.pair_dna_v3 import (
    PairDNAStoreV3,
    PairDNAValidationError,
    pair_profile_key_v3,
)


def _detector(
    *,
    window_size: int = 8,
    max_partitions: int = 8,
) -> OnlineConceptDriftDetectorV3:
    return OnlineConceptDriftDetectorV3(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        coordinate_space="NORMALIZED_MEDIAN_RANGE",
        order_domain="SOURCE_CANDLE_CLOSE_ORDER",
        feature_names=["BODY_RATIO", "MOTIF_DISTANCE"],
        window_size=window_size,
        significance_alpha=0.05,
        minimum_standardized_mean_shift=0.25,
        max_regime_partitions=max_partitions,
    )


def _observation(index: int, *, shifted: bool) -> dict[str, Any]:
    baseline = float(index % 4) / 20.0
    offset = 8.0 if shifted else 0.0
    return {
        "candle_id": f"C-{index}",
        "order_index": index,
        "is_closed": True,
        "coordinate_space": "NORMALIZED_MEDIAN_RANGE",
        "order_domain": "SOURCE_CANDLE_CLOSE_ORDER",
        "features": {
            "body_ratio": baseline + offset,
            "motif_distance": 0.5 * baseline + offset,
        },
    }


def _history(size: int) -> list[dict[str, Any]]:
    return [
        _observation(index, shifted=index >= 8)
        for index in range(size)
    ]


def _detected_state() -> tuple[OnlineConceptDriftDetectorV3, list[dict[str, Any]]]:
    detector = _detector()
    history = _history(16)
    result = detector.replay_retained_history(history)
    assert result["status"] == "DRIFT_DETECTED"
    return detector, history


def test_drift_snapshot_restart_replay_and_one_candle_append_are_stable() -> None:
    detector, history = _detected_state()
    prior_public = detector.snapshot()
    persisted = detector.persistence_snapshot()

    restored = OnlineConceptDriftDetectorV3.from_snapshot(
        persisted,
        symbol="CAD/JPY OTC",
        timeframe="M5",
    )
    assert restored.persistence_snapshot() == persisted
    assert restored.replay_retained_history(history)["status"] == "REPLAY_UNCHANGED"
    assert restored.snapshot()["partitions"] == prior_public["partitions"]

    appended_history = [*history, _observation(16, shifted=True)]
    appended = restored.replay_retained_history(appended_history)
    assert appended["status"] == "WARMING"
    assert appended["partitions"] == prior_public["partitions"]
    assert restored.persistence_snapshot()["configuration"]["window_size"] == 8
    assert restored.persistence_snapshot()["last_order_index"] == 16
    assert len(restored.persistence_snapshot()["rows"]) == 9

    tampered = dict(persisted)
    tampered["last_order_index"] = 999
    with pytest.raises(ConceptDriftValidationError, match="state_digest"):
        OnlineConceptDriftDetectorV3.from_snapshot(tampered)


def test_pair_dna_persists_private_drift_state_and_public_partition_contract(
    tmp_path: Path,
) -> None:
    detector, history = _detected_state()
    path = tmp_path / "pair_dna.json"
    store = PairDNAStoreV3(path, max_concept_drift_partitions=8)

    recorded = store.record_concept_drift_state(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        detector_state=detector.persistence_snapshot(),
    )
    assert recorded["status"] == "RECORDED"
    assert recorded["concept_drift"]["partition_count"] == 2
    assert "detector_state" not in recorded["concept_drift"]
    assert recorded["concept_drift"]["contract"][
        "partition_history_is_append_stable"
    ] is True

    restarted_store = PairDNAStoreV3(path, max_concept_drift_partitions=8)
    private = restarted_store.get_concept_drift_state("CAD/JPY OTC", "M5")
    assert private["status"] == "READY"
    restored = OnlineConceptDriftDetectorV3.from_snapshot(
        private["detector_state"],
        symbol="CAD/JPY OTC",
        timeframe="M5",
    )
    prior_ids = [
        row["regime_partition_id"] for row in restored.snapshot()["partitions"]
    ]
    restored.replay_retained_history(
        [*history, _observation(16, shifted=True)]
    )
    updated = restarted_store.record_concept_drift_state(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        detector_state=restored.persistence_snapshot(),
    )
    assert updated["status"] == "RECORDED"
    assert [
        row["regime_partition_id"]
        for row in updated["concept_drift"]["partitions"]
    ] == prior_ids
    assert restarted_store.record_concept_drift_state(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        detector_state=restored.persistence_snapshot(),
    )["status"] == "UNCHANGED"

    changed_configuration = _detector(window_size=9)
    changed_configuration.update(_observation(0, shifted=False))
    with pytest.raises(PairDNAValidationError, match="configuration are immutable"):
        restarted_store.record_concept_drift_state(
            symbol="CAD/JPY OTC",
            timeframe="M5",
            detector_state=changed_configuration.persistence_snapshot(),
        )


def test_pair_dna_allows_genesis_partition_to_receive_its_first_anchor(
    tmp_path: Path,
) -> None:
    detector = _detector()
    store = PairDNAStoreV3(tmp_path / "pair_dna.json")
    assert store.record_concept_drift_state(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        detector_state=detector.persistence_snapshot(),
    )["status"] == "RECORDED"

    detector.update(_observation(0, shifted=False))
    anchored = store.record_concept_drift_state(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        detector_state=detector.persistence_snapshot(),
    )
    assert anchored["status"] == "RECORDED"
    assert anchored["concept_drift"]["partitions"][0]["start_candle_id"] == "C-0"
    assert anchored["concept_drift"]["partitions"][0]["start_order_index"] == 0


def _studies() -> tuple[dict[str, Any], dict[str, Any]]:
    closes = [101.0, 103.0, 105.0, 105.05, 105.0, 103.0, 101.0]
    rows: list[dict[str, Any]] = []
    previous = 100.0
    for index, close in enumerate(closes):
        rows.append(
            {
                "candle_id": f"C-{index}",
                "timestamp": 1_700_000_000 + index * 300,
                "open": previous,
                "high": max(previous, close) + 0.5,
                "low": min(previous, close) - 0.5,
                "close": close,
                "is_closed": True,
            }
        )
        previous = close
    candle_study = analyze_candle_sequence_v3(rows, regime="TRENDING_UP")
    return candle_study, measure_market_behavior_v3(candle_study, inner_window=2)


def test_recent_sequence_object_types_are_bounded_and_legacy_safe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The production ceiling is a deliberate 1M storage bound; pin a small
    # ceiling here so the truncation behavior itself stays exercised.
    monkeypatch.setattr(pair_dna_v3, "MAX_RECENT_SEQUENCE_OBJECT_TYPES", 32)
    path = tmp_path / "pair_dna.json"
    store = PairDNAStoreV3(path)
    candle_study, behavior_study = _studies()
    objects = [
        {"object_type": f"OBJECT_{index:02d}"}
        for index in range(40)
    ]
    result = store.record_study(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        candle_study=candle_study,
        behavior_study=behavior_study,
        sequence_id="object-conditioned-sequence",
        objects=objects,
    )
    recent = result["profile"]["recent_sequences"][-1]
    assert len(recent["object_types"]) == pair_dna_v3.MAX_RECENT_SEQUENCE_OBJECT_TYPES
    assert recent["object_types"] == sorted(recent["object_types"])
    assert recent["object_types"][0] == "OBJECT_00"
    assert recent["object_types"][-1] == "OBJECT_31"

    raw = json.loads(path.read_text(encoding="utf-8"))
    pair_id = pair_profile_key_v3("CAD/JPY OTC", "M5")
    del raw["profiles"][pair_id]["recent_sequences"][-1]["object_types"]
    path.write_text(json.dumps(raw), encoding="utf-8")
    migrated = PairDNAStoreV3(path).get_profile("CAD/JPY OTC", "M5")
    assert migrated["profile"]["recent_sequences"][-1]["object_types"] == []
