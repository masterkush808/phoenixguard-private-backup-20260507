from __future__ import annotations

from phoenixguard.mobile_api.window_tracker import (
    _current_promoted_jpclf_timing_v3,  # pyright: ignore[reportPrivateUsage]
    _jpclf_planned_contract_duration_seconds,  # pyright: ignore[reportPrivateUsage]
)


def _tracking_timing(*, side: str = "UP", promoted: bool = True) -> dict[str, object]:
    safety = {
        "study_only": True,
        "causal": True,
        "execution_authority": False,
        "grants_entry_permission": False,
        "may_issue_orders": False,
    }
    gate = {
        "passed": promoted,
        "all_axes_improved": promoted,
        "support": {"passed": promoted, "baseline": 32, "candidate": 32},
    }
    return {
        "market_study_v3": {
            "status": "STUDIED",
            "closed_candle_key": "close-42",
            "path_clock_liquidity_v3": {
                "schema_version": "PG_PATH_CLOCK_LIQUIDITY_PUBLIC_STUDY_V3",
                "closed_candle_key": "close-42",
                "promotion_gate": gate,
                **safety,
                "timing_read": {
                    "status": "TIMING_SUPPORT",
                    "state": "ELIGIBLE_NOW",
                    "side": side,
                    "contract_duration_seconds": 1800,
                    "candidate_horizon_seconds": 1800,
                    "remaining_seconds": 1800,
                    "new_entry_eligible": True,
                    "timing_supports_entry": True,
                    "timing_veto": False,
                    "closed_candle_key": "close-42",
                    "promotion_gate": gate,
                    **safety,
                },
            },
        }
    }


def test_jpclf_duration_planner_never_admits_less_than_fifteen_minutes() -> None:
    assert _jpclf_planned_contract_duration_seconds("M1", {}, {}) == 900


def test_jpclf_duration_planner_aligns_to_an_observable_closed_candle() -> None:
    assert _jpclf_planned_contract_duration_seconds("M3", {}, {}) == 900


def test_jpclf_duration_planner_uses_the_closed_candle_decision_horizon() -> None:
    assert _jpclf_planned_contract_duration_seconds(
        "M5",
        {"target_horizon_candles": 12},
        {},
    ) == 3600


def test_jpclf_duration_planner_stays_inside_the_bounded_two_hour_field() -> None:
    assert _jpclf_planned_contract_duration_seconds(
        "M5",
        {"target_horizon_candles": 1000},
        {},
    ) == 7200


def test_jpclf_duration_planner_abstains_when_source_cadence_exceeds_field() -> None:
    assert _jpclf_planned_contract_duration_seconds("H4", {}, {}) == 0


def test_only_promoted_current_side_jpclf_timing_reaches_execution_timing() -> None:
    read = _current_promoted_jpclf_timing_v3(
        _tracking_timing(),
        side="BUY",
    )

    assert read["side"] == "BUY"
    assert read["timing_supports_entry"] is True
    assert read["grants_entry_permission"] is False


def test_unpromoted_or_wrong_side_jpclf_timing_is_ignored() -> None:
    assert not _current_promoted_jpclf_timing_v3(
        _tracking_timing(promoted=False),
        side="BUY",
    )
    assert not _current_promoted_jpclf_timing_v3(
        _tracking_timing(side="DOWN"),
        side="BUY",
    )
