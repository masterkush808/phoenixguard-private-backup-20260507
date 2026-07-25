from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
from typing import Any

import pytest

from phoenixguard.study._persistence_v3 import StudyPersistenceError
from phoenixguard.study.behavioral_sequence_v3 import measure_market_behavior_v3
from phoenixguard.study.candle_intelligence_v3 import analyze_candle_sequence_v3
from phoenixguard.study.object_relationship_graph_v3 import (
    build_object_relationship_graph_v3,
)
from phoenixguard.study.pair_dna_v3 import (
    DEFAULT_MAX_RETRACEMENT_BUCKETS,
    PAIR_DNA_DEDUPE_CAPACITY,
    PAIR_DNA_DEDUPE_FALSE_POSITIVE_CEILING,
    PAIR_DNA_DEDUPE_MAX_SEGMENTS,
    PAIR_DNA_DEDUPE_SEGMENT_BITS,
    PAIR_DNA_DEDUPE_SEGMENT_CAPACITY,
    PAIR_DNA_DEDUPE_SEGMENT_HASHES,
    PAIR_DNA_SCHEMA_VERSION,
    RETRACEMENT_CONFLUENCE_STUDY_SCHEMA_VERSION,
    PairDNAStoreV3,
    PairDNAValidationError,
)


def _raw_series(
    closes: list[float],
    *,
    include_ids: bool = True,
    include_timestamps: bool = True,
) -> list[dict[str, Any]]:
    candles: list[dict[str, Any]] = []
    previous = 100.0
    for index, close in enumerate(closes):
        row: dict[str, Any] = {
            "open": previous,
            "high": max(previous, close) + 0.5,
            "low": min(previous, close) - 0.5,
            "close": close,
            "is_closed": True,
        }
        if include_ids:
            row["candle_id"] = f"c{index}"
        if include_timestamps:
            row["timestamp"] = 1_700_000_000 + index * 300
        candles.append(row)
        previous = close
    return candles


def _studies_from_rows(
    candles: list[dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any]]:
    candle_study = analyze_candle_sequence_v3(candles, regime="TRENDING_UP")
    behavior_study = measure_market_behavior_v3(candle_study, inner_window=2)
    return candle_study, behavior_study


def _studies() -> tuple[dict[str, Any], dict[str, Any]]:
    return _studies_from_rows(
        _raw_series([101.0, 103.0, 105.0, 105.05, 105.0, 103.0, 101.0])
    )


