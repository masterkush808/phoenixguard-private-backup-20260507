from __future__ import annotations

from copy import deepcopy
import math
from typing import Any

import pytest

from phoenixguard.study.adaptive_feature_ontology_v3 import (
    AdaptiveFeatureOntologyV3,
    AdaptiveFeatureOntologyValidationError,
)
from phoenixguard.study.concept_drift_v3 import (
    ConceptDriftValidationError,
    OnlineConceptDriftDetectorV3,
)
from phoenixguard.study.cross_pair_association_v3 import (
    CrossPairAssociationValidationError,
    analyze_cross_pair_lead_lag_v3,
    build_cross_pair_association_graph_v3,
)
from phoenixguard.study.study_claim_proof_v3 import (
    PUBLIC_STUDY_CANONICAL_PROJECTION_VERSION,
    StudyClaimProofValidationError,
    canonical_public_study_hash_v3,
    issue_study_claim_certificate_v3,
    verify_study_claim_certificate_v3,
)


def _passing_feature_evaluation() -> dict[str, Any]:
    return {
        "support_count": 160,
        "holdout_support_count": 64,
        "independent_partition_count": 4,
        "closed_candle_only": True,
        "temporal_precedence_verified": True,
        "future_leakage_detected": False,
        "deterministic_derivation": True,
        "coordinate_space_preserved": True,
        "order_domain_preserved": True,
        "stability_score": 0.84,
        "effect_size": 0.42,
        "adjusted_p_value": 0.02,
    }


def test_shadow_feature_requires_gate_and_promotion_is_versioned_reversible() -> None:
    ontology = AdaptiveFeatureOntologyV3()
    proposed = ontology.propose_shadow_feature(
        feature_id="wick-asymmetry-after-imbalance",
        definition={
            "description": "Upper minus lower wick ratio after a proven imbalance",
            "inputs": ["upper_wick_ratio", "lower_wick_ratio"],
        },
        derivation={
            "algorithm_id": "WICK_ASYMMETRY",
            "algorithm_version": "3.1",
            "expression": "upper_wick_ratio-lower_wick_ratio",
        },
        closed_candle_ids=[f"C-{index}" for index in range(8)],
        coordinate_space="NORMALIZED_MEDIAN_RANGE",
        order_domain="SOURCE_CANDLE_CLOSE_ORDER",
    )

    feature = proposed["feature"]
    assert feature["namespace"] == "SHADOW"
    assert feature["status"] == "SHADOW"
    assert feature["revisions"][0]["execution_authority"] is False
    assert ontology.public_study_snapshot()["features"] == []
    with pytest.raises(
        AdaptiveFeatureOntologyValidationError,
        match="passing latest promotion gate",
    ):
        ontology.promote("wick-asymmetry-after-imbalance")

    failed = _passing_feature_evaluation()
    failed["future_leakage_detected"] = True
    evaluated = ontology.evaluate_promotion_gate(
        "wick-asymmetry-after-imbalance",
        evaluation=failed,
    )
    gate = evaluated["feature"]["revisions"][-1]["promotion_gate"]
    assert gate["passed"] is False
    assert gate["contract"]["establishes_causation"] is False
    with pytest.raises(AdaptiveFeatureOntologyValidationError):
        ontology.promote("wick-asymmetry-after-imbalance")

    ontology.evaluate_promotion_gate(
        "wick-asymmetry-after-imbalance",
        evaluation=_passing_feature_evaluation(),
    )
    promoted = ontology.promote("wick-asymmetry-after-imbalance")
    assert promoted["feature"]["status"] == "PROMOTED"
    assert promoted["feature"]["namespace"] == "PUBLIC_STUDY"
    assert promoted["execution_authority"] is False
    assert [
        row["feature_id"] for row in ontology.public_study_snapshot()["features"]
    ] == ["WICK-ASYMMETRY-AFTER-IMBALANCE"]
    promoted_revision = promoted["feature"]["current_revision"]

    rolled_back = ontology.rollback(
        "wick-asymmetry-after-imbalance",
        target_revision=1,
        reason="Holdout distribution changed after audit",
    )
    assert rolled_back["feature"]["status"] == "SHADOW"
    assert rolled_back["feature"]["namespace"] == "SHADOW"
    assert ontology.public_study_snapshot()["features"] == []
    assert rolled_back["feature"]["current_revision"] > promoted_revision
    assert rolled_back["feature"]["revisions"][-1]["rollback"][
        "target_revision"
    ] == 1
    assert [
        row["ontology_version"] for row in rolled_back["feature"]["revisions"]
    ] == sorted(
        row["ontology_version"]
        for row in rolled_back["feature"]["revisions"]
    )


