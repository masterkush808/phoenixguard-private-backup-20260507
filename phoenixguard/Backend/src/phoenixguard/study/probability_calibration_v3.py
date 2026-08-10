from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

import numpy as np
from numpy.typing import NDArray
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression


PROBABILITY_CALIBRATION_SCHEMA_VERSION = "PG_PROBABILITY_CALIBRATION_V3"


def _clip(values: Sequence[float] | NDArray[Any]) -> NDArray[Any]:
    return np.clip(np.asarray(values, dtype=np.float64), 1e-6, 1.0 - 1e-6)


def calibration_metrics_v3(
    y_true: Sequence[int] | NDArray[Any],
    probabilities: Sequence[float] | NDArray[Any],
    *,
    bins: int = 10,
) -> dict[str, Any]:
    y = np.asarray(y_true, dtype=np.float64)
    p = _clip(probabilities)
    if y.size == 0:
        return {
            "brier": 0.0,
            "log_loss": 0.0,
            "ece": 0.0,
            "accuracy": 0.0,
            "rows": 0,
            "reliability": [],
        }
    brier = float(np.mean((p - y) ** 2))
    log_loss = float(-np.mean(y * np.log(p) + (1.0 - y) * np.log(1.0 - p)))
    reliability: list[dict[str, Any]] = []
    ece = 0.0
    row_count = len(y)
    for index in range(max(1, int(bins))):
        low = index / bins
        high = (index + 1) / bins
        mask = (p >= low) & (p < high if index + 1 < bins else p <= high)
        count = int(mask.sum())
        if not count:
            continue
        confidence = float(p[mask].mean())
        accuracy = float(y[mask].mean())
        ece += (count / row_count) * abs(confidence - accuracy)
        reliability.append(
            {
                "low": round(low, 4),
                "high": round(high, 4),
                "count": count,
                "confidence": round(confidence, 6),
                "accuracy": round(accuracy, 6),
            }
        )
    correct = int(np.count_nonzero((p >= 0.5) == (y >= 0.5)))
    return {
        "brier": round(brier, 6),
        "log_loss": round(log_loss, 6),
        "ece": round(float(ece), 6),
        "accuracy": round(correct / row_count, 6),
        "rows": int(y.size),
        "reliability": reliability,
    }


@dataclass
class ProbabilityCalibratorV3:
    method: str = "identity"
    model: Any = None

    def predict(
        self,
        probabilities: Sequence[float] | NDArray[Any],
    ) -> NDArray[Any]:
        raw = _clip(probabilities)
        if self.method == "platt" and self.model is not None:
            logits = np.log(raw / (1.0 - raw)).reshape(-1, 1)
            return _clip(self.model.predict_proba(logits)[:, 1])
        if self.method == "isotonic" and self.model is not None:
            return _clip(self.model.predict(raw))
        if self.method == "temperature" and self.model is not None:
            temperature = max(0.05, float(self.model))
            logits = np.log(raw / (1.0 - raw)) / temperature
            return _clip(1.0 / (1.0 + np.exp(-logits)))
        return raw


def fit_calibrators_v3(
    y_true: Sequence[int] | NDArray[Any],
    raw_probabilities: Sequence[float] | NDArray[Any],
) -> dict[str, ProbabilityCalibratorV3]:
    y = np.asarray(y_true, dtype=np.int64)
    raw = _clip(raw_probabilities)
    result = {"identity": ProbabilityCalibratorV3()}
    if y.size < 20 or np.unique(y).size < 2:
        return result
    logits = np.log(raw / (1.0 - raw)).reshape(-1, 1)
    platt = LogisticRegression(C=0.5, max_iter=500, random_state=17)
    platt.fit(logits, y)
    result["platt"] = ProbabilityCalibratorV3("platt", platt)
    isotonic = IsotonicRegression(out_of_bounds="clip", y_min=0.001, y_max=0.999)
    isotonic.fit(raw, y)
    result["isotonic"] = ProbabilityCalibratorV3("isotonic", isotonic)
    best_temperature = 1.0
    best_loss = float("inf")
    raw_logits = np.log(raw / (1.0 - raw))
    for temperature in np.linspace(0.5, 3.0, 51):
        candidate = 1.0 / (1.0 + np.exp(-(raw_logits / temperature)))
        loss = float(
            -np.mean(
                y * np.log(_clip(candidate))
                + (1 - y) * np.log(_clip(1.0 - candidate))
            )
        )
        if loss < best_loss:
            best_loss = loss
            best_temperature = float(temperature)
    result["temperature"] = ProbabilityCalibratorV3("temperature", best_temperature)
    return result


def select_calibrator_v3(
    calibrators: dict[str, ProbabilityCalibratorV3],
    y_true: Sequence[int] | NDArray[Any],
    raw_probabilities: Sequence[float] | NDArray[Any],
) -> tuple[ProbabilityCalibratorV3, dict[str, Any]]:
    scored: dict[str, Any] = {}
    winner = calibrators.get("identity", ProbabilityCalibratorV3())
    winner_brier = float("inf")
    for name, calibrator in calibrators.items():
        metrics = calibration_metrics_v3(y_true, calibrator.predict(raw_probabilities))
        scored[name] = metrics
        if float(metrics["brier"]) < winner_brier:
            winner = calibrator
            winner_brier = float(metrics["brier"])
    return winner, {
        "schema_version": PROBABILITY_CALIBRATION_SCHEMA_VERSION,
        "selected_method": winner.method,
        "candidates": scored,
        "fitted_on_oof_calibration_families_only": True,
    }


def select_confidence_threshold_v3(
    y_true: Sequence[int] | NDArray[Any],
    probabilities: Sequence[float] | NDArray[Any],
    *,
    minimum_precision: float = 0.70,
    minimum_coverage: float = 0.20,
) -> dict[str, Any]:
    y = np.asarray(y_true, dtype=np.int64)
    p = _clip(probabilities)
    best_objective: dict[str, Any] | None = None
    best_fallback: dict[str, Any] | None = None
    for threshold in np.linspace(0.50, 0.95, 91):
        selected = p >= threshold
        count = int(selected.sum())
        coverage = count / max(1, int(y.size))
        precision = float(y[selected].mean()) if count else 0.0
        row = {
            "threshold": round(float(threshold), 4),
            "precision": round(precision, 6),
            "coverage": round(coverage, 6),
            "selected": count,
            "eligible": int(y.size),
            "meets_objective": bool(
                precision >= minimum_precision and coverage >= minimum_coverage
            ),
        }
        if row["meets_objective"]:
            if best_objective is None or threshold < float(best_objective["threshold"]):
                best_objective = row
        else:
            utility = precision + min(coverage, minimum_coverage)
            if row["coverage"] >= minimum_coverage and (
                best_fallback is None
                or utility > float(best_fallback.get("_utility", -1.0))
            ):
                row["_utility"] = utility
                best_fallback = row
    best = best_objective or best_fallback
    if best is None:
        best = {
            "threshold": 0.95,
            "precision": 0.0,
            "coverage": 0.0,
            "selected": 0,
            "eligible": int(y.size),
            "meets_objective": False,
        }
    best.pop("_utility", None)
    best["selected_on_calibration_families_only"] = True
    return best
