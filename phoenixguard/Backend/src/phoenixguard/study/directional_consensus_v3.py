from __future__ import annotations

from collections import OrderedDict
from threading import RLock
from typing import Any, Mapping, Sequence


DIRECTIONAL_CONSENSUS_SCHEMA_VERSION = "PG_ADAPTIVE_DIRECTIONAL_CONSENSUS_V3"
_SWITCH_CONFIRMATIONS = 3
_MAX_TRACKED_MARKETS = 1_000_000
_LOCK = RLock()
_STATE: OrderedDict[str, dict[str, Any]] = OrderedDict()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _side(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"UP", "UP_SWING", "BULL", "LONG"}:
        return "BUY"
    if text in {"DOWN", "DOWN_SWING", "BEAR", "SHORT"}:
        return "SELL"
    return text if text in {"BUY", "SELL"} else "UNRESOLVED"


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(fallback)


def _latest_indices(candles: Sequence[Mapping[str, Any]]) -> set[int]:
    result = {max(0, len(candles) - 1)} if candles else set()
    if candles:
        latest = candles[-1]
        for key in ("source_index", "index", "candle_index", "sequence_index"):
            candidate = int(_number(latest.get(key), -1.0))
            if candidate >= 0:
                result.add(candidate)
    return result


def _line_evidence(
    trendlines: Sequence[Any], candles: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    latest = _latest_indices(candles)
    rows: list[dict[str, Any]] = []
    for raw in trendlines:
        if not isinstance(raw, Mapping):
            continue
        row = dict(raw)
        direction = _side(row.get("direction"))
        if direction == "UNRESOLVED":
            role = str(row.get("trendline_role") or "").lower()
            direction = "BUY" if role == "support" else "SELL" if role == "resistance" else "UNRESOLVED"
        if direction == "UNRESOLVED":
            continue
        touch_count = int(_number(row.get("touch_count"), 0.0))
        anchor_span = int(_number(row.get("anchor_span_bars"), 0.0))
        confirmation = str(row.get("confirmation_state") or "").upper()
        confirmed = bool(
            touch_count >= 3
            and anchor_span >= 5
            and confirmation == "CONFIRMED"
            and not bool(row.get("significant_close", False))
            and str(row.get("breach_state") or "ACTIVE").upper() == "ACTIVE"
        )
        raw_indices = row.get("touch_candle_indices", [])
        touch_indices = {
            int(_number(item, -1.0))
            for item in raw_indices
            if _number(item, -1.0) >= 0
        } if isinstance(raw_indices, Sequence) and not isinstance(raw_indices, (str, bytes, bytearray)) else set()
        forming_touch = bool(row.get("forming_touch", False))
        current_touch = bool(forming_touch or latest.intersection(touch_indices))
        distance = max(0.0, _number(row.get("close_distance_norm"), 9.999))
        near_now = bool(distance <= 0.35 and row.get("current_projection_visible") is not False)
        rows.append(
            {
                "type": str(row.get("type") or "").upper(),
                "role": str(row.get("trendline_role") or "").lower(),
                "direction": direction,
                "scope": str(row.get("trendline_scope") or "").upper(),
                "touch_count": touch_count,
                "anchor_span_bars": anchor_span,
                "confirmation_state": confirmation,
                "confirmed": confirmed,
                "current_touch": current_touch,
                "forming_touch": forming_touch,
                "near_now": near_now,
                "close_distance_norm": round(distance, 4),
                "current_projection_visible": row.get("current_projection_visible") is not False,
                "geometry_status": str(row.get("geometry_status") or ""),
                "confidence": max(0.0, min(1.0, _number(row.get("confidence"), 0.0))),
            }
        )
    return rows


def _public_line(row: Mapping[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    return {key: value for key, value in row.items() if not key.startswith("_")}


def reset_directional_consensus_v3() -> None:
    with _LOCK:
        _STATE.clear()


def resolve_directional_consensus_v3(
    market_study_v3: Mapping[str, Any],
    *,
    symbol: str,
    timeframe: str,
    major_side: Any,
    global_side: Any,
    local_side: Any,
    trendlines: Sequence[Any],
    candles: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    study = dict(market_study_v3)
    latent_key = (
        "hidden_state_discovery_v3"
        if isinstance(study.get("hidden_state_discovery_v3"), Mapping)
        else "latent_state_discovery_v3"
    )
    latent = _mapping(study.get(latent_key))
    if not latent:
        return study
    hidden = _mapping(latent.get("hidden_state"))
    outcomes = _mapping(latent.get("directional_outcome_distribution"))
    control = _mapping(latent.get("control"))
    lines = _line_evidence(trendlines, candles)
    major = _side(major_side)
    global_direction = _side(global_side)
    local_direction = _side(local_side)
    latent_side = _side(hidden.get("direction") or hidden.get("state"))
    latent_age = max(0, int(_number(hidden.get("age_candles"), 0.0)))

    scores = {"BUY": 0.0, "SELL": 0.0}
    evidence: list[dict[str, Any]] = []

    def add(side: str, weight: float, source: str) -> None:
        if side in scores and weight > 0.0:
            scores[side] += weight
            evidence.append({"source": source, "side": side, "weight": round(weight, 4)})

    add(major, 4.0, "major_structure")
    add(global_direction, 2.5, "global_regression")
    add(local_direction, 1.5, "local_regression")
    add(latent_side, 1.25 if latent_age >= 3 else 0.25, "latent_local_state")
    add("BUY", max(0.0, min(1.0, _number(outcomes.get("BUY"), 0.0))) * 2.0, "pair_dna_buy_outcome")
    add("SELL", max(0.0, min(1.0, _number(outcomes.get("SELL"), 0.0))) * 2.0, "pair_dna_sell_outcome")
    for line in lines:
        weight = 0.15
        if line["confirmed"]:
            weight = 0.5
        if line["near_now"]:
            weight += 0.75
        if line["current_touch"]:
            weight += 1.0
        add(str(line["direction"]), weight, f"trendline_{line['role'] or 'unknown'}")

    ranked = sorted(scores, key=lambda side: scores[side], reverse=True)
    raw_candidate = ranked[0]
    margin = scores[ranked[0]] - scores[ranked[1]]
    candidate = raw_candidate if scores[raw_candidate] >= 3.0 and margin >= 1.5 else "UNRESOLVED"
    unanimous_structure = bool(
        candidate in {"BUY", "SELL"}
        and major == candidate
        and global_direction == candidate
        and local_direction == candidate
    )
    closed_key = str(study.get("closed_candle_key") or "").strip()
    observation_token = closed_key or (
        f"{hidden.get('state', '')}:{hidden.get('segment_count', '')}:{latent_age}"
    )
    state_key = f"{str(symbol).strip().upper()}|{str(timeframe).strip().upper()}"
    with _LOCK:
        retained = dict(
            _STATE.get(
                state_key,
                {
                    "stable_side": "UNRESOLVED",
                    "pending_side": "UNRESOLVED",
                    "pending_confirmations": 0,
                    "unresolved_confirmations": 0,
                    "last_observation_token": "",
                    "stable_since_closed_candle": "",
                },
            )
        )
        new_observation = bool(
            observation_token
            and observation_token != retained.get("last_observation_token")
        )
        stable = _side(retained.get("stable_side"))
        if stable == "UNRESOLVED":
            if unanimous_structure and margin >= 3.0:
                stable = candidate
                retained["stable_since_closed_candle"] = closed_key
                retained["pending_side"] = "UNRESOLVED"
                retained["pending_confirmations"] = 0
            elif new_observation and candidate in {"BUY", "SELL"}:
                if retained.get("pending_side") == candidate:
                    retained["pending_confirmations"] = int(retained.get("pending_confirmations", 0)) + 1
                else:
                    retained["pending_side"] = candidate
                    retained["pending_confirmations"] = 1
                if int(retained["pending_confirmations"]) >= 2:
                    stable = candidate
                    retained["stable_since_closed_candle"] = closed_key
                    retained["pending_side"] = "UNRESOLVED"
                    retained["pending_confirmations"] = 0
        elif candidate == stable:
            retained["pending_side"] = "UNRESOLVED"
            retained["pending_confirmations"] = 0
            retained["unresolved_confirmations"] = 0
        elif candidate in {"BUY", "SELL"} and new_observation:
            if retained.get("pending_side") == candidate:
                retained["pending_confirmations"] = int(retained.get("pending_confirmations", 0)) + 1
            else:
                retained["pending_side"] = candidate
                retained["pending_confirmations"] = 1
            if (
                int(retained["pending_confirmations"]) >= _SWITCH_CONFIRMATIONS
                and scores[candidate] - scores[stable] >= 2.0
            ):
                stable = candidate
                retained["stable_since_closed_candle"] = closed_key
                retained["pending_side"] = "UNRESOLVED"
                retained["pending_confirmations"] = 0
        elif candidate == "UNRESOLVED" and new_observation:
            retained["unresolved_confirmations"] = int(retained.get("unresolved_confirmations", 0)) + 1
            if int(retained["unresolved_confirmations"]) >= _SWITCH_CONFIRMATIONS:
                stable = "UNRESOLVED"
                retained["stable_since_closed_candle"] = ""
        retained["stable_side"] = stable
        if observation_token:
            retained["last_observation_token"] = observation_token
        _STATE[state_key] = retained
        _STATE.move_to_end(state_key)
        while len(_STATE) > _MAX_TRACKED_MARKETS:
            _STATE.popitem(last=False)

    matching = [row for row in lines if row["direction"] == stable]
    opposing = [row for row in lines if row["direction"] not in {stable, "UNRESOLVED"}]
    matching.sort(
        key=lambda row: (
            row["current_touch"],
            row["near_now"],
            row["confirmed"],
            row["scope"] == "MAJOR",
            row["touch_count"],
        ),
        reverse=True,
    )
    opposing.sort(
        key=lambda row: (
            row["current_touch"],
            row["near_now"],
            row["confirmed"],
            row["touch_count"],
        ),
        reverse=True,
    )
    matching_line = matching[0] if matching else None
    opposing_line = opposing[0] if opposing else None
    confirmed_reaction = bool(
        matching_line
        and matching_line["confirmed"]
        and matching_line["current_touch"]
        and not matching_line["forming_touch"]
    )
    developing_reaction = bool(
        matching_line and matching_line["confirmed"] and matching_line["forming_touch"]
    )
    opposing_force_near = bool(
        opposing_line
        and opposing_line["confirmed"]
        and (opposing_line["near_now"] or opposing_line["current_touch"])
    )
    if stable == "UNRESOLVED":
        status = "DIRECTION_CONFLICT"
        explanation = "BUY and SELL evidence is too close; no dominant direction is published."
    elif confirmed_reaction:
        status = "STRUCTURALLY_CONFIRMED_CONTROL"
        explanation = (
            f"{stable} direction is stable and a completed candle confirmed reaction at the side-matched {matching_line['role']} line."
        )
    elif developing_reaction:
        status = "STABLE_DIRECTION_TOUCH_DEVELOPING"
        explanation = (
            f"{stable} remains dominant, but the side-matched line reaction is still forming."
        )
    elif opposing_force_near:
        status = "STABLE_DIRECTION_AT_OPPOSING_FORCE"
        explanation = (
            f"{stable} remains the dominant structure, while confirmed {opposing_line['direction']} {opposing_line['role']} is nearby; this is opposing force, not a direction flip."
        )
    elif matching_line and matching_line["confirmed"]:
        status = "STABLE_DIRECTION_AWAITING_REACTION"
        explanation = (
            f"{stable} direction is stable; its {matching_line['role']} line is confirmed but no current completed-candle reaction exists."
        )
    elif matching_line:
        status = "STABLE_DIRECTION_DEVELOPING_LINE"
        explanation = (
            f"{stable} direction is stable; the side-matched {matching_line['role']} line has {matching_line['touch_count']} touches and remains developing."
        )
    else:
        status = "STABLE_DIRECTION_NO_MATCHING_LINE"
        explanation = f"{stable} direction is stable from multi-axis evidence; no valid side-matched current line exists."

    total_score = max(1e-9, scores["BUY"] + scores["SELL"])
    consensus = {
        "schema_version": DIRECTIONAL_CONSENSUS_SCHEMA_VERSION,
        "status": status,
        "stable_side": stable,
        "raw_candidate_side": candidate,
        "buy_score": round(scores["BUY"], 4),
        "sell_score": round(scores["SELL"], 4),
        "score_margin": round(margin, 4),
        "confidence": round(abs(scores["BUY"] - scores["SELL"]) / total_score, 6),
        "major_side": major,
        "global_side": global_direction,
        "local_regression_side": local_direction,
        "latent_local_side": latent_side,
        "latent_age_candles": latent_age,
        "pending_switch_side": _side(retained.get("pending_side")),
        "pending_switch_confirmations": int(retained.get("pending_confirmations", 0)),
        "switch_confirmations_required": _SWITCH_CONFIRMATIONS,
        "stable_since_closed_candle": str(retained.get("stable_since_closed_candle") or ""),
        "evidence": evidence,
        "matching_line": _public_line(matching_line),
        "opposing_line": _public_line(opposing_line),
        "opposing_force_near": opposing_force_near,
        "explanation": explanation,
        "study_only": True,
        "execution_authority": False,
        "grants_entry_permission": False,
    }
    control.update(
        {
            "side": stable if confirmed_reaction else "UNRESOLVED",
            "directional_side": stable,
            "candidate_side": candidate,
            "local_leg_side": latent_side,
            "status": status,
            "basis": "adaptive_multi_axis_directional_consensus",
            "explanation": explanation,
            "reaction_side": stable if confirmed_reaction or developing_reaction else "UNRESOLVED",
            "reaction_status": (
                "CLOSED_CANDLE_REACTION_CONFIRMED"
                if confirmed_reaction
                else "LIVE_TOUCH_DEVELOPING"
                if developing_reaction
                else "NO_CURRENT_CONFIRMED_LINE_REACTION"
            ),
            "entry_instruction": False,
            "execution_authority": False,
            "structural_evidence": {
                "strict_trendline_count": len(lines),
                "confirmed_trendline_count": sum(1 for row in lines if row["confirmed"]),
                "selected_line": _public_line(matching_line),
                "opposing_line": _public_line(opposing_line),
                "opposing_force_side": (
                    str(opposing_line["direction"]) if opposing_force_near and opposing_line else "UNRESOLVED"
                ),
                "reaction_status": (
                    "CLOSED_CANDLE_REACTION_CONFIRMED"
                    if confirmed_reaction
                    else "LIVE_TOUCH_DEVELOPING"
                    if developing_reaction
                    else "NO_CURRENT_CONFIRMED_LINE_REACTION"
                ),
            },
            "directional_consensus_v3": consensus,
        }
    )
    components = _mapping(latent.get("directional_components"))
    for side in ("BUY", "SELL"):
        component = _mapping(components.get(side))
        component["dominant_structure"] = stable == side
        component["local_leg_active"] = latent_side == side
        component["structural_control_confirmed"] = bool(
            confirmed_reaction and stable == side
        )
        components[side] = component
    latent["control"] = control
    latent["directional_components"] = components
    latent["directional_consensus_v3"] = consensus
    study[latent_key] = latent
    return study


__all__ = [
    "DIRECTIONAL_CONSENSUS_SCHEMA_VERSION",
    "reset_directional_consensus_v3",
    "resolve_directional_consensus_v3",
]