def test_feature_ontology_is_bounded_and_fails_closed() -> None:
    ontology = AdaptiveFeatureOntologyV3(max_features=1, max_revisions_per_feature=3)
    ontology.propose_shadow_feature(
        feature_id="one",
        definition={"description": "one"},
        derivation={"algorithm_id": "A", "algorithm_version": "3"},
        closed_candle_ids=["C-1"],
        coordinate_space="NORMALIZED_RETURN",
        order_domain="SOURCE_CANDLE_CLOSE_ORDER",
    )
    with pytest.raises(AdaptiveFeatureOntologyValidationError, match="capacity"):
        ontology.propose_shadow_feature(
            feature_id="two",
            definition={"description": "two"},
            derivation={"algorithm_id": "B", "algorithm_version": "3"},
            closed_candle_ids=["C-2"],
            coordinate_space="NORMALIZED_RETURN",
            order_domain="SOURCE_CANDLE_CLOSE_ORDER",
        )
    ontology.evaluate_promotion_gate("one", evaluation=_passing_feature_evaluation())
    ontology.promote("one")
    with pytest.raises(AdaptiveFeatureOntologyValidationError, match="revision capacity"):
        ontology.rollback("one", target_revision=1, reason="capacity audit")


def test_feature_ontology_snapshot_is_pair_scoped_and_restart_safe() -> None:
    ontology = AdaptiveFeatureOntologyV3(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        minimum_effect_size=0.15,
    )
    ontology.propose_shadow_feature(
        feature_id="wick-asymmetry",
        definition={"description": "closed-candle wick asymmetry"},
        derivation={"algorithm_id": "WICK", "algorithm_version": "3"},
        closed_candle_ids=[f"C-{index}" for index in range(64)],
        coordinate_space="NORMALIZED_RETURN",
        order_domain="SOURCE_CANDLE_CLOSE_ORDER",
    )
    ontology.evaluate_promotion_gate(
        "wick-asymmetry",
        evaluation=_passing_feature_evaluation(),
        closed_candle_ids=[f"C-{index}" for index in range(80)],
        coordinate_space="NORMALIZED_RETURN",
        order_domain="SOURCE_CANDLE_CLOSE_ORDER",
    )
    ontology.promote("wick-asymmetry")
    snapshot = ontology.snapshot()

    restored = AdaptiveFeatureOntologyV3.from_snapshot(
        snapshot,
        symbol="CAD/JPY OTC",
        timeframe="M5",
    )
    assert restored.snapshot() == snapshot
    assert restored.public_study_snapshot()["scope"] == {
        "symbol": "CAD/JPY OTC",
        "timeframe": "M5",
    }
    with pytest.raises(AdaptiveFeatureOntologyValidationError, match="symbol mismatch"):
        AdaptiveFeatureOntologyV3.from_snapshot(
            snapshot,
            symbol="GBP/JPY OTC",
            timeframe="M5",
        )


def _drift_detector(*, max_partitions: int = 8) -> OnlineConceptDriftDetectorV3:
    return OnlineConceptDriftDetectorV3(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        coordinate_space="NORMALIZED_MEDIAN_RANGE",
        order_domain="SOURCE_CANDLE_CLOSE_ORDER",
        feature_names=["BODY_RATIO", "MOTIF_DISTANCE"],
        window_size=8,
        significance_alpha=0.05,
        minimum_standardized_mean_shift=0.25,
        max_regime_partitions=max_partitions,
    )


