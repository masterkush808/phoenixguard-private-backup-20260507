from __future__ import annotations

import copy
import math
from collections.abc import Iterator
from typing import Any

import pytest

from phoenixguard.decision import chronos_scene_forecaster_v3 as provider
from phoenixguard.decision.forecast_path_geometry_v3 import (
    decode_forecast_path_geometry_v3,
)
from phoenixguard.decision.scene_forecast_contributor_v3 import (
    SCENE_FORECAST_CONTRIBUTION_SCHEMA_V3,
    build_scene_forecast_contribution_v3,
    closed_candle_identity_v3,
    reanchor_scene_forecast_geometry_v3,
    resolve_closed_candle_identity_v3,
    synchronize_scene_forecast_geometry_v3,
)


def _candles() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    close_y = 150.0
    for index in range(30):
        direction = "BUY" if index % 4 != 1 else "SELL"
        open_y = close_y
        close_y += -3.0 if direction == "BUY" else 2.0
        rows.append(
            {
                "track_id": index,
                "direction": direction,
                "center_x": 30.0 + index * 10.0,
                "open_y_px": open_y,
                "close_y_px": close_y,
                "wick_top_px": min(open_y, close_y) - 2.0,
                "wick_bottom_px": max(open_y, close_y) + 2.0,
                "price_proxy": 1.0 - close_y / 300.0,
                "bbox": [27.0 + index * 10.0, min(open_y, close_y) - 2.0, 33.0 + index * 10.0, max(open_y, close_y) + 2.0],
                "parse_confidence": 0.94,
                "is_closed": index < 29,
            }
        )
    return rows


