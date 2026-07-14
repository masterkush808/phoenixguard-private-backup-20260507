from __future__ import annotations

from phoenixguard.decision.selective_risk_v3 import (
    choose_class_conditional_thresholds,
    evaluate_class_conditional_selection,
    fit_temperature,
    source_cluster_accuracy_interval,
    temperature_softmax,
    wilson_lower_bound,
)


def test_class_conditional_thresholds_do_not_hide_a_weak_buy_class() -> None:
    probabilities = [[0.91, 0.09]] * 20 + [[0.08, 0.92]] * 20
    labels = [0] * 11 + [1] * 9 + [1] * 20
    decisions = [0] * 20 + [1] * 20

    result = choose_class_conditional_thresholds(
        probabilities,
        labels,
        decisions,
        target_precision=0.85,
        minimum_predictions=10,
    )

    assert result["thresholds"]["BUY"] > 1.0
    assert result["classes"]["BUY"]["enabled"] is False
    assert result["classes"]["SELL"]["enabled"] is True
    assert result["validation"]["per_class"]["SELL"]["precision"] == 1.0


def test_class_conditional_thresholds_maximize_qualifying_coverage() -> None:
    probabilities = [
        [0.99, 0.01],
        [0.95, 0.05],
        [0.90, 0.10],
        [0.80, 0.20],
        [0.05, 0.95],
        [0.10, 0.90],
        [0.15, 0.85],
        [0.25, 0.75],
    ]
    labels = [0, 0, 0, 1, 1, 1, 1, 0]
    decisions = [0, 0, 0, 0, 1, 1, 1, 1]

    result = choose_class_conditional_thresholds(
        probabilities,
        labels,
        decisions,
        target_precision=0.85,
        minimum_predictions=3,
    )
    evaluation = evaluate_class_conditional_selection(
        probabilities,
        labels,
        decisions,
        result["thresholds"],
    )

    assert result["thresholds"] == {"BUY": 0.9, "SELL": 0.85}
    assert evaluation["selected"] == 6
    assert evaluation["accuracy"] == 1.0
    assert evaluation["macro_predicted_class_precision"] == 1.0


def test_temperature_fit_reduces_overconfident_validation_nll() -> None:
    logits = [[8.0, 0.0], [8.0, 0.0], [8.0, 0.0], [8.0, 0.0]]
    labels = [0, 0, 0, 1]
    temperature = fit_temperature(logits, labels)
    before = temperature_softmax(logits, 1.0)
    after = temperature_softmax(logits, temperature)

    assert temperature > 1.0
    assert after[0][0] < before[0][0]


def test_wilson_lower_bound_is_conservative() -> None:
    assert wilson_lower_bound(0, 0) == 0.0
    assert 0.85 < wilson_lower_bound(100, 100) < 1.0
    assert wilson_lower_bound(9, 10) < 0.9


def test_bootstrap_resamples_sources_instead_of_events() -> None:
    interval = source_cluster_accuracy_interval(
        labels=[0] * 100 + [1],
        decisions=[0] * 100 + [0],
        source_ids=["large"] * 100 + ["small"],
        samples=200,
        seed=7,
    )

    assert interval["sources"] == 2
    assert interval["events"] == 101
    assert interval["accuracy"] > 0.98
    assert interval["lower_95"] < 0.1
