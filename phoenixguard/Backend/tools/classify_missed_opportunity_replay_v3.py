from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, cast


SCHEMA_VERSION = "PG_MISSED_OPPORTUNITY_REPLAY_CLASSIFIER_V3"

CLASSIFICATIONS = (
    "good_wait",
    "correct_block",
    "missed_opportunity",
    "late_chase_avoided",
    "bad_block",
    "late_recognition",
)

CLEAN_BLOCKERS = {"", "NONE", "OK", "ALLOW", "ALLOWED", "NO_BLOCKER", "NOT_BLOCKED"}
ENTER_NOW_STATES = {
    "ALLOW",
    "ALLOWED",
    "AUTHORIZED",
    "ENTER_NOW",
    "EXECUTION_READY",
    "INTRADAY_ENTER_NOW",
    "MATURE_OPPORTUNITY",
    "READY",
    "SWING_ENTER_NOW",
    "TIMING_ALLOWED_STUDY_ONLY",
}
WAIT_TOKENS = (
    "EARLY",
    "PREPARE",
    "PULLBACK",
    "RETEST",
    "WAIT",
    "WATCH",
    "CONFIRM",
)
LATE_TOKENS = (
    "CHASE",
    "EXHAUST",
    "EXTENDED",
    "LATE",
    "OVEREXTENDED",
    "RUNNING_AWAY",
    "SKIP_LATE",
    "TOO_LATE",
)
SAFETY_BLOCKER_TOKENS = (
    "BAD_ENTRY",
    "BROKER",
    "DANGER",
    "FRESHNESS",
    "INSTRUMENT_CONTEXT",
    "INVALID",
    "MARKET_CLOSED",
    "NO_PATH",
    "OPPOSING",
    "RUNTIME",
    "SOURCE_LOCK",
    "STALE",
    "SURFACE",
    "TRAP",
    "WRONG_SURFACE",
)
OPERATIONAL_BLOCKER_TOKENS = (
    "ENTRY_QUALITY",
    "LANE",
    "MODEL_COUNCIL",
    "NO_EXECUTION_PACKET",
    "PACKET",
    "PLAYBOOK_HARD",
    "PROMOTION",
    "SCORE",
    "THRESHOLD",
)
LATE_CANDLE_COUNT = 8.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(cast(Sequence[Any], value))
    return []


def text(value: Any, default: str = "") -> str:
    raw = str(value if value is not None else default).strip()
    return raw or default


def token(value: Any) -> str:
    return text(value).upper().replace("-", "_").replace(" ", "_")


def side(value: Any, default: str = "") -> str:
    raw = token(value)
    return raw if raw in {"BUY", "SELL"} else default


def number(value: Any, default: float | None = None) -> float | None:
    if value in (None, "", [], {}):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        parsed = number(value, 0.0) or 0.0
        return parsed != 0.0
    if isinstance(value, Mapping):
        return len(cast(Mapping[str, Any], value)) > 0
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return len(cast(Sequence[Any], value)) > 0
    raw = token(value)
    if raw in {"0", "FALSE", "N", "NO", "NONE", "OFF"}:
        return False
    if raw in {"1", "ALLOW", "ALLOWED", "FRESH", "ON", "PASS", "TRUE", "Y", "YES"}:
        return True
    return bool(raw)


def first_present(*values: Any) -> Any:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, Mapping) and not value:
            continue
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) and not value:
            continue
        return cast(Any, value)
    return None


def contains_any(value: Any, needles: Iterable[str]) -> bool:
    haystack = token(value)
    return any(needle in haystack for needle in needles)


def clean_blocker(value: Any) -> bool:
    return token(value) in CLEAN_BLOCKERS


def state_is_enter_now(value: Any) -> bool:
    raw = token(value)
    return raw in ENTER_NOW_STATES or raw.endswith("_ENTER_NOW")


def _bool_from_sources(*values: Any) -> bool:
    found = first_present(*values)
    return bool_value(found)


