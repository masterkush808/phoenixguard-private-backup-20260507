from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import torch

from phoenixguard.runtime.local_ensemble_runtime import LocalCVEnsembleRuntime


def _row(
    name: str,
    role: str,
    *,
    buy_prob: float,
    sell_prob: float,
    weight: float,
    buy_recall: float,
    sell_recall: float,
) -> dict[str, float | str | bool | dict[str, float]]:
    return {
        "name": name,
        "role": role,
        "live_enabled": True,
        "buy_prob": buy_prob,
        "sell_prob": sell_prob,
        "predicted_label": "BUY" if buy_prob >= sell_prob else "SELL",
        "confidence": max(buy_prob, sell_prob),
        "margin": abs(buy_prob - sell_prob),
        "entropy": LocalCVEnsembleRuntime._normalized_entropy(buy_prob, sell_prob),
        "dynamic_weight": weight,
        "shadow_weight": 0.0,
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
    prediction = {
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
    prediction = {
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


def test_cpu_runtime_profile_prefers_lightweight_models(monkeypatch) -> None:
    monkeypatch.delenv("PHOENIXGUARD_LOCAL_ENSEMBLE_MODELS", raising=False)
    models = LocalCVEnsembleRuntime._resolve_requested_models(None, torch.device("cpu"))
    assert models == list(LocalCVEnsembleRuntime.CPU_DEFAULT_MODELS)


def test_runtime_profile_honors_env_override(monkeypatch) -> None:
    monkeypatch.setenv("PHOENIXGUARD_LOCAL_ENSEMBLE_MODELS", "clip, swav, invalid, clip")
    models = LocalCVEnsembleRuntime._resolve_requested_models(None, torch.device("cpu"))
    assert models == ["clip", "swav"]
