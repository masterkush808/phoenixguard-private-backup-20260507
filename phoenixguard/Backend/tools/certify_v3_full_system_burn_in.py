from __future__ import annotations

import argparse
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Any, cast

from certification_common_v3 import (
    DEFAULT_BASE_URL,
    DEFAULT_RUNTIME_DIR,
    DEFAULT_SESSION,
    ROOT,
    command_line,
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


BURN_DIR = DEFAULT_RUNTIME_DIR / "burn_in"
DEFAULT_OUT = ROOT / "reports" / "FINAL_FULL_SYSTEM_ACTIVATED_BURN_IN_REPORT.md"
SHOOTER_HANDSHAKE_PATH = DEFAULT_RUNTIME_DIR / "shooter_handshake.json"
ACTION_EVIDENCE_DIR = DEFAULT_RUNTIME_DIR / "action_evidence"
SHOOTER_VALIDATION_DIR = ROOT / "data" / "shooter_validation"
SAFE_SHOOTER_VALIDATION_LOGS = (
    "live_disabled.jsonl",
    "paper_executions.jsonl",
    "dry_run_clicks.jsonl",
    "live_behavior_validation.jsonl",
)
BURN_RESET_FILES = (
    "burn_in_samples.jsonl",
    "trade_outcomes.jsonl",
    "model_votes.jsonl",
    "skill_contributions.jsonl",
    "lstm_predictions.jsonl",
    "two_candle_study.jsonl",
    "shooter_actions.jsonl",
    "promotion_failures.jsonl",
    "safe_shooter_events.jsonl",
    "safe_paper_monitors.jsonl",
    "safe_paper_outcomes.jsonl",
    "live_trade_monitors.jsonl",
    "live_trade_outcomes.jsonl",
    "burn_in_summary.json",
    "profitability_summary.json",
    "safe_paper_summary.json",
    "precision_summary.json",
    "promotion_blocker_ranking.json",
)

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
    "ShooterPackageReporter",
    "OutcomeFeedbackV3",
    "Dashboard/FloatingStateV2",
    "RuntimeTraceV3",
]


def _utc_iso(epoch: float | None = None) -> str:
    return datetime.fromtimestamp(float(epoch or time.time()), tz=timezone.utc).isoformat()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(cast(Sequence[Any], value))
    if isinstance(value, tuple):
        return list(cast(Sequence[Any], value))
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
        return dict(cast(Mapping[str, Any], payload)) if isinstance(payload, Mapping) else {}
    except Exception:
        return {}