def _drift_observation(index: int, *, shifted: bool) -> dict[str, Any]:
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


def test_online_drift_creates_deterministic_regime_partition_from_closed_data() -> None:
    first = _drift_detector()
    second = _drift_detector()
    first_result: dict[str, Any] = {}
    second_result: dict[str, Any] = {}
    for index in range(16):
        observation = _drift_observation(index, shifted=index >= 8)
        first_result = first.update(observation)
        second_result = second.update(observation)

    assert first_result["status"] == "DRIFT_DETECTED"
    assert first_result["metrics"]["statistically_significant_drift"] is True
    assert first_result["partition_count"] == 2
    assert first_result["current_regime_partition_id"] == second_result[
        "current_regime_partition_id"
    ]
    assert first_result["partitions"][0]["status"] == "CLOSED"
    assert first_result["partitions"][1]["created_by"] == (
        "STATISTICALLY_SIGNIFICANT_CONCEPT_DRIFT"
    )
    assert first_result["execution_authority"] is False
    assert first_result["predicts_direction"] is False
    assert first.snapshot()["buffered_closed_candles"] == 8
    assert "features" not in first.snapshot()


def test_online_drift_rejects_open_or_incompatible_and_bounds_partitions() -> None:
    detector = _drift_detector(max_partitions=1)
    open_row = _drift_observation(0, shifted=False)
    open_row["is_closed"] = False
    with pytest.raises(ConceptDriftValidationError, match="closed candles only"):
        detector.update(open_row)
    wrong_space = _drift_observation(0, shifted=False)
    wrong_space["coordinate_space"] = "PIXEL_PRICE_PROXY"
    with pytest.raises(ConceptDriftValidationError, match="coordinate_space"):
        detector.update(wrong_space)
    result: dict[str, Any] = {}
    for index in range(16):
        result = detector.update(_drift_observation(index, shifted=index >= 8))
    assert result["status"] == "DRIFT_PARTITION_CAPACITY_REACHED"
    assert result["partition_count"] == 1


def _pseudo_random_series(size: int) -> list[float]:
    state = 917_321
    result: list[float] = []
    for _ in range(size):
        state = (1_103_515_245 * state + 12_345) % (2**31)
        result.append((state / (2**31) - 0.5) * 2.0)
    return result


def _cross_pair_rows(
    pair_id: str,
    values: list[float],
    *,
    coordinate_space: str = "NORMALIZED_RETURN",
) -> list[dict[str, Any]]:
    return [
        {
            "pair_id": pair_id,
            "candle_id": f"{pair_id}-{index}",
            "closed_timestamp": 1_700_000_000 + index * 300,
            "is_closed": True,
            "coordinate_space": coordinate_space,
            "order_domain": "SOURCE_CANDLE_CLOSE_ORDER",
            "value": value,
        }
        for index, value in enumerate(values)
    ]


def test_cross_pair_lead_lag_publishes_only_significant_non_causal_association() -> None:
    size = 144
    source = _pseudo_random_series(size)
    target = [0.0, 0.0]
    noise = _pseudo_random_series(size + 17)[17:]
    for index in range(2, size):
        target.append(
            0.12 * target[index - 1]
            + 1.4 * source[index - 2]
            + 0.015 * noise[index]
        )

    result = analyze_cross_pair_lead_lag_v3(
        _cross_pair_rows("CAD/JPY OTC", source),
        _cross_pair_rows("GBP/USD OTC", target),
        max_lag=4,
        minimum_support=96,
        significance_alpha=0.05,
        max_null_shifts=127,
    )

    assert result["status"] == "SUPPORTED"
    association = next(
        row
        for row in result["significant_associations"]
        if row["source_pair_id"] == "CAD/JPY OTC"
        and row["target_pair_id"] == "GBP/USD OTC"
    )
    assert association["lag_completed_candles"] == 2
    assert association["granger_style_variance_reduction"] > 0.90
    assert association["mutual_information_nats"] > 0.0
    assert association["bonferroni_adjusted_p_value"] <= 0.05
    assert association["causal"] is False
    assert association["execution_authority"] is False
    assert result["contract"]["granger_style_is_proxy_not_causal_test"] is True
    assert result["contract"]["publishes_only_significant_associations"] is True