def _coverage_candles(count: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    close_y = 170.0
    for index in range(count):
        direction = "BUY" if index % 3 != 1 else "SELL"
        open_y = close_y
        close_y += -2.5 if direction == "BUY" else 1.8
        center_x = 20.0 + index * 8.0
        rows.append(
            {
                "track_id": index,
                "direction": direction,
                "center_x": center_x,
                "open_y_px": open_y,
                "close_y_px": close_y,
                "wick_top_px": min(open_y, close_y) - 1.5,
                "wick_bottom_px": max(open_y, close_y) + 1.5,
                "price_proxy": 1.0 - close_y / 300.0,
                "bbox": [
                    center_x - 3.0,
                    min(open_y, close_y) - 1.5,
                    center_x + 3.0,
                    max(open_y, close_y) + 1.5,
                ],
                "parse_confidence": 0.94,
                "is_closed": index < count - 1,
            }
        )
    return rows


def _ambiguous_fixed_width_boundary_rollover() -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Return a true one-bar shift whose two volatile edge rows rescaled."""

    prior = _candles()
    # Make the two stable predecessors unique so their ordered shift is an
    # identity proof rather than a match against a repeated candle motif.
    prior[-4].update(
        {
            "direction": "SELL",
            "open_y_px": 145.0,
            "close_y_px": 163.0,
            "wick_top_px": 139.0,
            "wick_bottom_px": 171.0,
        }
    )
    prior[-3].update(
        {
            "direction": "BUY",
            "open_y_px": 160.0,
            "close_y_px": 126.0,
            "wick_top_px": 118.0,
            "wick_bottom_px": 168.0,
        }
    )
    prior[-2].update(
        {
            "direction": "BUY",
            "open_y_px": 105.0,
            "close_y_px": 75.0,
            "wick_top_px": 65.0,
            "wick_bottom_px": 112.0,
        }
    )
    prior[-1].update(
        {
            "direction": "SELL",
            "open_y_px": 20.0,
            "close_y_px": 250.0,
            "wick_top_px": 10.0,
            "wick_bottom_px": 260.0,
        }
    )

    current: list[dict[str, Any]] = []
    for index, source in enumerate(prior[1:]):
        row = copy.deepcopy(source)
        row["track_id"] = index
        row["center_x"] = 30.0 + index * 10.0
        row["bbox"][0] = float(row["bbox"][0]) - 10.0
        row["bbox"][2] = float(row["bbox"][2]) - 10.0
        row["is_closed"] = True
        current.append(row)

    # The detector substantially re-estimated both edge candles. This defeats
    # direct former-forming/predecessor matching while older ordered history
    # still proves that the chart advanced exactly one visible slot.
    current[-2].update(
        {
            "direction": "SELL",
            "open_y_px": 205.0,
            "close_y_px": 175.0,
            "wick_top_px": 166.0,
            "wick_bottom_px": 214.0,
        }
    )
    current[-1].update(
        {
            "direction": "BUY",
            "open_y_px": 180.0,
            "close_y_px": 145.0,
            "wick_top_px": 138.0,
            "wick_bottom_px": 188.0,
        }
    )
    current.append(
        {
            "track_id": 29,
            "direction": "SELL",
            "center_x": 320.0,
            "open_y_px": 145.0,
            "close_y_px": 166.0,
            "wick_top_px": 139.0,
            "wick_bottom_px": 172.0,
            "price_proxy": 1.0 - 166.0 / 300.0,
            "bbox": [317.0, 139.0, 323.0, 172.0],
            "parse_confidence": 0.94,
            "is_closed": False,
        }
    )
    return prior, current


@pytest.fixture(autouse=True)
def reset_provider_fixture() -> Iterator[None]:
    provider.reset_provider_state_for_tests()
    yield
    provider.reset_provider_state_for_tests()


def test_scene_contributor_consumes_suite_and_returns_complete_candle_locked_path() -> None:
    candles = _candles()
    result = build_scene_forecast_contribution_v3(
        candles=candles,
        image_size=(800, 300),
        timeframe="M5",
        pair="NZD/USD OTC",
        projection={"direction": "BUY", "confidence": 0.72, "zones": []},
        candle_statistics={"sample_size": 29, "buy_ratio": 0.66, "sell_ratio": 0.34},
        behavior_payload={"current_state": "PULLBACK", "state_confidence": 0.62},
        decision_kernel={
            "dominant_side": "BUY",
            "belief_buy": 0.61,
            "belief_hold": 0.19,
            "belief_sell": 0.20,
        },
        smart_money_context={"dominant_side": "BUY", "confidence": 0.67},
        support_resistance_context={"dominant_side": "BUY"},
        trend_slopes={"global": 0.08, "local": -0.03, "current": 0.02},
        trend_directions={"global": "BUY", "local": "SELL", "current": "BUY"},
        allow_foundation_model=False,
    )

    assert result["schema_version"] == SCENE_FORECAST_CONTRIBUTION_SCHEMA_V3
    assert result["skill"] == "MULTIMODAL_SCENE_FORECAST"
    assert result["forecast_available"] is True
    assert len(result["line_points"]) == 73
    assert len(result["forecast_candles"]) == 72
    assert len(result["forecast_path"]) == 72
    assert len(result["forecast_scenarios"]) == 3
    assert result["forecast_anchor"]["verified_latest_close"] is True
    assert math.isclose(result["forecast_anchor"]["x_norm"], 310.0 / 800.0)
    assert result["closed_candle_key"] == closed_candle_identity_v3(
        candles, pair="NZD/USD OTC", timeframe="M5"
    )
    assert result["production_authorized"] is False
    assert result["selective_authorized"] is False
    assert result["trade_authorized"] is False
    assert result["provider"] == "BOOK_STRATEGY_CONDITIONED_SCENE_V3"
    assert result["provider_status"] == "BOOK_STRATEGY_CONTROLLED_FALLBACK"
    assert result["model_version"] == "BOOK_STRATEGY_CONDITIONED_SCENE_V3"
    assert result["requested_model_version"] == "chronos-2-small"
    consumed = result["scene_feature_audit"]["consumed_fields"]
    assert "projection.confidence" in consumed
    assert "decision_kernel.belief_buy" in consumed


def test_forming_candle_changes_do_not_change_closed_event_identity() -> None:
    candles = _candles()
    first = closed_candle_identity_v3(candles, pair="NZDUSD", timeframe="M5")
    candles[-1]["close_y_px"] = 20.0
    candles[-1]["price_proxy"] = 0.94
    second = closed_candle_identity_v3(candles, pair="NZDUSD", timeframe="M5")

    assert second == first


def test_closed_event_identity_ignores_subpixel_visual_jitter_and_replay() -> None:
    candles = _candles()
    first = closed_candle_identity_v3(candles, pair="NZDUSD", timeframe="M5")

    # Re-reading the identical frame is an exact replay, while a resampled
    # capture can move the detector by a fraction of one physical pixel. Both
    # observations still describe the same completed candle.
    replay = closed_candle_identity_v3(candles, pair="NZDUSD", timeframe="M5")
    for key in (
        "center_x",
        "open_y_px",
        "close_y_px",
        "wick_top_px",
        "wick_bottom_px",
    ):
        candles[-2][key] = float(candles[-2][key]) + 0.24
    jittered = closed_candle_identity_v3(
        candles,
        pair="NZDUSD",
        timeframe="M5",
    )

    assert replay == first
    assert jittered == first


def test_closed_event_identity_advances_only_after_real_candle_rollover() -> None:
    candles = _candles()
    previous = closed_candle_identity_v3(candles, pair="NZDUSD", timeframe="M5")

    # The previous forming candle is now completed and a new forming candle
    # exists at the right edge. This is the causal event transition used by
    # the forecaster; it is not inferred from elapsed wall-clock time.
    candles[-1]["is_closed"] = True
    next_forming = dict(candles[-1])
    next_forming.update(
        {
            "track_id": 30,
            "direction": "BUY",
            "center_x": 330.0,
            "open_y_px": 148.0,
            "close_y_px": 146.0,
            "wick_top_px": 144.0,
            "wick_bottom_px": 150.0,
            "is_closed": False,
        }
    )
    candles.append(next_forming)

    current = closed_candle_identity_v3(candles, pair="NZDUSD", timeframe="M5")

    assert current != previous


def test_source_bar_identity_wins_over_visual_geometry() -> None:
    candles = _candles()
    candles[-2]["bar_open_time"] = 1_783_755_200
    first = closed_candle_identity_v3(candles, pair="NZDUSD", timeframe="M5")

    candles[-2].update(
        {
            "track_id": 999,
            "center_x": 40.0,
            "open_y_px": 20.0,
            "close_y_px": 25.0,
            "wick_top_px": 10.0,
            "wick_bottom_px": 30.0,
        }
    )
    same_source_bar = closed_candle_identity_v3(
        candles,
        pair="NZDUSD",
        timeframe="M5",
    )

    assert same_source_bar == first


def test_stateful_identity_reuses_event_when_detector_reclassifies_closed_candle() -> None:
    candles = _candles()
    initial = resolve_closed_candle_identity_v3(
        candles,
        pair="NZDUSD",
        timeframe="M5",
        stream_frame_id=10,
        stream_process_token="process-a",
        stream_source_token="window-a",
        stream_continuity_eligible=True,
    )

    # Reproduce the live 842 -> 843 failure shape: latest-closed ordinal and
    # forming ordinal remain 28/29, while the detector changes the completed
    # candle from SELL to BUY and refines its geometry. The developing candle
    # is still the same physical observation, so this is not a market event.
    candles[-2].update(
        {
            "direction": "SELL",
            "center_x": float(candles[-2]["center_x"]) + 5.9,
            "open_y_px": float(candles[-2]["open_y_px"]) + 61.0,
            "close_y_px": float(candles[-2]["close_y_px"]) - 18.0,
            "wick_top_px": float(candles[-2]["wick_top_px"]) + 1.7,
            "wick_bottom_px": float(candles[-2]["wick_bottom_px"]) + 36.0,
        }
    )
    candles[-1]["close_y_px"] = float(candles[-1]["close_y_px"]) + 18.0
    reclassified = resolve_closed_candle_identity_v3(
        candles,
        pair="NZDUSD",
        timeframe="M5",
        previous_state=initial["state"],
    )

    assert reclassified["closed_candle_key"] == initial["closed_candle_key"]
    assert reclassified["closed_candle_sequence"] == 0
    assert reclassified["transition_observed"] is False
    assert reclassified["transition_reason"] == "FORMING_CANDLE_STILL_ACTIVE"


def test_stable_visible_history_baselines_only_latest_and_resets_on_context_change() -> (
    None
):
    candles = _candles()
    initial = resolve_closed_candle_identity_v3(
        candles,
        pair="NZDUSD",
        timeframe="M5",
        previous_sequence=9,
    )

    bindings = initial["stable_visible_candle_bindings"]
    assert bindings == initial["state"]["stable_visible_candle_bindings"]
    assert len(bindings) == 1
    assert bindings[0]["current_row_index"] == 28
    assert bindings[0]["closed_candle_key"] == initial["closed_candle_key"]
    assert bindings[0]["closed_candle_sequence"] == 9
    assert bindings[0]["proof_source"] == "INITIAL_CAUSAL_BASELINE_V3"
    assert bindings[0]["reobserved_observation"]["index"] == 28

    changed = resolve_closed_candle_identity_v3(
        candles,
        pair="EURUSD",
        timeframe="M15",
        previous_state=initial["state"],
        previous_key=initial["closed_candle_key"],
        previous_sequence=9,
    )

    assert changed["closed_candle_sequence"] == 0
    assert changed["closed_candle_key"] != initial["closed_candle_key"]
    assert changed["state"]["pair"] == "EURUSD"
    assert changed["state"]["timeframe"] == "M15"
    assert [
        row["closed_candle_sequence"]
        for row in changed["stable_visible_candle_bindings"]
    ] == [0]


def test_stateful_identity_reuses_event_during_closed_detector_dropout() -> None:
    candles = _candles()
    initial = resolve_closed_candle_identity_v3(
        candles,
        pair="NZDUSD",
        timeframe="M5",
    )

    # The latest closed detection disappears, but the right-edge forming
    # candle remains. Losing an observation cannot manufacture a candle close.
    del candles[-2]
    dropout = resolve_closed_candle_identity_v3(
        candles,
        pair="NZDUSD",
        timeframe="M5",
        previous_state=initial["state"],
    )

    assert dropout["closed_candle_key"] == initial["closed_candle_key"]
    assert dropout["closed_candle_sequence"] == 0
    assert dropout["transition_observed"] is False
    assert dropout["transition_reason"] == "FORMING_CANDLE_STILL_ACTIVE"


def test_stateful_identity_rebases_multi_step_detector_coverage_without_new_event() -> None:
    initial = resolve_closed_candle_identity_v3(
        _coverage_candles(39),
        pair="CHFJPY_OTC",
        timeframe="M5",
    )
    legacy_state = dict(initial["state"])
    for key in (
        "detected_candle_count",
        "detected_closed_count",
        "coverage_left_x",
        "coverage_right_x",
        "coverage_span_x",
    ):
        legacy_state.pop(key, None)

    repaired = resolve_closed_candle_identity_v3(
        _coverage_candles(64),
        pair="CHFJPY_OTC",
        timeframe="M5",
        previous_state=legacy_state,
    )

    assert repaired["closed_candle_key"] == initial["closed_candle_key"]
    assert repaired["closed_candle_sequence"] == initial["closed_candle_sequence"]
    assert repaired["transition_observed"] is False
    assert repaired["transition_reason"] == "DETECTOR_COVERAGE_REBASE"
    assert repaired["same_event_cache_rebuild_required"] is True
    assert repaired["match_scores"]["detector_coverage_rebase"] is True
    assert repaired["match_scores"]["detected_candle_count_growth"] == 25
    state = repaired["state"]
    assert state["detected_candle_count"] == 64
    assert state["detected_closed_count"] == 63
    assert state["latest_closed"]["index"] == 62
    assert state["forming"]["index"] == 63

    degraded = resolve_closed_candle_identity_v3(
        _coverage_candles(39),
        pair="CHFJPY_OTC",
        timeframe="M5",
        previous_state=state,
    )
    assert degraded["closed_candle_key"] == initial["closed_candle_key"]
    assert degraded["closed_candle_sequence"] == initial["closed_candle_sequence"]
    assert degraded["same_event_cache_rebuild_required"] is False
    assert degraded["match_scores"]["coverage_degradation_observed"] is True
    assert degraded["match_scores"]["coverage_high_water_preserved"] is True
    assert degraded["state"]["detected_candle_count"] == 64
    assert degraded["state"]["detected_closed_count"] == 63

    restored = resolve_closed_candle_identity_v3(
        _coverage_candles(64),
        pair="CHFJPY_OTC",
        timeframe="M5",
        previous_state=degraded["state"],
    )
    assert restored["closed_candle_key"] == initial["closed_candle_key"]
    assert restored["closed_candle_sequence"] == initial["closed_candle_sequence"]
    assert restored["transition_observed"] is False
    assert restored["same_event_cache_rebuild_required"] is False
    assert restored["match_scores"]["detected_candle_count_growth"] == 0


def test_stateful_identity_advances_when_prior_forming_candle_becomes_closed() -> None:
    candles = _candles()
    initial = resolve_closed_candle_identity_v3(
        candles,
        pair="NZDUSD",
        timeframe="M5",
    )

    prior_forming = candles[-1]
    prior_forming["is_closed"] = True
    candles.append(
        {
            "track_id": 30,
            "direction": "SELL",
            "center_x": float(prior_forming["center_x"]) + 10.0,
            "open_y_px": 90.0,
            "close_y_px": 104.0,
            "wick_top_px": 86.0,
            "wick_bottom_px": 108.0,
            "price_proxy": 0.66,
            "bbox": [327.0, 86.0, 333.0, 108.0],
            "is_closed": False,
        }
    )
    rollover = resolve_closed_candle_identity_v3(
        candles,
        pair="NZDUSD",
        timeframe="M5",
        previous_state=initial["state"],
    )

    assert rollover["closed_candle_key"] != initial["closed_candle_key"]
    assert rollover["closed_candle_sequence"] == 1
    assert rollover["transition_observed"] is True
    assert rollover["transition_reason"] == "VISUAL_FORMING_CANDLE_BECAME_CLOSED"
    assert rollover["same_event_cache_rebuild_required"] is False
    proof = rollover["prior_close_reobservation"]
    assert proof["status"] == "CONFIRMED"
    assert proof["prior_closed_candle_key"] == initial["closed_candle_key"]
    assert proof["prior_closed_candle_sequence"] == 0
    assert proof["current_row_index"] == 28


def test_continuous_stream_boundary_advances_one_ambiguous_m5_event_when_enabled() -> (
    None
):
    candles, current = _ambiguous_fixed_width_boundary_rollover()
    initial = resolve_closed_candle_identity_v3(
        candles,
        pair="NZDUSD",
        timeframe="M5",
        stream_frame_id=10,
        stream_process_token="process-a",
        stream_source_token="window-a",
        stream_continuity_eligible=True,
    )
    previous_state = dict(initial["state"])
    previous_state["latest_observed_epoch_seconds_v3"] = 299.0

    candidate = resolve_closed_candle_identity_v3(
        current,
        pair="NZDUSD",
        timeframe="M5",
        previous_state=previous_state,
        capture_epoch=301.0,
        allow_continuous_stream_boundary=True,
        stream_frame_id=11,
        stream_process_token="process-a",
        stream_source_token="window-a",
        stream_continuity_eligible=True,
    )
    resolution = resolve_closed_candle_identity_v3(
        current,
        pair="NZDUSD",
        timeframe="M5",
        previous_state=candidate["state"],
        capture_epoch=302.0,
        allow_continuous_stream_boundary=True,
        stream_frame_id=12,
        stream_process_token="process-a",
        stream_source_token="window-a",
        stream_continuity_eligible=True,
    )

    assert candidate["transition_observed"] is False
    assert candidate["closed_candle_sequence"] == 0
    assert candidate["transition_reason"] == (
        "STREAM_BOUNDARY_CANDIDATE_PENDING_CONFIRMATION"
    )
    assert candidate["state"]["confirmed_event_batch"] == []
    assert candidate["state"]["stream_boundary_candidate_v3"]["status"] == (
        "PENDING_CONFIRMATION"
    )
    assert candidate["match_scores"][
        "stream_boundary_predecessor_chain_proven"
    ] is True
    assert resolution["transition_observed"] is True
    assert resolution["transition_count"] == 1
    assert resolution["closed_candle_sequence"] == 1
    assert (
        resolution["transition_reason"]
        == "STREAM_CONTINUITY_BOUNDARY_CONFIRMED_CLOSED_CANDLE"
    )
    assert resolution["match_scores"]["stream_boundary_transition"] is True

    replay_state = dict(resolution["state"])
    replay_state["latest_observed_epoch_seconds_v3"] = 302.0
    replay = resolve_closed_candle_identity_v3(
        current,
        pair="NZDUSD",
        timeframe="M5",
        previous_state=replay_state,
        capture_epoch=303.0,
        allow_continuous_stream_boundary=True,
        stream_frame_id=13,
        stream_process_token="process-a",
        stream_source_token="window-a",
        stream_continuity_eligible=True,
    )
    assert replay["transition_observed"] is False
    assert replay["closed_candle_sequence"] == 1
    assert replay["state"]["confirmed_event_batch"] == []


def test_continuous_stream_boundary_proof_is_opt_in_and_gap_bounded() -> None:
    candles = _candles()
    initial = resolve_closed_candle_identity_v3(
        candles,
        pair="NZDUSD",
        timeframe="M5",
        stream_frame_id=10,
        stream_process_token="process-a",
        stream_source_token="window-a",
        stream_continuity_eligible=True,
    )
    previous_state = dict(initial["state"])
    previous_state["latest_observed_epoch_seconds_v3"] = 299.0

    disabled = resolve_closed_candle_identity_v3(
        candles,
        pair="NZDUSD",
        timeframe="M5",
        previous_state=previous_state,
        capture_epoch=301.0,
    )
    frozen_surface = resolve_closed_candle_identity_v3(
        candles,
        pair="NZDUSD",
        timeframe="M5",
        previous_state=previous_state,
        capture_epoch=301.0,
        allow_continuous_stream_boundary=True,
        stream_frame_id=11,
        stream_process_token="process-a",
        stream_source_token="window-a",
        stream_continuity_eligible=True,
    )
    capture_gap = resolve_closed_candle_identity_v3(
        candles,
        pair="NZDUSD",
        timeframe="M5",
        previous_state=previous_state,
        capture_epoch=601.0,
        allow_continuous_stream_boundary=True,
        stream_frame_id=11,
        stream_process_token="process-a",
        stream_source_token="window-a",
        stream_continuity_eligible=True,
    )

    assert disabled["transition_observed"] is False
    assert disabled["closed_candle_sequence"] == 0
    assert frozen_surface["transition_observed"] is False
    assert frozen_surface["closed_candle_sequence"] == 0
    assert (
        frozen_surface["match_scores"]["stream_boundary_reason"]
        == "EDGE_OBSERVATION_NOT_MATERIAL"
    )
    assert capture_gap["transition_observed"] is False
    assert capture_gap["closed_candle_sequence"] == 0
    assert (
        capture_gap["match_scores"]["stream_boundary_reason"]
        == "STREAM_CONTINUITY_GAP_TOO_LARGE"
    )


def test_continuous_stream_boundary_rejects_disjoint_pan_with_two_coincidences() -> (
    None
):
    candles = _candles()
    initial = resolve_closed_candle_identity_v3(
        candles,
        pair="NZDUSD",
        timeframe="M5",
        stream_frame_id=20,
        stream_process_token="process-a",
        stream_source_token="window-a",
        stream_continuity_eligible=True,
    )
    previous_state = dict(initial["state"])
    previous_state["latest_observed_epoch_seconds_v3"] = 299.0

    # Model a stable same-count pan/rezoom with two coincidentally identical,
    # non-adjacent historical candles. The former loose overlap-count proof
    # accepted this even though no ordered one-bar predecessor chain survived.
    disjoint = copy.deepcopy(candles)
    for index, row in enumerate(disjoint):
        if index in {6, 13}:
            continue
        for key in (
            "open_y_px",
            "close_y_px",
            "wick_top_px",
            "wick_bottom_px",
        ):
            row[key] = float(row[key]) + 200.0
        row["direction"] = (
            "SELL" if str(row["direction"]) == "BUY" else "BUY"
        )

    rejected = resolve_closed_candle_identity_v3(
        disjoint,
        pair="NZDUSD",
        timeframe="M5",
        previous_state=previous_state,
        capture_epoch=301.0,
        allow_continuous_stream_boundary=True,
        stream_frame_id=21,
        stream_process_token="process-a",
        stream_source_token="window-a",
        stream_continuity_eligible=True,
    )

    assert rejected["transition_observed"] is False
    assert rejected["closed_candle_sequence"] == 0
    assert "stream_boundary_candidate_v3" not in rejected["state"]
    assert rejected["match_scores"]["stream_boundary_reason"] == (
        "EXPECTED_PREDECESSOR_CHAIN_NOT_PROVEN"
    )
    assert rejected["match_scores"][
        "stream_boundary_predecessor_chain_proven"
    ] is False


@pytest.mark.parametrize(
    ("confirmation_frame", "confirmation_process", "confirmation_source"),
    (
        (32, "process-a", "window-b"),
        (33, "process-a", "window-a"),
        (32, "process-b", "window-a"),
    ),
)
def test_continuous_stream_boundary_confirmation_rejects_broken_lineage(
    confirmation_frame: int,
    confirmation_process: str,
    confirmation_source: str,
) -> None:
    candles, current = _ambiguous_fixed_width_boundary_rollover()
    initial = resolve_closed_candle_identity_v3(
        candles,
        pair="NZDUSD",
        timeframe="M5",
        stream_frame_id=30,
        stream_process_token="process-a",
        stream_source_token="window-a",
        stream_continuity_eligible=True,
    )
    previous_state = dict(initial["state"])
    previous_state["latest_observed_epoch_seconds_v3"] = 299.0
    candidate = resolve_closed_candle_identity_v3(
        current,
        pair="NZDUSD",
        timeframe="M5",
        previous_state=previous_state,
        capture_epoch=301.0,
        allow_continuous_stream_boundary=True,
        stream_frame_id=31,
        stream_process_token="process-a",
        stream_source_token="window-a",
        stream_continuity_eligible=True,
    )
    assert candidate["transition_reason"] == (
        "STREAM_BOUNDARY_CANDIDATE_PENDING_CONFIRMATION"
    )

    rejected = resolve_closed_candle_identity_v3(
        current,
        pair="NZDUSD",
        timeframe="M5",
        previous_state=candidate["state"],
        capture_epoch=302.0,
        allow_continuous_stream_boundary=True,
        stream_frame_id=confirmation_frame,
        stream_process_token=confirmation_process,
        stream_source_token=confirmation_source,
        stream_continuity_eligible=True,
    )
    assert rejected["transition_observed"] is False
    assert rejected["closed_candle_sequence"] == 0
    assert rejected["match_scores"][
        "stream_boundary_confirmation_reason"
    ] == "STREAM_CAPTURE_LINEAGE_NOT_CONTIGUOUS"
    assert "stream_boundary_candidate_v3" not in rejected["state"]


@pytest.mark.parametrize(
    ("prior_count", "current_count", "expected_audit_field"),
    (
        (39, 64, "stream_boundary_coverage_rebase"),
        (64, 39, "stream_boundary_coverage_degraded"),
    ),
)
def test_continuous_stream_boundary_rejects_detector_coverage_changes(
    prior_count: int,
    current_count: int,
    expected_audit_field: str,
) -> None:
    initial = resolve_closed_candle_identity_v3(
        _coverage_candles(prior_count),
        pair="NZDUSD",
        timeframe="M5",
        stream_frame_id=40,
        stream_process_token="process-a",
        stream_source_token="window-a",
        stream_continuity_eligible=True,
    )
    previous_state = dict(initial["state"])
    previous_state["latest_observed_epoch_seconds_v3"] = 299.0
    rejected = resolve_closed_candle_identity_v3(
        _coverage_candles(current_count),
        pair="NZDUSD",
        timeframe="M5",
        previous_state=previous_state,
        capture_epoch=301.0,
        allow_continuous_stream_boundary=True,
        stream_frame_id=41,
        stream_process_token="process-a",
        stream_source_token="window-a",
        stream_continuity_eligible=True,
    )

    assert rejected["transition_observed"] is False
    assert rejected["closed_candle_sequence"] == 0
    assert rejected["match_scores"]["stream_boundary_reason"] == (
        "DETECTOR_COVERAGE_CHANGE_VETOES_STREAM_BOUNDARY"
    )
    assert rejected["match_scores"][expected_audit_field] is True
    assert "stream_boundary_candidate_v3" not in rejected["state"]


def _roll_forward_candles(
    candles: list[dict[str, Any]],
    *,
    completed_after_anchor: int,
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in candles]
    rows[-1]["is_closed"] = True
    close_y = float(rows[-1]["close_y_px"])
    center_x = float(rows[-1]["center_x"])
    for offset in range(1, completed_after_anchor + 1):
        direction = "BUY" if offset % 2 else "SELL"
        open_y = close_y
        close_y += -4.0 if direction == "BUY" else 3.0
        center_x += 10.0
        rows.append(
            {
                "track_id": 29 + offset,
                "direction": direction,
                "center_x": center_x,
                "open_y_px": open_y,
                "close_y_px": close_y,
                "wick_top_px": min(open_y, close_y) - 2.0,
                "wick_bottom_px": max(open_y, close_y) + 2.0,
                "price_proxy": 1.0 - close_y / 300.0,
                "bbox": [
                    center_x - 3.0,
                    min(open_y, close_y) - 2.0,
                    center_x + 3.0,
                    max(open_y, close_y) + 2.0,
                ],
                "parse_confidence": 0.94,
                "is_closed": offset < completed_after_anchor,
            }
        )
    return rows


def test_stable_visible_history_rebinds_six_rollovers_without_track_identity() -> None:
    rows = _candles()
    rows[-2].update(
        {
            "open_y_px": 180.0,
            "close_y_px": 166.0,
            "wick_top_px": 158.0,
            "wick_bottom_px": 187.0,
            "bbox": [307.0, 158.0, 313.0, 187.0],
        }
    )
    rows[-1].update(
        {
            "open_y_px": 211.0,
            "close_y_px": 194.0,
            "wick_top_px": 185.0,
            "wick_bottom_px": 219.0,
            "bbox": [317.0, 185.0, 323.0, 219.0],
        }
    )
    resolution = resolve_closed_candle_identity_v3(
        rows,
        pair="GBPUSD_OTC",
        timeframe="M5",
    )

    for frame in range(1, 7):
        current = [dict(row) for row in rows]
        current[-1]["is_closed"] = True
        prior_forming = current[-1]
        center_x = float(prior_forming["center_x"]) + 10.0
        open_y = float(prior_forming["close_y_px"])
        body = 5.0 + frame * 1.7
        direction = "BUY" if frame % 2 else "SELL"
        close_y = open_y - body if direction == "BUY" else open_y + body
        current.append(
            {
                "track_id": "forming",
                "direction": direction,
                "center_x": center_x,
                "open_y_px": open_y,
                "close_y_px": close_y,
                "wick_top_px": min(open_y, close_y) - (2.0 + frame),
                "wick_bottom_px": max(open_y, close_y) + (3.0 + frame),
                "price_proxy": 1.0 - close_y / 300.0,
                "bbox": [
                    center_x - 3.0,
                    min(open_y, close_y) - (2.0 + frame),
                    center_x + 3.0,
                    max(open_y, close_y) + (3.0 + frame),
                ],
                "parse_confidence": 0.94,
                "is_closed": False,
            }
        )
        for index, row in enumerate(current):
            # Reacquisition deliberately replaces every rolling detector id.
            row["track_id"] = f"frame-{frame}-slot-{index}"
        resolution = resolve_closed_candle_identity_v3(
            current,
            pair="GBPUSD_OTC",
            timeframe="M5",
            previous_state=resolution["state"],
        )
        assert resolution["transition_observed"] is True
        assert resolution["transition_count"] == 1
        rows = current

    bindings = resolution["stable_visible_candle_bindings"]
    assert bindings == resolution["state"]["stable_visible_candle_bindings"]
    assert len(bindings) <= 32
    assert [row["closed_candle_sequence"] for row in bindings] == list(range(7))
    assert [row["current_row_index"] for row in bindings] == list(range(28, 35))
    assert len({row["closed_candle_key"] for row in bindings}) == 7
    assert all(
        str(row["reobserved_observation"]["track_id"]).startswith("frame-6-")
        for row in bindings
    )
    assert all(
        row["proof_source"] == "UNIQUE_VISUAL_REOBSERVATION_V3" for row in bindings[:-1]
    )


def test_stateful_identity_reacquires_each_visible_missed_closed_candle() -> None:
    initial_rows = _candles()
    initial = resolve_closed_candle_identity_v3(
        initial_rows,
        pair="GBPUSD_OTC",
        timeframe="M5",
    )
    advanced_rows = _roll_forward_candles(
        initial_rows,
        completed_after_anchor=3,
    )

    recovered = resolve_closed_candle_identity_v3(
        advanced_rows,
        pair="GBPUSD_OTC",
        timeframe="M5",
        previous_state=initial["state"],
    )

    assert recovered["transition_observed"] is True
    assert recovered["transition_reason"] == "VISUAL_CLOSED_CANDLE_GAP_REACQUIRED"
    assert recovered["transition_count"] == 3
    assert recovered["closed_candle_sequence"] == 3
    batch = recovered["state"]["confirmed_event_batch"]
    assert [row["closed_candle_sequence"] for row in batch] == [1, 2, 3]
    assert [row["observation"]["track_id"] for row in batch] == ["29", "30", "31"]
    assert len({row["closed_candle_key"] for row in batch}) == 3
    assert recovered["state"]["reacquisition"]["status"] == "CONFIRMED"


def test_stateful_identity_retains_twenty_four_visible_closed_events() -> None:
    initial_rows = _candles()
    initial = resolve_closed_candle_identity_v3(
        initial_rows,
        pair="GBPUSD_OTC",
        timeframe="M5",
    )
    advanced_rows = _roll_forward_candles(
        initial_rows,
        completed_after_anchor=24,
    )

    recovered = resolve_closed_candle_identity_v3(
        advanced_rows,
        pair="GBPUSD_OTC",
        timeframe="M5",
        previous_state=initial["state"],
    )

    # The detector retains the full bounded history even when repeated visual
    # shapes make the old forming candle ambiguous. Retention and confirmation
    # are intentionally separate: ambiguity must not manufacture 24 events.
    assert recovered["transition_observed"] is False
    assert recovered["transition_count"] == 0
    assert recovered["closed_candle_sequence"] == 0
    assert len(recovered["state"]["closed_tail"]) == 24
    assert recovered["state"]["confirmed_event_batch"] == []


def test_stateful_identity_does_not_invent_a_gap_when_visible_chain_is_broken() -> None:
    initial_rows = _candles()
    initial = resolve_closed_candle_identity_v3(
        initial_rows,
        pair="GBPUSD_OTC",
        timeframe="M5",
    )
    advanced_rows = _roll_forward_candles(
        initial_rows,
        completed_after_anchor=3,
    )
    del advanced_rows[-3]

    unresolved = resolve_closed_candle_identity_v3(
        advanced_rows,
        pair="GBPUSD_OTC",
        timeframe="M5",
        previous_state=initial["state"],
    )

    assert unresolved["transition_observed"] is False
    assert unresolved["closed_candle_sequence"] == 0
    assert unresolved["state"]["confirmed_event_batch"] == []
    assert unresolved["stable_visible_candle_bindings"] == []
    assert unresolved["state"]["reacquisition"]["status"] == "NOT_CONFIRMED"
    assert (
        unresolved["state"]["reacquisition"]["reason"]
        == "REACQUISITION_CHAIN_NOT_CONTIGUOUS"
    )


def test_screenshot_rollover_does_not_use_legacy_match_when_reacquisition_is_ambiguous() -> None:
    initial_rows = _candles()
    initial = resolve_closed_candle_identity_v3(
        initial_rows,
        pair="GBPUSD_OTC",
        timeframe="M5",
    )
    ambiguous_rows = _roll_forward_candles(
        initial_rows,
        completed_after_anchor=1,
    )
    duplicate_anchor = dict(initial_rows[-1])
    duplicate_anchor["is_closed"] = True
    ambiguous_rows.insert(-2, duplicate_anchor)

    unresolved = resolve_closed_candle_identity_v3(
        ambiguous_rows,
        pair="GBPUSD_OTC",
        timeframe="M5",
        previous_state=initial["state"],
    )

    assert unresolved["match_scores"]["forming_became_closed"] >= 0.62
    assert unresolved["state"]["reacquisition"]["status"] == "NOT_CONFIRMED"
    assert unresolved["transition_observed"] is False
    assert unresolved["closed_candle_sequence"] == 0
    assert unresolved["state"]["confirmed_event_batch"] == []
    assert unresolved["stable_visible_candle_bindings"] == []


def test_source_rollover_wins_over_simultaneous_detector_coverage_expansion() -> None:
    initial_rows = _coverage_candles(39)
    initial_rows[-2]["bar_open_time"] = 1_783_755_200
    initial = resolve_closed_candle_identity_v3(
        initial_rows,
        pair="CHFJPY_OTC",
        timeframe="M5",
    )
    expanded_rows = _coverage_candles(64)
    expanded_rows[-2]["bar_open_time"] = 1_783_755_500
    advanced = resolve_closed_candle_identity_v3(
        expanded_rows,
        pair="CHFJPY_OTC",
        timeframe="M5",
        previous_state=initial["state"],
    )

    assert advanced["transition_observed"] is True
    assert advanced["transition_reason"] == "SOURCE_BAR_ID_ADVANCED"
    assert advanced["closed_candle_sequence"] == 1
    assert advanced["closed_candle_key"] != initial["closed_candle_key"]
    assert advanced["match_scores"]["detector_coverage_rebase"] is True
    assert advanced["same_event_cache_rebuild_required"] is False


@pytest.mark.parametrize(
    ("identity_field", "prior_identity", "current_identity"),
    (
        ("bar_open_time", 1_783_755_200, 1_783_756_100),
        ("bar_open_time", 1_783_755_200_000, 1_783_756_100_000),
        ("source_bar_id", "broker-bar-a", "broker-bar-d"),
    ),
)
def test_source_identity_gap_cannot_masquerade_as_one_candle_horizon(
    identity_field: str,
    prior_identity: object,
    current_identity: object,
) -> None:
    initial_rows = _coverage_candles(39)
    initial_rows[-2][identity_field] = prior_identity
    initial = resolve_closed_candle_identity_v3(
        initial_rows,
        pair="CHFJPY_OTC",
        timeframe="M5",
    )
    current_rows = _coverage_candles(39)
    current_rows[-2][identity_field] = current_identity
    unresolved = resolve_closed_candle_identity_v3(
        current_rows,
        pair="CHFJPY_OTC",
        timeframe="M5",
        previous_state=initial["state"],
    )

    assert unresolved["transition_observed"] is False
    assert unresolved["transition_count"] == 0
    assert unresolved["closed_candle_sequence"] == 0
    assert unresolved["closed_candle_key"] == initial["closed_candle_key"]
    assert unresolved["transition_reason"] == "SOURCE_BAR_GAP_UNPROVEN"
    assert unresolved["state"]["confirmed_event_batch"] == []
    assert unresolved["match_scores"]["source_one_step_horizon_proven"] is False


def test_source_bar_id_and_market_time_remain_independent_proofs() -> None:
    initial_rows = _coverage_candles(39)
    initial_rows[-2].update(
        {
            "source_bar_id": "broker-bar-a",
            "bar_open_time": 1_783_755_200,
        }
    )
    initial = resolve_closed_candle_identity_v3(
        initial_rows,
        pair="CHFJPY_OTC",
        timeframe="M5",
    )
    current_rows = _coverage_candles(39)
    current_rows[-2].update(
        {
            "source_bar_id": "broker-bar-b",
            "bar_open_time": 1_783_755_500,
        }
    )

    advanced = resolve_closed_candle_identity_v3(
        current_rows,
        pair="CHFJPY_OTC",
        timeframe="M5",
        previous_state=initial["state"],
    )

    latest = advanced["state"]["latest_closed"]
    assert latest["source_identity_field"] == "source_bar_id"
    assert latest["source_identity_value"] == "broker-bar-b"
    assert latest["source_time_field"] == "bar_open_time"
    assert latest["source_time_semantics"] == "BAR_OPEN"
    assert latest["source_time_seconds"] == 1_783_755_500
    assert advanced["transition_observed"] is True
    assert advanced["transition_reason"] == "SOURCE_BAR_ID_ADVANCED"
    assert advanced["match_scores"]["source_time_step_count"] == 1


def test_source_bar_id_cannot_hide_a_multi_interval_time_gap() -> None:
    initial_rows = _coverage_candles(39)
    initial_rows[-2].update(
        {
            "source_bar_id": "broker-bar-a",
            "bar_open_time": 1_783_755_200,
        }
    )
    initial = resolve_closed_candle_identity_v3(
        initial_rows,
        pair="CHFJPY_OTC",
        timeframe="M5",
    )
    current_rows = _coverage_candles(39)
    current_rows[-2].update(
        {
            "source_bar_id": "broker-bar-d",
            "bar_open_time": 1_783_756_100,
        }
    )

    unresolved = resolve_closed_candle_identity_v3(
        current_rows,
        pair="CHFJPY_OTC",
        timeframe="M5",
        previous_state=initial["state"],
    )

    assert unresolved["transition_observed"] is False
    assert unresolved["transition_reason"] == "SOURCE_BAR_GAP_UNPROVEN"
    assert unresolved["match_scores"]["source_time_step_count"] == 3
    assert unresolved["closed_candle_key"] == initial["closed_candle_key"]


def test_conflicting_source_times_fail_closed_even_with_a_new_bar_id() -> None:
    initial_rows = _coverage_candles(39)
    initial_rows[-2].update(
        {
            "source_bar_id": "broker-bar-a",
            "bar_open_time": 1_783_755_200,
            "open_time": 1_783_755_200,
        }
    )
    initial = resolve_closed_candle_identity_v3(
        initial_rows,
        pair="CHFJPY_OTC",
        timeframe="M5",
    )
    current_rows = _coverage_candles(39)
    current_rows[-2].update(
        {
            "source_bar_id": "broker-bar-b",
            "bar_open_time": 1_783_755_500,
            "open_time": 1_783_755_800,
        }
    )

    unresolved = resolve_closed_candle_identity_v3(
        current_rows,
        pair="CHFJPY_OTC",
        timeframe="M5",
        previous_state=initial["state"],
    )

    assert unresolved["transition_observed"] is False
    assert unresolved["transition_reason"] == "SOURCE_BAR_GAP_UNPROVEN"
    assert unresolved["match_scores"]["source_time_step_count"] == 0
    assert unresolved["match_scores"]["source_time_conflict_current"] is True


def test_cached_geometry_reanchor_is_atomic_across_routes_ohlc_and_interval() -> None:
    base_cycle = [
        0.52,
        0.49,
        0.535,
        0.505,
        0.55,
        0.525,
        0.565,
        0.54,
        0.575,
        0.555,
        0.59,
        0.57,
    ]
    base = [
        min(0.95, max(0.05, value + 0.001 * cycle))
        for cycle in range(6)
        for value in base_cycle
    ]
    close = {
        "p10": [value - 0.04 for value in base],
        "p50": base,
        "p90": [value + 0.04 for value in base],
    }
    upper: dict[str, list[float]] = {}
    lower: dict[str, list[float]] = {}
    for key, trajectory in close.items():
        prior = 0.50
        upper[key] = []
        lower[key] = []
        for value in trajectory:
            upper[key].append(max(prior, value) + 0.008)
            lower[key].append(min(prior, value) - 0.008)
            prior = value
    issued = decode_forecast_path_geometry_v3(
        anchor={
            "x_norm": 0.40,
            "y_norm": 0.50,
            "price_norm": 0.50,
            "event_step_x_norm": 0.006,
            "verified_latest_close": True,
        },
        close_quantiles=close,
        upper_quantiles=upper,
        lower_quantiles=lower,
        calibrated=True,
    )
    issued["raw_side_probabilities"] = {"BUY": 0.6, "HOLD": 0.2, "SELL": 0.2}
    issued["progression_play"] = {}
    original_points = [list(point) for point in issued["line_points"]]
    original_movements = [row["movement_side"] for row in issued["forecast_candles"]]

    reanchored = reanchor_scene_forecast_geometry_v3(
        issued,
        anchor={
            "x_norm": 0.72,
            "y_norm": 0.86,
            "price_norm": 0.14,
            "event_step_x_norm": 0.0035,
            "verified_latest_close": True,
            "source": "TRACKER_LATEST_CLOSED_CANDLE",
        },
    )

    expected_origin = [0.72, 0.86]
    assert issued["line_points"] == original_points
    assert reanchored["line_points"][0] == expected_origin
    assert len(reanchored["line_points"]) == 73
    assert len(reanchored["forecast_candles"]) == 72
    assert len(reanchored["forecast_scenarios"]) == 3
    assert all(
        scenario["line_points"][0] == expected_origin
        for scenario in reanchored["forecast_scenarios"]
    )
    assert sum(
        bool(scenario["selected"])
        for scenario in reanchored["forecast_scenarios"]
    ) == 1
    assert [row["movement_side"] for row in reanchored["forecast_candles"]] == original_movements
    assert len({round(point[1], 12) for point in reanchored["line_points"][-8:]}) > 4
    assert len(reanchored["forecast_path"]) == 72
    assert all(
        0.0 <= float(value) <= 1.0
        for point in reanchored["forecast_band_points"]
        for value in point
    )
    assert all(
        points[0] == expected_origin
        for points in reanchored["forecast_quantiles"].values()
    )
    for candle in reanchored["forecast_candles"]:
        assert candle["high_y_norm"] <= min(
            candle["open_y_norm"], candle["close_y_norm"]
        )
        assert candle["low_y_norm"] >= max(
            candle["open_y_norm"], candle["close_y_norm"]
        )
        assert candle["interval_top_y_norm"] <= candle["interval_bottom_y_norm"]
    assert reanchored["geometry_reanchor"]["x_gain"] < 1.0
    assert reanchored["geometry_reanchor"]["pointwise_clipping_applied"] is False
    assert reanchored["geometry_transform"]["anchor_y_norm"] == 0.86
    assert reanchored["geometry_transform"]["pointwise_clipping_applied"] is False
    assert math.isclose(
        reanchored["forecast_anchor"]["event_step_x_norm"],
        reanchored["line_points"][1][0] - reanchored["line_points"][0][0],
    )


def test_book_strategy_control_owns_causal_suite_direction_without_same_candle_flip() -> None:
    common: dict[str, Any] = {
        "candles": _candles(),
        "image_size": (800, 300),
        "timeframe": "M5",
        "pair": "NZDUSD_OTC",
        "projection": {"confidence": 0.9, "zones": []},
        "candle_statistics": {"sample_size": 29},
        "behavior_payload": {"current_state": "TRANSITION"},
        "smart_money_context": {"confidence": 0.8},
        "support_resistance_context": {
            "buy_structure_score": 0.5,
            "sell_structure_score": 0.5,
        },
        "trend_slopes": {"global": 0.0, "local": 0.0, "current": 0.0},
        "trend_directions": {"global": "HOLD", "local": "HOLD"},
        "allow_foundation_model": False,
    }
    buy = build_scene_forecast_contribution_v3(
        **common,
        event_key_override="suite-direction-buy",
        decision_kernel={
            "belief_buy": 0.9,
            "belief_hold": 0.05,
            "belief_sell": 0.05,
            "p_next_buy": 0.9,
            "p_next_sell": 0.05,
            "buy_evidence": 0.9,
            "sell_evidence": 0.05,
        },
    )
    sell = build_scene_forecast_contribution_v3(
        **common,
        event_key_override="suite-direction-sell",
        decision_kernel={
            "belief_buy": 0.05,
            "belief_hold": 0.05,
            "belief_sell": 0.9,
            "p_next_buy": 0.05,
            "p_next_sell": 0.9,
            "buy_evidence": 0.05,
            "sell_evidence": 0.9,
        },
    )

    assert buy["fallback"]["suite_features_used"] is True
    assert buy["fallback"]["method"] == "BOOK_RULE_CONDITIONED_CAUSAL_ANALOG"
    assert buy["fallback"]["suite_direction_bias"] > 0.0
    assert sell["fallback"]["suite_direction_bias"] == buy["fallback"]["suite_direction_bias"]
    assert sell["book_strategy_forecast_control_v3"]["forecast_side"] == buy[
        "book_strategy_forecast_control_v3"
    ]["forecast_side"]
    assert buy["line_points"][-1][1] < sell["line_points"][-1][1]


def test_scenario_selection_switches_side_line_and_all_seventy_two_candles_atomically() -> None:
    result = build_scene_forecast_contribution_v3(
        candles=_candles(),
        image_size=(800, 300),
        timeframe="M5",
        pair="NZDUSD_OTC",
        decision_kernel={"belief_sell": 0.9, "belief_hold": 0.05, "belief_buy": 0.05},
        allow_foundation_model=False,
    )
    bear = next(row for row in result["forecast_scenarios"] if row["role"] == "bear")

    selected = synchronize_scene_forecast_geometry_v3(result, selected_role="bear")

    assert selected["line_points"] == bear["line_points"]
    assert selected["forecast_candles"] == bear["forecast_candles"]
    assert selected["path_side"] == bear["side"]
    assert selected["side"] == bear["side"]
    assert selected["selected_scenario_role"] == "bear"
    assert len([row for row in selected["forecast_scenarios"] if row["selected"]]) == 1
    assert next(row for row in selected["forecast_scenarios"] if row["selected"])[
        "role"
    ] == "bear"
    assert len(selected["forecast_path"]) == 72
    assert selected["next_1_direction"] == selected["forecast_path"][0][
        "movement_direction"
    ]