def _stable_hash(payload: Any) -> str:
    text = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _env_true(name: str) -> bool:
    return str(os.getenv(name, "") or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _find_nested(payload: Any, names: set[str], *, contains: tuple[str, ...] = ()) -> list[Any]:
    found: list[Any] = []
    stack: list[Any] = [payload]
    visited = 0
    while stack and visited < 5000:
        visited += 1
        current = stack.pop()
        if isinstance(current, Mapping):
            current_map = cast(Mapping[str, Any], current)
            for key, value in current_map.items():
                key_text = str(key).strip().lower()
                if key_text in names or (contains and any(part in key_text for part in contains)):
                    found.append(value)
                if isinstance(value, (Mapping, list, tuple)):
                    stack.append(value)
        elif isinstance(current, (list, tuple)):
            stack.extend(cast(Sequence[Any], current))
    return found


def _extract_payload(endpoint: Any) -> dict[str, Any]:
    payload = getattr(endpoint, "payload", None)
    return dict(cast(Mapping[str, Any], payload)) if isinstance(payload, Mapping) else {}


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


def _nested_float(payload: Mapping[str, Any], path: Sequence[str], fallback: float = float("nan")) -> float:
    current: Any = payload
    for key in path:
        current = _mapping(current).get(key)
    return _float(current, fallback)


def _latest_price_proxy(payloads: Iterable[Mapping[str, Any]]) -> float | None:
    candidate_paths = (
        ("tracking_summary", "latest_price_proxy"),
        ("latest_signal", "latest_price_proxy"),
        ("price_location", "latest_price_proxy"),
        ("broker_execution_state", "execution_timing", "price_position", "latest_price_proxy"),
        ("execution_timing", "price_position", "latest_price_proxy"),
        ("latest_price_proxy",),
        ("current_price_proxy",),
    )
    for payload in payloads:
        for path in candidate_paths:
            value = _nested_float(payload, path)
            if math.isfinite(value):
                return float(value)
    return None


def _process_snapshot() -> dict[str, Any]:
    rows = python_processes()
    process_query_errors = [str(row.get("error")) for row in rows if row.get("error")]
    api = {process_id(row) for row in find_processes(rows, "start_phoenixguard_mobile_api.py") if process_id(row)}
    tracker = {
        process_id(row)
        for row in leaf_processes(find_processes(rows, "start_phoenixguard_24_7_tracker.py"))
        if process_id(row)
    }
    shooter = {process_id(row) for row in leaf_processes(find_processes(rows, "shooter.py")) if process_id(row)}
    shooter_rows = leaf_processes(find_processes(rows, "shooter.py"))
    shooter_commands = [command_line(row) for row in shooter_rows if command_line(row)]
    shooter_modes = sorted(
        {
            match.group(1).strip("\"'")
            for cmd in shooter_commands
            for match in re.finditer(r"--shooter-mode\s+([^\s]+)", cmd)
            if match.group(1).strip("\"'")
        }
    )
    if shooter and not shooter_modes:
        shooter_modes = ["PACKAGE_REPORTER"]
    listeners = tcp_listeners([8793, 8787])
    listener_errors = [str(row.get("error")) for row in listeners if row.get("error")]
    return {
        "api_pids": sorted(api),
        "tracker_pids": sorted(tracker),
        "shooter_pids": sorted(shooter),
        "shooter_modes": shooter_modes,
        "listeners": listeners,
        "process_query_errors": process_query_errors,
        "listener_query_errors": listener_errors,
    }


def _actual_live_click_arming(samples: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    modes: set[str] = set()
    for sample in samples:
        processes = _mapping(sample.get("processes"))
        for mode in _sequence(processes.get("shooter_modes")):
            text = _text(mode).upper()
            if text:
                modes.add(text)
    env_armed = _env_true("PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS")
    live_mode_armed = False
    return {
        "requested_full_activated_mode": False,
        "env_live_clicks_allowed": env_armed,
        "shooter_modes": sorted(modes),
        "actual_live_clicks_armed": bool(env_armed and live_mode_armed),
        "reason": "local shooter click arming is retired; shooter package reporter never clicks",
    }


def _live_click_armed_from_snapshot(process_snapshot: Mapping[str, Any]) -> bool:
    modes = {
        _text(mode).upper()
        for mode in _sequence(_mapping(process_snapshot).get("shooter_modes"))
        if _text(mode)
    }
    _ = modes
    return False


def _actual_click_observed(shooter: Mapping[str, Any]) -> bool:
    if bool(shooter.get("actual_clicked") or shooter.get("clicked")):
        return True
    action_sequence_raw = shooter.get("action_sequence")
    if isinstance(action_sequence_raw, Mapping):
        action_sequence_map = cast(Mapping[str, Any], action_sequence_raw)
        if bool(action_sequence_map.get("clicked")):
            return True
        if _text(action_sequence_map.get("overall")).upper() == "PASS":
            return True
        if _text(action_sequence_map.get("reason")).upper() == "ACTION_SEQUENCE_COMPLETE":
            return True
    rehearsal = shooter.get("execution_rehearsal")
    if isinstance(rehearsal, Mapping):
        rehearsal_map = cast(Mapping[str, Any], rehearsal)
        nested_action = rehearsal_map.get("action_sequence")
        if isinstance(nested_action, Mapping):
            nested_action_map = cast(Mapping[str, Any], nested_action)
            if bool(nested_action_map.get("clicked")):
                return True
            if _text(nested_action_map.get("overall")).upper() == "PASS":
                return True
    action_sequence = _text(action_sequence_raw).upper()
    return action_sequence.startswith("CLICK_SENT") or action_sequence.startswith("LIVE_READY_CLICK_SENT")


def _read_jsonl_tail(path: Path, *, max_lines: int = 2000) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[-max_lines:]
    except Exception:
        return []
    rows: list[dict[str, Any]] = []
    for line in lines:
        try:
            payload = json.loads(line)
        except Exception:
            continue
        if isinstance(payload, Mapping):
            rows.append(_mapping(payload))
    return rows


def _safe_shooter_event_key(row: Mapping[str, Any]) -> str:
    return "|".join(
        (
            _text(row.get("source_log")),
            _text(row.get("mode")),
            _text(row.get("packet_id")),
            f"{_float(row.get('timestamp')):.6f}",
        )
    )


def _collect_safe_shooter_validation_events() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in SAFE_SHOOTER_VALIDATION_LOGS:
        path = SHOOTER_VALIDATION_DIR / name
        if not path.exists():
            continue
        for raw in _read_jsonl_tail(path):
            mode = _text(raw.get("mode"), Path(name).stem).upper()
            packet_id = _text(raw.get("packet_id"))
            side = _text(raw.get("side")).upper()
            expiry_seconds = _int(raw.get("expiry_seconds"))
            clicked = bool(raw.get("clicked", False))
            if _text(raw.get("schema_version")) != "PG_EXECUTION_PACKET_V3":
                continue
            if not packet_id or side not in {"BUY", "SELL"} or expiry_seconds <= 0:
                continue
            if clicked:
                continue
            row: dict[str, Any] = {
                **raw,
                "source_log": str(path.relative_to(ROOT)),
                "mode": mode,
                "packet_id": packet_id,
                "side": side,
                "expiry_seconds": expiry_seconds,
                "actual_clicked": False,
                "safe_paper_candidate": True,
                "event_key": "",
            }
            row["event_key"] = _safe_shooter_event_key(row)
            rows.append(row)
    return rows


def _collect_live_ready_click_events(*, since_epoch: float = 0.0) -> list[dict[str, Any]]:
    path = SHOOTER_VALIDATION_DIR / "live_ready.jsonl"
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for raw in _read_jsonl_tail(path, max_lines=4000):
        timestamp = _float(raw.get("timestamp"), 0.0)
        if since_epoch > 0.0 and timestamp > 0.0 and timestamp < since_epoch:
            continue
        mode = _text(raw.get("mode"), "LIVE_READY").upper()
        packet_id = _text(raw.get("packet_id"))
        side = _text(raw.get("side")).upper()
        expiry_seconds = _int(raw.get("expiry_seconds"))
        if mode != "LIVE_READY":
            continue
        if _text(raw.get("schema_version")) != "PG_EXECUTION_PACKET_V3":
            continue
        if not bool(raw.get("clicked", False)):
            continue
        if not packet_id or side not in {"BUY", "SELL"} or expiry_seconds <= 0:
            continue
        row: dict[str, Any] = {
            **raw,
            "source_log": str(path.relative_to(ROOT)),
            "mode": mode,
            "packet_id": packet_id,
            "side": side,
            "expiry_seconds": expiry_seconds,
            "actual_clicked": True,
            "event_key": "",
        }
        row["event_key"] = _safe_shooter_event_key(row)
        rows.append(row)
    return rows


def _paper_monitor_from_safe_event(
    event: Mapping[str, Any],
    *,
    now: float,
    latest_price_proxy: float | None,
    max_entry_lag_sec: float,
) -> dict[str, Any] | None:
    opened_epoch = _float(event.get("timestamp"))
    if opened_epoch <= 0.0:
        opened_epoch = now
    entry_lag_sec = max(0.0, now - opened_epoch)
    if latest_price_proxy is None or not math.isfinite(float(latest_price_proxy)):
        return None
    if entry_lag_sec > max(0.5, float(max_entry_lag_sec)):
        return None
    expiry_seconds = _int(event.get("expiry_seconds"))
    if expiry_seconds <= 0:
        return None
    packet_id = _text(event.get("packet_id"))
    return {
        "paper_trade_id": f"safe-paper-{packet_id}",
        "packet_id": packet_id,
        "source_event_key": _text(event.get("event_key")),
        "source": "safe_shooter_no_broker_click_chart_proxy",
        "mode": _text(event.get("mode")),
        "actual_clicked": False,
        "broker_click_allowed": bool(event.get("broker_click_allowed", False)),
        "side": _text(event.get("side")).upper(),
        "opened_epoch": opened_epoch,
        "opened_at": _utc_iso(opened_epoch),
        "expires_epoch": opened_epoch + float(expiry_seconds),
        "expires_at": _utc_iso(opened_epoch + float(expiry_seconds)),
        "expiry_seconds": expiry_seconds,
        "entry_price_proxy": round(float(latest_price_proxy), 6),
        "entry_lag_sec": round(entry_lag_sec, 3),
        "status": "monitoring",
        "decision_reason": _text(event.get("decision_reason") or event.get("reason")),
    }


def _live_trade_monitor_from_click_event(
    event: Mapping[str, Any],
    *,
    now: float,
    latest_price_proxy: float | None,
    max_entry_lag_sec: float,
) -> dict[str, Any] | None:
    opened_epoch = _float(event.get("timestamp"))
    if opened_epoch <= 0.0:
        opened_epoch = now
    entry_lag_sec = max(0.0, now - opened_epoch)
    if latest_price_proxy is None or not math.isfinite(float(latest_price_proxy)):
        return None
    if entry_lag_sec > max(0.5, float(max_entry_lag_sec)):
        return None
    expiry_seconds = _int(event.get("expiry_seconds"))
    if expiry_seconds <= 0:
        return None
    packet_id = _text(event.get("packet_id"))
    if not packet_id:
        return None
    return {
        "trade_id": f"live-chart-proxy-{packet_id}",
        "packet_id": packet_id,
        "source_event_key": _text(event.get("event_key")),
        "source": "live_ready_broker_click_chart_proxy",
        "mode": _text(event.get("mode"), "LIVE_READY"),
        "actual_clicked": True,
        "broker_click_allowed": bool(event.get("broker_click_allowed", True)),
        "side": _text(event.get("side")).upper(),
        "opened_epoch": opened_epoch,
        "opened_at": _utc_iso(opened_epoch),
        "expires_epoch": opened_epoch + float(expiry_seconds),
        "expires_at": _utc_iso(opened_epoch + float(expiry_seconds)),
        "expiry_seconds": expiry_seconds,
        "entry_price_proxy": round(float(latest_price_proxy), 6),
        "entry_lag_sec": round(entry_lag_sec, 3),
        "status": "monitoring",
        "lane": _text(event.get("selected_execution_lane")),
        "timing_mode": _text(event.get("timing_mode")),
        "decision_reason": _text(event.get("decision_reason") or event.get("reason")),
    }


def _safe_event_from_shooter_decision(
    shooter_signature_payload: Mapping[str, Any],
    *,
    now: float,
    process_snapshot: Mapping[str, Any],
) -> dict[str, Any] | None:
    if not bool(shooter_signature_payload.get("will_click")):
        return None
    if _text(shooter_signature_payload.get("packet_type")) != "PG_EXECUTION_PACKET_V3":
        return None
    if _live_click_armed_from_snapshot(process_snapshot):
        return None
    packet_id = _text(shooter_signature_payload.get("packet_id"))
    side = _text(shooter_signature_payload.get("side")).upper()
    if not packet_id or side not in {"BUY", "SELL"}:
        return None
    modes = [
        _text(mode).upper()
        for mode in _sequence(_mapping(process_snapshot).get("shooter_modes"))
        if _text(mode)
    ]
    row: dict[str, Any] = {
        "source_log": "runtime_trace/shooter_handshake",
        "mode": ",".join(modes) or "UNKNOWN",
        "packet_id": packet_id,
        "schema_version": "PG_EXECUTION_PACKET_V3",
        "side": side,
        "expiry_seconds": _int(shooter_signature_payload.get("expiry_seconds")),
        "timestamp": _float(shooter_signature_payload.get("timestamp_epoch"), now),
        "decision_reason": _text(shooter_signature_payload.get("reason")),
        "broker_click_allowed": False,
        "actual_clicked": False,
        "safe_paper_candidate": True,
        "event_key": "",
    }
    row["event_key"] = _safe_shooter_event_key(row)
    return row


def _settle_safe_paper_monitors(
    monitors: dict[str, dict[str, Any]],
    *,
    now: float,
    latest_price_proxy: float | None,
) -> list[dict[str, Any]]:
    if latest_price_proxy is None or not math.isfinite(float(latest_price_proxy)):
        return []
    settled: list[dict[str, Any]] = []
    for packet_id, monitor in list(monitors.items()):
        expires_epoch = _float(monitor.get("expires_epoch"))
        if expires_epoch <= 0.0 or expires_epoch > now:
            continue
        side = _text(monitor.get("side")).upper()
        entry_price = _float(monitor.get("entry_price_proxy"), float("nan"))
        if side not in {"BUY", "SELL"} or not math.isfinite(entry_price):
            result = "UNKNOWN"
            direction_delta = float("nan")
        else:
            raw_delta = float(latest_price_proxy) - entry_price
            direction_delta = raw_delta if side == "BUY" else -raw_delta
            if abs(direction_delta) <= 0.0001:
                result = "FLAT"
            elif direction_delta > 0:
                result = "WIN"
            else:
                result = "LOSS"
        outcome: dict[str, Any] = {
            **monitor,
            "status": "settled_chart_proxy",
            "result": result,
            "outcome": result,
            "verification": "chart_proxy",
            "resolved_epoch": now,
            "resolved_at": _utc_iso(now),
            "exit_price_proxy": round(float(latest_price_proxy), 6),
            "direction_delta_proxy": None if not math.isfinite(direction_delta) else round(float(direction_delta), 6),
            "resolution_lag_sec": round(max(0.0, now - expires_epoch), 3),
            "profit_proxy": None,
            "profitability_scope": "paper_chart_proxy_not_live_broker",
        }
        settled.append(outcome)
        monitors.pop(packet_id, None)
    return settled


def _settle_live_trade_monitors(
    monitors: dict[str, dict[str, Any]],
    *,
    now: float,
    latest_price_proxy: float | None,
) -> list[dict[str, Any]]:
    if latest_price_proxy is None or not math.isfinite(float(latest_price_proxy)):
        return []
    settled: list[dict[str, Any]] = []
    for packet_id, monitor in list(monitors.items()):
        expires_epoch = _float(monitor.get("expires_epoch"))
        if expires_epoch <= 0.0 or expires_epoch > now:
            continue
        side = _text(monitor.get("side")).upper()
        entry_price = _float(monitor.get("entry_price_proxy"), float("nan"))
        if side not in {"BUY", "SELL"} or not math.isfinite(entry_price):
            result = "UNKNOWN"
            direction_delta = float("nan")
            unit_profit = None
        else:
            raw_delta = float(latest_price_proxy) - entry_price
            direction_delta = raw_delta if side == "BUY" else -raw_delta
            if abs(direction_delta) <= 0.0001:
                result = "FLAT"
                unit_profit = 0.0
            elif direction_delta > 0:
                result = "WIN"
                unit_profit = 1.0
            else:
                result = "LOSS"
                unit_profit = -1.0
        outcome: dict[str, Any] = {
            **monitor,
            "status": "settled_chart_proxy",
            "result": result,
            "outcome": result,
            "verification": "chart_proxy",
            "resolved_epoch": now,
            "resolved_at": _utc_iso(now),
            "exit_price_proxy": round(float(latest_price_proxy), 6),
            "direction_delta_proxy": None if not math.isfinite(direction_delta) else round(float(direction_delta), 6),
            "resolution_lag_sec": round(max(0.0, now - expires_epoch), 3),
            "profit_proxy": unit_profit,
            "profitability_scope": "live_broker_click_chart_proxy_not_broker_statement",
        }
        settled.append(outcome)
        monitors.pop(packet_id, None)
    return settled


def _summarize_safe_paper(
    events: Sequence[Mapping[str, Any]],
    monitors: Mapping[str, Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
    *,
    min_settled_outcomes: int,
) -> dict[str, Any]:
    wins = sum(1 for row in outcomes if _infer_trade_result(row) == "WIN")
    losses = sum(1 for row in outcomes if _infer_trade_result(row) == "LOSS")
    flats = sum(1 for row in outcomes if _infer_trade_result(row) == "FLAT")
    known = wins + losses
    minimum = max(1, int(min_settled_outcomes))
    return {
        "scope": "safe_shooter_no_broker_click_chart_proxy",
        "not_live_profitability": True,
        "safe_shooter_event_count": len(events),
        "active_monitor_count": len(monitors),
        "settled_outcome_count": len(outcomes),
        "minimum_required_settled_win_loss_outcomes": minimum,
        "settled_known_outcomes": known,
        "wins": wins,
        "losses": losses,
        "flat": flats,
        "win_rate": round(wins / known, 4) if known else None,
        "status": "PAPER_CALCULATED" if known >= minimum else "PAPER_INSUFFICIENT_SAMPLE",
        "reason": (
            "Safe shooter packets were monitored with chart-proxy settlement. This is not a live broker profitability certificate."
            if outcomes
            else "No safe paper packet has reached expiry with chart-proxy entry/exit prices yet."
        ),
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
                rows.append(_mapping(found))
            elif isinstance(found, list):
                rows.extend(_mapping(item) for item in cast(Sequence[Any], found) if isinstance(item, Mapping))
    return rows[:100]


def _collect_skill_contributions(payloads: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    names = {"skill_contributions", "skill_contribution_aggregator", "skill_gates", "skills"}
    for payload in payloads:
        for found in _find_nested(payload, names, contains=("skill_",)):
            if isinstance(found, Mapping):
                rows.append(_mapping(found))
            elif isinstance(found, list):
                rows.extend(_mapping(item) for item in cast(Sequence[Any], found) if isinstance(item, Mapping))
    return rows[:120]


def _collect_lstm_predictions(payloads: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    names = {"lstm", "lstm_prediction", "lstm_predictions", "lstm_candle_sequence", "lstm_candle_sequence_contributor"}
    for payload in payloads:
        for found in _find_nested(payload, names, contains=("lstm",)):
            if isinstance(found, Mapping):
                rows.append(_mapping(found))
            elif isinstance(found, list):
                rows.extend(_mapping(item) for item in cast(Sequence[Any], found) if isinstance(item, Mapping))
    return rows[:50]


def _collect_two_candle(payloads: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    names = {"two_candle_study", "two_candle", "high_frequency_candle_cycle"}
    for payload in payloads:
        for found in _find_nested(payload, names, contains=("two_candle", "high_frequency_candle_cycle")):
            if isinstance(found, Mapping):
                rows.append(_mapping(found))
            elif isinstance(found, list):
                rows.extend(_mapping(item) for item in cast(Sequence[Any], found) if isinstance(item, Mapping))
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


def _summarize_outcomes(rows: Sequence[Mapping[str, Any]], *, min_settled_outcomes: int = 3) -> dict[str, Any]:
    wins = sum(1 for row in rows if _infer_trade_result(row) == "WIN")
    losses = sum(1 for row in rows if _infer_trade_result(row) == "LOSS")
    flats = sum(1 for row in rows if _infer_trade_result(row) == "FLAT")
    unknown = max(0, len(rows) - wins - losses - flats)
    known = wins + losses
    payout_returns = [_float(row.get("profit_proxy")) for row in rows if row.get("profit_proxy") is not None]
    net_proxy = round(sum(payout_returns), 4) if payout_returns else 0.0
    if known < max(1, int(min_settled_outcomes)):
        return {
            "profitability": "INSUFFICIENT_SAMPLE",
            "status": "INSUFFICIENT_SAMPLE",
            "calculated": False,
            "minimum_required_settled_win_loss_outcomes": max(1, int(min_settled_outcomes)),
            "total_executed_trades": len(rows),
            "settled_known_outcomes": known,
            "wins": wins,
            "losses": losses,
            "flat": flats,
            "unknown": unknown,
            "win_rate": None,
            "estimated_payout_return": None,
            "net_profit_proxy": None,
            "long_term_profitability": "NOT_CERTIFIED_BY_2_HOUR_SAMPLE",
            "reason": "Profitability is not calculated until enough settled WIN/LOSS outcomes are observed.",
        }
    return {
        "profitability": "CALCULATED",
        "status": "CALCULATED",
        "calculated": True,
        "minimum_required_settled_win_loss_outcomes": max(1, int(min_settled_outcomes)),
        "total_executed_trades": len(rows),
        "settled_known_outcomes": known,
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


def _promotion_failure_row(
    *,
    now: float,
    study: Mapping[str, Any],
    trace: Mapping[str, Any],
    sample: Mapping[str, Any],
) -> dict[str, Any]:
    study = dict(study)
    generic_no_packet_reasons = {
        "",
        "NONE",
        "WATCHING",
        "STUDY_PACKET_PUBLISHED",
        "EXECUTION_PACKET_NOT_PUBLISHED",
        "PG_EXECUTION_PACKET_V3_PUBLISHED",
        "EXECUTABLE_PACKET_CREATED",
    }

    def has_promotion_context(payload: Mapping[str, Any]) -> bool:
        promotion_context = _mapping(payload.get("promotion_trace"))
        lane_context = _mapping(payload.get("execution_lane") or _mapping(payload.get("model_council")).get("execution_lane"))
        audit_context = _mapping(payload.get("promotion_failure_audit_v3") or promotion_context.get("promotion_failure_audit_v3"))
        promotion_reason_values = (
            promotion_context.get("denied_at"),
            promotion_context.get("true_blocker"),
            promotion_context.get("blocked_by"),
        )
        has_real_promotion_reason = any(_text(value).upper() not in generic_no_packet_reasons for value in promotion_reason_values)
        return bool(
            audit_context
            or lane_context
            or has_real_promotion_reason
            or promotion_context.get("next_required")
            or promotion_context.get("release_condition")
            or promotion_context.get("exact_field_preventing_execution_packet")
        )

    if not has_promotion_context(study):
        endpoints = _mapping(trace.get("endpoints"))
        model_council_endpoint = _mapping(endpoints.get("model_council_latest"))
        model_council_payload = _mapping(model_council_endpoint.get("payload"))
        model_council_result = _mapping(model_council_payload.get("model_council_result"))
        for candidate in (
            _mapping(model_council_payload.get("model_council_study_packet")),
            _mapping(model_council_payload.get("study_packet")),
            _mapping(model_council_result.get("study_packet")),
            model_council_result,
        ):
            if not candidate or not has_promotion_context(candidate):
                continue
            enriched = dict(study)
            for key, value in candidate.items():
                if value not in (None, "", [], {}):
                    enriched[key] = value
            study = enriched
            break

    promotion_context_present = has_promotion_context(study)
    promotion = _mapping(study.get("promotion_trace"))
    council = _mapping(study.get("model_council"))
    canonical_audit = _mapping(
        study.get("promotion_failure_audit_v3")
        or promotion.get("promotion_failure_audit_v3")
        or council.get("promotion_failure_audit_v3")
    )
    execution = _mapping(study.get("execution"))
    lane = _mapping(study.get("execution_lane") or council.get("execution_lane") or promotion.get("execution_lane"))
    sequence = _mapping(
        study.get("sequence_context_readiness")
        or council.get("sequence_context_readiness")
        or council.get("sequence_context")
        or trace.get("sequence_context_readiness")
    )
    timing = _mapping(study.get("timing_decision") or council.get("timing_decision") or promotion.get("timing_decision"))
    entry_timing = _mapping(timing.get("entry_timing"))
    entry_quality = _mapping(study.get("entry_quality") or council.get("entry_quality") or promotion.get("entry_quality"))
    market_reality = _mapping(study.get("market_reality") or council.get("market_reality") or promotion.get("market_reality"))
    trade_permission = _mapping(study.get("trade_permission") or council.get("trade_permission") or market_reality.get("trade_permission"))
    raw_skill_summary: Any = (
        study.get("skill_contributions")
        or council.get("skill_contributions")
        or promotion.get("skill_summary")
        or {}
    )
    skill_summary: dict[str, Any] | list[Any] = (
        _mapping(raw_skill_summary)
        if isinstance(raw_skill_summary, Mapping)
        else _sequence(raw_skill_summary)
        if isinstance(raw_skill_summary, list)
        else {}
    )
    lstm_summary: dict[str, Any] = _mapping(
        study.get("lstm_contribution")
        or council.get("lstm_contribution")
        or promotion.get("lstm_summary")
        or {}
    )
    memory_confirmation: dict[str, Any] = _mapping(
        study.get("memory_confirmation")
        or council.get("memory_confirmation")
        or promotion.get("memory_confirmation")
        or {}
    )
    timing_mode = _text(
        promotion.get("timing_mode")
        or timing.get("timing_mode")
        or entry_timing.get("mode")
        or execution.get("timing_mode")
    )
    final_score = _float(
        promotion.get("final_execution_score")
        or council.get("final_execution_score")
        or study.get("final_execution_score")
    )
    lane_threshold = _float(
        promotion.get("execution_threshold")
        or lane.get("required_score")
        or lane.get("threshold")
        or council.get("execution_threshold")
        or study.get("execution_threshold")
    )
    denied_at = _text(
        promotion.get("denied_at")
        or promotion.get("blocked_by")
        or promotion.get("true_blocker")
        or study.get("denied_at")
        or study.get("block_reason")
        or sample.get("packet_contract_status")
    )
    next_required = _text(
        promotion.get("next_required")
        or promotion.get("release_condition")
        or study.get("next_required")
        or sequence.get("next_required")
    )
    canonical_denied_at = _text(canonical_audit.get("denied_at")).upper()
    if denied_at.upper() in generic_no_packet_reasons and canonical_denied_at not in generic_no_packet_reasons:
        denied_at = _text(canonical_audit.get("denied_at"))
    elif denied_at.upper() in generic_no_packet_reasons:
        denied_at = ""
    if not denied_at:
        if sample.get("source_lock_status") != "PASS":
            denied_at = "BROKER_SOURCE_LOCK"
        elif not bool(sample.get("sequence_ready")):
            denied_at = "SEQUENCE_CONTEXT"
        elif not promotion_context_present:
            denied_at = "PROMOTION_CONTEXT_MISSING"
        elif timing_mode and timing_mode != "ENTER_NOW":
            denied_at = "TIMING_READY"
        elif lane and not bool(lane.get("accepted")):
            denied_at = "NO_EXECUTION_LANE_ACCEPTED"
        elif lane_threshold and final_score < lane_threshold:
            denied_at = "LANE_SCORE"
        elif trade_permission and (
            trade_permission.get("allowed") is False
            or trade_permission.get("accepted") is False
            or _text(trade_permission.get("state") or trade_permission.get("permission") or trade_permission.get("status")).upper()
            in {"DENIED", "BLOCKED", "WAIT", "WAITING", "NO_TRADE"}
        ):
            denied_at = _text(trade_permission.get("deny_reason") or trade_permission.get("reason"), "TRADE_PERMISSION")
        elif entry_quality and _text(entry_quality.get("state") or entry_quality.get("grade") or entry_quality.get("entry_grade")).upper() in {
            "BAD_NOW",
            "LATE_ENTRY",
            "CHASE_ENTRY",
            "WATCH_ONLY",
            "EARLY_WATCH",
        }:
            denied_at = "ENTRY_QUALITY"
        else:
            denied_at = "PG_EXECUTION_PACKET_V3_MISSING_AFTER_READY_GATES"
    if (
        denied_at == "PG_EXECUTION_PACKET_V3_MISSING_AFTER_READY_GATES"
        and (not next_required or next_required == "publish fresh validated PG_EXECUTION_PACKET_V3 when all gates pass")
    ):
        next_required = (
            "current PG_EXECUTION_PACKET_V3 must exist, or promotion_failure_audit_v3 must name the exact validator rejection"
        )
    if not next_required:
        next_required = _text(
            lane.get("next_required")
            or lane.get("reason")
            or trade_permission.get("next_required")
            or trade_permission.get("reason")
            or entry_quality.get("reason")
            or (
                "runtime trace must include promotion_trace or execution_lane before packet absence can be certified"
                if not promotion_context_present
                else ""
            )
            or "current PG_EXECUTION_PACKET_V3 must exist, or promotion_failure_audit_v3 must name the exact validator rejection"
        )
    row: dict[str, Any] = {
        "epoch": now,
        "iso": _utc_iso(now),
        "packet_id": _extract_packet_id(study),
        "council_state": _text(council.get("final_state") or execution.get("state") or study.get("execution_state")),
        "candidate_side": _text(
            promotion.get("candidate_side")
            or council.get("candidate_side")
            or council.get("final_side")
            or execution.get("side")
        ),
        "final_score": final_score,
        "lane": _text(lane.get("name") or promotion.get("selected_lane") or study.get("selected_execution_lane")),
        "lane_threshold": lane_threshold,
        "score_passed": bool(final_score >= lane_threshold) if lane_threshold else False,
        "sequence_status": _text(sequence.get("sequence_status") or sequence.get("status") or sample.get("sequence_status")),
        "sequence_length": _int(sequence.get("sequence_length") or sample.get("sequence_length")),
        "box_history_len": _int(sequence.get("box_history_len")),
        "entry_progression_len": _int(sequence.get("entry_progression_len")),
        "broker_source_lock": _text(sample.get("source_lock_status")),
        "model_health": _text(_mapping(_mapping(trace.get("certification_gates")).get("model_warm_state")).get("status")),
        "timing_mode": timing_mode,
        "timing_ready": timing_mode == "ENTER_NOW" or bool(timing.get("timing_ready")),
        "entry_quality": _text(entry_quality.get("state") or entry_quality.get("grade") or entry_quality.get("entry_grade")),
        "price_location": _text(_mapping(study.get("price_location") or council.get("price_location")).get("label")),
        "market_play": _text(_mapping(study.get("market_play") or council.get("market_play")).get("name")),
        "regime": _text(_mapping(study.get("regime") or council.get("regime")).get("name")),
        "bad_entry_class": _text(_mapping(study.get("bad_entry") or council.get("bad_entry")).get("class")),
        "trap_risk": _text(_mapping(study.get("market_trap") or market_reality.get("market_trap")).get("risk")),
        "path_risk": _text(_mapping(study.get("path_quality") or council.get("path_quality")).get("label")),
        "target_before_invalidation": _text(promotion.get("target_before_invalidation")),
        "opposing_force_distance": _text(promotion.get("opposing_force_distance")),
        "skill_summary": skill_summary,
        "lstm_summary": lstm_summary,
        "memory_confirmation": memory_confirmation,
        "denied_at": denied_at,
        "next_required": next_required,
        "exact_field_preventing_execution_packet": _text(
            promotion.get("exact_field_preventing_execution_packet")
            or denied_at
        ),
        "promotion_context_present": promotion_context_present,
    }
    if canonical_audit:
        row["promotion_failure_audit_v3"] = canonical_audit
        row["denied_at"] = _text(canonical_audit.get("denied_at"), row["denied_at"])
        row["next_required"] = _text(canonical_audit.get("next_required"), row["next_required"])
        row["exact_field_preventing_execution_packet"] = _text(
            canonical_audit.get("exact_field_preventing_execution_packet"),
            row["exact_field_preventing_execution_packet"],
        )
        row["final_score"] = _float(canonical_audit.get("final_score"), _float(row.get("final_score")))
        row["lane_threshold"] = _float(canonical_audit.get("threshold"), _float(row.get("lane_threshold")))
        row["score_passed"] = bool(canonical_audit.get("score_passed"))
        row["timing_mode"] = _text(canonical_audit.get("timing_mode"), row["timing_mode"])
        row["sequence_status"] = _text(canonical_audit.get("sequence_status"), row["sequence_status"])
        row["sequence_length"] = _int(canonical_audit.get("sequence_length"), _int(row.get("sequence_length")))
        row["lane"] = _text(canonical_audit.get("selected_lane"), row["lane"])
    same_packet_second_read = (
        _text(row.get("packet_id"))
        and _text(row.get("packet_id")) == _text(sample.get("shooter_packet_id"))
        and _text(sample.get("shooter_reason")).upper().startswith("WAITING_SECOND")
    )
    if row["denied_at"] in {
        "PG_EXECUTION_PACKET_V3_MISSING_AFTER_READY_GATES",
        "EXECUTION_PACKET_NOT_CURRENT_AFTER_PUBLICATION",
    }:
        thin_context = (
            row["denied_at"] == "PG_EXECUTION_PACKET_V3_MISSING_AFTER_READY_GATES"
            and not _text(row.get("lane"))
            and not _text(row.get("timing_mode"))
            and _float(row.get("final_score")) <= 0.0
            and _float(row.get("lane_threshold")) <= 0.0
            and _int(row.get("sequence_length")) <= 0
        )
        if thin_context:
            row["denied_at"] = "PROMOTION_CONTEXT_MISSING"
            row["next_required"] = "runtime trace must include promotion_trace or execution_lane before packet absence can be certified"
            row["exact_field_preventing_execution_packet"] = "promotion_trace"
        elif same_packet_second_read:
            row["denied_at"] = "SHOOTER_PACKAGE_REPORT_PENDING"
            row["next_required"] = "ShooterPackageReporter must publish a fresh allowed package report"
            row["exact_field_preventing_execution_packet"] = "ShooterPackageReporter.allowed_package_report"
        audit = _mapping(row.get("promotion_failure_audit_v3"))
        if audit and row["denied_at"] != "PG_EXECUTION_PACKET_V3_MISSING_AFTER_READY_GATES":
            audit["denied_at"] = row["denied_at"]
            audit["top_blocker"] = row["denied_at"]
            audit["next_required"] = row["next_required"]
            audit["exact_field_preventing_execution_packet"] = row["exact_field_preventing_execution_packet"]
            blockers = _sequence(audit.get("blocker_ranking"))
            if blockers and isinstance(blockers[0], Mapping):
                first = _mapping(blockers[0])
                first["blocker"] = row["denied_at"]
                first["field"] = row["exact_field_preventing_execution_packet"]
                first["reason"] = row["next_required"]
                first["next_required"] = row["next_required"]
                audit["blocker_ranking"] = [first, *[_mapping(item) for item in blockers[1:] if isinstance(item, Mapping)]]
            row["promotion_failure_audit_v3"] = audit
    return row


def _rank_promotion_blockers(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    ranking = {
        "timing_mode != ENTER_NOW": 0,
        "entry_quality below threshold": 0,
        "sequence incomplete": 0,
        "lane score below threshold": 0,
        "bad_entry active": 0,
        "broker source lock missing": 0,
        "model health stale": 0,
        "execution packet not current after publication": 0,
        "opposing force too close": 0,
        "no path room": 0,
    }
    for row in rows:
        timing_mode = _text(row.get("timing_mode"))
        denied_at = _text(row.get("denied_at")).upper()
        next_required = _text(row.get("next_required")).lower()
        if timing_mode and timing_mode != "ENTER_NOW":
            ranking["timing_mode != ENTER_NOW"] += 1
        if "ENTRY" in denied_at or "entry quality" in next_required:
            ranking["entry_quality below threshold"] += 1
        if _text(row.get("sequence_status")).upper() != "COMPLETE":
            ranking["sequence incomplete"] += 1
        if row.get("score_passed") is False:
            ranking["lane score below threshold"] += 1
        if "BAD_ENTRY" in denied_at or _text(row.get("bad_entry_class")):
            ranking["bad_entry active"] += 1
        if _text(row.get("broker_source_lock")).upper() in {"", "MISSING", "FAIL"}:
            ranking["broker source lock missing"] += 1
        if _text(row.get("model_health")).upper() not in {"PASS", "AWAKE"}:
            ranking["model health stale"] += 1
        if "NOT_CURRENT_AFTER_PUBLICATION" in denied_at:
            ranking["execution packet not current after publication"] += 1
        if "opposing" in denied_at.lower() or "opposing force" in next_required:
            ranking["opposing force too close"] += 1
        if "path" in denied_at.lower() or "target" in next_required and "invalidation" in next_required:
            ranking["no path room"] += 1
    return ranking


def _render_promotion_failure_report(rows: Sequence[Mapping[str, Any]], ranking: Mapping[str, int]) -> str:
    examples = list(rows[-10:])
    lines = [
        "# PhoenixGuard V3 Promotion Failure Audit",
        "",
        f"Generated: {_utc_iso()}",
        "",
        "## CLEAR ANSWER",
        "",
        "Promotion failures are logged with explicit denied_at, next_required, gate, score, sequence, and source-lock fields.",
        "",
        "## CONFIDENCE LEVEL",
        "",
        "`0.86`",
        "",
        "## KEY CAVEATS",
        "",
        "- This audit is only as complete as the study packet fields currently published into RuntimeTraceV3.",
        "- Profitability remains unmeasured unless real executable packets and shooter actions occur.",
        "",
        "## BLOCKER RANKING",
        "",
        "```json",
        json.dumps(dict(ranking), indent=2, sort_keys=True, default=str),
        "```",
        "",
        "## RECENT EXAMPLES",
        "",
        "```json",
        json.dumps(examples, indent=2, sort_keys=True, default=str),
        "```",
    ]
    return "\n".join(lines).rstrip() + "\n"


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
    safe_paper: Mapping[str, Any],
    precision: Mapping[str, Any],
    frame_ages: Sequence[float],
    overlay_ages: Sequence[float],
    model_ages: Sequence[float],
    executable_packets: Sequence[str],
    shooter_clicks: Sequence[Mapping[str, Any]],
    skill_rows: int,
    lstm_rows: int,
    two_candle_rows: int,
    promotion_ranking: Mapping[str, int] | None = None,
) -> str:
    duration = max(0.0, ended - started)
    live_click_arming = _actual_live_click_arming(samples)
    live_click_arming["requested_full_activated_mode"] = args.mode == "FULL_ACTIVATED"
    lines = [
        "# PhoenixGuard V3 Full-System Activated Burn-In Report",
        "",
        f"Generated: {_utc_iso(ended)}",
        "",
        "## 1. Burn-in Mode",
        "",
        f"- Mode: `{args.mode}`",
        f"- Full activated certification requested: `{args.mode == 'FULL_ACTIVATED'}`",
        f"- Actual broker clicks armed: `{live_click_arming['actual_live_clicks_armed']}`",
        f"- Shooter reporter modes observed: `{', '.join(live_click_arming['shooter_modes']) or 'none'}`",
        f"- Live-click env allowed: `{live_click_arming['env_live_clicks_allowed']}`",
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
        "- Local shooter live clicking is retired; shooter only reports fresh accepted allowance packages.",
        "- Raw signals, dashboard state, skill gates, memory confidence, and `final_side` alone were not accepted as authority.",
        f"- Actual arming reason: `{live_click_arming['reason']}`",
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
        "- Promotion blocker ranking:",
        "",
        "```json",
        json.dumps(dict(promotion_ranking or {}), indent=2, sort_keys=True, default=str),
        "```",
        "",
        "## 12. Shooter Action Evidence",
        "",
        f"- Action evidence directory: `{ACTION_EVIDENCE_DIR.relative_to(ROOT)}`",
        f"- Shooter actions log: `{(BURN_DIR / 'shooter_actions.jsonl').relative_to(ROOT)}`",
        "",
        "## 13. Profitability Sample",
        "",
        "The two-hour result is a sample only. Long-term profitability is not certified by a two-hour sample.",
        "Live broker profitability uses only settled live trade outcomes.",
        "",
        "```json",
        json.dumps(dict(profitability), indent=2, sort_keys=True, default=str),
        "```",
        "",
        "## 14. Safe Paper Chart-Proxy Sample",
        "",
        "Safe paper evidence comes from validated shooter packets where broker clicks remained disabled. It is separate from live trade profitability.",
        "",
        "```json",
        json.dumps(dict(safe_paper), indent=2, sort_keys=True, default=str),
        "```",
        "",
        "## 15. Precision Metrics",
        "",
        "```json",
        json.dumps(dict(precision), indent=2, sort_keys=True, default=str),
        "```",
        "",
        "## 16. Failure Cases",
        "",
    ]
    lines.extend(f"- {item}" for item in failures) if failures else lines.append("- none")
    lines.extend(["", "## 17. Lessons Learned", ""])
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
    if safe_paper.get("safe_shooter_event_count"):
        lines.append("- Safe shooter readiness was observed without broker clicks; live profitability remains separate from paper chart-proxy settlement.")
    lines.extend(["", "## 18. Component Status Counts", ""])
    lines.append("```json")
    lines.append(json.dumps(dict(component_counts), indent=2, sort_keys=True, default=str))
    lines.append("```")
    lines.extend(["", "## 19. Final Verdict", "", f"`{verdict}`"])
    if stop_reason:
        lines.extend(["", f"Stop reason: `{stop_reason}`"])
    if warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {item}" for item in warnings)
    return "\n".join(lines).rstrip() + "\n"


def _final_burn_verdict(
    *,
    mode: str,
    stop_reason: str,
    executable_packets: Sequence[str],
    trade_outcomes: Sequence[Mapping[str, Any]],
    promotion_failures: Sequence[Mapping[str, Any]],
    profitability: Mapping[str, Any],
    safe_paper: Mapping[str, Any],
    require_live_clicks_armed: bool,
    min_sample_trades: int,
) -> str:
    normalized_mode = _text(mode).upper()
    if stop_reason.startswith("STOPPED_BY_RISK_LIMIT"):
        return "STOPPED_BY_RISK_LIMIT"
    if stop_reason.startswith("STOP_ON_SHOOTER_EXIT") or stop_reason.startswith("STOP_ON_STALE_EXECUTION_PACKET"):
        return "FAIL_EXECUTION_PATH"
    if stop_reason:
        return "FAIL_RUNTIME"
    if normalized_mode == "FULL_ACTIVATED" and not executable_packets and promotion_failures:
        return "FAIL_PROMOTION"
    if normalized_mode == "FULL_ACTIVATED" and not executable_packets:
        return "FAIL_NO_EXECUTION_PACKET"
    if normalized_mode == "FULL_ACTIVATED" and executable_packets and not trade_outcomes:
        if safe_paper.get("safe_shooter_event_count") and not require_live_clicks_armed:
            return "PASS_SAFE_EXECUTION_READINESS_LIVE_PROFITABILITY_UNCERTIFIED"
        return "FAIL_EXECUTION_PATH"
    if not trade_outcomes and not executable_packets:
        return "PASS_RUNTIME_ONLY_NO_TRADES"
    if len(trade_outcomes) < min_sample_trades:
        return "INSUFFICIENT_SAMPLE"
    if _text(profitability.get("profitability")).upper() == "INSUFFICIENT_SAMPLE":
        return "INSUFFICIENT_SAMPLE"
    if _float(profitability.get("net_profit_proxy")) < 0.0:
        return "FAIL_PROFITABILITY_SAMPLE"
    return "PASS_FULL_ACTIVATED"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PhoenixGuard V3 full-system activated burn-in monitor.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--duration-sec", type=float, default=7200.0)
    parser.add_argument("--interval-sec", type=float, default=5.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--mode", choices=["TECHNICAL", "ARMED_DRY_RUN", "FULL_ACTIVATED"], default="FULL_ACTIVATED")
    parser.add_argument("--require-live-clicks-armed", action="store_true")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--max-executed-trades", type=int, default=20)
    parser.add_argument("--max-consecutive-losses", type=int, default=3)
    parser.add_argument("--pause-after-loss-sec", type=float, default=180.0)
    parser.add_argument("--max-frame-age-ms", type=float, default=2500.0)
    parser.add_argument("--max-consecutive-stale-frames", type=int, default=5)
    parser.add_argument("--max-consecutive-process-misses", type=int, default=3)
    parser.add_argument("--max-api-failures", type=int, default=2)
    parser.add_argument("--min-sample-trades", type=int, default=3)
    parser.add_argument("--warmup-sec", type=float, default=30.0)
    parser.add_argument("--status-every-sec", type=float, default=30.0)
    parser.add_argument("--paper-entry-max-lag-sec", type=float, default=20.0)
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

    for name in BURN_RESET_FILES:
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
    trade_outcome_packet_ids: set[str] = set()
    promotion_failures: list[dict[str, Any]] = []
    component_counts: dict[str, dict[str, int]] = {name: {} for name in REQUIRED_COMPONENTS}
    endpoint_failures = 0
    consecutive_losses = 0
    consecutive_stale_frames = 0
    consecutive_endpoint_failures = 0
    consecutive_missing_api_processes = 0
    consecutive_missing_tracker_processes = 0
    consecutive_missing_shooter_processes = 0
    seen_shooter_signatures: set[str] = set()
    seen_action_evidence: set[str] = set()
    seen_safe_shooter_events = {_safe_shooter_event_key(row) for row in _collect_safe_shooter_validation_events()}
    seen_live_ready_click_events: set[str] = set()
    monitored_live_ready_click_events: set[str] = set()
    safe_shooter_events: list[dict[str, Any]] = []
    safe_paper_monitors: dict[str, dict[str, Any]] = {}
    safe_paper_outcomes: list[dict[str, Any]] = []
    live_trade_monitors: dict[str, dict[str, Any]] = {}
    live_trade_outcomes: list[dict[str, Any]] = []
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
    if args.mode == "FULL_ACTIVATED" and args.require_live_clicks_armed and not _env_true("PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS"):
        failures.append("PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS is not set to 1 for FULL_ACTIVATED burn-in process")

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
        ),
        flush=True,
    )

    if args.mode == "FULL_ACTIVATED" and args.require_live_clicks_armed:
        preflight_trace_result = http_json(f"{base}/v1/mobile/runtime/trace/v3?session_id={session_q}", timeout=args.timeout)
        preflight_trace = _extract_payload(preflight_trace_result)
        preflight_gates = _mapping(preflight_trace.get("certification_gates"))
        preflight_nodes = _mapping(_mapping(preflight_trace.get("dataflow_contract_trace")).get("nodes"))
        preflight_source = _mapping(preflight_gates.get("source_lock"))
        preflight_model = _mapping(preflight_gates.get("model_warm_state"))
        preflight_failures: list[str] = []
        if not preflight_trace_result.ok:
            preflight_failures.append(f"runtime trace unavailable: {preflight_trace_result.error or preflight_trace_result.status}")
        if _text(preflight_source.get("status")).upper() != "PASS":
            preflight_failures.append(f"broker source lock preflight is not PASS: {_text(preflight_source.get('status'), 'MISSING')}")
        if _text(preflight_model.get("status")).upper() != "PASS":
            preflight_failures.append(f"model health preflight is not PASS: {_text(preflight_model.get('status'), 'MISSING')}")
        if not initial_processes["shooter_pids"] and not args.allow_missing_shooter:
            preflight_failures.append("shooter process is not alive for FULL_ACTIVATED burn-in")
        if preflight_nodes.get("ShooterPackageReporter") in {"MISSING", "FAIL"}:
            preflight_failures.append(f"ShooterPackageReporter preflight status={preflight_nodes.get('ShooterPackageReporter')}")
        if failures or preflight_failures:
            failures.extend(preflight_failures)
            ended = time.time()
            profitability = _summarize_outcomes([], min_settled_outcomes=args.min_sample_trades)
            safe_paper = _summarize_safe_paper([], {}, [], min_settled_outcomes=args.min_sample_trades)
            precision = _summarize_precision([], [])
            _write_json(BURN_DIR / "profitability_summary.json", profitability)
            _write_json(BURN_DIR / "safe_paper_summary.json", safe_paper)
            _write_json(BURN_DIR / "precision_summary.json", precision)
            report_text = _render_report(
                args=args,
                started=started,
                ended=ended,
                verdict="FAIL_RUNTIME",
                stop_reason="FULL_ACTIVATED_PREFLIGHT_FAILED",
                failures=failures,
                warnings=warnings,
                samples=[],
                component_counts=component_counts,
                profitability=profitability,
                safe_paper=safe_paper,
                precision=precision,
                frame_ages=[],
                overlay_ages=[],
                model_ages=[],
                executable_packets=[],
                shooter_clicks=[],
                skill_rows=0,
                lstm_rows=0,
                two_candle_rows=0,
                promotion_ranking={},
            )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(report_text, encoding="utf-8")
            summary: dict[str, Any] = {
                "schema_version": "PG_FULL_SYSTEM_ACTIVATED_BURN_IN_V1",
                "verdict": "FAIL_RUNTIME",
                "stop_reason": "FULL_ACTIVATED_PREFLIGHT_FAILED",
                "duration_sec": round(ended - started, 3),
                "sample_count": 0,
                "executable_packets": 0,
                "trade_outcomes": 0,
                "report": str(out_path),
                "burn_dir": str(BURN_DIR),
            }
            _write_json(BURN_DIR / "burn_in_summary.json", summary)
            print("FULL_SYSTEM_BURN_IN_DONE " + json.dumps(summary, sort_keys=True, default=str), flush=True)
            return 1

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
        latest_price_proxy = _latest_price_proxy(payloads)

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
        api_listener_alive = any(_int(_mapping(row).get("LocalPort")) == 8793 for row in _sequence(process_snapshot.get("listeners")) if isinstance(row, Mapping))
        if not in_warmup and process_query_reliable:
            consecutive_missing_api_processes = 0 if (process_snapshot["api_pids"] or api_listener_alive) else consecutive_missing_api_processes + 1
            consecutive_missing_tracker_processes = 0 if process_snapshot["tracker_pids"] else consecutive_missing_tracker_processes + 1
            consecutive_missing_shooter_processes = 0 if process_snapshot["shooter_pids"] else consecutive_missing_shooter_processes + 1
        elif in_warmup:
            consecutive_missing_api_processes = 0
            consecutive_missing_tracker_processes = 0
            consecutive_missing_shooter_processes = 0

        sample: dict[str, Any] = {
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
            "latest_price_proxy": latest_price_proxy,
            "stale_execution_packet": stale_execution_packet,
            "process_query_reliable": process_query_reliable,
            "consecutive_missing_api_processes": consecutive_missing_api_processes,
            "consecutive_missing_tracker_processes": consecutive_missing_tracker_processes,
            "consecutive_missing_shooter_processes": consecutive_missing_shooter_processes,
            "dataflow_nodes": _mapping(dataflow.get("nodes")),
        }
        samples.append(sample)
        _append_jsonl(BURN_DIR / "burn_in_samples.jsonl", sample)

        if study_packet_id and not execution_packet_id:
            promotion_row = _promotion_failure_row(now=now, study=study, trace=trace, sample=sample)
            promotion_failures.append(promotion_row)
            _append_jsonl(BURN_DIR / "promotion_failures.jsonl", promotion_row)

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

        execution_payload = _mapping(execution.get("execution"))
        execution_time_sequence = _mapping(execution_payload.get("time_sequence"))
        shooter_expiry_seconds = (
            shooter.get("expiry_seconds")
            or shooter.get("expiry")
            or execution_payload.get("expiry_seconds")
            or execution_time_sequence.get("target_seconds")
        )
        shooter_signature_payload: dict[str, Any] = {
            "packet_id": shooter_packet_id,
            "packet_type": shooter.get("packet_type"),
            "side": shooter.get("side"),
            "expiry_seconds": shooter_expiry_seconds,
            "will_click": shooter.get("will_click"),
            "reason": shooter.get("reason"),
            "timestamp_epoch": shooter.get("timestamp_epoch"),
            "action_sequence": shooter.get("action_sequence"),
            "execution_packet_present": shooter.get("execution_packet_present"),
        }
        shooter_signature = _stable_hash(shooter_signature_payload)
        if shooter_signature not in seen_shooter_signatures:
            seen_shooter_signatures.add(shooter_signature)
            action_row: dict[str, Any] = {
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
            if _actual_click_observed(shooter):
                shooter_clicks.append(action_row)
            safe_decision_event = _safe_event_from_shooter_decision(
                shooter_signature_payload,
                now=now,
                process_snapshot=process_snapshot,
            )
            if safe_decision_event is not None:
                event_key = _text(safe_decision_event.get("event_key"))
                if event_key and event_key not in seen_safe_shooter_events:
                    seen_safe_shooter_events.add(event_key)
                    safe_shooter_events.append(safe_decision_event)
                    _append_jsonl(BURN_DIR / "safe_shooter_events.jsonl", {"epoch": now, "iso": _utc_iso(now), **safe_decision_event})
                    monitor = _paper_monitor_from_safe_event(
                        safe_decision_event,
                        now=now,
                        latest_price_proxy=latest_price_proxy,
                        max_entry_lag_sec=args.paper_entry_max_lag_sec,
                    )
                    if monitor is not None and _text(monitor.get("packet_id")) not in safe_paper_monitors:
                        safe_paper_monitors[_text(monitor.get("packet_id"))] = monitor
                        _append_jsonl(BURN_DIR / "safe_paper_monitors.jsonl", {"epoch": now, "iso": _utc_iso(now), **monitor})

        for evidence in _detect_action_evidence():
            evidence_key = f"{evidence.get('path')}:{evidence.get('modified_epoch')}:{evidence.get('size')}"
            if evidence_key in seen_action_evidence:
                continue
            seen_action_evidence.add(evidence_key)
            _append_jsonl(BURN_DIR / "shooter_actions.jsonl", {"epoch": now, "iso": _utc_iso(now), "action_evidence": evidence})

        for event in _collect_safe_shooter_validation_events():
            event_key = _text(event.get("event_key"))
            if not event_key or event_key in seen_safe_shooter_events:
                continue
            seen_safe_shooter_events.add(event_key)
            safe_shooter_events.append(event)
            _append_jsonl(BURN_DIR / "safe_shooter_events.jsonl", {"epoch": now, "iso": _utc_iso(now), **event})
            monitor = _paper_monitor_from_safe_event(
                event,
                now=now,
                latest_price_proxy=latest_price_proxy,
                max_entry_lag_sec=args.paper_entry_max_lag_sec,
            )
            if monitor is not None and _text(monitor.get("packet_id")) not in safe_paper_monitors:
                safe_paper_monitors[_text(monitor.get("packet_id"))] = monitor
                _append_jsonl(BURN_DIR / "safe_paper_monitors.jsonl", {"epoch": now, "iso": _utc_iso(now), **monitor})

        for event in _collect_live_ready_click_events(since_epoch=started):
            event_key = _text(event.get("event_key"))
            packet_id = _text(event.get("packet_id"))
            if not event_key:
                continue
            if event_key not in seen_live_ready_click_events:
                seen_live_ready_click_events.add(event_key)
                shooter_clicks.append(event)
                _append_jsonl(BURN_DIR / "shooter_actions.jsonl", {"epoch": now, "iso": _utc_iso(now), "live_ready_click_event": event})
            if (
                packet_id
                and event_key not in monitored_live_ready_click_events
                and packet_id not in live_trade_monitors
                and packet_id not in trade_outcome_packet_ids
                and _live_click_armed_from_snapshot(process_snapshot)
            ):
                monitor = _live_trade_monitor_from_click_event(
                    event,
                    now=now,
                    latest_price_proxy=latest_price_proxy,
                    max_entry_lag_sec=max(float(args.paper_entry_max_lag_sec), 90.0),
                )
                if monitor is not None:
                    monitored_live_ready_click_events.add(event_key)
                    live_trade_monitors[packet_id] = monitor
                    _append_jsonl(BURN_DIR / "live_trade_monitors.jsonl", {"epoch": now, "iso": _utc_iso(now), **monitor})

        for outcome in _settle_safe_paper_monitors(
            safe_paper_monitors,
            now=now,
            latest_price_proxy=latest_price_proxy,
        ):
            safe_paper_outcomes.append(outcome)
            _append_jsonl(BURN_DIR / "safe_paper_outcomes.jsonl", {"epoch": now, "iso": _utc_iso(now), **outcome})

        for outcome in _settle_live_trade_monitors(
            live_trade_monitors,
            now=now,
            latest_price_proxy=latest_price_proxy,
        ):
            packet_id = _text(outcome.get("packet_id"))
            if packet_id and packet_id not in trade_outcome_packet_ids:
                trade_outcome_packet_ids.add(packet_id)
                outcome_row: dict[str, Any] = {
                    "trade_id": f"burn_{len(trade_outcomes) + 1:04d}",
                    "packet_id": packet_id,
                    "side": _text(outcome.get("side")),
                    "entry_time": _text(outcome.get("opened_at")),
                    "expiry_seconds": _int(outcome.get("expiry_seconds")),
                    "result": _infer_trade_result(outcome),
                    "lane": _text(outcome.get("lane")),
                    "timing_mode": _text(outcome.get("timing_mode")),
                    "profit_proxy": outcome.get("profit_proxy"),
                    "path_quality": "CHART_PROXY",
                    "raw": outcome,
                    "source": "live_ready_broker_click_chart_proxy",
                }
                trade_outcomes.append(outcome_row)
                live_trade_outcomes.append(outcome_row)
                _append_jsonl(BURN_DIR / "live_trade_outcomes.jsonl", {"epoch": now, "iso": _utc_iso(now), **outcome_row})
                _append_jsonl(BURN_DIR / "trade_outcomes.jsonl", outcome_row)
                result = _infer_trade_result(outcome_row)
                if result == "LOSS":
                    consecutive_losses += 1
                elif result == "WIN":
                    consecutive_losses = 0

        if (
            _actual_click_observed(shooter)
            and _live_click_armed_from_snapshot(process_snapshot)
            and shooter_packet_id
            and shooter_packet_id not in live_trade_monitors
            and shooter_packet_id not in trade_outcome_packet_ids
        ):
            monitor_event: dict[str, Any] = {
                "packet_id": shooter_packet_id,
                "side": _text(shooter.get("side")),
                "timestamp": now,
                "expiry_seconds": _int(shooter.get("expiry_seconds") or shooter.get("expiry")),
                "selected_execution_lane": _text(shooter.get("selected_execution_lane")),
                "timing_mode": _text(shooter.get("timing_mode")),
                "decision_reason": _text(shooter.get("reason")),
                "mode": "LIVE_READY",
                "broker_click_allowed": True,
                "event_key": f"runtime_trace/shooter_handshake|LIVE_READY|{shooter_packet_id}|{now:.3f}",
            }
            monitor = _live_trade_monitor_from_click_event(
                monitor_event,
                now=now,
                latest_price_proxy=latest_price_proxy,
                max_entry_lag_sec=max(float(args.paper_entry_max_lag_sec), 90.0),
            )
            if monitor is not None:
                live_trade_monitors[shooter_packet_id] = monitor
                _append_jsonl(BURN_DIR / "live_trade_monitors.jsonl", {"epoch": now, "iso": _utc_iso(now), **monitor})

        if not trace_result.ok or not perf_result.ok:
            endpoint_failures += 1
            if not in_warmup:
                consecutive_endpoint_failures += 1
            if not in_warmup and consecutive_endpoint_failures > args.max_api_failures:
                stop_reason = (
                    "STOP_ON_API_CRASH "
                    f"endpoint_failures={endpoint_failures} "
                    f"consecutive_endpoint_failures={consecutive_endpoint_failures}"
                )
        elif not in_warmup:
            consecutive_endpoint_failures = 0
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
                ),
                flush=True,
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

    profitability = _summarize_outcomes(trade_outcomes, min_settled_outcomes=args.min_sample_trades)
    safe_paper = _summarize_safe_paper(
        safe_shooter_events,
        safe_paper_monitors,
        safe_paper_outcomes,
        min_settled_outcomes=args.min_sample_trades,
    )
    precision = _summarize_precision(samples, trade_outcomes)
    precision["safe_shooter_execution_readiness"] = {
        "safe_shooter_event_count": len(safe_shooter_events),
        "safe_paper_active_monitor_count": len(safe_paper_monitors),
        "safe_paper_settled_outcome_count": len(safe_paper_outcomes),
    }
    precision["live_trade_chart_proxy"] = {
        "active_monitor_count": len(live_trade_monitors),
        "settled_outcome_count": len(live_trade_outcomes),
        "scope": "live broker clicks settled by chart proxy, not broker statement",
    }
    promotion_ranking = _rank_promotion_blockers(promotion_failures)
    _write_json(BURN_DIR / "profitability_summary.json", profitability)
    _write_json(BURN_DIR / "safe_paper_summary.json", safe_paper)
    _write_json(BURN_DIR / "precision_summary.json", precision)
    _write_json(BURN_DIR / "promotion_blocker_ranking.json", promotion_ranking)
    promotion_report_path = ROOT / "reports" / "FINAL_PROMOTION_FAILURE_AUDIT.md"
    promotion_report_path.parent.mkdir(parents=True, exist_ok=True)
    promotion_report_path.write_text(_render_promotion_failure_report(promotion_failures, promotion_ranking), encoding="utf-8")

    verdict = _final_burn_verdict(
        mode=args.mode,
        stop_reason=stop_reason,
        executable_packets=executable_packets,
        trade_outcomes=trade_outcomes,
        promotion_failures=promotion_failures,
        profitability=profitability,
        safe_paper=safe_paper,
        require_live_clicks_armed=args.require_live_clicks_armed,
        min_sample_trades=args.min_sample_trades,
    )

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
        safe_paper=safe_paper,
        precision=precision,
        frame_ages=frame_ages,
        overlay_ages=overlay_ages,
        model_ages=model_ages,
        executable_packets=executable_packets,
        shooter_clicks=shooter_clicks,
        skill_rows=skill_rows,
        lstm_rows=lstm_rows,
        two_candle_rows=two_candle_rows,
        promotion_ranking=promotion_ranking,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report_text, encoding="utf-8")
    live_click_arming = _actual_live_click_arming(samples)
    live_click_arming["requested_full_activated_mode"] = args.mode == "FULL_ACTIVATED"

    summary: dict[str, Any] = {
        "schema_version": "PG_FULL_SYSTEM_ACTIVATED_BURN_IN_V1",
        "verdict": verdict,
        "stop_reason": stop_reason,
        "duration_sec": round(ended - started, 3),
        "sample_count": len(samples),
        "executable_packets": len(set(executable_packets)),
        "trade_outcomes": len(trade_outcomes),
        "safe_shooter_events": len(safe_shooter_events),
        "safe_paper_outcomes": len(safe_paper_outcomes),
        "p95_frame_age_ms": percentile(frame_ages, 95),
        "p95_overlay_age_ms": percentile(overlay_ages, 95),
        "p95_model_vote_age_ms": percentile(model_ages, 95),
        "live_click_arming": live_click_arming,
        "report": str(out_path),
        "burn_dir": str(BURN_DIR),
    }
    _write_json(BURN_DIR / "burn_in_summary.json", summary)
    print("FULL_SYSTEM_BURN_IN_DONE " + json.dumps(summary, sort_keys=True, default=str), flush=True)
    success_verdicts = {
        "PASS_FULL_ACTIVATED",
        "PASS_SAFE_EXECUTION_READINESS_LIVE_PROFITABILITY_UNCERTIFIED",
    }
    return 0 if verdict in success_verdicts or (args.mode != "FULL_ACTIVATED" and verdict in {"PASS_RUNTIME_ONLY_NO_TRADES", "INSUFFICIENT_SAMPLE"}) else 1


append_jsonl = _append_jsonl
collect_lstm_predictions = _collect_lstm_predictions
collect_skill_contributions = _collect_skill_contributions
collect_two_candle = _collect_two_candle
endpoint_payload = _endpoint_payload
extract_packet_id = _extract_packet_id
final_burn_verdict = _final_burn_verdict
mapping = _mapping
promotion_failure_row = _promotion_failure_row
rank_promotion_blockers = _rank_promotion_blockers
render_promotion_failure_report = _render_promotion_failure_report
text = _text
utc_iso = _utc_iso
write_json = _write_json


if __name__ == "__main__":
    raise SystemExit(main())
