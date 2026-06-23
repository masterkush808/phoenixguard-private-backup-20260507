from __future__ import annotations
import pytest

import sys
from pathlib import Path
from typing import Any, Callable, Mapping, cast

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import torch

from phoenixguard.runtime.local_ensemble_runtime import LocalCVEnsembleRuntime


def _normalized_entropy(buy_prob: float, sell_prob: float) -> float:
    fn = cast(Callable[[float, float], float], getattr(LocalCVEnsembleRuntime, "_normalized_entropy"))
    return fn(buy_prob, sell_prob)


def _apply_adaptation_profile(
    row: dict[str, Any],
    name: str,
    adaptation_profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    fn = cast(
        Callable[[dict[str, Any], str, Mapping[str, Any] | None], dict[str, Any]],
        getattr(LocalCVEnsembleRuntime, "_apply_adaptation_profile"),
    )
    return fn(row, name, adaptation_profile)


def _resolve_requested_models(
    requested_models: list[str] | None,
    device: torch.device,
) -> list[str]:
    fn = cast(
        Callable[[list[str] | None, torch.device], list[str]],
        getattr(LocalCVEnsembleRuntime, "_resolve_requested_models"),
    )
    return fn(requested_models, device)


def _select_prediction_models(
    runtime: LocalCVEnsembleRuntime,
    routing_context: Mapping[str, Any] | None = None,
) -> tuple[list[str], dict[str, Any]]:
    method = cast(
        Callable[[Mapping[str, Any] | None], tuple[list[str], dict[str, Any]]],
        getattr(runtime, "_select_prediction_models"),
    )
    return method(routing_context)


def _aggregate_ensemble_view(
    model_outputs: Mapping[str, Mapping[str, Any]],
    *,
    route_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    fn = cast(
        Callable[..., dict[str, Any]],
        getattr(LocalCVEnsembleRuntime, "_aggregate_ensemble_view"),
    )
    return fn(model_outputs, route_summary=route_summary)


def _build_route_summary(
    model_outputs: Mapping[str, Mapping[str, Any]],
    *,
    routing_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    fn = cast(
        Callable[..., dict[str, Any]],
        getattr(LocalCVEnsembleRuntime, "_build_route_summary"),
    )
    return fn(model_outputs, routing_context=routing_context)


def _row(
    name: str,
    role: str,
    *,
    buy_prob: float,
    sell_prob: float,
    weight: float,
    buy_recall: float,
    sell_recall: float,
    decision_threshold: float = 0.5,
    runtime_calibration: dict[str, object] | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "role": role,
        "live_enabled": True,
        "buy_prob": buy_prob,
        "sell_prob": sell_prob,
        "predicted_label": "BUY" if buy_prob >= sell_prob else "SELL",
        "confidence": max(buy_prob, sell_prob),
        "margin": abs(buy_prob - sell_prob),
        "entropy": _normalized_entropy(buy_prob, sell_prob),
        "dynamic_weight": weight,
        "shadow_weight": 0.0,
        "decision_threshold": decision_threshold,
        "runtime_calibration": dict(runtime_calibration or {}),
        "metrics": {
            "buy_recall": buy_recall * 100.0,
            "sell_recall": sell_recall * 100.0,
        },
    }


def _runtime() -> LocalCVEnsembleRuntime:
    runtime = LocalCVEnsembleRuntime.__new__(LocalCVEnsembleRuntime)
    runtime.failed_models = {}
    return runtime


def test_router_promotes_buy_specialists_for_buy_bias() -> None:
    runtime = _runtime()
    prediction: dict[str, Any] = {
        "models": {
            "swav": _row("swav", "generalist", buy_prob=0.57, sell_prob=0.43, weight=1.10, buy_recall=0.70, sell_recall=0.73),
            "clip": _row("clip", "buy_specialist", buy_prob=0.73, sell_prob=0.27, weight=0.95, buy_recall=0.75, sell_recall=0.67),
            "byol": _row("byol", "buy_specialist", buy_prob=0.76, sell_prob=0.24, weight=0.88, buy_recall=0.80, sell_recall=0.60),
            "simclr": _row("simclr", "sell_specialist", buy_prob=0.42, sell_prob=0.58, weight=0.96, buy_recall=0.60, sell_recall=0.77),
            "dinov2": _row("dinov2", "structure_specialist", buy_prob=0.68, sell_prob=0.32, weight=0.90, buy_recall=0.70, sell_recall=0.70),
            "mobilenetv3": _row("mobilenetv3", "execution_specialist", buy_prob=0.66, sell_prob=0.34, weight=0.92, buy_recall=0.70, sell_recall=0.70),
        },
        "ensemble": {"failed_models": {}},
    }
    rerouted = runtime.reroute_prediction(
        prediction,
        routing_context={
            "chart_state": {
                "direction": "BUY",
                "direction_probability": 0.67,
                "projection_bias_direction": "BUY",
                "projection_bias_confidence": 0.79,
                "path_clarity": 0.72,
                "box_sequence_agreement": 0.69,
                "grounded_confidence": 0.76,
                "structure_trade_ready": True,
                "sequence_buy_pressure": 0.82,
                "sequence_sell_pressure": 0.19,
                "structure_buy_pressure": 0.78,
                "structure_sell_pressure": 0.20,
                "support_strength": 0.70,
                "resistance_strength": 0.14,
            },
            "sequence_state": {"sequence_model": {"buy_pressure": 0.82, "sell_pressure": 0.19, "uncertainty": 0.22}},
            "grounded_chart": {"grounded_confidence": 0.76, "structure_summary": {"buy_pressure": 0.78, "sell_pressure": 0.20, "support_strength": 0.70, "resistance_strength": 0.14, "structure_bias_confidence": 0.58}},
            "reasoning_trace": {"market_state": {"macro_trend": "BULL"}},
        },
    )

    assert rerouted["ensemble"]["router_direction"] == "BUY"
    assert rerouted["ensemble"]["predicted_label"] == "BUY"
    assert float(rerouted["models"]["clip"]["routing_factor"]) > float(rerouted["models"]["simclr"]["routing_factor"])
    assert float(rerouted["models"]["byol"]["routing_factor"]) > 1.0


def test_router_promotes_sell_specialist_for_sell_bias() -> None:
    runtime = _runtime()
    prediction: dict[str, Any] = {
        "models": {
            "swav": _row("swav", "generalist", buy_prob=0.49, sell_prob=0.51, weight=1.06, buy_recall=0.70, sell_recall=0.73),
            "clip": _row("clip", "buy_specialist", buy_prob=0.61, sell_prob=0.39, weight=0.94, buy_recall=0.75, sell_recall=0.67),
            "byol": _row("byol", "buy_specialist", buy_prob=0.64, sell_prob=0.36, weight=0.87, buy_recall=0.80, sell_recall=0.60),
            "simclr": _row("simclr", "sell_specialist", buy_prob=0.24, sell_prob=0.76, weight=0.98, buy_recall=0.60, sell_recall=0.77),
            "dinov2": _row("dinov2", "structure_specialist", buy_prob=0.34, sell_prob=0.66, weight=0.90, buy_recall=0.70, sell_recall=0.70),
            "mobilenetv3": _row("mobilenetv3", "execution_specialist", buy_prob=0.41, sell_prob=0.59, weight=0.92, buy_recall=0.70, sell_recall=0.70),
        },
        "ensemble": {"failed_models": {}},
    }
    rerouted = runtime.reroute_prediction(
        prediction,
        routing_context={
            "chart_state": {
                "direction": "SELL",
                "direction_probability": 0.63,
                "projection_bias_direction": "SELL",
                "projection_bias_confidence": 0.75,
                "path_clarity": 0.66,
                "box_sequence_agreement": 0.61,
                "grounded_confidence": 0.71,
                "structure_trade_ready": True,
                "sequence_buy_pressure": 0.18,
                "sequence_sell_pressure": 0.81,
                "structure_buy_pressure": 0.16,
                "structure_sell_pressure": 0.74,
                "support_strength": 0.12,
                "resistance_strength": 0.68,
            },
            "sequence_state": {"sequence_model": {"buy_pressure": 0.18, "sell_pressure": 0.81, "uncertainty": 0.26}},
            "grounded_chart": {"grounded_confidence": 0.71, "structure_summary": {"buy_pressure": 0.16, "sell_pressure": 0.74, "support_strength": 0.12, "resistance_strength": 0.68, "structure_bias_confidence": 0.55}},
            "reasoning_trace": {"market_state": {"macro_trend": "BEAR"}},
        },
    )

    assert rerouted["ensemble"]["router_direction"] == "SELL"
    assert rerouted["ensemble"]["predicted_label"] == "SELL"
    assert float(rerouted["models"]["simclr"]["routing_factor"]) > float(rerouted["models"]["clip"]["routing_factor"])
    assert float(rerouted["models"]["simclr"]["dynamic_weight"]) > float(prediction["models"]["simclr"]["dynamic_weight"])


def test_router_respects_bearish_council_projection_against_macro_bias() -> None:
    runtime = _runtime()
    prediction: dict[str, Any] = {
        "models": {
            "swav": _row("swav", "generalist", buy_prob=0.54, sell_prob=0.46, weight=1.04, buy_recall=0.70, sell_recall=0.73),
            "clip": _row("clip", "buy_specialist", buy_prob=0.63, sell_prob=0.37, weight=0.96, buy_recall=0.75, sell_recall=0.67),
            "byol": _row("byol", "buy_specialist", buy_prob=0.61, sell_prob=0.39, weight=0.89, buy_recall=0.80, sell_recall=0.60),
            "simclr": _row("simclr", "sell_specialist", buy_prob=0.31, sell_prob=0.69, weight=0.97, buy_recall=0.60, sell_recall=0.77),
            "dinov2": _row("dinov2", "structure_specialist", buy_prob=0.48, sell_prob=0.52, weight=0.91, buy_recall=0.70, sell_recall=0.70),
            "mobilenetv3": _row("mobilenetv3", "execution_specialist", buy_prob=0.52, sell_prob=0.48, weight=0.93, buy_recall=0.70, sell_recall=0.70),
        },
        "ensemble": {"failed_models": {}},
    }
    rerouted = runtime.reroute_prediction(
        prediction,
        routing_context={
            "chart_state": {
                "direction": "BUY",
                "direction_probability": 0.55,
                "projection_bias_direction": "SELL",
                "projection_bias_confidence": 0.82,
                "council_projection_direction": "SELL",
                "council_projection_confidence": 0.92,
                "council_current_box_direction": "SELL",
                "council_current_box_confidence": 0.88,
                "council_router_direction": "SELL",
                "council_router_strength": 0.61,
                "path_clarity": 0.68,
                "box_sequence_agreement": 0.72,
                "grounded_confidence": 0.42,
                "structure_trade_ready": False,
                "sequence_buy_pressure": 0.30,
                "sequence_sell_pressure": 0.66,
                "structure_buy_pressure": 0.90,
                "structure_sell_pressure": 0.30,
                "support_strength": 0.18,
                "resistance_strength": 0.20,
            },
            "sequence_state": {"sequence_model": {"buy_pressure": 0.30, "sell_pressure": 0.66, "uncertainty": 0.24}},
            "grounded_chart": {"grounded_confidence": 0.42, "structure_summary": {"buy_pressure": 0.90, "sell_pressure": 0.30, "support_strength": 0.18, "resistance_strength": 0.20, "structure_bias_confidence": 0.50}},
            "reasoning_trace": {"market_state": {"macro_trend": "BULL"}},
        },
    )

    assert rerouted["ensemble"]["router_direction"] == "SELL"
    assert float(rerouted["models"]["simclr"]["routing_factor"]) > float(rerouted["models"]["clip"]["routing_factor"])


def test_adaptation_profile_preserves_thresholded_sell_vote() -> None:
    row = {
        "buy_prob": 0.53,
        "sell_prob": 0.47,
        "predicted_label": "SELL",
        "decision_threshold": 0.55,
        "dynamic_weight": 1.0,
    }

    adjusted = _apply_adaptation_profile(
        row,
        "swav",
        {
            "confidence_scale": 1.0,
            "direction_bias": {"BUY": 0.0, "SELL": 0.0},
            "model_weight_biases": {},
        },
    )

    assert adjusted["predicted_label"] == "SELL"
    assert abs(float(adjusted["threshold_gap"]) - 0.02) < 1e-6


def test_cpu_runtime_profile_prefers_lightweight_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHOENIXGUARD_LOCAL_ENSEMBLE_MODELS", raising=False)
    models = _resolve_requested_models(None, torch.device("cpu"))
    assert models == list(LocalCVEnsembleRuntime.CPU_DEFAULT_MODELS)


def test_runtime_profile_honors_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIXGUARD_LOCAL_ENSEMBLE_MODELS", "clip, swav, invalid, clip")
    models = _resolve_requested_models(None, torch.device("cpu"))
    assert models == ["clip", "swav"]


def test_cpu_selection_prioritizes_sell_specialist_before_always_on_models() -> None:
    runtime = _runtime()
    runtime.compute_device = torch.device("cpu")
    runtime.max_loaded_models = 2
    runtime.loaded_model_names = ["mobilenetv3", "simclr", "swav"]
    runtime.model_info = cast(Any, {name: object() for name in runtime.loaded_model_names})

    selected, summary = _select_prediction_models(
        runtime,
        {
            "chart_state": {
                "projection_bias_direction": "SELL",
                "direction": "SELL",
                "direction_probability": 0.72,
                "projection_bias_confidence": 0.78,
                "council_projection_direction": "SELL",
                "council_projection_confidence": 0.80,
                "council_current_box_direction": "SELL",
                "council_current_box_confidence": 0.70,
                "council_router_direction": "SELL",
                "council_router_strength": 0.75,
                "path_clarity": 0.61,
                "box_sequence_agreement": 0.58,
                "grounded_confidence": 0.52,
                "structure_trade_ready": True,
                "macro_trend": "BEAR",
                "sequence_buy_pressure": 0.22,
                "sequence_sell_pressure": 0.74,
                "structure_buy_pressure": 0.18,
                "structure_sell_pressure": 0.69,
                "support_strength": 0.20,
                "resistance_strength": 0.66,
                "structure_bias_confidence": 0.71,
                "sequence_uncertainty": 0.24,
            },
            "sequence_state": {"path_clarity": 0.61, "box_sequence_agreement": 0.58},
            "grounded_chart": {"grounded_confidence": 0.52},
            "memory_summary": {"ambiguity": 0.12},
            "reasoning_trace": {"market_state": {"macro_trend": "BEAR"}},
        }
    )

    assert selected == ["simclr", "swav"]
    assert summary["reason"] in {"sell_route", "uncertainty_route"}
    assert "mobilenetv3" in summary["skipped_models"]


def test_sell_route_runtime_calibration_uses_threshold_centered_support() -> None:
    prediction: dict[str, Any] = {
        "models": {
            "simclr": _row(
                "simclr",
                "sell_specialist",
                buy_prob=0.56,
                sell_prob=0.44,
                weight=1.30,
                buy_recall=0.55,
                sell_recall=0.77,
                decision_threshold=0.518,
                runtime_calibration={
                    "route_support_modes": {"SELL": "threshold_centered"},
                    "route_decision_thresholds": {"SELL": 0.59},
                },
            ),
            "swav": _row(
                "swav",
                "generalist",
                buy_prob=0.50,
                sell_prob=0.50,
                weight=0.80,
                buy_recall=0.70,
                sell_recall=0.73,
                decision_threshold=0.520,
                runtime_calibration={
                    "route_support_modes": {"SELL": "threshold_centered"},
                    "route_decision_thresholds": {"SELL": 0.47},
                },
            ),
        },
        "ensemble": {"failed_models": {}},
    }

    buy_route = _aggregate_ensemble_view(
        prediction["models"],
        route_summary={"route_direction": "BUY", "route_strength": 0.74},
    )
    sell_route = _aggregate_ensemble_view(
        prediction["models"],
        route_summary={"route_direction": "SELL", "route_strength": 0.74},
    )

    assert buy_route["predicted_label"] == "BUY"
    assert sell_route["predicted_label"] == "SELL"
    assert float(sell_route["buy_prob"]) < float(buy_route["buy_prob"])


def test_route_summary_promotes_macro_aligned_countertrend_reclaim() -> None:
    route_summary = _build_route_summary(
        {
            "swav": _row(
                "swav",
                "generalist",
                buy_prob=0.49,
                sell_prob=0.51,
                weight=1.00,
                buy_recall=0.70,
                sell_recall=0.73,
            ),
            "clip": _row(
                "clip",
                "buy_specialist",
                buy_prob=0.56,
                sell_prob=0.44,
                weight=0.96,
                buy_recall=0.75,
                sell_recall=0.67,
            ),
            "simclr": _row(
                "simclr",
                "sell_specialist",
                buy_prob=0.41,
                sell_prob=0.59,
                weight=0.98,
                buy_recall=0.55,
                sell_recall=0.77,
            ),
        },
        routing_context={
            "chart_state": {
                "direction": "SELL",
                "direction_probability": 0.55,
                "projection_bias_direction": "BUY",
                "projection_bias_confidence": 0.68,
                "council_projection_direction": "BUY",
                "council_projection_confidence": 0.28,
                "path_clarity": 0.63,
                "box_sequence_agreement": 0.59,
                "grounded_confidence": 0.56,
                "structure_trade_ready": False,
                "macro_trend": "BULL",
                "local_phase": "counter_trend_pullback",
                "momentum_bias": "bullish",
                "sequence_buy_pressure": 0.22,
                "sequence_sell_pressure": 0.58,
                "structure_buy_pressure": 0.18,
                "structure_sell_pressure": 0.44,
                "support_strength": 0.47,
                "resistance_strength": 0.35,
                "structure_bias_confidence": 0.34,
            },
            "sequence_state": {"sequence_model": {"buy_pressure": 0.22, "sell_pressure": 0.58, "uncertainty": 0.18}},
            "grounded_chart": {
                "grounded_confidence": 0.56,
                "structure_summary": {
                    "buy_pressure": 0.18,
                    "sell_pressure": 0.44,
                    "support_strength": 0.47,
                    "resistance_strength": 0.35,
                    "structure_bias_confidence": 0.34,
                },
            },
            "reasoning_trace": {"market_state": {"macro_trend": "BULL", "local_phase": "counter_trend_pullback"}},
        },
    )

    assert route_summary["route_direction"] == "BUY"
    assert float(route_summary["countertrend_reclaim_bonus"]) > 0.0
    assert route_summary["countertrend_reclaim_direction"] == "BUY"
    assert float(route_summary["buy_support"]) > float(route_summary["sell_support"])


def test_route_weight_multiplier_reweights_counter_route_models() -> None:
    base_prediction: dict[str, Any] = {
        "models": {
            "clip": _row(
                "clip",
                "buy_specialist",
                buy_prob=0.70,
                sell_prob=0.30,
                weight=1.0,
                buy_recall=0.75,
                sell_recall=0.67,
                decision_threshold=0.56,
                runtime_calibration={
                    "route_support_modes": {"SELL": "threshold_centered"},
                    "route_decision_thresholds": {"SELL": 0.56},
                },
            ),
            "simclr": _row(
                "simclr",
                "sell_specialist",
                buy_prob=0.52,
                sell_prob=0.48,
                weight=1.0,
                buy_recall=0.55,
                sell_recall=0.77,
                decision_threshold=0.56,
                runtime_calibration={
                    "route_support_modes": {"SELL": "threshold_centered"},
                    "route_decision_thresholds": {"SELL": 0.56},
                },
            ),
        },
        "ensemble": {"failed_models": {}},
    }
    calibrated_prediction: dict[str, Any] = {
        "models": {
            "clip": _row(
                "clip",
                "buy_specialist",
                buy_prob=0.70,
                sell_prob=0.30,
                weight=1.0,
                buy_recall=0.75,
                sell_recall=0.67,
                decision_threshold=0.56,
                runtime_calibration={
                    "route_support_modes": {"SELL": "threshold_centered"},
                    "route_decision_thresholds": {"SELL": 0.56},
                    "route_weight_multipliers": {"SELL": 0.30},
                },
            ),
            "simclr": _row(
                "simclr",
                "sell_specialist",
                buy_prob=0.52,
                sell_prob=0.48,
                weight=1.0,
                buy_recall=0.55,
                sell_recall=0.77,
                decision_threshold=0.56,
                runtime_calibration={
                    "route_support_modes": {"SELL": "threshold_centered"},
                    "route_decision_thresholds": {"SELL": 0.56},
                    "route_weight_multipliers": {"SELL": 1.50},
                },
            ),
        },
        "ensemble": {"failed_models": {}},
    }

    unweighted = _aggregate_ensemble_view(
        base_prediction["models"],
        route_summary={"route_direction": "SELL", "route_strength": 0.74},
    )
    weighted = _aggregate_ensemble_view(
        calibrated_prediction["models"],
        route_summary={"route_direction": "SELL", "route_strength": 0.74},
    )

    assert unweighted["predicted_label"] == "BUY"
    assert weighted["predicted_label"] == "SELL"
    assert float(calibrated_prediction["models"]["clip"]["effective_dynamic_weight"]) < float(
        calibrated_prediction["models"]["clip"]["dynamic_weight"]
    )
    assert float(calibrated_prediction["models"]["simclr"]["effective_dynamic_weight"]) > float(
        calibrated_prediction["models"]["simclr"]["dynamic_weight"]
    )