def normalize_replay_row(row: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten burn/replay row variants into the fields used by the classifier."""
    root = mapping(row)
    entry = mapping(root.get("entry"))
    audit = mapping(root.get("grade_a_star_audit") or root.get("audit"))
    promotion_failure = mapping(audit.get("promotion_failure_audit_v3") or root.get("promotion_failure_audit_v3"))
    execution_opportunity = mapping(
        audit.get("execution_opportunity")
        or root.get("execution_opportunity")
        or promotion_failure.get("execution_opportunity")
    )
    council = mapping(root.get("model_council") or root.get("council") or root.get("council_response"))
    promotion = mapping(root.get("promotion_trace") or council.get("promotion_trace") or root.get("promotion"))
    timing = mapping(root.get("timing_decision") or audit.get("timing_decision") or entry.get("timing_decision") or promotion.get("timing_decision"))
    allowance = mapping(root.get("allowance_package") or entry.get("allowance_package") or promotion.get("allowance_package") or council.get("allowance_package"))
    book_strategy = mapping(
        root.get("book_strategy")
        or entry.get("book_strategy")
        or promotion.get("book_strategy")
        or council.get("book_strategy")
        or allowance.get("book_strategy")
        or promotion_failure.get("book_strategy")
    )
    book_evidence = mapping(book_strategy.get("evidence") or root.get("evidence") or entry.get("evidence") or allowance.get("evidence"))
    candle_context = mapping(
        root.get("candle_movement_context_v3")
        or root.get("candle_movement")
        or entry.get("candle_movement_context_v3")
        or entry.get("candle_movement")
    )
    current_leg = mapping(root.get("current_leg") or candle_context.get("current_leg") or book_evidence.get("current_leg"))
    frames = mapping(root.get("frames"))
    expected_move_time = mapping(
        root.get("expected_move_time")
        or entry.get("expected_move_time")
        or allowance.get("expected_move_time")
        or book_evidence.get("expected_move_time")
    )

    blocker_raw = first_present(
        root.get("blocker"),
        root.get("blocked_by"),
        root.get("denied_at"),
        root.get("top_blocker"),
        entry.get("blocked_by"),
        entry.get("denied_at"),
        entry.get("blocker"),
        promotion.get("blocked_by"),
        promotion.get("denied_at"),
        promotion_failure.get("top_blocker"),
        promotion_failure.get("denied_at"),
        audit.get("top_blocker"),
        audit.get("denied_at"),
    )
    blocker = token(blocker_raw)

    playbook_state = token(
        first_present(
            root.get("book_strategy_state"),
            root.get("playbook_state"),
            entry.get("playbook_state"),
            promotion.get("book_strategy_state"),
            council.get("book_strategy_state"),
            allowance.get("book_strategy_maturity"),
            book_strategy.get("maturity_state"),
            book_strategy.get("state"),
        )
    )
    maturity = token(
        first_present(
            root.get("maturity"),
            root.get("opportunity_maturity"),
            root.get("opportunity_maturity_state"),
            entry.get("opportunity_maturity"),
            entry.get("opportunity_maturity_state"),
            promotion.get("opportunity_maturity_state"),
            council.get("opportunity_maturity_state"),
            allowance.get("opportunity_maturity"),
            execution_opportunity.get("state"),
            execution_opportunity.get("class"),
            playbook_state,
        )
    )
    timing_mode = token(first_present(root.get("timing_mode"), entry.get("timing_mode"), timing.get("timing_mode"), timing.get("mode")))
    leg_stage = token(
        first_present(
            root.get("current_leg_stage"),
            current_leg.get("move_stage"),
            current_leg.get("stage"),
            current_leg.get("maturity"),
            current_leg.get("state"),
            book_evidence.get("current_leg_stage"),
        )
    )
    candle_count = number(
        first_present(
            root.get("current_leg_candle_count"),
            current_leg.get("candle_count"),
            current_leg.get("candles"),
            current_leg.get("count"),
            book_evidence.get("current_leg_candle_count"),
            expected_move_time.get("current_leg_candle_count"),
        )
    )
    final_score = number(first_present(root.get("final_score"), entry.get("final_score"), promotion.get("final_score"), allowance.get("score")))
    threshold = number(first_present(root.get("threshold"), entry.get("threshold"), promotion.get("threshold"), allowance.get("threshold")))

    allowed = _bool_from_sources(root.get("allowed"), root.get("entry_allowed"), entry.get("allowed"))
    entry_now_allowed = _bool_from_sources(
        root.get("entry_now_allowed"),
        entry.get("entry_now_allowed"),
        timing.get("entry_now_allowed"),
        timing.get("playbook_strategy_authorized"),
    )
    strategy_allowed = _bool_from_sources(
        root.get("strategy_allowed"),
        root.get("playbook_entry_allowed"),
        entry.get("strategy_allowed"),
        entry.get("playbook_entry_allowed"),
    )
    allowance_ready = bool(
        _bool_from_sources(allowance.get("accepted"), allowance.get("decision_accepted"))
        and _bool_from_sources(allowance.get("execution_ready"))
    )
    playbook_authorized = _bool_from_sources(
        root.get("playbook_strategy_authorized"),
        root.get("playbook_authorized"),
        entry.get("playbook_strategy_authorized"),
        timing.get("playbook_strategy_authorized"),
        allowance_ready,
    )

    packet_present = _bool_from_sources(
        root.get("execution_packet_present"),
        root.get("packet_present"),
        root.get("execution_packet"),
        root.get("model_council_packet"),
        root.get("packet_id"),
        entry.get("packet_present"),
        entry.get("packet_export_present"),
        entry.get("execution_packet_present"),
        entry.get("packet_id"),
        council.get("execution_packet_present"),
        council.get("execution_packet"),
        council.get("model_council_packet"),
        promotion.get("packet_id"),
    )
    execution_authorized = _bool_from_sources(root.get("execution_authorized"), entry.get("execution_authorized"))
    executable = bool(execution_authorized or (allowed and packet_present))

    trade_side = side(
        first_present(
            root.get("side"),
            entry.get("side"),
            timing.get("direction_side"),
            promotion.get("candidate_side"),
            promotion.get("final_side"),
            execution_opportunity.get("side"),
            current_leg.get("side"),
        )
    )

    missed_study = mapping(audit.get("missed_live_opportunity_study") or root.get("missed_live_opportunity_study"))
    missed_study_active = _bool_from_sources(
        root.get("missed_live_opportunity_study"),
        entry.get("missed_live_opportunity_study"),
        missed_study.get("active"),
    )
    score_passed = bool(final_score is not None and threshold is not None and final_score >= threshold)
    enter_now_signal = any(
        (
            entry_now_allowed,
            strategy_allowed,
            playbook_authorized,
            allowance_ready,
            packet_present,
            state_is_enter_now(maturity),
            state_is_enter_now(playbook_state),
            state_is_enter_now(execution_opportunity.get("class")),
            score_passed,
        )
    )
    strong_opportunity = bool(trade_side in {"BUY", "SELL"} and (enter_now_signal or missed_study_active))

    signal_text = " ".join(
        token(value)
        for value in (
            blocker,
            maturity,
            playbook_state,
            timing_mode,
            leg_stage,
            entry.get("next_required"),
            audit.get("next_required"),
            execution_opportunity.get("reason"),
            book_strategy.get("playbook"),
            root.get("playbook") if not isinstance(root.get("playbook"), Mapping) else "",
        )
        if text(value)
    )
    wait_signal = bool(
        contains_any(signal_text, WAIT_TOKENS)
        or token(execution_opportunity.get("class")) == "EARLY_OPPORTUNITY"
    )
    late_context = bool(
        contains_any(signal_text, LATE_TOKENS)
        or _bool_from_sources(
            root.get("late_chase"),
            root.get("late_chase_risk"),
            entry.get("directional_location_chase_risk"),
            entry.get("late_chase_risk"),
            current_leg.get("exhausted"),
            current_leg.get("late"),
            book_evidence.get("current_leg_exhausted"),
        )
        or leg_stage in {"EXHAUSTED", "EXTENDED", "LATE", "OVEREXTENDED"}
        or (leg_stage == "MATURE" and candle_count is not None and candle_count >= LATE_CANDLE_COUNT)
    )
    safety_blocker = bool(not clean_blocker(blocker) and contains_any(blocker, SAFETY_BLOCKER_TOKENS))
    operational_blocker = bool(
        not clean_blocker(blocker)
        and not safety_blocker
        and not contains_any(blocker, LATE_TOKENS)
        and (contains_any(blocker, OPERATIONAL_BLOCKER_TOKENS) or not wait_signal)
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "seq": int(number(root.get("seq"), 0.0) or 0),
        "frame_id": int(number(first_present(frames.get("display_frame_id"), frames.get("frame_id"), root.get("frame_id"), root.get("frame")), 0.0) or 0),
        "state_version": int(number(first_present(root.get("state_version"), frames.get("state_version")), 0.0) or 0),
        "capture_count": int(number(first_present(root.get("capture_count"), frames.get("capture_count")), 0.0) or 0),
        "side": trade_side,
        "allowed": allowed,
        "entry_now_allowed": entry_now_allowed,
        "strategy_allowed": strategy_allowed,
        "playbook_authorized": playbook_authorized,
        "allowance_ready": allowance_ready,
        "execution_packet_present": packet_present,
        "execution_authorized": execution_authorized,
        "executable": executable,
        "blocker": blocker,
        "clean_blocker": clean_blocker(blocker),
        "maturity": maturity,
        "playbook_state": playbook_state,
        "timing_mode": timing_mode,
        "current_leg_side": side(first_present(current_leg.get("side"), root.get("current_leg_side"))),
        "current_leg_stage": leg_stage,
        "current_leg_candle_count": candle_count,
        "final_score": final_score,
        "threshold": threshold,
        "score_passed": score_passed,
        "strong_opportunity": strong_opportunity,
        "enter_now_signal": enter_now_signal,
        "missed_study_active": missed_study_active,
        "wait_signal": wait_signal,
        "late_context": late_context,
        "safety_blocker": safety_blocker,
        "operational_blocker": operational_blocker,
    }


def classify_features(features: Mapping[str, Any]) -> dict[str, Any]:
    strong_opportunity = bool(features.get("strong_opportunity"))
    executable = bool(features.get("executable"))
    late_context = bool(features.get("late_context"))
    safety_blocker = bool(features.get("safety_blocker"))
    operational_blocker = bool(features.get("operational_blocker"))
    wait_signal = bool(features.get("wait_signal"))
    clean = bool(features.get("clean_blocker"))

    rationale: list[str] = []
    if strong_opportunity:
        rationale.append("strong_opportunity")
    if executable:
        rationale.append("executable_authorization")
    if late_context:
        rationale.append("late_context")
    if safety_blocker:
        rationale.append("safety_blocker")
    if operational_blocker:
        rationale.append("operational_blocker")
    if wait_signal:
        rationale.append("wait_signal")

    if late_context:
        if strong_opportunity or executable or bool(features.get("entry_now_allowed")) or bool(features.get("execution_packet_present")):
            classification = "late_recognition"
            reason = "entry evidence appeared after the leg was already late, mature, exhausted, or chasing"
        else:
            classification = "late_chase_avoided"
            reason = "late/chase context was blocked before executable authorization"
    elif strong_opportunity and not executable:
        if safety_blocker:
            classification = "correct_block"
            reason = "safety blocker overrode an otherwise mature opportunity"
        elif operational_blocker:
            classification = "bad_block"
            reason = "mature opportunity was stopped by an operational or authorization blocker"
        else:
            classification = "missed_opportunity"
            reason = "mature opportunity did not receive executable authorization"
    elif executable:
        if safety_blocker or operational_blocker:
            classification = "bad_block"
            reason = "executable authorization contradicted a blocker"
        else:
            classification = "good_wait"
            reason = "clean executable authorization observed"
    elif wait_signal and (clean or not safety_blocker):
        classification = "good_wait"
        reason = "row is still waiting for pullback, retest, confirmation, or preparation"
    elif safety_blocker or operational_blocker or not clean:
        classification = "correct_block"
        reason = "blocked row did not contain enough mature opportunity evidence"
    else:
        classification = "good_wait"
        reason = "no executable opportunity evidence was present"

    return {
        "schema_version": SCHEMA_VERSION,
        "classification": classification,
        "reason": reason,
        "rationale": rationale,
    }


def classify_replay_row(row: Mapping[str, Any], *, row_index: int = 0) -> dict[str, Any]:
    features = normalize_replay_row(row)
    verdict = classify_features(features)
    return {
        "schema_version": SCHEMA_VERSION,
        "row_index": row_index,
        "classification": verdict["classification"],
        "reason": verdict["reason"],
        "rationale": verdict["rationale"],
        "window": {
            "seq": features["seq"],
            "frame_id": features["frame_id"],
            "state_version": features["state_version"],
            "capture_count": features["capture_count"],
        },
        "decision": {
            "side": features["side"],
            "allowed": features["allowed"],
            "entry_now_allowed": features["entry_now_allowed"],
            "execution_packet_present": features["execution_packet_present"],
            "execution_authorized": features["execution_authorized"],
            "executable": features["executable"],
            "blocker": features["blocker"],
            "maturity": features["maturity"],
            "playbook_state": features["playbook_state"],
            "timing_mode": features["timing_mode"],
        },
        "current_leg": {
            "side": features["current_leg_side"],
            "stage": features["current_leg_stage"],
            "candle_count": features["current_leg_candle_count"],
        },
        "signals": {
            "strong_opportunity": features["strong_opportunity"],
            "enter_now_signal": features["enter_now_signal"],
            "missed_study_active": features["missed_study_active"],
            "wait_signal": features["wait_signal"],
            "late_context": features["late_context"],
            "safety_blocker": features["safety_blocker"],
            "operational_blocker": features["operational_blocker"],
            "score_passed": features["score_passed"],
        },
    }


def classify_replay_rows(rows: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [classify_replay_row(row, row_index=index) for index, row in enumerate(rows)]


def summarize_classifications(classifications: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = Counter(text(row.get("classification")) for row in classifications)
    missed_like = sum(counts[name] for name in ("missed_opportunity", "bad_block", "late_recognition"))
    return {
        "schema_version": SCHEMA_VERSION,
        "row_count": len(classifications),
        "counts": {name: counts.get(name, 0) for name in CLASSIFICATIONS},
        "missed_like_count": missed_like,
    }


def load_replay_rows(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if source.suffix.lower() == ".jsonl":
        rows: list[dict[str, Any]] = []
        with source.open("r", encoding="utf-8") as handle:
            for line_no, line in enumerate(handle, start=1):
                raw = line.strip()
                if not raw:
                    continue
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"{source}:{line_no}: invalid JSONL row: {exc}") from exc
                row = mapping(payload)
                if row:
                    rows.append(row)
        return rows

    payload = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        rows = []
        for item in cast(list[Any], payload):
            row = mapping(item)
            if row:
                rows.append(row)
        return rows
    root = mapping(payload)
    for key in ("rows", "samples", "events", "classifications"):
        values = sequence(root.get(key))
        if values:
            return [row for row in (mapping(item) for item in values) if row]
    return [root] if root else []


def write_classification_output(path: str | Path, classifications: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    summary = summarize_classifications(classifications)
    if target.suffix.lower() == ".jsonl":
        with target.open("w", encoding="utf-8") as handle:
            for row in classifications:
                handle.write(json.dumps(dict(row), ensure_ascii=True, separators=(",", ":"), default=str) + "\n")
        return summary

    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": utc_now(),
        "summary": summary,
        "classifications": list(classifications),
    }
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str) + "\n", encoding="utf-8")
    return summary


def classify_file(input_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    rows = load_replay_rows(input_path)
    classifications = classify_replay_rows(rows)
    summary = write_classification_output(output_path, classifications)
    return {
        "schema_version": SCHEMA_VERSION,
        "input": str(input_path),
        "output": str(output_path),
        "summary": summary,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify PhoenixGuard burn/replay decision authorization windows for missed opportunities."
    )
    parser.add_argument("--input", required=True, help="Path to JSON or JSONL burn/replay rows.")
    parser.add_argument("--output", required=True, help="Path to write JSON report or JSONL classifications.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = classify_file(args.input, args.output)
    print(json.dumps(result, indent=2, ensure_ascii=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