def test_cross_pair_requires_exact_closed_timestamp_and_normalized_geometry() -> None:
    values = [math.sin(index) for index in range(40)]
    left = _cross_pair_rows("LEFT", values)
    right = _cross_pair_rows("RIGHT", values)
    right[20]["closed_timestamp"] += 1
    with pytest.raises(
        CrossPairAssociationValidationError,
        match="contiguous uniform closed-timestamp",
    ):
        analyze_cross_pair_lead_lag_v3(
            left,
            right,
            max_lag=2,
            minimum_support=32,
        )

    raw_left = _cross_pair_rows("LEFT", values, coordinate_space="PRICE")
    raw_right = _cross_pair_rows("RIGHT", values, coordinate_space="PRICE")
    with pytest.raises(
        CrossPairAssociationValidationError,
        match="normalized compatible",
    ):
        analyze_cross_pair_lead_lag_v3(
            raw_left,
            raw_right,
            max_lag=2,
            minimum_support=32,
        )

    open_left = _cross_pair_rows("LEFT", values)
    open_left[-1]["is_closed"] = False
    with pytest.raises(CrossPairAssociationValidationError, match="not a completed"):
        analyze_cross_pair_lead_lag_v3(
            open_left,
            _cross_pair_rows("RIGHT", values),
            max_lag=2,
            minimum_support=32,
        )


def test_multi_pair_association_graph_is_bounded_and_study_only() -> None:
    size = 96
    source = _pseudo_random_series(size)
    target = [0.0, 0.0]
    for index in range(2, size):
        target.append(1.2 * source[index - 2])
    graph = build_cross_pair_association_graph_v3(
        {
            "CAD/JPY OTC": _cross_pair_rows("CAD/JPY OTC", source),
            "GBP/USD OTC": _cross_pair_rows("GBP/USD OTC", target),
        },
        max_lag=3,
        minimum_support=64,
        max_null_shifts=63,
        max_pairs=2,
        max_edges=2,
    )

    assert graph["status"] == "SUPPORTED"
    assert len(graph["nodes"]) == 2
    assert 1 <= len(graph["edges"]) <= 2
    assert graph["edges_truncated_by_bound"] is False
    assert graph["contract"]["global_network_causation_claimed"] is False
    assert graph["execution_authority"] is False


def _proof_candles() -> list[dict[str, Any]]:
    return [
        {
            "candle_id": f"CADJPY-M5-{index}",
            "order_index": 100 + index,
            "closed_timestamp": 1_700_000_000 + index * 300,
            "is_closed": True,
            "coordinate_space": "NORMALIZED_MEDIAN_RANGE",
            "order_domain": "SOURCE_CANDLE_CLOSE_ORDER",
        }
        for index in range(6)
    ]


def _issue_proof() -> dict[str, Any]:
    return issue_study_claim_certificate_v3(
        claim_type="EXPECTED_REST_DURATION",
        claim_payload={
            "partition_id": "PGREG-0002-ABC",
            "median_closed_candles": 5,
            "support": 91,
        },
        closed_candles=_proof_candles(),
        coordinate_space="NORMALIZED_MEDIAN_RANGE",
        order_domain="SOURCE_CANDLE_CLOSE_ORDER",
        inputs={
            "durations": [3, 4, 5, 5, 8],
            "partition_id": "PGREG-0002-ABC",
        },
        derivation={
            "algorithm_id": "KAPLAN_MEIER_RESTRICTED_MEDIAN",
            "algorithm_version": "3.0.0",
            "parameters": {"maximum_horizon": 64},
        },
    )


