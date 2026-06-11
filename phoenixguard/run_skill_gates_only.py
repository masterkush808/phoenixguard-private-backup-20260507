from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

import numpy as np
from PIL import Image

from adaptive_runtime import _build_heuristic_grounded_chart
from config import MODELS, RUNTIME
from cv_module import CVPatternDetector
from ensemble import TransitionSummary
from main import (
    _build_chart_state,
    _build_next_box_hypotheses,
    _build_sequence_model_summary,
    _build_transition_summary,
    _derive_proxy_price_series,
    _ensemble_base_probs,
    _estimate_implied_move_pct,
    _extract_chart_structure,
    _extract_latest_signal_state,
    _fuse_transition_probabilities,
)
from preprocess import extract_price_floats, indicator_regex_filter, load_any_file_as_image
from regression_module import ForecastRouter
from skill_gates import CurriculumGates


class _NullLogger:
    def info(self, *args: object, **kwargs: object) -> None:
        return None

    def warning(self, *args: object, **kwargs: object) -> None:
        return None

    def error(self, *args: object, **kwargs: object) -> None:
        return None

    def exception(self, *args: object, **kwargs: object) -> None:
        return None


def _clip01(value: Any, default: float = 0.0) -> float:
    try:
        return float(np.clip(float(value), 0.0, 1.0))
    except (TypeError, ValueError):
        return float(np.clip(default, 0.0, 1.0))


def _json_default(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (np.integer, np.floating)):
        return value.item()
    return str(value)


def _load_annotation_text(annotation_text: str, annotation_file: str) -> str:
    parts: list[str] = []
    if annotation_text.strip():
        parts.append(annotation_text.strip())
    if annotation_file.strip():
        annotation_path = Path(annotation_file).expanduser().resolve()
        if not annotation_path.exists():
            raise FileNotFoundError(f"Annotation file not found: {annotation_path}")
        parts.append(annotation_path.read_text(encoding="utf-8"))
    return "\n".join(part for part in parts if part.strip())