def _record(
    store: PairDNAStoreV3,
    sequence_id: str,
    *,
    symbol: str = "CAD/JPY OTC",
    outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    candle_study, behavior_study = _studies()
    return store.record_study(
        symbol=symbol,
        timeframe="M5",
        candle_study=candle_study,
        behavior_study=behavior_study,
        sequence_id=sequence_id,
        objects=[{"object_type": "PRICE_IMBALANCE"}, {"object_type": "REACTION_ZONE"}],
        outcome=outcome,
    )


def _retracement_study(
    *observations: dict[str, Any],
    status: str = "STUDIED",
) -> dict[str, Any]:
    return {
        "schema_version": RETRACEMENT_CONFLUENCE_STUDY_SCHEMA_VERSION,
        "status": status,
        "study_only": True,
        "observation_only": True,
        "execution_authority": False,
        "observations": list(observations),
    }


def _retracement_observation(
    study_id: str,
    *,
    level_id: str = "OTE_70_5",
    level_ratio: float = 0.705,
    regime: str = "TRENDING_UP",
    side: str = "BULLISH",
    coordinate_space: str = "PRICE",
    object_type: str = "ORDER_BLOCK",
    relation: str = "RETRACEMENT_LEVEL_OVERLAPS_OBJECT",
    status: str = "COMPLETED",
    identity_stable: bool = True,
    observational_confluence: bool = True,
) -> dict[str, Any]:
    custom = level_id == "CUSTOM_71_8"
    return {
        "study_id": study_id,
        "status": status,
        "identity_stable": identity_stable,
        "observational_confluence": observational_confluence,
        "causal": False,
        "regime": regime,
        "side": side,
        "coordinate_space": coordinate_space,
        "level_id": level_id,
        "level_ratio": level_ratio,
        "classification": (
            "USER_DEFINED_EXPERIMENTAL_NONSTANDARD"
            if custom
            else "ICT_STYLE_OTE_REFERENCE"
        ),
        "experimental": custom,
        "user_defined": custom,
        "standard_fibonacci": False,
        "object_type": object_type,
        "relation": relation,
    }


def _shifted_studies(offset: int) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = _raw_series([101.0, 103.0, 105.0, 105.05, 105.0, 103.0, 101.0])
    for row in rows:
        row["timestamp"] = int(row["timestamp"]) + offset
    return _studies_from_rows(rows)


def test_pair_dna_persists_normalized_behavior_and_actual_market_outcome(tmp_path: Path) -> None:
    path = tmp_path / "pair_dna.json"
    store = PairDNAStoreV3(path, max_pairs=4, recent_sequence_limit=8)

    recorded = _record(
        store,
        "sequence-1",
        outcome={"direction": "DOWN", "realized_return": 2.5, "success": True},
    )
    profile_result = PairDNAStoreV3(path, max_pairs=4, recent_sequence_limit=8).get_profile(
        "CAD/JPY OTC",
        "M5",
    )

    assert recorded["status"] == "RECORDED"
    assert profile_result["schema_version"] == PAIR_DNA_SCHEMA_VERSION
    profile = profile_result["profile"]
    assert profile["observation_count"] == 1
    assert profile["coordinate_space_counts"] == {"PRICE": 7}
    # The first segment is left-censored and the last remains open.  Only the
    # bounded REST segment has both stable boundaries in this first window.
    assert set(profile["behavior"]["segment_averages"]) == {"PRICE|REST"}
    assert all(
        "absolute_change_in_median_ranges" in row
        for row in profile["behavior"]["segment_averages"].values()
    )
    associations = profile["marginal_and_pairwise_outcome_associations"]
    object_association = next(row for row in associations if row["feature"] == "OBJECT:PRICE_IMBALANCE")
    assert object_association["direction_probabilities"]["DOWN"] > object_association["direction_probabilities"]["UP"]
    assert profile["outcome_association_contract"]["causal"] is False
    assert profile["behavior"]["transition_counts"] == {
        "PRICE|REST->DOWN_SWING": 1
    }
    assert any(row["feature"].startswith("PAIR:CANDLE_TYPE=") for row in associations)
    assert not list(tmp_path.glob("*.tmp"))


def test_pair_dna_does_not_infer_market_direction_from_positive_pnl(tmp_path: Path) -> None:
    store = PairDNAStoreV3(tmp_path / "pair_dna.json")

    _record(store, "profitable-sell-without-direction", outcome={"realized_return": 3.0, "success": True})
    profile = store.get_profile("CAD/JPY OTC", "M5")["profile"]

    assert profile["marginal_and_pairwise_outcome_associations"] == []


def test_pair_dna_bloom_prevents_reingestion_after_recent_ring_rotates(tmp_path: Path) -> None:
    store = PairDNAStoreV3(
        tmp_path / "pair_dna.json",
        max_pairs=2,
        recent_sequence_limit=2,
    )
    for sequence_id in ("sequence-1", "sequence-2", "sequence-3"):
        assert _record(store, sequence_id)["status"] == "RECORDED"

    repeated = _record(store, "sequence-1")
    profile = store.get_profile("CAD/JPY OTC", "M5")["profile"]

    assert repeated["status"] == "POSSIBLE_DUPLICATE_IGNORED"
    assert profile["observation_count"] == 3
    assert profile["sequence_dedupe_bloom"]["insertions"] == 3
    assert profile["sequence_dedupe_bloom"]["algorithm"] == "SHA256_SEGMENTED_BLOOM_V2"
    assert profile["seen_sequence_ids"] == ["sequence-2", "sequence-3"]


def test_pair_dna_migrates_safe_legacy_bloom_without_forgetting_identity(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pair_dna.json"
    store = PairDNAStoreV3(path)
    _record(store, "legacy-sequence")
    payload = json.loads(path.read_text(encoding="utf-8"))
    profile = next(iter(payload["profiles"].values()))
    digest = hashlib.sha256(b"legacy-sequence").digest()
    positions = (
        int.from_bytes(digest[index * 4 : index * 4 + 4], "big") % 16_384
        for index in range(5)
    )
    bitmap = 0
    for position in positions:
        bitmap |= 1 << position
    profile["sequence_dedupe_bloom"] = {
        "algorithm": "SHA256_BLOOM_V1",
        "bits": 16_384,
        "hashes": 5,
        "insertions": 1,
        "bitmap_hex": format(bitmap, "04096x"),
    }
    path.write_text(json.dumps(payload), encoding="utf-8")

    assert _record(PairDNAStoreV3(path), "legacy-sequence")["status"] == (
        "DUPLICATE_IGNORED"
    )
    assert _record(PairDNAStoreV3(path), "after-migration")["status"] == "RECORDED"
    migrated = PairDNAStoreV3(path).get_profile("CAD/JPY OTC", "M5")["profile"]
    assert migrated["sequence_dedupe_bloom"]["algorithm"] == (
        "SHA256_SEGMENTED_BLOOM_V2"
    )
    assert migrated["sequence_dedupe_bloom"]["insertions"] == 2


def test_pair_dna_legacy_profile_establishes_boundary_baseline_without_replay(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pair_dna.json"
    store = PairDNAStoreV3(path)
    _record(store, "before-ledger")
    payload = json.loads(path.read_text(encoding="utf-8"))
    stored_profile = next(iter(payload["profiles"].values()))
    before_candles = stored_profile["candle_count"]
    before_segments = dict(stored_profile["behavior"]["segment_counts"])
    stored_profile.pop("identity_ledger")
    path.write_text(json.dumps(payload), encoding="utf-8")

    _record(PairDNAStoreV3(path), "migration-baseline")
    migrated = PairDNAStoreV3(path).get_profile("CAD/JPY OTC", "M5")["profile"]

    assert migrated["candle_count"] == before_candles
    assert migrated["behavior"]["segment_counts"] == before_segments
    assert migrated["identity_ledger"]["baseline_initialized"] is True
    assert migrated["identity_ledger"]["completed_boundary_high_watermark"][
        "identity"
    ].startswith("MIGRATION_BASELINE:")


def test_pair_dna_overlapping_windows_count_each_closed_candle_once(tmp_path: Path) -> None:
    path = tmp_path / "pair_dna.json"
    store = PairDNAStoreV3(path, recent_sequence_limit=8)
    full = _raw_series([101.0, 103.0, 105.0, 105.05, 105.0, 103.0, 101.0, 99.0])
    first_candles, first_behavior = _studies_from_rows(full[:7])
    second_candles, second_behavior = _studies_from_rows(full[2:])

    store.record_study(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        candle_study=first_candles,
        behavior_study=first_behavior,
        sequence_id="overlap-1",
    )
    store.record_study(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        candle_study=second_candles,
        behavior_study=second_behavior,
        sequence_id="overlap-2",
    )
    # A distinct wrapper identity around the same market window must also not
    # inflate candle or observation aggregates.
    store.record_study(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        candle_study=second_candles,
        behavior_study=second_behavior,
        sequence_id="overlap-3",
    )

    profile = store.get_profile("CAD/JPY OTC", "M5")["profile"]
    assert profile["candle_count"] == 8
    assert profile["coordinate_space_counts"] == {"PRICE": 8}
    assert profile["observation_count"] == 3
    assert profile["identity_ledger"]["accepted_candles"] == 8
    assert profile["identity_ledger"]["skipped_overlapping_candles"] == 11


def test_pair_dna_counts_completed_segment_boundary_and_transition_once(tmp_path: Path) -> None:
    store = PairDNAStoreV3(tmp_path / "pair_dna.json", recent_sequence_limit=16)
    full = _raw_series(
        [101.0, 103.0, 105.0, 105.05, 105.0, 103.0, 101.0, 99.0, 101.0, 103.0]
    )
    # The second rolling window begins exactly at the previously open DOWN
    # segment.  Its persisted start boundary proves that this otherwise
    # left-censored first row is complete once UP begins.
    windows = (full[:7], full[5:9], full[5:])
    for index, window in enumerate(windows, start=1):
        candle_study, behavior_study = _studies_from_rows(window)
        store.record_study(
            symbol="CAD/JPY OTC",
            timeframe="M5",
            candle_study=candle_study,
            behavior_study=behavior_study,
            sequence_id=f"boundary-{index}",
        )

    # Rewrap the final window under another sequence id.  All closed boundaries
    # are behind the high-water mark and therefore remain unchanged.
    final_candles, final_behavior = _studies_from_rows(full[5:])
    store.record_study(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        candle_study=final_candles,
        behavior_study=final_behavior,
        sequence_id="boundary-rewrapped",
    )
    profile = store.get_profile("CAD/JPY OTC", "M5")["profile"]

    assert profile["behavior"]["segment_counts"] == {
        "PRICE|DOWN_SWING": 1,
        "PRICE|REST": 1,
    }
    assert profile["behavior"]["transition_counts"] == {
        "PRICE|DOWN_SWING->UP_SWING": 1,
        "PRICE|REST->DOWN_SWING": 1,
    }
    assert profile["identity_ledger"]["accepted_completed_segments"] == 2


def test_pair_dna_unstable_fallback_identities_do_not_touch_lifelong_aggregates(
    tmp_path: Path,
) -> None:
    candle_study, behavior_study = _studies_from_rows(
        _raw_series(
            [101.0, 103.0, 105.0, 104.0],
            include_ids=False,
            include_timestamps=False,
        )
    )
    store = PairDNAStoreV3(tmp_path / "pair_dna.json")
    store.record_study(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        candle_study=candle_study,
        behavior_study=behavior_study,
        sequence_id="unstable-window",
        objects=[{"object_type": "PRICE_IMBALANCE"}],
        outcome={"direction": "UP", "success": True},
    )
    profile = store.get_profile("CAD/JPY OTC", "M5")["profile"]

    # The envelope is auditable, but none of its unstable candle/object/outcome
    # evidence is allowed into the lifelong behavioral aggregates.
    assert profile["observation_count"] == 1
    assert profile["candle_count"] == 0
    assert profile["coordinate_space_counts"] == {}
    assert profile["object_type_counts"] == {}
    assert profile["marginal_and_pairwise_outcome_associations"] == []
    assert profile["identity_ledger"]["skipped_unstable_candles"] == 4


@pytest.mark.parametrize("timestamp_first", (True, False))
def test_pair_dna_never_mixes_timestamp_and_tracker_event_order_domains(
    tmp_path: Path,
    timestamp_first: bool,
) -> None:
    timestamp_candles, timestamp_behavior = _studies_from_rows(
        _raw_series([101.0, 102.0, 103.0])
    )
    event_candles, event_behavior = _studies_from_rows(
        _raw_series(
            [101.0, 102.0, 103.0],
            include_ids=False,
            include_timestamps=False,
        )
    )
    event_candles["candles"][-1].update(
        {
            "identity_stable": True,
            "stable_candle_identity": "EXPLICIT:resolver-close-0",
            "identity_proof_source": "PG_CLOSED_CANDLE_IDENTITY_STATE_V3",
            "closed_candle_sequence": 0,
        }
    )
    store = PairDNAStoreV3(tmp_path / "pair_dna.json")
    studies = (
        (timestamp_candles, timestamp_behavior, "timestamp-study"),
        (event_candles, event_behavior, "event-study"),
    )
    if not timestamp_first:
        studies = tuple(reversed(studies))

    first_candles, first_behavior, first_id = studies[0]
    second_candles, second_behavior, second_id = studies[1]
    store.record_study(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        candle_study=first_candles,
        behavior_study=first_behavior,
        sequence_id=first_id,
    )
    before = store.get_profile("CAD/JPY OTC", "M5")["profile"]
    store.record_study(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        candle_study=second_candles,
        behavior_study=second_behavior,
        sequence_id=second_id,
    )
    after = store.get_profile("CAD/JPY OTC", "M5")["profile"]

    expected_domain = (
        "CLOSED_TIMESTAMP_V1"
        if timestamp_first
        else "TRACKER_EVENT_SEQUENCE_V3"
    )
    assert after["identity_ledger"]["candle_order_domain"] == expected_domain
    assert after["candle_count"] == before["candle_count"]
    assert after["identity_ledger"]["skipped_order_domain_conflicts"] >= 1


def test_pair_dna_segmented_dedupe_capacity_and_probability_invariant() -> None:
    per_segment = (
        1.0
        - math.exp(
            -PAIR_DNA_DEDUPE_SEGMENT_HASHES
            * PAIR_DNA_DEDUPE_SEGMENT_CAPACITY
            / PAIR_DNA_DEDUPE_SEGMENT_BITS
        )
    ) ** PAIR_DNA_DEDUPE_SEGMENT_HASHES
    union_ceiling = 1.0 - (1.0 - per_segment) ** PAIR_DNA_DEDUPE_MAX_SEGMENTS

    assert PAIR_DNA_DEDUPE_CAPACITY >= 10_000
    assert PAIR_DNA_DEDUPE_CAPACITY == (
        PAIR_DNA_DEDUPE_SEGMENT_CAPACITY * PAIR_DNA_DEDUPE_MAX_SEGMENTS
    )
    assert union_ceiling <= PAIR_DNA_DEDUPE_FALSE_POSITIVE_CEILING


def test_pair_dna_retracement_confluence_is_partitioned_empirical_evidence(
    tmp_path: Path,
) -> None:
    store = PairDNAStoreV3(tmp_path / "pair_dna.json")
    candle_study, behavior_study = _studies()
    retracement_study = _retracement_study(
        _retracement_observation("ote-ob-1"),
        _retracement_observation(
            "custom-ob-1",
            level_id="CUSTOM_71_8",
            level_ratio=0.718,
        ),
        _retracement_observation(
            "custom-ob-2",
            level_id="CUSTOM_71_8",
            level_ratio=0.718,
            relation="RETRACEMENT_LEVEL_NEAR_TOUCHES_OBJECT",
        ),
        _retracement_observation(
            "custom-fvg-down-1",
            level_id="CUSTOM_71_8",
            level_ratio=0.718,
            regime="TRENDING_DOWN",
            side="BEARISH",
            coordinate_space="NORMALIZED_PRICE_PROXY",
            object_type="FVG_IMBALANCE",
        ),
    )

    recorded = store.record_study(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        candle_study=candle_study,
        behavior_study=behavior_study,
        sequence_id="retracement-envelope-1",
        outcome={"direction": "DOWN", "success": True, "realized_return": -0.75},
        retracement_study=retracement_study,
    )

    retracement = recorded["profile"]["retracement_confluence"]
    assert retracement["completed_study_count"] == 4
    assert len(retracement["buckets"]) == 3
    assert len(retracement["empirical_partitions"]) == 3
    assert retracement["interpretation_contract"] == {
        "analysis_kind": "PARTITIONED_EMPIRICAL_FREQUENCY",
        "causal": False,
        "predictive_probability": False,
        "entry_signal": False,
        "grants_entry_permission": False,
        "grants_execution_permission": False,
        "requires_support_count_with_every_rate": True,
        "overall_directional_study_success_used": False,
        "returns_are_side_adjusted": True,
        "custom_71_8_is_experimental": True,
        "custom_71_8_is_standard_fibonacci": False,
        "note": (
            "Rates summarize completed historical observations only; they are "
            "not forecasts, trade instructions, or proof of causation."
        ),
    }
    custom_partition = next(
        row
        for row in retracement["empirical_partitions"]
        if row["partition"]["level_id"] == "CUSTOM_71_8"
        and row["partition"]["object_type"] == "ORDER_BLOCK"
    )
    assert custom_partition["partition"] == {
        "symbol": "CAD/JPY OTC",
        "timeframe": "M5",
        "regime": "TRENDING_UP",
        "regime_basis": "CURRENT_STUDY_FRAME_AT_CONFLUENCE_OBSERVATION",
        "side": "BULLISH",
        "coordinate_space": "PRICE",
        "level_id": "CUSTOM_71_8",
        "level_ratio": 0.718,
        "classification": "USER_DEFINED_EXPERIMENTAL_NONSTANDARD",
        "experimental": True,
        "user_defined": True,
        "standard_fibonacci": False,
        "object_type": "ORDER_BLOCK",
    }
    assert custom_partition["support"] == {
        "completed_studies": 2,
        "directional_alignment_label_count": 2,
        "side_adjusted_return_count": 2,
    }
    assert custom_partition["counts"]["outcome_directions"] == {"DOWN": 2}
    assert custom_partition["counts"]["directional_alignment_count"] == 0
    assert custom_partition["empirical_rates"] == {
        "direction_frequency": {"DOWN": 1.0},
        "directional_alignment_rate": 0.0,
        "average_side_adjusted_return": -0.75,
    }
    custom_bucket = retracement["buckets"][custom_partition["bucket_id"]]
    assert custom_bucket["directional_alignment_label_count"] == 2
    assert custom_bucket["directional_alignment_count"] == 0
    assert custom_bucket["side_adjusted_return_count"] == 2
    assert custom_bucket["side_adjusted_return_sum"] == -1.5
    assert not {
        "success_label_count",
        "success_count",
        "realized_return_count",
        "realized_return_sum",
    } & set(custom_bucket)
    assert retracement["level_catalog"]["OTE_70_5"] == {
        "level_ratio": 0.705,
        "classification": "ICT_STYLE_OTE_REFERENCE",
        "experimental": False,
        "user_defined": False,
        "standard_fibonacci": False,
    }
    assert retracement["level_catalog"]["CUSTOM_71_8"] == {
        "level_ratio": 0.718,
        "classification": "USER_DEFINED_EXPERIMENTAL_NONSTANDARD",
        "experimental": True,
        "user_defined": True,
        "standard_fibonacci": False,
    }
    assert retracement["level_support"] == [
        {
            "level_id": "OTE_70_5",
            "completed_study_count": 1,
            "level_ratio": 0.705,
            "classification": "ICT_STYLE_OTE_REFERENCE",
            "experimental": False,
            "user_defined": False,
            "standard_fibonacci": False,
        },
        {
            "level_id": "CUSTOM_71_8",
            "completed_study_count": 3,
            "level_ratio": 0.718,
            "classification": "USER_DEFINED_EXPERIMENTAL_NONSTANDARD",
            "experimental": True,
            "user_defined": True,
            "standard_fibonacci": False,
        },
    ]
    bearish_partition = next(
        row
        for row in retracement["empirical_partitions"]
        if row["partition"]["side"] == "BEARISH"
    )
    assert bearish_partition["counts"]["directional_alignment_count"] == 1
    assert bearish_partition["empirical_rates"]["directional_alignment_rate"] == 1.0
    assert bearish_partition["empirical_rates"]["average_side_adjusted_return"] == 0.75


def test_pair_dna_consumes_the_graph_retracement_contract_without_translation(
    tmp_path: Path,
) -> None:
    geometries = (
        (102.0, 103.0, 101.0, 102.5),
        (101.5, 102.0, 100.0, 101.0),
        (102.5, 106.0, 102.0, 105.0),
        (105.5, 110.0, 105.0, 109.0),
        (108.5, 109.0, 104.0, 105.0),
    )
    graph_candles = [
        {
            "candle_id": f"graph-bar-{index}",
            "timestamp": 1_700_100_000 + index * 300,
            "closed": True,
            "identity_stable": True,
            "coordinate_space": "PRICE",
            "regime": "TRENDING_UP",
            "sequence_position": {
                "index": index,
                "is_latest": index == len(geometries) - 1,
            },
            "ohlc": {
                "open": open_value,
                "high": high,
                "low": low,
                "close": close,
            },
        }
        for index, (open_value, high, low, close) in enumerate(geometries)
    ]
    graph = build_object_relationship_graph_v3(
        graph_candles,
        [
            {
                "object_type": "order block",
                "object_id": "graph-ob-1",
                "identity_stable": True,
                "identity_scope": "EXPLICIT_TEST_PROOF",
                "confidence": 0.9,
                "value_bounds": [102.8, 103.0],
                "value_coordinate_space": "PRICE",
                "value_axis_source": "TEST_EXPLICIT",
            }
        ],
    )
    graph_study = graph["retracement_study"]
    assert graph_study["status"] == "STUDIED"
    assert graph_study["observations"]
    candle_study, behavior_study = _studies()

    recorded = PairDNAStoreV3(tmp_path / "pair_dna.json").record_study(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        candle_study=candle_study,
        behavior_study=behavior_study,
        sequence_id="real-graph-contract-envelope",
        outcome={"direction": "UP", "success": True},
        retracement_study=graph_study,
    )

    retracement = recorded["profile"]["retracement_confluence"]
    assert retracement["completed_study_count"] == len(graph_study["observations"])
    assert {
        row["partition"]["level_id"] for row in retracement["empirical_partitions"]
    } == {"OTE_70_5", "CUSTOM_71_8"}


def test_pair_dna_retracement_completed_study_dedupe_survives_new_envelopes(
    tmp_path: Path,
) -> None:
    store = PairDNAStoreV3(
        tmp_path / "pair_dna.json",
        recent_sequence_limit=2,
    )
    retracement = _retracement_study(_retracement_observation("durable-study-1"))
    first_candles, first_behavior = _shifted_studies(0)
    second_candles, second_behavior = _shifted_studies(10_000)

    for sequence_id, candles, behavior in (
        ("envelope-1", first_candles, first_behavior),
        ("envelope-2", second_candles, second_behavior),
    ):
        store.record_study(
            symbol="CAD/JPY OTC",
            timeframe="M5",
            candle_study=candles,
            behavior_study=behavior,
            sequence_id=sequence_id,
            outcome={"direction": "UP", "success": True},
            retracement_study=retracement,
        )

    profile = store.get_profile("CAD/JPY OTC", "M5")["profile"]
    aggregate = profile["retracement_confluence"]
    assert profile["observation_count"] == 2
    assert aggregate["completed_study_count"] == 1
    assert aggregate["study_dedupe_bloom"]["insertions"] == 1
    assert aggregate["recent_study_ids"] == ["durable-study-1"]
    assert aggregate["empirical_partitions"][0]["support"]["completed_studies"] == 1


def test_pair_dna_retracement_ignores_unmatured_or_nonconfluent_rows(
    tmp_path: Path,
) -> None:
    store = PairDNAStoreV3(tmp_path / "pair_dna.json")
    candle_study, behavior_study = _studies()
    store.record_study(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        candle_study=candle_study,
        behavior_study=behavior_study,
        sequence_id="unmatured-envelope",
        outcome={},
        retracement_study=_retracement_study(
            _retracement_observation("pending-1", status="PENDING"),
            _retracement_observation(
                "not-confluence-1", observational_confluence=False
            ),
        ),
    )

    retracement = store.get_profile("CAD/JPY OTC", "M5")["profile"][
        "retracement_confluence"
    ]
    assert retracement["completed_study_count"] == 0
    assert retracement["buckets"] == {}
    assert retracement["empirical_partitions"] == []


def test_pair_dna_retracement_does_not_infer_direction_from_positive_return(
    tmp_path: Path,
) -> None:
    store = PairDNAStoreV3(tmp_path / "pair_dna.json")
    candle_study, behavior_study = _studies()
    store.record_study(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        candle_study=candle_study,
        behavior_study=behavior_study,
        sequence_id="return-only-envelope",
        outcome={"realized_return": 1.25, "success": True},
        retracement_study=_retracement_study(
            _retracement_observation("return-only-study")
        ),
    )

    partition = store.get_profile("CAD/JPY OTC", "M5")["profile"][
        "retracement_confluence"
    ]["empirical_partitions"][0]
    assert partition["support"] == {
        "completed_studies": 1,
        "directional_alignment_label_count": 0,
        "side_adjusted_return_count": 1,
    }
    assert partition["counts"]["outcome_directions"] == {}
    assert partition["empirical_rates"]["direction_frequency"] == {}
    assert partition["empirical_rates"]["directional_alignment_rate"] is None
    assert partition["empirical_rates"]["average_side_adjusted_return"] == 1.25
    assert "success" not in partition["counts"]


@pytest.mark.parametrize(
    ("mutations", "match"),
    (
        ({"level_ratio": 0.718}, "level_ratio does not match"),
        ({"standard_fibonacci": True}, "standard_fibonacci contradicts"),
        ({"identity_stable": False}, "identity_stable must be true"),
        ({"classification": "STANDARD_FIBONACCI"}, "classification contradicts"),
        ({"symbol": "GBP/USD OTC"}, "symbol does not match"),
        ({"causal": True}, "causal must be explicitly false"),
        ({"execution_authority": True}, "cannot carry trade authority"),
    ),
)
def test_pair_dna_retracement_rejects_contradictory_or_unstable_evidence(
    tmp_path: Path,
    mutations: dict[str, Any],
    match: str,
) -> None:
    store = PairDNAStoreV3(tmp_path / "pair_dna.json")
    candle_study, behavior_study = _studies()
    row = _retracement_observation("invalid-study")
    row.update(mutations)

    with pytest.raises(PairDNAValidationError, match=match):
        store.record_study(
            symbol="CAD/JPY OTC",
            timeframe="M5",
            candle_study=candle_study,
            behavior_study=behavior_study,
            sequence_id="invalid-envelope",
            retracement_study=_retracement_study(row),
        )


@pytest.mark.parametrize("coordinate_space", ("NORMALIZED_FRAME", None))
def test_pair_dna_retracement_rejects_unsupported_or_missing_value_axis(
    tmp_path: Path,
    coordinate_space: str | None,
) -> None:
    store = PairDNAStoreV3(tmp_path / "pair_dna.json")
    candle_study, behavior_study = _studies()
    row = _retracement_observation("invalid-coordinate-study")
    if coordinate_space is None:
        row.pop("coordinate_space")
    else:
        row["coordinate_space"] = coordinate_space

    with pytest.raises(PairDNAValidationError, match="coordinate_space"):
        store.record_study(
            symbol="CAD/JPY OTC",
            timeframe="M5",
            candle_study=candle_study,
            behavior_study=behavior_study,
            sequence_id="invalid-coordinate-envelope",
            retracement_study=_retracement_study(row),
        )


def test_pair_dna_retracement_bucket_capacity_fails_without_partial_write(
    tmp_path: Path,
) -> None:
    path = tmp_path / "pair_dna.json"
    store = PairDNAStoreV3(path, max_retracement_buckets=1)
    first_candles, first_behavior = _shifted_studies(0)
    store.record_study(
        symbol="CAD/JPY OTC",
        timeframe="M5",
        candle_study=first_candles,
        behavior_study=first_behavior,
        sequence_id="capacity-envelope-1",
        outcome={"direction": "UP"},
        retracement_study=_retracement_study(
            _retracement_observation("capacity-study-1")
        ),
    )
    before = path.read_bytes()
    second_candles, second_behavior = _shifted_studies(10_000)

    with pytest.raises(PairDNAValidationError, match="bucket capacity reached"):
        store.record_study(
            symbol="CAD/JPY OTC",
            timeframe="M5",
            candle_study=second_candles,
            behavior_study=second_behavior,
            sequence_id="capacity-envelope-2",
            outcome={"direction": "DOWN"},
            retracement_study=_retracement_study(
                _retracement_observation(
                    "capacity-study-2",
                    level_id="CUSTOM_71_8",
                    level_ratio=0.718,
                )
            ),
        )

    assert path.read_bytes() == before
    assert DEFAULT_MAX_RETRACEMENT_BUCKETS <= 4096


def test_pair_dna_retracement_defaults_migrate_old_v3_profiles(tmp_path: Path) -> None:
    path = tmp_path / "pair_dna.json"
    store = PairDNAStoreV3(path)
    _record(store, "legacy-before-retracement")
    payload = json.loads(path.read_text(encoding="utf-8"))
    next(iter(payload["profiles"].values())).pop("retracement_confluence")
    path.write_text(json.dumps(payload), encoding="utf-8")

    migrated = PairDNAStoreV3(path).get_profile("CAD/JPY OTC", "M5")["profile"]
    retracement = migrated["retracement_confluence"]
    assert retracement["schema_version"] == "PG_PAIR_DNA_RETRACEMENT_AGGREGATES_V3"
    assert retracement["completed_study_count"] == 0
    assert retracement["execution_authority"] is False
    assert retracement["empirical_partitions"] == []


def test_pair_dna_never_evicts_lifelong_pair_profile_at_capacity(tmp_path: Path) -> None:
    store = PairDNAStoreV3(tmp_path / "pair_dna.json", max_pairs=1)
    _record(store, "cadjpy-1")

    with pytest.raises(PairDNAValidationError, match="capacity reached"):
        _record(store, "gbpusd-1", symbol="GBP/USD OTC")

    profiles = store.list_profiles()
    assert [(row["symbol"], row["observation_count"]) for row in profiles] == [
        ("CAD/JPY OTC", 1)
    ]


def test_pair_dna_store_fails_closed_on_corruption(tmp_path: Path) -> None:
    path = tmp_path / "pair_dna.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(StudyPersistenceError, match="valid UTF-8 JSON"):
        PairDNAStoreV3(path).list_profiles()

    path.write_text(json.dumps({"schema_version": "WRONG"}), encoding="utf-8")
    with pytest.raises(PairDNAValidationError, match="schema"):
        PairDNAStoreV3(path).list_profiles()

    path.write_text(
        json.dumps(
            {
                "schema_version": PAIR_DNA_SCHEMA_VERSION,
                "study_only": True,
                "execution_authority": False,
                "next_ordinal": 1,
                "profiles": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(PairDNAValidationError, match="profiles must be a mapping"):
        PairDNAStoreV3(path).list_profiles()


def test_pair_dna_threaded_updates_are_locked_and_lossless(tmp_path: Path) -> None:
    store = PairDNAStoreV3(
        tmp_path / "pair_dna.json",
        max_pairs=2,
        recent_sequence_limit=32,
    )

    def record_index(index: int) -> dict[str, Any]:
        return _record(store, f"thread-{index}")

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(record_index, range(8)))

    assert {row["status"] for row in results} == {"RECORDED"}
    profile = store.get_profile("CAD/JPY OTC", "M5")["profile"]
    assert profile["observation_count"] == 8
    assert profile["sequence_dedupe_bloom"]["insertions"] == 8
