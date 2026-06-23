from __future__ import annotations

from typing import Any, Mapping, Sequence, cast


def _clip01(value: Any) -> float:
    try:
        number = float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if number < 0.0:
        return 0.0
    if number > 1.0:
        return 1.0
    return number


def _direction(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    if normalized.startswith("BUY"):
        return "BUY"
    if normalized.startswith("SELL"):
        return "SELL"
    return "HOLD"


def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def _mapping_rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    rows = cast(Sequence[object], value)
    return [
        _mapping(item)
        for item in rows
        if isinstance(item, Mapping)
    ]


def _normalize_probabilities(probabilities: Mapping[str, Any] | None) -> dict[str, float]:
    raw = {
        "BUY": max(0.0, _clip01((probabilities or {}).get("BUY", 0.0))),
        "SELL": max(0.0, _clip01((probabilities or {}).get("SELL", 0.0))),
        "HOLD": max(0.0, _clip01((probabilities or {}).get("HOLD", 0.0))),
    }
    total = float(sum(raw.values()))
    if total <= 1e-9:
        return {"BUY": 1.0 / 3.0, "SELL": 1.0 / 3.0, "HOLD": 1.0 / 3.0}
    return {key: float(value / total) for key, value in raw.items()}


def _dedupe(items: Sequence[str], *, limit: int = 6) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        deduped.append(text)
        if len(deduped) >= max(1, int(limit)):
            break
    return deduped


def _family_display_name(family: str, direction: str) -> str:
    display_map = {
        "consolidation_breakout": "Consolidation Breakout",
        "impulse_continuation": "Impulse Continuation",
        "reversal_release": "Reversal Release",
        "pullback_continuation": "Pullback Continuation",
        "breakout_continuation": "Breakout Continuation",
        "impulse_follow_through": "Impulse Follow-Through",
        "countertrend_reversal": "Countertrend Reversal",
        "directional_continuation": "Directional Continuation",
        "directional_pressure": "Directional Pressure",
        "stand_aside": "Stand Aside",
    }
    base = display_map.get(str(family or "").strip().lower(), "Directional Pressure")
    if direction in {"BUY", "SELL"}:
        return f"{direction} {base}"
    return base


def _profile_value(snapshot: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    profile = cast(Mapping[str, Any], snapshot.get("profile", {}))
    return _clip01(profile.get(key, default))


def _profile_direction(snapshot: Mapping[str, Any], key: str) -> str:
    profile = cast(Mapping[str, Any], snapshot.get("profile", {}))
    return _direction(profile.get(key, "HOLD"))


def _chart_value(snapshot: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    chart_state = cast(Mapping[str, Any], snapshot.get("chart_state", {}))
    return _clip01(chart_state.get(key, default))


def _sequence_value(snapshot: Mapping[str, Any], key: str, default: float = 0.0) -> float:
    sequence = cast(Mapping[str, Any], snapshot.get("sequence", {}))
    return _clip01(sequence.get(key, default))


def _sequence_box(snapshot: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    sequence = cast(Mapping[str, Any], snapshot.get("sequence", {}))
    return cast(Mapping[str, Any], sequence.get(key, {}))


def _sequence_history(snapshot: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    sequence = cast(Mapping[str, Any], snapshot.get("sequence", {}))
    history = cast(Sequence[Mapping[str, Any]], sequence.get("box_history", []))
    return [dict(item) for item in history]


def _pattern_rows(snapshot: Mapping[str, Any], direction: str) -> list[Mapping[str, Any]]:
    patterns = cast(Mapping[str, Any], snapshot.get("patterns", {}))
    rows = cast(Sequence[Mapping[str, Any]], patterns.get(direction, []))
    return [dict(item) for item in rows]


def _pattern_density(snapshot: Mapping[str, Any], direction: str) -> float:
    rows = _pattern_rows(snapshot, direction)[:4]
    total_weight = float(sum(_clip01(float(item.get("weight", 0.0) or 0.0) / 2.0) for item in rows))
    return _clip01(total_weight / 1.8)


def _pattern_labels(snapshot: Mapping[str, Any], direction: str, *, limit: int = 3) -> list[str]:
    labels = [
        str(item.get("pattern", "")).replace("_", " ").strip()
        for item in _pattern_rows(snapshot, direction)[: max(1, int(limit))]
        if str(item.get("pattern", "")).strip()
    ]
    return _dedupe(labels, limit=limit)


def _model_vote_ratio(snapshot: Mapping[str, Any], direction: str) -> float:
    ensemble = cast(Mapping[str, Any], snapshot.get("ensemble", {}))
    votes = cast(Sequence[Mapping[str, Any]], ensemble.get("model_votes", []))
    if not votes:
        predicted_direction = _direction(ensemble.get("predicted_label", "HOLD"))
        confidence = _clip01(ensemble.get("confidence", 0.0))
        return confidence if predicted_direction == direction else 0.0
    total = 0.0
    aligned = 0.0
    for vote in votes:
        strength = 0.45 + 0.55 * _clip01(vote.get("confidence", 0.0))
        total += strength
        if _direction(vote.get("direction", "HOLD")) == direction:
            aligned += strength
    return _clip01(aligned / max(total, 1e-6))


def _frame_support(snapshot: Mapping[str, Any], direction: str) -> float:
    support = 0.0
    if _profile_direction(snapshot, "bias_direction") == direction:
        support += 0.08 + 0.12 * _profile_value(snapshot, "bias_strength")
    if _profile_direction(snapshot, "entry_direction") == direction:
        support += 0.06 + 0.10 * _profile_value(snapshot, "entry_confidence")
    if _profile_direction(snapshot, "projection_direction") == direction:
        support += 0.05 + 0.08 * _profile_value(snapshot, "projection_confidence")
    if _direction(snapshot.get("action", "HOLD")) == direction:
        support += 0.04 + 0.08 * _clip01(snapshot.get("confidence", 0.0))
    if _direction(cast(Mapping[str, Any], snapshot.get("ensemble", {})).get("predicted_label", "HOLD")) == direction:
        support += 0.04 + 0.06 * _clip01(cast(Mapping[str, Any], snapshot.get("ensemble", {})).get("confidence", 0.0))
    return _clip01(support)


def _resolve_play_family(direction: str, combined: Mapping[str, Any], frames: Sequence[Mapping[str, Any]]) -> str:
    structure_setup = str(cast(Mapping[str, Any], combined.get("chart_state", {})).get("structure_setup", "") or "").strip().lower()
    entry_type = str(cast(Mapping[str, Any], combined.get("chart_state", {})).get("entry_type", "") or "").strip().lower()
    current_box = _sequence_box(combined, "current_box")
    next_box = _sequence_box(combined, "primary_next_box")
    current_box_type = str(current_box.get("box_type", "balance") or "balance").strip().lower()
    next_box_type = str(next_box.get("box_type", current_box_type) or current_box_type).strip().lower()
    current_dir = _direction(current_box.get("direction", "HOLD"))
    projected_dir = _direction(next_box.get("direction", _profile_direction(combined, "projection_direction")))
    continuation_prob = max(
        _profile_value(combined, "continuation_probability"),
        _chart_value(combined, "continuation_probability"),
        _sequence_value(combined, "continuation_probability"),
    )
    reversal_prob = max(
        _profile_value(combined, "reversal_probability"),
        _chart_value(combined, "reversal_probability"),
        _sequence_value(combined, "reversal_probability"),
    )
    has_consolidation = bool(
        cast(Mapping[str, Any], combined.get("chart_state", {})).get("has_active_consolidation", False)
        or cast(Mapping[str, Any], combined.get("sequence", {})).get("has_active_consolidation", False)
    )
    frame_support = max((_frame_support(frame, direction) for frame in frames), default=0.0)

    if structure_setup == "consolidation_breakout" and direction in {projected_dir, current_dir}:
        return "consolidation_breakout"
    if structure_setup == "impulse_chain" and direction in {projected_dir, current_dir}:
        return "impulse_continuation"
    if structure_setup == "reversal_release":
        return "reversal_release" if direction in {projected_dir, current_dir} else "countertrend_reversal"
    if current_box_type == "pullback" and direction == projected_dir and continuation_prob >= 0.46:
        return "pullback_continuation"
    if has_consolidation and next_box_type == "impulse" and direction == projected_dir:
        return "breakout_continuation"
    if next_box_type == "impulse" and direction == projected_dir:
        return "impulse_follow_through"
    if entry_type == "reversal" or reversal_prob >= continuation_prob + 0.10:
        return "countertrend_reversal"
    if frame_support >= 0.55 and continuation_prob >= 0.52:
        return "directional_continuation"
    return "directional_pressure"


def _transition_edge(direction: str, combined: Mapping[str, Any], family: str) -> float:
    transitions = cast(Mapping[str, Any], combined.get("transitions", {}))
    continue_prob = _clip01(transitions.get("continue", 0.0))
    pullback_prob = _clip01(transitions.get("pullback", 0.0))
    reversal_prob = _clip01(transitions.get("reversal_attempt", 0.0))
    fakeout_prob = _clip01(transitions.get("fakeout", 0.0))
    current_dir = _direction(_sequence_box(combined, "current_box").get("direction", "HOLD"))
    projected_dir = _direction(_sequence_box(combined, "primary_next_box").get("direction", "HOLD"))
    family_is_reversal = "reversal" in family
    aligned_to_flow = direction in {projected_dir, current_dir}
    if family_is_reversal:
        edge = 0.58 * reversal_prob + 0.24 * fakeout_prob + 0.18 * max(0.0, 1.0 - continue_prob)
        if aligned_to_flow:
            edge *= 0.84
        return _clip01(edge)
    edge = 0.56 * continue_prob + 0.24 * pullback_prob + 0.20 * max(0.0, 1.0 - fakeout_prob)
    if not aligned_to_flow:
        edge *= 0.78
    return _clip01(edge)


def _multi_timeframe_alignment(direction: str, combined: Mapping[str, Any], frames: Sequence[Mapping[str, Any]]) -> float:
    multi = cast(Mapping[str, Any], combined.get("multi_timeframe", {}))
    gate_state = str(multi.get("gate_state", "watch") or "watch").strip().lower()
    gate_strength = _clip01(multi.get("gate_strength", 0.0))
    if not frames:
        if gate_state == "confirmed":
            return _clip01(0.58 + 0.34 * gate_strength)
        if gate_state == "blocked":
            return 0.18
        return _clip01(0.34 + 0.18 * gate_strength)

    aligned = 0.0
    possible = 0.0
    for index, frame in enumerate(frames):
        weight = 1.0 if index == 0 else 1.12
        possible += 2.0 * weight
        if _profile_direction(frame, "bias_direction") == direction:
            aligned += weight * (0.50 + 0.50 * _profile_value(frame, "bias_strength"))
        if _profile_direction(frame, "projection_direction") == direction:
            aligned += weight * (0.34 + 0.66 * _profile_value(frame, "projection_confidence"))
    ratio = _clip01(aligned / max(possible, 1e-6))
    if gate_state == "confirmed":
        ratio = max(ratio, _clip01(0.62 + 0.30 * gate_strength))
    elif gate_state == "blocked":
        ratio *= 0.42
    else:
        ratio *= 0.80 + 0.12 * gate_strength
    return _clip01(ratio)


def _timing_quality(direction: str, combined: Mapping[str, Any]) -> float:
    timing = cast(Mapping[str, Any], combined.get("timing", {}))
    state = str(timing.get("entry_state", "WATCH") or "WATCH").strip().upper()
    timing_score = _clip01(timing.get("timing_score", 0.0))
    action = _direction(combined.get("action", "HOLD"))
    execution_action = _direction(combined.get("execution_action", action))
    projected_dir = _profile_direction(combined, "projection_direction")
    if state == "READY" and execution_action == direction:
        return _clip01(0.64 + 0.32 * timing_score)
    if state == "READY" and direction in {action, projected_dir}:
        return _clip01(0.52 + 0.28 * timing_score)
    if state == "WATCH":
        if direction in {action, projected_dir}:
            return _clip01(0.28 + 0.28 * timing_score)
        return _clip01(0.16 + 0.16 * timing_score)
    if state == "PREMATURE":
        return _clip01(0.18 + 0.16 * timing_score)
    if state == "LATE":
        return _clip01(0.08 + 0.10 * timing_score)
    return _clip01(0.12 + 0.18 * timing_score)


def _collect_sequence_motifs(
    combined: Mapping[str, Any],
    frames: Sequence[Mapping[str, Any]],
    family_map: Mapping[str, str],
) -> list[dict[str, Any]]:
    aggregated: dict[str, dict[str, Any]] = {}

    def add(direction: str, label: str, weight: float, count: float, source: str) -> None:
        normalized_direction = _direction(direction)
        text = str(label or "").strip()
        if normalized_direction not in {"BUY", "SELL"} or not text or weight <= 0.0:
            return
        key = f"{normalized_direction}|{text}"
        row = aggregated.setdefault(
            key,
            {
                "direction": normalized_direction,
                "label": text,
                "count": 0.0,
                "weight": 0.0,
                "sources": [],
            },
        )
        row["count"] = float(row["count"]) + float(max(0.0, count))
        row["weight"] = float(row["weight"]) + float(max(0.0, weight))
        sources = cast(list[str], row["sources"])
        if source and source not in sources:
            sources.append(source)

    combined_source = str(combined.get("label", "Combined Desk") or "Combined Desk")
    for direction in ("BUY", "SELL"):
        family = family_map.get(direction, "directional_pressure")
        family_label = _family_display_name(family, direction)
        if _profile_direction(combined, "bias_direction") == direction:
            add(direction, family_label, 0.38 + 0.30 * _profile_value(combined, "bias_strength"), 1.0, combined_source)
        if _profile_direction(combined, "entry_direction") == direction:
            add(direction, family_label, 0.26 + 0.28 * _profile_value(combined, "entry_confidence"), 1.0, combined_source)
        if _profile_direction(combined, "projection_direction") == direction:
            add(direction, f"{direction} projected follow-through", 0.18 + 0.24 * _profile_value(combined, "projection_confidence"), 1.0, combined_source)

    for frame in frames:
        frame_source = str(frame.get("label", "Frame") or "Frame")
        for direction in ("BUY", "SELL"):
            family = family_map.get(direction, "directional_pressure")
            family_label = _family_display_name(family, direction)
            if _profile_direction(frame, "bias_direction") == direction:
                add(direction, family_label, 0.24 + 0.22 * _profile_value(frame, "bias_strength"), 1.0, frame_source)
            if _profile_direction(frame, "projection_direction") == direction:
                add(direction, f"{direction} projected follow-through", 0.12 + 0.18 * _profile_value(frame, "projection_confidence"), 1.0, frame_source)
        for pattern_dir in ("BUY", "SELL"):
            for item in _pattern_rows(frame, pattern_dir)[:2]:
                add(
                    pattern_dir,
                    f"{pattern_dir} {str(item.get('pattern', '')).replace('_', ' ').strip()}",
                    0.08 + 0.14 * _clip01(float(item.get("weight", 0.0) or 0.0) / 2.0),
                    float(max(1.0, float(item.get("count", 1.0) or 1.0))),
                    frame_source,
                )

    for item in _sequence_history(combined)[-5:]:
        direction = _direction(item.get("direction", "HOLD"))
        if direction not in {"BUY", "SELL"}:
            continue
        box_type = str(item.get("box_type", "flow") or "flow").replace("_", " ").strip()
        add(
            direction,
            f"{direction} {box_type} box",
            0.12 + 0.18 * _clip01(item.get("confidence", 0.0)),
            1.0,
            combined_source,
        )

    for key in ("current_box", "primary_next_box"):
        box = _sequence_box(combined, key)
        direction = _direction(box.get("direction", "HOLD"))
        if direction not in {"BUY", "SELL"}:
            continue
        box_type = str(box.get("box_type", "flow") or "flow").replace("_", " ").strip()
        label = f"{direction} {'current' if key == 'current_box' else 'next'} {box_type}"
        add(direction, label, 0.16 + 0.18 * _clip01(box.get("confidence", 0.0)), 1.0, combined_source)

    for pattern_dir in ("BUY", "SELL"):
        for item in _pattern_rows(combined, pattern_dir)[:3]:
            add(
                pattern_dir,
                f"{pattern_dir} {str(item.get('pattern', '')).replace('_', ' ').strip()}",
                0.10 + 0.16 * _clip01(float(item.get("weight", 0.0) or 0.0) / 2.0),
                float(max(1.0, float(item.get("count", 1.0) or 1.0))),
                combined_source,
            )

    ranked = sorted(
        aggregated.values(),
        key=lambda row: (float(row["weight"]), float(row["count"])),
        reverse=True,
    )
    return [
        {
            "direction": str(row["direction"]),
            "label": str(row["label"]),
            "count": int(round(float(row["count"]))),
            "weight": round(float(row["weight"]), 4),
            "sources": list(cast(list[str], row["sources"]))[:4],
        }
        for row in ranked[:8]
    ]


def _frequency_metrics(direction: str, frequent_sequences: Sequence[Mapping[str, Any]]) -> tuple[float, int, list[str]]:
    rows = [row for row in frequent_sequences if _direction(row.get("direction", "HOLD")) == direction][:4]
    if not rows:
        return 0.0, 0, []
    total_weight = float(sum(float(row.get("weight", 0.0) or 0.0) for row in rows))
    total_count = int(sum(int(row.get("count", 0) or 0) for row in rows))
    labels = [str(row.get("label", "")) for row in rows]
    return _clip01(total_weight / 2.4), total_count, _dedupe(labels, limit=3)


def _build_directional_play(
    direction: str,
    combined: Mapping[str, Any],
    frames: Sequence[Mapping[str, Any]],
    *,
    family: str,
    frequent_sequences: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    evidence: list[tuple[float, str]] = []

    def add(weight: float, label: str) -> None:
        clipped = max(0.0, float(weight))
        text = str(label or "").strip()
        if clipped <= 1e-6 or not text:
            return
        evidence.append((clipped, text))

    action = _direction(combined.get("action", "HOLD"))
    execution_action = _direction(combined.get("execution_action", action))
    action_confidence = _clip01(combined.get("confidence", 0.0))
    if action == direction:
        add(0.08 + 0.18 * action_confidence, f"desk action {direction} {action_confidence:.2f}")
    if execution_action == direction:
        add(0.10 + 0.08 * _timing_quality(direction, combined), f"execution route already favors {direction}")

    if _profile_direction(combined, "bias_direction") == direction:
        add(0.06 + 0.18 * _profile_value(combined, "bias_strength"), f"desk bias {direction} {_profile_value(combined, 'bias_strength'):.2f}")
    if _profile_direction(combined, "entry_direction") == direction:
        add(0.05 + 0.16 * _profile_value(combined, "entry_confidence"), f"entry pressure {direction} {_profile_value(combined, 'entry_confidence'):.2f}")
    if _profile_direction(combined, "projection_direction") == direction:
        projected_weight = 0.04 + 0.14 * _profile_value(combined, "projection_confidence") + 0.04 * _profile_value(combined, "projection_dominance")
        add(projected_weight, f"projection path {direction} {_profile_value(combined, 'projection_confidence'):.2f}")
    if _profile_direction(combined, "structure_direction") == direction:
        add(0.04 + 0.10 * _profile_value(combined, "structure_confidence"), f"structure bias {direction} {_profile_value(combined, 'structure_confidence'):.2f}")
    if _profile_direction(combined, "sequence_direction") == direction:
        add(0.04 + 0.10 * _profile_value(combined, "sequence_confidence"), f"sequence bias {direction} {_profile_value(combined, 'sequence_confidence'):.2f}")
    if _profile_direction(combined, "council_bias_direction") == direction:
        council_weight = 0.04 + 0.12 * _profile_value(combined, "council_bias_confidence") + 0.03 * _profile_value(combined, "council_alignment_score")
        add(council_weight, f"council bias {direction} {_profile_value(combined, 'council_bias_confidence'):.2f}")
    if _profile_direction(combined, "council_projection_direction") == direction:
        add(0.03 + 0.08 * _profile_value(combined, "council_projection_confidence"), f"council projection {direction} {_profile_value(combined, 'council_projection_confidence'):.2f}")
    if _profile_direction(combined, "council_router_direction") == direction:
        add(0.02 + 0.06 * _profile_value(combined, "council_router_strength"), f"router alignment {direction} {_profile_value(combined, 'council_router_strength'):.2f}")

    sequence_pressure = _chart_value(combined, "sequence_buy_pressure" if direction == "BUY" else "sequence_sell_pressure")
    structure_pressure = _chart_value(combined, "structure_buy_pressure" if direction == "BUY" else "structure_sell_pressure")
    continuation_prob = max(
        _chart_value(combined, "continuation_probability"),
        _sequence_value(combined, "continuation_probability"),
    )
    reversal_prob = max(
        _chart_value(combined, "reversal_probability"),
        _sequence_value(combined, "reversal_probability"),
    )
    path_clarity = max(
        _chart_value(combined, "path_clarity"),
        _sequence_value(combined, "path_clarity"),
    )
    add(0.04 + 0.10 * sequence_pressure, f"sequence pressure {direction} {sequence_pressure:.2f}")
    add(0.03 + 0.08 * structure_pressure, f"structure pressure {direction} {structure_pressure:.2f}")
    if "reversal" in family:
        add(0.03 + 0.10 * reversal_prob, f"reversal pressure {reversal_prob:.2f}")
    else:
        add(0.03 + 0.10 * continuation_prob, f"continuation pressure {continuation_prob:.2f}")
    add(0.02 + 0.06 * path_clarity, f"path clarity {path_clarity:.2f}")

    transition_edge = _transition_edge(direction, combined, family)
    add(0.04 + 0.10 * transition_edge, f"transition edge {transition_edge:.2f}")

    mtf_alignment = _multi_timeframe_alignment(direction, combined, frames)
    add(0.04 + 0.10 * mtf_alignment, f"multi-timeframe alignment {mtf_alignment:.2f}")

    timing_quality = _timing_quality(direction, combined)
    add(0.02 + 0.08 * timing_quality, f"timing quality {timing_quality:.2f}")

    memory_direction = _direction(combined.get("memory_direction", "HOLD"))
    memory_similarity = _clip01(combined.get("memory_similarity", 0.0))
    if memory_direction == direction and memory_similarity > 0.0:
        add(0.02 + 0.08 * memory_similarity, f"memory alignment {memory_similarity:.2f}")

    model_ratio = max(
        _model_vote_ratio(combined, direction),
        max((_model_vote_ratio(frame, direction) for frame in frames), default=0.0),
    )
    add(0.03 + 0.10 * model_ratio, f"model vote ratio {model_ratio:.2f}")

    pattern_density = max(
        _pattern_density(combined, direction),
        max((_pattern_density(frame, direction) for frame in frames), default=0.0),
    )
    add(0.02 + 0.08 * pattern_density, f"pattern density {pattern_density:.2f}")

    for frame in frames:
        support = _frame_support(frame, direction)
        if support <= 1e-6:
            continue
        add(0.04 + 0.10 * support, f"{str(frame.get('label', 'frame'))} support {support:.2f}")

    support_raw = float(sum(weight for weight, _label in evidence))
    support_score = _clip01(support_raw / 1.55)
    frequency_score, frequency_count, frequency_labels = _frequency_metrics(direction, frequent_sequences)

    opposite = "SELL" if direction == "BUY" else "BUY"
    fakeout_risk = max(
        _chart_value(combined, "fakeout_probability"),
        _sequence_value(combined, "fakeout_probability"),
    )
    disagreement = _clip01(cast(Mapping[str, Any], combined.get("ensemble", {})).get("disagreement", 0.0))
    memory_conflict = memory_similarity if memory_direction == opposite else 0.0
    opposing_pattern_density = _pattern_density(combined, opposite)
    thematic_risk = continuation_prob if "reversal" in family else reversal_prob
    gate_state = str(cast(Mapping[str, Any], combined.get("multi_timeframe", {})).get("gate_state", "watch") or "watch").strip().lower()
    gate_penalty = 0.0
    if gate_state == "blocked":
        gate_penalty = 0.30
    elif gate_state == "watch":
        gate_penalty = 0.12
    if _profile_direction(combined, "projection_direction") not in {direction, "HOLD"} and "reversal" not in family:
        gate_penalty += 0.06
    risk_score = _clip01(
        0.26 * fakeout_risk
        + 0.18 * max(0.0, 1.0 - path_clarity)
        + 0.14 * disagreement
        + 0.12 * opposing_pattern_density
        + 0.12 * max(0.0, 1.0 - mtf_alignment)
        + 0.10 * memory_conflict
        + 0.08 * thematic_risk
        + gate_penalty
    )

    likelihood = _clip01(
        (
            0.52 * support_score
            + 0.16 * frequency_score
            + 0.14 * mtf_alignment
            + 0.10 * transition_edge
            + 0.08 * timing_quality
        )
        * (0.70 + 0.30 * max(0.0, 1.0 - risk_score))
    )

    evidence_sorted = [label for _weight, label in sorted(evidence, key=lambda item: item[0], reverse=True)]
    return {
        "direction": direction,
        "family": family,
        "display_name": _family_display_name(family, direction),
        "support_score": support_score,
        "frequency_score": frequency_score,
        "frequency_count": frequency_count,
        "risk_score": risk_score,
        "likelihood": likelihood,
        "model_agreement": model_ratio,
        "pattern_density": pattern_density,
        "transition_edge": transition_edge,
        "multi_timeframe_alignment": mtf_alignment,
        "timing_quality": timing_quality,
        "top_patterns": _dedupe(
            [
                *_pattern_labels(combined, direction),
                *[label for frame in frames for label in _pattern_labels(frame, direction, limit=1)],
            ],
            limit=4,
        ),
        "frequent_sequences": frequency_labels,
        "evidence_chain": _dedupe(evidence_sorted, limit=6),
    }


def _hold_score(combined: Mapping[str, Any], buy_play: Mapping[str, Any], sell_play: Mapping[str, Any]) -> float:
    multi = cast(Mapping[str, Any], combined.get("multi_timeframe", {}))
    gate_state = str(multi.get("gate_state", "watch") or "watch").strip().lower()
    gate_strength = _clip01(multi.get("gate_strength", 0.0))
    fakeout_risk = max(
        _chart_value(combined, "fakeout_probability"),
        _sequence_value(combined, "fakeout_probability"),
    )
    path_clarity = max(
        _chart_value(combined, "path_clarity"),
        _sequence_value(combined, "path_clarity"),
    )
    disagreement = _clip01(cast(Mapping[str, Any], combined.get("ensemble", {})).get("disagreement", 0.0))
    spread = abs(float(buy_play.get("likelihood", 0.0) or 0.0) - float(sell_play.get("likelihood", 0.0) or 0.0))
    timing_state = str(cast(Mapping[str, Any], combined.get("timing", {})).get("entry_state", "WATCH") or "WATCH").strip().upper()
    timing_penalty = 0.0 if timing_state == "READY" else (0.10 if timing_state == "WATCH" else 0.14)

    blocked_bonus = 0.26 + 0.22 * gate_strength if gate_state == "blocked" else 0.0
    watch_bonus = 0.10 + 0.10 * gate_strength if gate_state == "watch" else 0.0
    return _clip01(
        0.12
        + blocked_bonus
        + watch_bonus
        + 0.22 * fakeout_risk
        + 0.18 * max(0.0, 1.0 - path_clarity)
        + 0.12 * disagreement
        + 0.14 * max(0.0, 1.0 - min(1.0, spread * 2.0))
        + timing_penalty
    )


def analyze_best_play(input_snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    snapshot = _mapping(input_snapshot)
    combined = _mapping(snapshot.get("combined", {}))
    if not combined:
        return {
            "status": "empty",
            "recommended_direction": "HOLD",
            "recommended_play": _family_display_name("stand_aside", "HOLD"),
            "likelihoods": {"BUY": 0.0, "SELL": 0.0, "HOLD": 1.0},
            "frequent_sequences": [],
            "buy_play": {},
            "sell_play": {},
            "recommended_reasons": [],
        }

    frames = _mapping_rows(snapshot.get("frames"))
    family_map = {
        "BUY": _resolve_play_family("BUY", combined, frames),
        "SELL": _resolve_play_family("SELL", combined, frames),
    }
    frequent_sequences = _collect_sequence_motifs(combined, frames, family_map)
    buy_play = _build_directional_play("BUY", combined, frames, family=family_map["BUY"], frequent_sequences=frequent_sequences)
    sell_play = _build_directional_play("SELL", combined, frames, family=family_map["SELL"], frequent_sequences=frequent_sequences)
    hold_raw = _hold_score(combined, buy_play, sell_play)
    likelihoods = _normalize_probabilities({"BUY": buy_play["likelihood"], "SELL": sell_play["likelihood"], "HOLD": hold_raw})

    recommended_direction = max(likelihoods.items(), key=lambda item: float(item[1]))[0]
    directional_spread = abs(float(likelihoods["BUY"]) - float(likelihoods["SELL"]))
    if recommended_direction in {"BUY", "SELL"} and directional_spread < 0.08 and float(likelihoods["HOLD"]) >= 0.26:
        recommended_direction = "HOLD"

    if recommended_direction == "BUY":
        recommended_play = str(buy_play.get("display_name", _family_display_name(family_map["BUY"], "BUY")))
        recommended_reasons = cast(list[str], buy_play.get("evidence_chain", []))
        recommended_risk = float(buy_play.get("risk_score", 0.0) or 0.0)
    elif recommended_direction == "SELL":
        recommended_play = str(sell_play.get("display_name", _family_display_name(family_map["SELL"], "SELL")))
        recommended_reasons = cast(list[str], sell_play.get("evidence_chain", []))
        recommended_risk = float(sell_play.get("risk_score", 0.0) or 0.0)
    else:
        recommended_play = _family_display_name("stand_aside", "HOLD")
        recommended_reasons = _dedupe(
            [
                "buy and sell plays remain too close together",
                "risk or gating pressure still argues for patience",
                "wait for a cleaner sequence or stronger multi-timeframe alignment",
            ],
            limit=3,
        )
        recommended_risk = max(float(buy_play.get("risk_score", 0.0) or 0.0), float(sell_play.get("risk_score", 0.0) or 0.0))

    return {
        "status": "ready",
        "recommended_direction": recommended_direction,
        "recommended_play": recommended_play,
        "recommended_confidence": float(likelihoods.get(recommended_direction, 0.0)),
        "recommended_risk": recommended_risk,
        "likelihoods": likelihoods,
        "frequent_sequences": frequent_sequences,
        "buy_play": buy_play,
        "sell_play": sell_play,
        "recommended_reasons": _dedupe(recommended_reasons, limit=5),
        "frame_count": int(len(frames)),
    }