def _gate_summary(details: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    total = len(details)
    passing = sum(1 for gate in details if bool(gate.get("pass_fail", False)))
    failing = [str(gate.get("name", "")) for gate in details if not bool(gate.get("pass_fail", False))]
    return {
        "passing": int(passing),
        "total": int(total),
        "failing": failing,
    }


def _new_cv_proxy(logger: _NullLogger) -> CVPatternDetector:
    proxy = CVPatternDetector.__new__(CVPatternDetector)
    proxy.logger = logger
    proxy.model_name = "isolated_heuristic"
    proxy.model = None
    proxy.strict_model_only = False
    proxy.use_hf_endpoint = False
    proxy.hf_model_id = None
    proxy.hf_client = None
    proxy.hf_remote_url = ""
    proxy.memory_clf = None
    proxy.latest_dir_clf = None
    proxy.next_dir_clf = None
    proxy.wick_dom_clf = None
    proxy.move_bucket_clf = None
    proxy.seq_dir_clf = None
    proxy.macro_trend_clf = None
    proxy.local_phase_clf = None
    proxy.phase_risk_clf = None
    proxy.intent_next_clf = None
    proxy.global_scaler = None
    proxy.latest_scaler = None
    proxy.seq_scaler = None
    proxy.local_scaler = None
    proxy.macro_scaler = None
    proxy.intent_scaler = None
    proxy.memory_clf_meta = {}
    proxy.taxonomy_label_maps = {}
    proxy.ensemble_cv = None
    return proxy


def _build_heuristic_detections(
    cv_proxy: CVPatternDetector,
    image_rgb: Image.Image,
    chart_geometry: Mapping[str, Any],
    sequence_state: Mapping[str, Any],
    sequence_model: Mapping[str, Any],
) -> list[dict[str, Any]]:
    detections = list(cv_proxy._heuristic_candle_detect(image_rgb))
    latest_bbox = cast(list[float], chart_geometry.get("latest_candle_bbox", [0.0, 0.0, 0.0, 0.0]))
    geom_conf = _clip01(chart_geometry.get("geometry_confidence", 0.0), 0.0)
    body_pct = _clip01(chart_geometry.get("body_height_pct", 0.0), 0.0)
    latest_is_buy = _clip01(chart_geometry.get("candle_color_green", 0.0), 0.0) >= 0.5
    latest_conf = float(np.clip(0.32 + 0.48 * geom_conf + 0.20 * body_pct, 0.0, 1.0))
    detections.append(
        {
            "pattern": "latest_candle_buy" if latest_is_buy else "latest_candle_sell",
            "confidence": latest_conf,
            "bbox": latest_bbox,
            "source": "isolated_local",
        }
    )

    primary_next_box = cast(dict[str, Any], sequence_state.get("primary_next_box", {}))
    next_direction = str(primary_next_box.get("direction", sequence_model.get("direction", "HOLD"))).upper()
    next_bbox = cast(list[float], primary_next_box.get("bbox", latest_bbox))
    next_conf = float(
        np.clip(
            0.24
            + 0.46 * _clip01(primary_next_box.get("confidence", sequence_state.get("path_clarity", 0.0)), 0.0)
            + 0.30 * _clip01(sequence_state.get("path_clarity", 0.0), 0.0),
            0.0,
            1.0,
        )
    )
    if next_direction in {"BUY", "SELL"}:
        detections.append(
            {
                "pattern": f"next_candle_{next_direction.lower()}",
                "confidence": next_conf,
                "bbox": next_bbox,
                "source": "isolated_local",
            }
        )

    lower_wick = _clip01(chart_geometry.get("lower_wick_pct", 0.0), 0.0)
    upper_wick = _clip01(chart_geometry.get("upper_wick_pct", 0.0), 0.0)
    wick_pattern = "wick_dominance_lower" if lower_wick >= upper_wick else "wick_dominance_upper"
    wick_conf = float(np.clip(max(lower_wick, upper_wick) * (0.60 + 0.40 * geom_conf), 0.0, 1.0))
    detections.append(
        {
            "pattern": wick_pattern,
            "confidence": wick_conf,
            "bbox": latest_bbox,
            "source": "isolated_local",
        }
    )

    move_strength = float(
        np.clip(
            0.40 * body_pct
            + 0.35 * _clip01(sequence_state.get("path_clarity", 0.0), 0.0)
            + 0.25 * max(
                _clip01(sequence_model.get("buy_pressure", 0.0), 0.0),
                _clip01(sequence_model.get("sell_pressure", 0.0), 0.0),
            ),
            0.0,
            1.0,
        )
    )
    move_pattern = "next_move_small"
    if move_strength >= 0.60:
        move_pattern = "next_move_large"
    elif move_strength >= 0.34:
        move_pattern = "next_move_medium"
    detections.append(
        {
            "pattern": move_pattern,
            "confidence": float(np.clip(0.40 + 0.52 * move_strength, 0.0, 1.0)),
            "bbox": next_bbox if len(next_bbox) == 4 else latest_bbox,
            "source": "isolated_local",
        }
    )

    seq_direction = str(sequence_model.get("direction", "HOLD")).upper()
    seq_conf = float(
        np.clip(
            max(
                _clip01(sequence_model.get("buy_pressure", 0.0), 0.0),
                _clip01(sequence_model.get("sell_pressure", 0.0), 0.0),
            ),
            0.0,
            1.0,
        )
    )
    if seq_direction in {"BUY", "SELL"} and seq_conf >= 0.35:
        detections.append(
            {
                "pattern": "buy_memory_bias" if seq_direction == "BUY" else "sell_memory_bias",
                "confidence": seq_conf,
                "bbox": [0.0, 0.0, float(image_rgb.width), float(image_rgb.height)],
                "source": "isolated_local",
            }
        )

    detections.append(
        {
            "pattern": "latest_parse_quality",
            "confidence": geom_conf,
            "bbox": latest_bbox,
            "source": "isolated_local",
        }
    )

    if _clip01(sequence_state.get("has_active_consolidation", False), 0.0) >= 0.5:
        detections.append(
            {
                "pattern": "consolidation",
                "confidence": float(np.clip(0.42 + 0.45 * _clip01(sequence_state.get("recent_box_consolidation", 0.0), 0.0), 0.0, 1.0)),
                "bbox": cast(list[float], cast(dict[str, Any], sequence_state.get("current_box", {})).get("bbox", latest_bbox)),
                "source": "isolated_local",
            }
        )
    if str(primary_next_box.get("box_type", "")).lower() == "impulse" and next_direction in {"BUY", "SELL"}:
        detections.append(
            {
                "pattern": "breakout",
                "confidence": float(np.clip(0.38 + 0.52 * _clip01(primary_next_box.get("confidence", 0.0), 0.0), 0.0, 1.0)),
                "bbox": next_bbox if len(next_bbox) == 4 else latest_bbox,
                "source": "isolated_local",
            }
        )
    return detections


def _build_heuristic_local_ensemble(
    chart_geometry: Mapping[str, Any],
    sequence_state: Mapping[str, Any],
    grounded_chart: Mapping[str, Any],
    sequence_model: Mapping[str, Any],
) -> dict[str, Any]:
    structure = cast(dict[str, Any], grounded_chart.get("structure_summary", {}))
    primary_next_box = cast(dict[str, Any], sequence_state.get("primary_next_box", {}))
    next_direction = str(primary_next_box.get("direction", sequence_model.get("direction", "HOLD"))).upper()
    seq_buy = _clip01(sequence_model.get("buy_pressure", 0.0), 0.0)
    seq_sell = _clip01(sequence_model.get("sell_pressure", 0.0), 0.0)
    seq_uncertainty = _clip01(sequence_model.get("uncertainty", 0.0), 0.0)
    path_clarity = _clip01(sequence_state.get("path_clarity", 0.0), 0.0)
    geom_conf = _clip01(chart_geometry.get("geometry_confidence", 0.0), 0.0)
    support_strength = _clip01(structure.get("support_strength", 0.0), 0.0)
    resistance_strength = _clip01(structure.get("resistance_strength", 0.0), 0.0)
    structure_buy = _clip01(structure.get("buy_pressure", 0.0), 0.0)
    structure_sell = _clip01(structure.get("sell_pressure", 0.0), 0.0)
    breakout_strength = _clip01(structure.get("breakout_strength", 0.0), 0.0)
    fakeout_probability = _clip01(sequence_state.get("fakeout_probability", 0.0), 0.0)

    buy_score = float(
        np.clip(
            0.34 * seq_buy
            + 0.24 * structure_buy
            + 0.12 * support_strength
            + 0.12 * breakout_strength * float(next_direction == "BUY")
            + 0.10 * path_clarity
            + 0.08 * geom_conf,
            0.0,
            1.0,
        )
    )
    sell_score = float(
        np.clip(
            0.34 * seq_sell
            + 0.24 * structure_sell
            + 0.12 * resistance_strength
            + 0.12 * breakout_strength * float(next_direction == "SELL")
            + 0.10 * path_clarity
            + 0.08 * geom_conf,
            0.0,
            1.0,
        )
    )
    directional_total = max(buy_score + sell_score, 1e-8)
    hold_prob = float(
        np.clip(
            0.08
            + 0.30 * seq_uncertainty
            + 0.18 * (1.0 - path_clarity)
            + 0.18 * fakeout_probability
            + 0.12 * (1.0 - geom_conf),
            0.05,
            0.56,
        )
    )
    remaining = max(1.0 - hold_prob, 1e-8)
    buy_prob = float(remaining * buy_score / directional_total)
    sell_prob = float(remaining * sell_score / directional_total)
    total = max(buy_prob + sell_prob + hold_prob, 1e-8)
    buy_prob /= total
    sell_prob /= total
    hold_prob /= total
    predicted_label = "BUY" if buy_prob >= sell_prob else "SELL"
    confidence = max(buy_prob, sell_prob)
    disagreement = float(np.clip(0.18 + 0.42 * seq_uncertainty, 0.0, 1.0))
    champion = "heuristic_sequence" if str(sequence_model.get("direction", "HOLD")).upper() == predicted_label else "heuristic_structure"
    confirmer = "heuristic_structure" if champion == "heuristic_sequence" else "heuristic_sequence"
    return {
        "models": {
            "heuristic_sequence": {
                "name": "heuristic_sequence",
                "role": "generalist",
                "live_enabled": True,
                "buy_prob": seq_buy,
                "sell_prob": seq_sell,
                "predicted_label": str(sequence_model.get("direction", "HOLD")).upper(),
                "confidence": max(seq_buy, seq_sell),
                "dynamic_weight": 1.0,
            },
            "heuristic_structure": {
                "name": "heuristic_structure",
                "role": "structure_specialist",
                "live_enabled": True,
                "buy_prob": structure_buy,
                "sell_prob": structure_sell,
                "predicted_label": "BUY" if structure_buy >= structure_sell else "SELL",
                "confidence": max(structure_buy, structure_sell),
                "dynamic_weight": 0.92,
            },
        },
        "ensemble": {
            "buy_prob": buy_prob,
            "sell_prob": sell_prob,
            "hold_prob": hold_prob,
            "predicted_label": predicted_label,
            "confidence": confidence,
            "margin": abs(buy_prob - sell_prob),
            "entropy": float(-np.sum(np.asarray([buy_prob, sell_prob], dtype=np.float64) * np.log(np.clip(np.asarray([buy_prob, sell_prob], dtype=np.float64), 1e-8, 1.0))) / np.log(2.0)),
            "disagreement": disagreement,
            "consensus_ratio": float(np.clip(1.0 - disagreement, 0.0, 1.0)),
            "vote_counts": {
                "BUY": int(buy_prob >= sell_prob),
                "SELL": int(sell_prob > buy_prob),
            },
            "champion_model": champion,
            "confirmer_model": confirmer,
            "live_models": ["heuristic_sequence", "heuristic_structure"],
            "shadow_models": [],
            "failed_models": {},
            "router_mode": "isolated_local",
        },
    }


def _isolated_gate_result(image_path: Path, annotation_text: str) -> dict[str, Any]:
    logger = _NullLogger()
    cv_proxy = _new_cv_proxy(logger)
    forecast_engine = ForecastRouter(
        model_name=MODELS.chronos_model,
        logger=logger,
        max_interval_pct=RUNTIME.conformal_max_interval_pct,
    )
    gates_engine = CurriculumGates(logger)

    img_raw, meta = load_any_file_as_image(str(image_path))
    image_rgb = img_raw.convert("RGB")

    chart_geometry, sequence_state = _extract_chart_structure(cv_proxy, image_rgb)
    sequence_state["sequence_model"] = _build_sequence_model_summary(sequence_state, chart_geometry, market_state={})
    detections = _build_heuristic_detections(
        cv_proxy,
        image_rgb,
        chart_geometry,
        sequence_state,
        cast(dict[str, Any], sequence_state.get("sequence_model", {})),
    )
    reasoning_trace = cv_proxy.build_reasoning_trace(detections, image_rgb=image_rgb)
    sequence_state["sequence_model"] = _build_sequence_model_summary(
        sequence_state,
        chart_geometry,
        market_state=cast(dict[str, Any], reasoning_trace.get("market_state", {})),
    )
    grounded_chart = _build_heuristic_grounded_chart(
        image_rgb,
        detections=detections,
        chart_geometry=chart_geometry,
        sequence_state=sequence_state,
    )
    local_ensemble = _build_heuristic_local_ensemble(
        chart_geometry,
        sequence_state,
        grounded_chart,
        cast(dict[str, Any], sequence_state.get("sequence_model", {})),
    )
    chart_state = _build_chart_state(
        detections=detections,
        local_ensemble=local_ensemble,
        reasoning_trace=reasoning_trace,
        chart_geometry=chart_geometry,
        sequence_state=sequence_state,
        grounded_chart=grounded_chart,
    )

    fused_transition_probabilities = _fuse_transition_probabilities(
        reasoning_trace,
        {},
        sequence_state=sequence_state,
    )
    transition_summary: TransitionSummary = _build_transition_summary(fused_transition_probabilities)
    sequence_state.update(
        {
            "continuation_probability": float(fused_transition_probabilities.get("continue", 0.25)),
            "pullback_probability": float(fused_transition_probabilities.get("pullback", 0.25)),
            "reversal_probability": float(fused_transition_probabilities.get("reversal_attempt", 0.25)),
            "fakeout_probability": float(fused_transition_probabilities.get("fakeout", 0.25)),
        }
    )
    sequence_state["next_box_hypotheses"] = _build_next_box_hypotheses(
        cast(list[dict[str, Any]], sequence_state.get("box_history", [])),
        sequence_state,
        chart_geometry,
        market_state=cast(dict[str, Any], reasoning_trace.get("market_state", {})),
        memory_summary={},
        memory_episode_matches=[],
    )
    next_box_hypotheses = cast(list[dict[str, Any]], sequence_state.get("next_box_hypotheses", []))
    sequence_state["primary_next_box"] = dict(next_box_hypotheses[0]) if next_box_hypotheses else {}
    sequence_state["path_clarity"] = float(
        np.clip(
            cast(dict[str, Any], sequence_state.get("primary_next_box", {})).get("path_clarity", sequence_state.get("path_clarity", 0.0)),
            0.0,
            1.0,
        )
    )
    sequence_state["sequence_model"] = _build_sequence_model_summary(
        sequence_state,
        chart_geometry,
        market_state=cast(dict[str, Any], reasoning_trace.get("market_state", {})),
    )
    detections = _build_heuristic_detections(
        cv_proxy,
        image_rgb,
        chart_geometry,
        sequence_state,
        cast(dict[str, Any], sequence_state.get("sequence_model", {})),
    )
    reasoning_trace["sequence_state"] = sequence_state
    chart_state = _build_chart_state(
        detections=detections,
        local_ensemble=local_ensemble,
        reasoning_trace=reasoning_trace,
        chart_geometry=chart_geometry,
        sequence_state=sequence_state,
        grounded_chart=grounded_chart,
    )

    base_probs = _ensemble_base_probs(local_ensemble, chart_state=chart_state, memory_summary={})
    chart_state["mcts"] = {
        "buy_prob": float(base_probs["BUY"]),
        "sell_prob": float(base_probs["SELL"]),
        "hold_prob": float(base_probs["HOLD"]),
        "n_sims": 0,
        "value": float(base_probs["BUY"] - base_probs["SELL"]),
    }
    forecast = forecast_engine.forecast_3m(
        chart_state,
        quantiles=RUNTIME.quantiles,
        detections=detections,
        memory_similarity=0.0,
        memory_direction="HOLD",
        transition_summary=fused_transition_probabilities,
        memory_summary={},
    )
    explanation_text = str(chart_state.get("raw_description", ""))
    _, cleaned_expl = indicator_regex_filter(explanation_text)
    extracted_prices = extract_price_floats(annotation_text)
    if len(extracted_prices) < 4:
        extracted_prices = _derive_proxy_price_series(sequence_state)
    sub_signals = [(float(detection.get("confidence", 0.0) or 0.0), str(detection.get("pattern", ""))) for detection in detections]
    module_logits = np.array(
        [float(base_probs["BUY"]), float(base_probs["SELL"]), float(base_probs["HOLD"])],
        dtype=np.float32,
    )
    latest_signal_state = _extract_latest_signal_state(detections)
    latest_parse_quality = float(latest_signal_state["latest_parse_quality"])
    latest_candle_confidence = float(latest_signal_state["latest_candle_confidence"])
    gate_outputs = gates_engine.run_all(
        probs=base_probs,
        q05=float(forecast["q05"]),
        q95=float(forecast["q95"]),
        momentum_bias=str(chart_state.get("momentum_bias", "neutral")),
        explanation=cleaned_expl,
        sub_signals=sub_signals,
        module_logits=module_logits,
        recent_feedback_count=0,
        queue_depth=0,
        gpu_mem_ok=False,
        has_dashboard=True,
        risk_ethical_ok=True,
        chart_state=chart_state,
        prices=extracted_prices,
        direction_prob=float(chart_state.get("direction_probability", 0.5) or 0.5),
        mcts=cast(dict[str, Any], chart_state.get("mcts", {})),
        memory_sim=0.0,
        latest_candle_confidence=latest_candle_confidence,
        geometry_conflict=False,
    )

    ensemble_view = cast(dict[str, Any], local_ensemble.get("ensemble", {}))
    ensemble_conf = float(ensemble_view.get("confidence", 0.5) or 0.5)
    ensemble_disagreement = float(ensemble_view.get("disagreement", 0.0) or 0.0)
    cv_quality = float(
        np.clip(
            0.34 * ensemble_conf
            + 0.24 * latest_parse_quality
            + 0.20 * latest_candle_confidence
            + 0.22 * (1.0 - ensemble_disagreement),
            0.0,
            1.0,
        )
    )
    support_gate_outputs = gates_engine.run_support_gates(
        chart_state=chart_state,
        market_state=cast(dict[str, Any], reasoning_trace.get("market_state", {})),
        forecast=cast(dict[str, Any], forecast),
        transition_summary=dict(transition_summary),
        memory_summary={},
        ood_summary={},
        memory_similarity=0.0,
        memory_label="HOLD",
        latest_candle_confidence=latest_candle_confidence,
        geometry_conflict=False,
        reliability=cv_quality,
        use_execution_permission=RUNTIME.use_execution_permission,
        use_macro_local_alignment=RUNTIME.use_macro_local_alignment_gate,
        use_opposition_strength=RUNTIME.use_opposition_strength_gate,
    )

    core_gate_details = [
        {
            "name": str(gate.name),
            "score": float(gate.score),
            "pass_fail": bool(gate.pass_fail),
            "detail": dict(gate.detail),
        }
        for gate in gate_outputs
    ]
    support_gate_details = [
        {
            "name": str(gate.name),
            "score": float(gate.score),
            "pass_fail": bool(gate.pass_fail),
            "detail": dict(gate.detail),
        }
        for gate in support_gate_outputs
    ]
    implied_move = _estimate_implied_move_pct(
        detections,
        float(chart_state.get("direction_probability", 0.5) or 0.5),
        float(cast(dict[str, Any], chart_state.get("entry_candle", {})).get("body_pct", 0.0) or 0.0),
    )
    return {
        "mode": "isolated_local",
        "meta": meta,
        "detections": detections,
        "chart_geometry": chart_geometry,
        "sequence_state": sequence_state,
        "grounded_chart": grounded_chart,
        "local_ensemble": local_ensemble,
        "chart_state": chart_state,
        "forecast_debug": dict(forecast),
        "transition_summary": dict(transition_summary),
        "cv_reasoning_trace": reasoning_trace,
        "action": str(chart_state.get("direction", ensemble_view.get("predicted_label", "HOLD"))),
        "confidence": float(ensemble_conf),
        "expected_3min_move_pct": float(implied_move),
        "memory_similarity": 0.0,
        "memory_direction": "HOLD",
        "latest_parse_quality": latest_parse_quality,
        "latest_candle_confidence": latest_candle_confidence,
        "gate_scores": {str(gate["name"]): float(gate["score"]) for gate in core_gate_details},
        "support_gate_scores": {str(gate["name"]): float(gate["score"]) for gate in support_gate_details},
        "gate_details": core_gate_details,
        "support_gate_details": support_gate_details,
    }


def _build_gate_payload(image_path: Path, result: Mapping[str, Any]) -> dict[str, Any]:
    gate_details = list(result.get("gate_details", []))
    support_gate_details = list(result.get("support_gate_details", []))
    chart_state = dict(result.get("chart_state", {}))
    local_ensemble = dict(result.get("local_ensemble", {}))
    ensemble_view = dict(local_ensemble.get("ensemble", {}))
    return {
        "image_path": str(image_path),
        "mode": str(result.get("mode", "isolated_local")),
        "context": {
            "action": str(result.get("action", "HOLD")),
            "confidence": float(result.get("confidence", 0.0) or 0.0),
            "chart_direction": str(chart_state.get("direction", "HOLD")),
            "macro_trend": str(chart_state.get("macro_trend", "unknown")),
            "local_phase": str(chart_state.get("local_phase", "unknown")),
            "structure_setup": str(chart_state.get("structure_setup", "none")),
            "projection_bias_direction": str(chart_state.get("projection_bias_direction", "HOLD")),
            "projection_bias_confidence": float(chart_state.get("projection_bias_confidence", 0.0) or 0.0),
            "latest_parse_quality": float(result.get("latest_parse_quality", 0.0) or 0.0),
            "latest_candle_confidence": float(result.get("latest_candle_confidence", 0.0) or 0.0),
            "memory_similarity": float(result.get("memory_similarity", 0.0) or 0.0),
            "memory_direction": str(result.get("memory_direction", "HOLD")),
            "ensemble_direction": str(ensemble_view.get("predicted_label", "HOLD")),
            "ensemble_confidence": float(ensemble_view.get("confidence", 0.0) or 0.0),
        },
        "core_gates": {
            "summary": _gate_summary(gate_details),
            "scores": dict(result.get("gate_scores", {})),
            "details": gate_details,
        },
        "support_gates": {
            "summary": _gate_summary(support_gate_details),
            "scores": dict(result.get("support_gate_scores", {})),
            "details": support_gate_details,
        },
    }


def _print_gate_block(title: str, details: Sequence[Mapping[str, Any]]) -> None:
    summary = _gate_summary(details)
    print(f"{title}: {summary['passing']}/{summary['total']} passing")
    for gate in details:
        name = str(gate.get("name", "gate"))
        score = float(gate.get("score", 0.0) or 0.0)
        status = "PASS" if bool(gate.get("pass_fail", False)) else "FAIL"
        print(f"  {status:4}  {name:24} score={score:.3f}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run PhoenixGuard skill gates on a single image and print only the gate results.",
    )
    parser.add_argument("--image", required=True, help="Path to the chart image to test.")
    parser.add_argument(
        "--annotation-text",
        default="",
        help="Optional annotation/OCR text to feed into the gate path.",
    )
    parser.add_argument(
        "--annotation-file",
        default="",
        help="Optional text file whose contents will be used as annotation text.",
    )
    parser.add_argument(
        "--overlay-mode",
        default="history-plus-projection",
        help="Retained for compatibility. Ignored in isolated mode.",
    )
    parser.add_argument(
        "--min-conf-global",
        type=float,
        default=0.42,
        help="Retained for compatibility. Ignored in isolated mode.",
    )
    parser.add_argument(
        "--min-conf-latest",
        type=float,
        default=0.50,
        help="Retained for compatibility. Ignored in isolated mode.",
    )
    parser.add_argument(
        "--full-pipeline",
        action="store_true",
        help="Use the old full runtime path instead of isolated local gate mode.",
    )
    parser.add_argument(
        "--json-out",
        default="",
        help="Optional path to save the gate payload as JSON.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    image_path = Path(str(args.image)).expanduser().resolve()
    if not image_path.exists():
        print(f"Image not found: {image_path}", file=sys.stderr)
        return 2

    try:
        annotation_text = _load_annotation_text(
            annotation_text=str(args.annotation_text or ""),
            annotation_file=str(args.annotation_file or ""),
        )
    except Exception as exc:
        print(f"Failed to load annotation text: {exc}", file=sys.stderr)
        return 2

    try:
        if bool(args.full_pipeline):
            from main import run_inference

            result, _image, _plot, _raw = run_inference(
                str(image_path),
                annotation_text=annotation_text,
                overlay_mode=str(args.overlay_mode),
                min_conf_global=float(args.min_conf_global),
                min_conf_latest=float(args.min_conf_latest),
                side_effect_free=True,
            )
            result = dict(result)
            result["mode"] = "full_pipeline"
        else:
            result = _isolated_gate_result(image_path, annotation_text)
    except Exception as exc:
        print(f"Skill-gate run failed: {exc}", file=sys.stderr)
        return 1

    payload = _build_gate_payload(image_path, result)
    context = dict(payload.get("context", {}))
    print(f"Mode: {payload['mode']}")
    print(f"Image: {payload['image_path']}")
    print(
        "Context: "
        f"action={context.get('action', 'HOLD')} "
        f"conf={float(context.get('confidence', 0.0) or 0.0):.3f} "
        f"chart={context.get('chart_direction', 'HOLD')} "
        f"phase={context.get('local_phase', 'unknown')} "
        f"setup={context.get('structure_setup', 'none')}"
    )
    print(
        "Signals: "
        f"parse={float(context.get('latest_parse_quality', 0.0) or 0.0):.3f} "
        f"latest={float(context.get('latest_candle_confidence', 0.0) or 0.0):.3f} "
        f"memory={float(context.get('memory_similarity', 0.0) or 0.0):.3f} "
        f"projection={context.get('projection_bias_direction', 'HOLD')}:{float(context.get('projection_bias_confidence', 0.0) or 0.0):.3f}"
    )
    print()
    _print_gate_block("Core Gates", payload["core_gates"]["details"])
    print()
    _print_gate_block("Support Gates", payload["support_gates"]["details"])

    if str(args.json_out or "").strip():
        json_path = Path(str(args.json_out)).expanduser().resolve()
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(payload, ensure_ascii=True, indent=2, default=_json_default),
            encoding="utf-8",
        )
        print()
        print(f"Saved JSON: {json_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
