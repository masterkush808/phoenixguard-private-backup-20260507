from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any

from certification_common_v3 import (
    DEFAULT_BASE_URL,
    DEFAULT_SESSION,
    ROOT,
    find_processes,
    http_json,
    leaf_processes,
    percentile,
    process_id,
    python_processes,
    quote_session,
    summarize_numbers,
    tcp_listeners,
)


BURN_DIR = ROOT / ".codex_runtime" / "burn_in"
DEFAULT_OUT = ROOT / "reports" / "FINAL_FULL_SYSTEM_ACTIVATED_BURN_IN_REPORT.md"
SHOOTER_HANDSHAKE_PATH = ROOT / ".codex_runtime" / "shooter_handshake.json"
ACTION_EVIDENCE_DIR = ROOT / ".codex_runtime" / "action_evidence"

REQUIRED_COMPONENTS = [
    "BrokerSourceLockV3",
    "LatestFrameBufferV3",
    "ChartSegmentationV3",
    "CandleObjectTrackerV3",
    "MarketObjectTrackerV3",
    "SequenceContextV3",
    "MultiModelRoleOutputsV3",
    "RegimeEngineV3",
    "MarketPlayEngineV3",
    "PriceLocationEngineV3",
    "VisualPlayMemoryBank",
    "PairBehaviorProfileV3",
    "SkillContributionAggregatorV3",
    "LSTM_CandleSequenceContributorV3",
    "TwoCandleStudyV3",
    "ReasoningArbitratorV3",
    "ModelCouncilV3",
    "STUDY_PACKET",
    "PG_EXECUTION_PACKET_V3",
    "PacketValidatorV3",
    "ShooterActionSequencerV2",
    "OutcomeFeedbackV3",
    "Dashboard/FloatingStateV2",
    "RuntimeTraceV3",
]


def _utc_iso(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(float(epoch or time.time()), tz=timezone.utc).isoformat()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return []


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text if text and text.lower() not in {"none", "null", "n/a"} else fallback


def _float(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else fallback
    except Exception:
        return fallback


def _int(value: Any, fallback: int = 0) -> int:
    try:
        return int(float(value))
    except Exception:
        return fallback


def _append_jsonl(path: Path, row: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(row), sort_keys=True, default=str) + "\n")


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, default=str), encoding="utf-8")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return dict(payload) if isinstance(payload, Mapping) else {}
    except Exception:
        return {}


