from phoenixguard.core.timing_policy_v3 import (
    MAXIMUM_STUDIED_TRADE_DURATION_SECONDS,
    MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS,
    duration_eligibility_contract_v3,
)


def test_duration_policy_excludes_every_move_under_fifteen_minutes() -> None:
    for duration in (899, 899.1, 899.5, 899.999):
        result = duration_eligibility_contract_v3(duration)

        assert result["status"] == "EXCLUDED_UNDER_15_MINUTES"
        assert result["eligible"] is False
        assert result["considered"] is False
        assert result["minimum_eligible_duration_seconds"] == 900
        assert result["execution_authority"] is False
        assert result["can_grant_entry_permission"] is False


def test_duration_policy_includes_the_exact_fifteen_minute_boundary() -> None:
    result = duration_eligibility_contract_v3(
        MINIMUM_ELIGIBLE_TRADE_DURATION_SECONDS
    )

    assert result["status"] == "ELIGIBLE"
    assert result["duration_seconds"] == 900
    assert result["eligible"] is True


def test_duration_policy_bounds_the_joint_field_at_two_hours() -> None:
    assert duration_eligibility_contract_v3(
        MAXIMUM_STUDIED_TRADE_DURATION_SECONDS
    )["eligible"] is True
    assert duration_eligibility_contract_v3(
        MAXIMUM_STUDIED_TRADE_DURATION_SECONDS + 1
    )["status"] == "EXCLUDED_ABOVE_BOUNDED_HORIZON"


def test_duration_policy_fails_closed_when_duration_is_unproven() -> None:
    for value in (None, "", 0, -1, float("nan"), True):
        result = duration_eligibility_contract_v3(value)
        assert result["status"] == "MISSING_DURATION"
        assert result["eligible"] is False
