from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from typing import Any


SIDES = ("BUY", "SELL")


def _clip_probability(value: float) -> float:
    return max(1e-9, min(1.0 - 1e-9, float(value)))


def temperature_softmax(
    logits: Sequence[Sequence[float]],
    temperature: float = 1.0,
) -> list[list[float]]:
    """Convert binary logits to probabilities with one held-out temperature."""

    scale = max(0.05, float(temperature))
    output: list[list[float]] = []
    for row in logits:
        values = [float(value) / scale for value in row[:2]]
        if len(values) < 2:
            values = (values + [0.0, 0.0])[:2]
        maximum = max(values)
        exponentials = [math.exp(value - maximum) for value in values]
        denominator = max(1e-12, sum(exponentials))
        output.append([value / denominator for value in exponentials])
    return output


def probability_nll(probabilities: Sequence[Sequence[float]], labels: Sequence[int]) -> float:
    if not labels:
        return 0.0
    losses = [
        -math.log(_clip_probability(float(probabilities[index][int(label)])))
        for index, label in enumerate(labels)
    ]
    return sum(losses) / len(losses)


def fit_temperature(
    logits: Sequence[Sequence[float]],
    labels: Sequence[int],
) -> float:
    """Fit temperature on validation data only with deterministic log-space search."""

    if not logits or not labels:
        return 1.0
    # A dense one-dimensional search is stable, dependency-free, and avoids
    # accidentally fitting calibration parameters on the locked test split.
    candidates = [math.exp(math.log(0.2) + index * (math.log(5.0) - math.log(0.2)) / 320.0) for index in range(321)]
    return min(
        candidates,
        key=lambda value: probability_nll(temperature_softmax(logits, value), labels),
    )


def wilson_lower_bound(correct: int, total: int, *, z: float = 1.96) -> float:
    if total <= 0:
        return 0.0
    count = float(total)
    proportion = max(0.0, min(1.0, float(correct) / count))
    z_squared = float(z) ** 2
    center = proportion + z_squared / (2.0 * count)
    margin = float(z) * math.sqrt((proportion * (1.0 - proportion) + z_squared / (4.0 * count)) / count)
    return max(0.0, (center - margin) / (1.0 + z_squared / count))


def _selected_indices(
    probabilities: Sequence[Sequence[float]],
    decisions: Sequence[int],
    thresholds: Mapping[str, Any],
) -> list[int]:
    selected: list[int] = []
    for index, decision in enumerate(decisions):
        side_index = int(decision)
        side = SIDES[side_index]
        threshold = float(thresholds.get(side, 1.01) or 1.01)
        confidence = float(probabilities[index][side_index])
        if confidence + 1e-12 >= threshold:
            selected.append(index)
    return selected


def evaluate_class_conditional_selection(
    probabilities: Sequence[Sequence[float]],
    labels: Sequence[int],
    decisions: Sequence[int],
    thresholds: Mapping[str, Any],
) -> dict[str, Any]:
    selected = _selected_indices(probabilities, decisions, thresholds)
    per_class: dict[str, dict[str, Any]] = {}
    precisions: list[float] = []
    for class_index, side in enumerate(SIDES):
        members = [index for index in selected if int(decisions[index]) == class_index]
        correct = sum(int(int(labels[index]) == class_index) for index in members)
        precision = correct / len(members) if members else 0.0
        if members:
            precisions.append(precision)
        per_class[side] = {
            "selected": len(members),
            "correct": correct,
            "precision": round(precision, 6),
            "wilson_lower_95": round(wilson_lower_bound(correct, len(members)), 6),
            "threshold": round(float(thresholds.get(side, 1.01) or 1.01), 6),
        }
    correct_total = sum(int(int(labels[index]) == int(decisions[index])) for index in selected)
    accuracy = correct_total / len(selected) if selected else 0.0
    return {
        "selected": len(selected),
        "total": len(labels),
        "coverage": round(len(selected) / len(labels), 6) if labels else 0.0,
        "accuracy": round(accuracy, 6),
        "macro_predicted_class_precision": round(sum(precisions) / len(precisions), 6) if precisions else 0.0,
        "wilson_lower_95": round(wilson_lower_bound(correct_total, len(selected)), 6),
        "per_class": per_class,
    }