def test_study_claim_proof_binds_all_material_and_verifies_deterministically() -> None:
    certificate = _issue_proof()
    assert certificate == _issue_proof()
    verification = verify_study_claim_certificate_v3(
        certificate,
        claim_payload={
            "partition_id": "PGREG-0002-ABC",
            "median_closed_candles": 5,
            "support": 91,
        },
        closed_candles=_proof_candles(),
        inputs={
            "durations": [3, 4, 5, 5, 8],
            "partition_id": "PGREG-0002-ABC",
        },
        derivation={
            "algorithm_id": "KAPLAN_MEIER_RESTRICTED_MEDIAN",
            "algorithm_version": "3.0.0",
            "parameters": {"maximum_horizon": 64},
        },
    )

    assert verification["status"] == "VALID"
    assert verification["valid"] is True
    assert verification["verified_bindings"]["binds_inputs"] is True
    assert certificate["evidence"]["closed_candle_ids"] == [
        f"CADJPY-M5-{index}" for index in range(6)
    ]
    assert certificate["execution_authority"] is False
    assert certificate["causal"] is False


def test_study_claim_proof_rejects_tampering_open_candles_and_authority() -> None:
    certificate = _issue_proof()
    tampered = deepcopy(certificate)
    tampered["interpretation"] = "This now proves a trade."
    verification = verify_study_claim_certificate_v3(
        tampered,
        claim_payload={
            "partition_id": "PGREG-0002-ABC",
            "median_closed_candles": 5,
            "support": 91,
        },
        closed_candles=_proof_candles(),
        inputs={
            "durations": [3, 4, 5, 5, 8],
            "partition_id": "PGREG-0002-ABC",
        },
        derivation={
            "algorithm_id": "KAPLAN_MEIER_RESTRICTED_MEDIAN",
            "algorithm_version": "3.0.0",
            "parameters": {"maximum_horizon": 64},
        },
    )
    assert verification["status"] == "INVALID"
    assert "certificate envelope digest mismatch" in verification["reasons"]

    open_candles = _proof_candles()
    open_candles[-1]["is_closed"] = False
    with pytest.raises(StudyClaimProofValidationError, match="is not closed"):
        issue_study_claim_certificate_v3(
            claim_type="MOTIF_MATCH",
            claim_payload={"similarity": 0.8},
            closed_candles=open_candles,
            coordinate_space="NORMALIZED_MEDIAN_RANGE",
            order_domain="SOURCE_CANDLE_CLOSE_ORDER",
            inputs={"motif": [1, 2, 3]},
            derivation={"algorithm_id": "MOTIF", "algorithm_version": "3"},
        )


def test_final_public_study_projection_has_stable_non_circular_hash() -> None:
    study: dict[str, Any] = {
        "schema_version": "PG_EXAMPLE_STUDY_V3",
        "status": "STUDIED",
        "summary": {"support": 91, "median": 5},
        "study_only": True,
        "execution_authority": False,
    }
    expected = canonical_public_study_hash_v3(study)
    study["claim_proof_id"] = "PGPROOF-EXAMPLE"
    study["claim_bound_study_hash"] = expected
    study["claim_bound_projection"] = PUBLIC_STUDY_CANONICAL_PROJECTION_VERSION

    assert canonical_public_study_hash_v3(study) == expected
    changed = deepcopy(study)
    changed["summary"]["median"] = 6
    assert canonical_public_study_hash_v3(changed) != expected
    with pytest.raises(StudyClaimProofValidationError, match="trade authority"):
        issue_study_claim_certificate_v3(
            claim_type="MOTIF_MATCH",
            claim_payload={"grants_entry_permission": True},
            closed_candles=_proof_candles(),
            coordinate_space="NORMALIZED_MEDIAN_RANGE",
            order_domain="SOURCE_CANDLE_CLOSE_ORDER",
            inputs={"motif": [1, 2, 3]},
            derivation={"algorithm_id": "MOTIF", "algorithm_version": "3"},
        )