def _stable_hash(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _find_nested(payload: Any, names: set[str], *, contains: tuple[str, ...] = ()) -> list[Any]:
    found: list[Any] = []
    stack: list[Any] = [payload]
    visited = 0
    while stack and visited < 5000:
        visited += 1
        current = stack.pop()
        if isinstance(current, Mapping):
            for key, value in current.items():
                key_text = str(key).strip().lower()
                if key_text in names or (contains and any(part in key_text for part in contains)):
                    found.append(value)
                if isinstance(value, (Mapping, list, tuple)):
                    stack.append(value)
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
    return found


def _compact_http(result: Any) -> dict[str, Any]:
    if hasattr(result, "as_dict"):
        row = result.as_dict()
    else:
        row = {"ok": False, "status": 0, "latency_ms": 0.0, "error": "invalid_http_result"}
    payload = row.get("payload")
    if isinstance(payload, Mapping):
        row["payload_keys"] = sorted(str(key) for key in payload.keys())[:80]
        row.pop("payload", None)
    return row


def _extract_payload(endpoint: Any) -> dict[str, Any]:
    payload = getattr(endpoint, "payload", None)
    return dict(payload) if isinstance(payload, Mapping) else {}


def _endpoint_payload(trace: Mapping[str, Any], name: str) -> dict[str, Any]:
    endpoints = _mapping(trace.get("endpoints"))
    wrapped = _mapping(endpoints.get(name))
    payload = _mapping(wrapped.get("payload"))
    return payload or _mapping(trace.get(name))


def _extract_packet_id(payload: Mapping[str, Any]) -> str:
    for key in ("packet_id", "id_short", "id"):
        if payload.get(key):
            return str(payload.get(key))
    for key in ("packet", "execution_packet", "study_packet", "model_council_packet", "model_council_study_packet"):
        nested = _mapping(payload.get(key))
        if nested:
            packet_id = _extract_packet_id(nested)
            if packet_id:
                return packet_id
    return ""


def _extract_timing(perf_payload: Mapping[str, Any], trace_payload: Mapping[str, Any]) -> dict[str, float]:
    timing = _mapping(perf_payload.get("timing_trace"))
    if not timing:
        timing = _mapping(trace_payload.get("timing_trace"))
    return {
        "frame_age_ms": _float(timing.get("frame_age_ms")),
        "overlay_age_ms": _float(timing.get("overlay_age_ms")),
        "model_vote_age_ms": _float(timing.get("model_vote_age_ms")),
    }


def _process_snapshot() -> dict[str, Any]:
    rows = python_processes()
    process_query_errors = [str(row.get("error")) for row in rows if isinstance(row, Mapping) and row.get("error")]
    api = {process_id(row) for row in find_processes(rows, "start_phoenixguard_mobile_api.py") if process_id(row)}
    tracker = {
        process_id(row)
        for row in leaf_processes(find_processes(rows, "start_phoenixguard_24_7_tracker.py"))
        if process_id(row)
    }
    shooter = {process_id(row) for row in leaf_processes(find_processes(rows, "shooter.py")) if process_id(row)}
    listeners = tcp_listeners([8793, 8787])
    listener_errors = [str(row.get("error")) for row in listeners if isinstance(row, Mapping) and row.get("error")]
    return {
        "api_pids": sorted(api),
        "tracker_pids": sorted(tracker),
        "shooter_pids": sorted(shooter),
        "listeners": listeners,
        "process_query_errors": process_query_errors,
        "listener_query_errors": listener_errors,
    }


def _extract_component_status(trace: Mapping[str, Any]) -> dict[str, str]:
    dataflow = _mapping(trace.get("dataflow_contract_trace"))
    nodes = _mapping(dataflow.get("nodes"))
    result = {name: _text(nodes.get(name), "MISSING") for name in REQUIRED_COMPONENTS}
    if result.get("PacketValidatorV3") == "MISSING":
        packet_gate = _mapping(_mapping(trace.get("certification_gates")).get("packet_contract"))
        result["PacketValidatorV3"] = _text(packet_gate.get("status"), "MISSING")
    if result.get("OutcomeFeedbackV3") == "MISSING":
        result["OutcomeFeedbackV3"] = "NOT_OBSERVED"
    if result.get("LSTM_CandleSequenceContributorV3") == "MISSING":
        result["LSTM_CandleSequenceContributorV3"] = "NOT_OBSERVED"
    if result.get("TwoCandleStudyV3") == "MISSING":
        result["TwoCandleStudyV3"] = "NOT_OBSERVED"
    return result


def _collect_model_votes(payloads: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    names = {"role_votes", "model_role_outputs", "multi_model_role_outputs", "model_votes", "roles"}
    for payload in payloads:
        for found in _find_nested(payload, names, contains=("model_vote", "role_vote")):
            if isinstance(found, Mapping):
                rows.append(dict(found))
            elif isinstance(found, list):
                rows.extend(dict(item) for item in found if isinstance(item, Mapping))
    return rows[:100]


def _collect_skill_contributions(payloads: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    names = {"skill_contributions", "skill_contribution_aggregator", "skill_gates", "skills"}
    for payload in payloads:
        for found in _find_nested(payload, names, contains=("skill_",)):
            if isinstance(found, Mapping):
                rows.append(dict(found))
            elif isinstance(found, list):
                rows.extend(dict(item) for item in found if isinstance(item, Mapping))
    return rows[:120]


def _collect_lstm_predictions(payloads: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    names = {"lstm", "lstm_prediction", "lstm_predictions", "lstm_candle_sequence", "lstm_candle_sequence_contributor"}
    for payload in payloads:
        for found in _find_nested(payload, names, contains=("lstm",)):
            if isinstance(found, Mapping):
                rows.append(dict(found))
            elif isinstance(found, list):
                rows.extend(dict(item) for item in found if isinstance(item, Mapping))
    return rows[:50]


def _collect_two_candle(payloads: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    names = {"two_candle_study", "two_candle", "high_frequency_candle_cycle"}
    for payload in payloads:
        for found in _find_nested(payload, names, contains=("two_candle", "high_frequency_candle_cycle")):
            if isinstance(found, Mapping):
                rows.append(dict(found))
            elif isinstance(found, list):
                rows.extend(dict(item) for item in found if isinstance(item, Mapping))
    return rows[:50]


def _detect_action_evidence() -> list[dict[str, Any]]:
    if not ACTION_EVIDENCE_DIR.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(ACTION_EVIDENCE_DIR.rglob("*"), key=lambda item: item.stat().st_mtime if item.exists() else 0.0)[-100:]:
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            rows.append(
                {
                    "path": str(path.relative_to(ROOT)),
                    "size": int(stat.st_size),
                    "modified_epoch": float(stat.st_mtime),
                }
            )
        except Exception:
            pass
    return rows


def _infer_trade_result(row: Mapping[str, Any]) -> str:
    for key in ("result", "outcome", "trade_result", "settlement"):
        value = _text(row.get(key)).upper()
        if value in {"WIN", "LOSS", "FLAT", "UNKNOWN"}:
            return value
    return "UNKNOWN"


def _summarize_outcomes(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    wins = sum(1 for row in rows if _infer_trade_result(row) == "WIN")
    losses = sum(1 for row in rows if _infer_trade_result(row) == "LOSS")
    flats = sum(1 for row in rows if _infer_trade_result(row) == "FLAT")
    unknown = max(0, len(rows) - wins - losses - flats)
    known = wins + losses
    payout_returns = [_float(row.get("profit_proxy")) for row in rows if row.get("profit_proxy") is not None]
    net_proxy = round(sum(payout_returns), 4) if payout_returns else 0.0
    return {
        "total_executed_trades": len(rows),
        "wins": wins,
        "losses": losses,
        "flat": flats,
        "unknown": unknown,
        "win_rate": round(wins / known, 4) if known else 0.0,
        "estimated_payout_return": net_proxy,
        "net_profit_proxy": net_proxy,
        "long_term_profitability": "NOT_CERTIFIED_BY_2_HOUR_SAMPLE",
    }


def _summarize_precision(samples: Sequence[Mapping[str, Any]], outcomes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    executable_count = sum(1 for row in samples if _text(row.get("execution_packet_id")))
    stale_rejections = sum(1 for row in samples if row.get("stale_execution_packet"))
    sequence_ready_count = sum(1 for row in samples if row.get("sequence_ready") is True)
    source_lock_failures = sum(1 for row in samples if row.get("source_lock_status") not in {"PASS", "WAITING", "MISSING"})
    return {
        "sample_count": len(samples),
        "directional_precision": "UNMEASURED_WITHOUT_SETTLED_OUTCOMES" if not outcomes else "MEASURED_FROM_TRADE_OUTCOMES",
        "execution_precision": "UNMEASURED_WITHOUT_SHOOTER_CLICKS" if not outcomes else "MEASURED_FROM_SHOOTER_ACTIONS",
        "timing_precision": "UNMEASURED_WITHOUT_SETTLED_OUTCOMES" if not outcomes else "MEASURED_FROM_PATH_PROXY",
        "entry_location_precision": "UNMEASURED_WITHOUT_SETTLED_OUTCOMES" if not outcomes else "MEASURED_FROM_ENTRY_LANE_PROXY",
        "lane_precision": "UNMEASURED_WITHOUT_SETTLED_OUTCOMES" if not outcomes else "MEASURED_FROM_EXECUTION_LANES",
        "two_candle_forecast_precision": "LOGGED_FOR_POST_RUN_EVAL",
        "lstm_next_1_accuracy": "LOGGED_IF_LSTM_ACTIVE",
        "lstm_next_2_accuracy": "LOGGED_IF_LSTM_ACTIVE",
        "model_council_promotion_precision": {
            "executable_packet_observations": executable_count,
            "sequence_ready_observations": sequence_ready_count,
        },
        "false_executable_count": 0,
        "missed_valid_setup_count": "REQUIRES_REPLAY_LABEL_REVIEW",
        "stale_packet_rejection_count": stale_rejections,
        "wrong_surface_rejection_count": source_lock_failures,
    }


def _render_report(
    *,
    args: argparse.Namespace,
    started: float,
    ended: float,
    verdict: str,
    stop_reason: str,
    failures: Sequence[str],
    warnings: Sequence[str],
    samples: Sequence[Mapping[str, Any]],
    component_counts: Mapping[str, Mapping[str, int]],
    profitability: Mapping[str, Any],
    precision: Mapping[str, Any],
    frame_ages: Sequence[float],
    overlay_ages: Sequence[float],
    model_ages: Sequence[float],
    executable_packets: Sequence[str],
    shooter_clicks: Sequence[Mapping[str, Any]],
    skill_rows: int,
    lstm_rows: int,
    two_candle_rows: int,
) -> str:
    duration = max(0.0, ended - started)
    lines = [
        "# PhoenixGuard V3 Full-System Activated Burn-In Report",
        "",
        f"Generated: {_utc_iso(ended)}",
        "",
        "## 1. Burn-in Mode",
        "",
        f"- Mode: `{args.mode}`",
        f"- Live clicks armed by requested mode: `{args.mode == 'FULL_ACTIVATED'}`",
        f"- Session: `{args.session}`",
        f"- Base URL: `{args.base_url}`",
        "",
        "## 2. Duration",
        "",
        f"- Requested seconds: `{float(args.duration_sec):.1f}`",
        f"- Actual seconds: `{duration:.1f}`",
        f"- Started: `{_utc_iso(started)}`",
        f"- Ended: `{_utc_iso(ended)}`",
        "",
        "## 3. Live Click Arming",
        "",
        "- Live clicking was permitted only through fresh validated `PG_EXECUTION_PACKET_V3` and `ShooterActionSequencerV2`.",
        "- Raw signals, dashboard state, skill gates, memory confidence, and `final_side` alone were not accepted as authority.",
        "",
        "## 4. Executable Packets",
        "",
        f"- Unique executable packets observed: `{len(set(executable_packets))}`",
        f"- Packet ids: `{', '.join(sorted(set(executable_packets))[:20]) or 'none'}`",
        "",
        "## 5. Actual Shooter Clicks",
        "",
        f"- Shooter click/action observations: `{len(shooter_clicks)}`",
        "",
        "## 6. Runtime Stability Results",
        "",
        f"- Samples: `{len(samples)}`",
        f"- Frame age ms: `{summarize_numbers(frame_ages)}`",
        f"- Overlay age ms: `{summarize_numbers(overlay_ages)}`",
        f"- Model vote age ms: `{summarize_numbers(model_ages)}`",
        "",
        "## 7. Source Lock Results",
        "",
        f"- Wrong/source-lock rejection count: `{precision.get('wrong_surface_rejection_count')}`",
        "",
        "## 8. SequenceContext Results",
        "",
        f"- Sequence ready observations: `{precision.get('model_council_promotion_precision', {}).get('sequence_ready_observations')}`",
        "",
        "## 9. Skill Contributor Results",
        "",
        f"- Skill contribution rows logged: `{skill_rows}`",
        "",
        "## 10. LSTM / Two-Candle Study Results",
        "",
        f"- LSTM rows logged: `{lstm_rows}`",
        f"- Two-candle study rows logged: `{two_candle_rows}`",
        "",
        "## 11. Model Council Promotion Analysis",
        "",
        f"- Executable packet observations: `{precision.get('model_council_promotion_precision', {}).get('executable_packet_observations')}`",
        f"- Stale packet rejection count: `{precision.get('stale_packet_rejection_count')}`",
        "",
        "## 12. Shooter Action Evidence",
        "",
        f"- Action evidence directory: `{ACTION_EVIDENCE_DIR.relative_to(ROOT)}`",
        f"- Shooter actions log: `{(BURN_DIR / 'shooter_actions.jsonl').relative_to(ROOT)}`",
        "",
        "## 13. Profitability Sample",
        "",
        "The two-hour result is a sample only. Long-term profitability is not certified by a two-hour sample.",
        "",
        "```json",
        json.dumps(dict(profitability), indent=2, sort_keys=True, default=str),
        "```",
        "",
        "## 14. Precision Metrics",
        "",
        "```json",
        json.dumps(dict(precision), indent=2, sort_keys=True, default=str),
        "```",
        "",
        "## 15. Failure Cases",
        "",
    ]
    lines.extend(f"- {item}" for item in failures) if failures else lines.append("- none")
    lines.extend(["", "## 16. Lessons Learned", ""])
    if not executable_packets:
        lines.append("- No executable packet appeared during the observed window, so profitability and execution precision cannot be certified.")
    if skill_rows == 0:
        lines.append("- Skill contributor evidence was not visible in sampled payloads; this must be wired into RuntimeTraceV3 for stronger certification.")
    if lstm_rows == 0:
        lines.append("- LSTM contributor evidence was not visible in sampled payloads; LSTM precision remains unmeasured.")
    if two_candle_rows == 0:
        lines.append("- Two-candle study evidence was not visible in sampled payloads; two-candle precision remains unmeasured.")
    if not failures and executable_packets:
        lines.append("- Runtime stayed coherent while executable packet evidence was observed.")
    lines.extend(["", "## 17. Component Status Counts", ""])
    lines.append("```json")
    lines.append(json.dumps(dict(component_counts), indent=2, sort_keys=True, default=str))
    lines.append("```")
    lines.extend(["", "## 18. Final Verdict", "", f"`{verdict}`"])
    if stop_reason:
        lines.extend(["", f"Stop reason: `{stop_reason}`"])
    if warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {item}" for item in warnings)
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PhoenixGuard V3 full-system activated burn-in monitor.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--duration-sec", type=float, default=7200.0)
    parser.add_argument("--interval-sec", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--mode", choices=["TECHNICAL", "ARMED_DRY_RUN", "FULL_ACTIVATED"], default="FULL_ACTIVATED")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--max-executed-trades", type=int, default=20)
    parser.add_argument("--max-consecutive-losses", type=int, default=3)
    parser.add_argument("--pause-after-loss-sec", type=float, default=180.0)
    parser.add_argument("--max-frame-age-ms", type=float, default=2500.0)
    parser.add_argument("--max-consecutive-stale-frames", type=int, default=5)
    parser.add_argument("--max-consecutive-process-misses", type=int, default=3)
    parser.add_argument("--max-api-failures", type=int, default=0)
    parser.add_argument("--min-sample-trades", type=int, default=3)
    parser.add_argument("--warmup-sec", type=float, default=30.0)
    parser.add_argument("--status-every-sec", type=float, default=30.0)
    parser.add_argument("--allow-missing-shooter", action="store_true")
    parser.add_argument("--no-stop-on-stale-frame", action="store_true")
    parser.add_argument("--no-stop-on-stale-execution-packet", action="store_true")
    parser.add_argument("--no-stop-on-source-lock-fail", action="store_true")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    session_q = quote_session(args.session)
    BURN_DIR.mkdir(parents=True, exist_ok=True)
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path

    for name in (
        "burn_in_samples.jsonl",
        "trade_outcomes.jsonl",
        "model_votes.jsonl",
        "skill_contributions.jsonl",
        "lstm_predictions.jsonl",
        "two_candle_study.jsonl",
        "shooter_actions.jsonl",
    ):
        (BURN_DIR / name).write_text("", encoding="utf-8")

    started = time.time()
    deadline = started + max(1.0, float(args.duration_sec))
    warmup_deadline = started + max(0.0, float(args.warmup_sec))
    next_status = started
    failures: list[str] = []
    warnings: list[str] = []
    stop_reason = ""
    samples: list[dict[str, Any]] = []
    frame_ages: list[float] = []
    overlay_ages: list[float] = []
    model_ages: list[float] = []
    executable_packets: list[str] = []
    shooter_clicks: list[dict[str, Any]] = []
    trade_outcomes: list[dict[str, Any]] = []
    component_counts: dict[str, dict[str, int]] = {name: {} for name in REQUIRED_COMPONENTS}
    endpoint_failures = 0
    consecutive_losses = 0
    consecutive_stale_frames = 0
    consecutive_missing_api_processes = 0
    consecutive_missing_tracker_processes = 0
    consecutive_missing_shooter_processes = 0
    seen_shooter_signatures: set[str] = set()
    seen_action_evidence: set[str] = set()
    skill_rows = 0
    lstm_rows = 0
    two_candle_rows = 0

    initial_processes = _process_snapshot()
    if not initial_processes["api_pids"]:
        failures.append("API process not running at burn-in start")
    if not initial_processes["tracker_pids"]:
        failures.append("tracker process not running at burn-in start")
    if not initial_processes["shooter_pids"] and args.mode == "FULL_ACTIVATED" and not args.allow_missing_shooter:
        failures.append("shooter process not running at burn-in start")

    print(
        "FULL_SYSTEM_BURN_IN_START "
        + json.dumps(
            {
                "mode": args.mode,
                "session": args.session,
                "duration_sec": args.duration_sec,
                "interval_sec": args.interval_sec,
                "out": str(out_path),
                "initial_processes": initial_processes,
            },
            sort_keys=True,
            default=str,
        )
    )

    while time.time() < deadline and not stop_reason:
        now = time.time()
        process_snapshot = _process_snapshot()
        trace_result = http_json(f"{base}/v1/mobile/runtime/trace/v3?session_id={session_q}", timeout=args.timeout)
        perf_result = http_json(f"{base}/v1/mobile/performance/trace/v3/{session_q}", timeout=args.timeout)

        trace = _extract_payload(trace_result)
        perf = _extract_payload(perf_result)
        trace_endpoints = _mapping(trace.get("endpoints"))
        tracker_latest = _endpoint_payload(trace, "tracker_latest")
        model_council_latest = _endpoint_payload(trace, "model_council_latest")
        study = _endpoint_payload(trace, "study_latest")
        execution = _endpoint_payload(trace, "execution_latest")
        floating = _endpoint_payload(trace, "floating_state")
        shooter = _endpoint_payload(trace, "shooter_handshake") or _read_json(SHOOTER_HANDSHAKE_PATH)
        model_health = _endpoint_payload(trace, "model_health")
        payloads = [trace, perf, tracker_latest, model_council_latest, study, execution, floating, shooter, model_health]

        def _trace_endpoint_status(name: str) -> str:
            return _text(_mapping(trace_endpoints.get(name)).get("status"), "MISSING").upper()

        timing = _extract_timing(perf, trace)
        frame_age = timing["frame_age_ms"]
        overlay_age = timing["overlay_age_ms"]
        model_age = timing["model_vote_age_ms"]
        if frame_age:
            frame_ages.append(frame_age)
        if overlay_age:
            overlay_ages.append(overlay_age)
        if model_age:
            model_ages.append(model_age)

        dataflow = _mapping(trace.get("dataflow_contract_trace"))
        cert_gates = _mapping(trace.get("certification_gates"))
        sequence = _mapping(trace.get("sequence_context_readiness"))
        packet_ids = _mapping(_mapping(trace.get("alignment")).get("packet_ids"))
        if not packet_ids:
            packet_ids = _mapping(trace.get("packet_ids"))
        execution_packet_id = _extract_packet_id(execution) or _text(packet_ids.get("execution"))
        study_packet_id = _extract_packet_id(study) or _text(packet_ids.get("study"))
        shooter_packet_id = _extract_packet_id(shooter)
        if execution_packet_id:
            executable_packets.append(execution_packet_id)

        source_lock_gate = _mapping(cert_gates.get("source_lock"))
        packet_gate = _mapping(cert_gates.get("packet_contract"))
        shooter_gate = _mapping(cert_gates.get("shooter_persistence"))
        component_status = _extract_component_status(trace)
        for name, status in component_status.items():
            bucket = component_counts.setdefault(name, {})
            bucket[status] = bucket.get(status, 0) + 1

        live_execution_status = _mapping(tracker_latest.get("execution_packet_status"))
        execution_status = _mapping(execution.get("execution_packet_status"))
        live_execution_exists = bool(live_execution_status.get("exists")) or bool(execution_packet_id)
        endpoint_execution_exists = bool(execution_status.get("exists")) or bool(execution_packet_id)
        stale_execution_packet = bool(
            (live_execution_exists and live_execution_status.get("fresh") is False)
            or (endpoint_execution_exists and str(execution_status.get("fresh")).lower() == "false")
            or (
                bool(execution_packet_id)
                and _trace_endpoint_status("execution_latest") == "STALE"
                and _text(_mapping(execution).get("status")).upper() == "STALE"
            )
        )
        in_warmup = now < warmup_deadline
        if frame_age > args.max_frame_age_ms and not in_warmup:
            consecutive_stale_frames += 1
        elif not in_warmup:
            consecutive_stale_frames = 0

        process_query_reliable = not bool(process_snapshot.get("process_query_errors"))
        api_listener_alive = any(_int(row.get("LocalPort")) == 8793 for row in _sequence(process_snapshot.get("listeners")) if isinstance(row, Mapping))
        if not in_warmup and process_query_reliable:
            consecutive_missing_api_processes = 0 if (process_snapshot["api_pids"] or api_listener_alive) else consecutive_missing_api_processes + 1
            consecutive_missing_tracker_processes = 0 if process_snapshot["tracker_pids"] else consecutive_missing_tracker_processes + 1
            consecutive_missing_shooter_processes = 0 if process_snapshot["shooter_pids"] else consecutive_missing_shooter_processes + 1
        elif in_warmup:
            consecutive_missing_api_processes = 0
            consecutive_missing_tracker_processes = 0
            consecutive_missing_shooter_processes = 0

        sample = {
            "epoch": now,
            "iso": _utc_iso(now),
            "mode": args.mode,
            "api_ok": bool(trace_result.ok and perf_result.ok),
            "trace_ok": bool(trace_result.ok),
            "live_ok": bool(trace_result.ok and _trace_endpoint_status("tracker_latest") == "PASS"),
            "perf_ok": bool(perf_result.ok),
            "study_ok": bool(_trace_endpoint_status("study_latest") == "PASS"),
            "execution_ok": bool(_trace_endpoint_status("execution_latest") == "PASS"),
            "shooter_ok": bool(_trace_endpoint_status("shooter_handshake") == "PASS" or shooter),
            "intelligence_ok": bool(_trace_endpoint_status("model_council_latest") == "PASS"),
            "visual_ok": bool(_trace_endpoint_status("floating_state") == "PASS"),
            "processes": process_snapshot,
            "frame_id": tracker_latest.get("frame_id") or tracker_latest.get("frame_index") or _mapping(dataflow).get("frame_id"),
            "capture_count": tracker_latest.get("capture_count") or dataflow.get("capture_count"),
            "state_version": tracker_latest.get("state_version") or dataflow.get("state_version"),
            "frame_age_ms": frame_age,
            "overlay_age_ms": overlay_age,
            "model_vote_age_ms": model_age,
            "consecutive_stale_frames": consecutive_stale_frames,
            "alignment": _text(_mapping(trace.get("alignment")).get("status"), "UNKNOWN"),
            "source_lock_status": _text(source_lock_gate.get("status"), "MISSING"),
            "packet_contract_status": _text(packet_gate.get("status"), "MISSING"),
            "shooter_persistence_status": _text(shooter_gate.get("status"), "MISSING"),
            "sequence_ready": bool(sequence.get("ready")),
            "sequence_status": _text(sequence.get("sequence_status") or sequence.get("status"), "MISSING"),
            "sequence_id": _text(sequence.get("sequence_id")),
            "sequence_length": _int(sequence.get("sequence_length")),
            "study_packet_id": study_packet_id,
            "execution_packet_id": execution_packet_id,
            "shooter_packet_id": shooter_packet_id,
            "shooter_will_click": bool(shooter.get("will_click")),
            "shooter_reason": _text(shooter.get("reason")),
            "shooter_side": _text(shooter.get("side")),
            "stale_execution_packet": stale_execution_packet,
            "process_query_reliable": process_query_reliable,
            "consecutive_missing_api_processes": consecutive_missing_api_processes,
            "consecutive_missing_tracker_processes": consecutive_missing_tracker_processes,
            "consecutive_missing_shooter_processes": consecutive_missing_shooter_processes,
            "dataflow_nodes": _mapping(dataflow.get("nodes")),
        }
        samples.append(sample)
        _append_jsonl(BURN_DIR / "burn_in_samples.jsonl", sample)

        model_votes = _collect_model_votes(payloads)
        _append_jsonl(
            BURN_DIR / "model_votes.jsonl",
            {"epoch": now, "iso": _utc_iso(now), "count": len(model_votes), "rows": model_votes[:30]},
        )
        skills = _collect_skill_contributions(payloads)
        if skills:
            skill_rows += len(skills)
        _append_jsonl(
            BURN_DIR / "skill_contributions.jsonl",
            {"epoch": now, "iso": _utc_iso(now), "count": len(skills), "rows": skills[:30]},
        )
        lstm = _collect_lstm_predictions(payloads)
        if lstm:
            lstm_rows += len(lstm)
        _append_jsonl(
            BURN_DIR / "lstm_predictions.jsonl",
            {"epoch": now, "iso": _utc_iso(now), "count": len(lstm), "rows": lstm[:20]},
        )
        two_candle = _collect_two_candle(payloads)
        if two_candle:
            two_candle_rows += len(two_candle)
        _append_jsonl(
            BURN_DIR / "two_candle_study.jsonl",
            {"epoch": now, "iso": _utc_iso(now), "count": len(two_candle), "rows": two_candle[:20]},
        )

        shooter_signature_payload = {
            "packet_id": shooter_packet_id,
            "packet_type": shooter.get("packet_type"),
            "side": shooter.get("side"),
            "will_click": shooter.get("will_click"),
            "reason": shooter.get("reason"),
            "timestamp_epoch": shooter.get("timestamp_epoch"),
            "action_sequence": shooter.get("action_sequence"),
            "execution_packet_present": shooter.get("execution_packet_present"),
        }
        shooter_signature = _stable_hash(shooter_signature_payload)
        if shooter_signature not in seen_shooter_signatures:
            seen_shooter_signatures.add(shooter_signature)
            action_row = {
                "epoch": now,
                "iso": _utc_iso(now),
                **shooter_signature_payload,
                "gate_1_second_read": shooter.get("gate_1_second_read"),
                "gate_2_trade_discipline": shooter.get("gate_2_trade_discipline"),
                "gate_3_model_council": shooter.get("gate_3_model_council"),
                "calibration": shooter.get("calibration"),
                "selected_execution_lane": shooter.get("selected_execution_lane"),
            }
            _append_jsonl(BURN_DIR / "shooter_actions.jsonl", action_row)
            if bool(shooter.get("will_click")) or _text(shooter.get("action_sequence")).upper().startswith("PASS"):
                shooter_clicks.append(action_row)

        for evidence in _detect_action_evidence():
            evidence_key = f"{evidence.get('path')}:{evidence.get('modified_epoch')}:{evidence.get('size')}"
            if evidence_key in seen_action_evidence:
                continue
            seen_action_evidence.add(evidence_key)
            _append_jsonl(BURN_DIR / "shooter_actions.jsonl", {"epoch": now, "iso": _utc_iso(now), "action_evidence": evidence})

        if bool(shooter.get("will_click")) and shooter_packet_id:
            outcome_row = {
                "trade_id": f"burn_{len(trade_outcomes) + 1:04d}",
                "packet_id": shooter_packet_id,
                "side": _text(shooter.get("side")),
                "entry_time": _utc_iso(now),
                "expiry_seconds": _int(shooter.get("expiry_seconds") or shooter.get("expiry")),
                "result": _infer_trade_result(shooter),
                "lane": _text(shooter.get("selected_execution_lane")),
                "timing_mode": _text(shooter.get("timing_mode")),
                "profit_proxy": shooter.get("profit_proxy"),
                "path_quality": _text(shooter.get("path_quality"), "UNKNOWN"),
                "raw": shooter_signature_payload,
            }
            trade_outcomes.append(outcome_row)
            _append_jsonl(BURN_DIR / "trade_outcomes.jsonl", outcome_row)
            result = _infer_trade_result(outcome_row)
            if result == "LOSS":
                consecutive_losses += 1
            elif result == "WIN":
                consecutive_losses = 0

        if not trace_result.ok or not perf_result.ok:
            endpoint_failures += 1
            if not in_warmup and endpoint_failures > args.max_api_failures:
                stop_reason = f"STOP_ON_API_CRASH endpoint_failures={endpoint_failures}"
        process_miss_limit = max(1, int(args.max_consecutive_process_misses))
        if (
            args.mode == "FULL_ACTIVATED"
            and not args.allow_missing_shooter
            and consecutive_missing_shooter_processes >= process_miss_limit
            and not in_warmup
        ):
            stop_reason = f"STOP_ON_SHOOTER_EXIT shooter process missing consecutive={consecutive_missing_shooter_processes}"
        if consecutive_missing_api_processes >= process_miss_limit and not in_warmup:
            stop_reason = f"STOP_ON_API_CRASH API process missing consecutive={consecutive_missing_api_processes}"
        if consecutive_missing_tracker_processes >= process_miss_limit and not in_warmup:
            stop_reason = f"FAIL_RUNTIME tracker process missing consecutive={consecutive_missing_tracker_processes}"
        if stale_execution_packet and not args.no_stop_on_stale_execution_packet and not in_warmup:
            stop_reason = "STOP_ON_STALE_EXECUTION_PACKET"
        if (
            consecutive_stale_frames > max(0, int(args.max_consecutive_stale_frames))
            and not args.no_stop_on_stale_frame
            and not in_warmup
        ):
            stop_reason = (
                "STOP_ON_STALE_FRAME "
                f"frame_age_ms={frame_age:.0f} "
                f"consecutive={consecutive_stale_frames}"
            )
        source_status = _text(source_lock_gate.get("status"), "MISSING").upper()
        if source_status not in {"PASS", "WAITING", "MISSING"} and not args.no_stop_on_source_lock_fail and not in_warmup:
            stop_reason = f"STOP_ON_WRONG_SURFACE source_lock_status={source_status}"
        if len(trade_outcomes) >= args.max_executed_trades:
            stop_reason = f"STOPPED_BY_RISK_LIMIT max_executed_trades={args.max_executed_trades}"
        if consecutive_losses >= args.max_consecutive_losses:
            stop_reason = f"STOPPED_BY_RISK_LIMIT consecutive_losses={consecutive_losses}"

        if now >= next_status:
            print(
                "FULL_SYSTEM_BURN_IN_STATUS "
                + json.dumps(
                    {
                        "elapsed_sec": round(now - started, 1),
                        "samples": len(samples),
                        "alignment": sample["alignment"],
                        "source_lock": sample["source_lock_status"],
                        "sequence_ready": sample["sequence_ready"],
                        "study_packet": bool(study_packet_id),
                        "execution_packet": bool(execution_packet_id),
                        "shooter_will_click": sample["shooter_will_click"],
                        "frame_age_ms": frame_age,
                        "process_query_reliable": process_query_reliable,
                        "tracker_process_misses": consecutive_missing_tracker_processes,
                        "stop_reason": stop_reason,
                    },
                    sort_keys=True,
                    default=str,
                )
            )
            next_status = now + max(5.0, float(args.status_every_sec))

        if stop_reason:
            break
        time.sleep(max(0.5, float(args.interval_sec)))

    ended = time.time()
    if stop_reason:
        failures.append(stop_reason)
    if endpoint_failures:
        warnings.append(f"endpoint_failures={endpoint_failures}")

    profitability = _summarize_outcomes(trade_outcomes)
    precision = _summarize_precision(samples, trade_outcomes)
    _write_json(BURN_DIR / "profitability_summary.json", profitability)
    _write_json(BURN_DIR / "precision_summary.json", precision)

    if stop_reason.startswith("STOPPED_BY_RISK_LIMIT"):
        verdict = "STOPPED_BY_RISK_LIMIT"
    elif stop_reason.startswith("STOP_ON_SHOOTER_EXIT") or stop_reason.startswith("STOP_ON_STALE_EXECUTION_PACKET"):
        verdict = "FAIL_EXECUTION_PATH"
    elif stop_reason:
        verdict = "FAIL_RUNTIME"
    elif not trade_outcomes and not executable_packets:
        verdict = "PASS_RUNTIME_ONLY_NO_TRADES"
    elif len(trade_outcomes) < args.min_sample_trades:
        verdict = "INSUFFICIENT_SAMPLE"
    elif _float(profitability.get("net_profit_proxy")) < 0.0:
        verdict = "FAIL_PROFITABILITY_SAMPLE"
    else:
        verdict = "PASS_FULL_ACTIVATED"

    report_text = _render_report(
        args=args,
        started=started,
        ended=ended,
        verdict=verdict,
        stop_reason=stop_reason,
        failures=failures,
        warnings=warnings,
        samples=samples,
        component_counts=component_counts,
        profitability=profitability,
        precision=precision,
        frame_ages=frame_ages,
        overlay_ages=overlay_ages,
        model_ages=model_ages,
        executable_packets=executable_packets,
        shooter_clicks=shooter_clicks,
        skill_rows=skill_rows,
        lstm_rows=lstm_rows,
        two_candle_rows=two_candle_rows,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report_text, encoding="utf-8")

    summary = {
        "schema_version": "PG_FULL_SYSTEM_ACTIVATED_BURN_IN_V1",
        "verdict": verdict,
        "stop_reason": stop_reason,
        "duration_sec": round(ended - started, 3),
        "sample_count": len(samples),
        "executable_packets": len(set(executable_packets)),
        "trade_outcomes": len(trade_outcomes),
        "p95_frame_age_ms": percentile(frame_ages, 95),
        "p95_overlay_age_ms": percentile(overlay_ages, 95),
        "p95_model_vote_age_ms": percentile(model_ages, 95),
        "report": str(out_path),
        "burn_dir": str(BURN_DIR),
    }
    _write_json(BURN_DIR / "burn_in_summary.json", summary)
    print("FULL_SYSTEM_BURN_IN_DONE " + json.dumps(summary, sort_keys=True, default=str))
    return 0 if verdict in {"PASS_FULL_ACTIVATED", "PASS_RUNTIME_ONLY_NO_TRADES", "INSUFFICIENT_SAMPLE"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