def choose_class_conditional_thresholds(
    probabilities: Sequence[Sequence[float]],
    labels: Sequence[int],
    decisions: Sequence[int],
    *,
    target_precision: float = 0.85,
    minimum_predictions: int = 20,
) -> dict[str, Any]:
    """Choose maximum-coverage BUY/SELL thresholds on validation data only.

    Each predicted class has to meet the target independently. A class that
    cannot do so is disabled with a threshold above one; the other class cannot
    hide that failure inside a favorable aggregate accuracy.
    """

    thresholds: dict[str, float] = {}
    diagnostics: dict[str, dict[str, Any]] = {}
    required = max(1, int(minimum_predictions))
    target = max(0.5, min(1.0, float(target_precision)))
    for class_index, side in enumerate(SIDES):
        members = [
            (float(probabilities[index][class_index]), int(int(labels[index]) == class_index), index)
            for index in range(min(len(labels), len(decisions), len(probabilities)))
            if int(decisions[index]) == class_index
        ]
        candidates = sorted({confidence for confidence, _correct, _index in members}, reverse=True)
        best: tuple[int, float, int] | None = None
        for threshold in candidates:
            selected = [(confidence, correct) for confidence, correct, _index in members if confidence + 1e-12 >= threshold]
            total = len(selected)
            correct = sum(item[1] for item in selected)
            precision = correct / total if total else 0.0
            if total >= required and precision + 1e-12 >= target:
                candidate = (total, threshold, correct)
                if best is None or candidate[0] > best[0] or (candidate[0] == best[0] and candidate[1] > best[1]):
                    best = candidate
        if best is None:
            thresholds[side] = 1.01
            diagnostics[side] = {
                "enabled": False,
                "available_predictions": len(members),
                "reason": "validation_precision_target_not_met",
            }
            continue
        total, threshold, correct = best
        thresholds[side] = threshold
        diagnostics[side] = {
            "enabled": True,
            "selected": total,
            "correct": correct,
            "precision": round(correct / total, 6),
            "wilson_lower_95": round(wilson_lower_bound(correct, total), 6),
        }
    evaluation = evaluate_class_conditional_selection(probabilities, labels, decisions, thresholds)
    return {
        "target_precision": target,
        "minimum_predictions_per_class": required,
        "thresholds": {side: round(value, 6) for side, value in thresholds.items()},
        "classes": diagnostics,
        "validation": evaluation,
    }


def calibration_metrics(
    probabilities: Sequence[Sequence[float]],
    labels: Sequence[int],
    *,
    bins: int = 10,
) -> dict[str, Any]:
    if not labels:
        return {"nll": 0.0, "brier": 0.0, "classwise_ece": {side: 0.0 for side in SIDES}}
    count = min(len(probabilities), len(labels))
    nll = probability_nll(probabilities[:count], labels[:count])
    brier = sum(
        sum((float(probabilities[index][class_index]) - float(int(int(labels[index]) == class_index))) ** 2 for class_index in range(2))
        for index in range(count)
    ) / count
    classwise: dict[str, float] = {}
    bin_count = max(2, int(bins))
    for class_index, side in enumerate(SIDES):
        error = 0.0
        for bin_index in range(bin_count):
            low = bin_index / bin_count
            high = (bin_index + 1) / bin_count
            members = [
                index
                for index in range(count)
                if low <= float(probabilities[index][class_index])
                and (float(probabilities[index][class_index]) <= high if bin_index == bin_count - 1 else float(probabilities[index][class_index]) < high)
            ]
            if not members:
                continue
            confidence = sum(float(probabilities[index][class_index]) for index in members) / len(members)
            frequency = sum(int(int(labels[index]) == class_index) for index in members) / len(members)
            error += len(members) / count * abs(confidence - frequency)
        classwise[side] = round(error, 6)
    return {"nll": round(nll, 6), "brier": round(brier, 6), "classwise_ece": classwise}


def source_cluster_accuracy_interval(
    labels: Sequence[int],
    decisions: Sequence[int],
    source_ids: Sequence[str],
    *,
    selected: Sequence[bool] | None = None,
    samples: int = 1000,
    seed: int = 42,
) -> dict[str, Any]:
    """Bootstrap independent screenshot sources, never correlated event rows."""

    count = min(len(labels), len(decisions), len(source_ids))
    mask = list(selected) if selected is not None else [True] * count
    grouped: dict[str, list[int]] = {}
    for index in range(count):
        if index < len(mask) and bool(mask[index]):
            grouped.setdefault(str(source_ids[index]), []).append(index)
    sources = sorted(grouped)
    flat = [index for source in sources for index in grouped[source]]
    correct = sum(int(int(labels[index]) == int(decisions[index])) for index in flat)
    point = correct / len(flat) if flat else 0.0
    if not sources or samples <= 0:
        return {
            "accuracy": round(point, 6),
            "lower_95": round(point, 6),
            "upper_95": round(point, 6),
            "sources": len(sources),
            "events": len(flat),
        }
    generator = random.Random(int(seed))
    estimates: list[float] = []
    for _sample in range(int(samples)):
        draw = [sources[generator.randrange(len(sources))] for _ in range(len(sources))]
        indices = [index for source in draw for index in grouped[source]]
        if indices:
            estimates.append(
                sum(int(int(labels[index]) == int(decisions[index])) for index in indices) / len(indices)
            )
    estimates.sort()
    lower_index = max(0, min(len(estimates) - 1, int(math.floor(0.025 * (len(estimates) - 1)))))
    upper_index = max(0, min(len(estimates) - 1, int(math.ceil(0.975 * (len(estimates) - 1)))))
    return {
        "accuracy": round(point, 6),
        "lower_95": round(estimates[lower_index] if estimates else point, 6),
        "upper_95": round(estimates[upper_index] if estimates else point, 6),
        "sources": len(sources),
        "events": len(flat),
    }


__all__ = [
    "SIDES",
    "calibration_metrics",
    "choose_class_conditional_thresholds",
    "evaluate_class_conditional_selection",
    "fit_temperature",
    "probability_nll",
    "source_cluster_accuracy_interval",
    "temperature_softmax",
    "wilson_lower_bound",
]
