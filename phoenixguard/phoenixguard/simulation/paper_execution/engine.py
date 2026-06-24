from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, cast

from phoenixguard.decision.candle_outcome_tracker import track_candle_outcome
from phoenixguard.execution.execution_rehearsal import rehearse_execution
from phoenixguard.execution.packet_v3 import validate_execution_packet_v3


PAPER_EXECUTION_ENGINE_VERSION = "PG_SIM_PAPER_EXECUTION_ENGINE_V1"
DEFAULT_PAPER_EXECUTION_ROOT = Path("data") / "simulation" / "paper_execution"
BrokerClickExecutor = Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any] | bool]


@dataclass(frozen=True)
class PaperExecutionPaths:
    packet_log: Path = DEFAULT_PAPER_EXECUTION_ROOT / "executable_packets.jsonl"
    broker_demo_log: Path = DEFAULT_PAPER_EXECUTION_ROOT / "broker_demo_rehearsals.jsonl"
    package_report_log: Path = DEFAULT_PAPER_EXECUTION_ROOT / "package_reports.jsonl"

    @classmethod
    def in_dir(cls, root: Path | str) -> "PaperExecutionPaths":
        base = Path(root)
        return cls(
            packet_log=base / "executable_packets.jsonl",
            broker_demo_log=base / "broker_demo_rehearsals.jsonl",
            package_report_log=base / "package_reports.jsonl",
        )


def _mapping(value: object) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    rows = cast(Sequence[object], value)
    return [
        dict(cast(Mapping[str, Any], item))
        for item in rows
        if isinstance(item, Mapping)
    ]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _upper(value: Any) -> str:
    return _text(value).upper()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


def _resolve_now(packet: Mapping[str, Any], now_epoch: float | None) -> float:
    if now_epoch is not None:
        return float(now_epoch)
    created = _float(packet.get("created_epoch"), 0.0)
    if created > 0.0:
        return created
    return time.time()


def _json_clone(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, ensure_ascii=True, default=str))


def _digest(value: Any, *, length: int = 16) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:length]


def _append_jsonl(path: Path, record: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(record), sort_keys=True, ensure_ascii=True, default=str) + "\n")
    return path


def _packet_id(packet: Mapping[str, Any]) -> str:
    return _text(packet.get("packet_id") or packet.get("decision_id"))


def _side(packet: Mapping[str, Any]) -> str:
    execution = _mapping(packet.get("execution"))
    council = _mapping(packet.get("model_council"))
    side = _upper(execution.get("side") or council.get("final_side"))
    return side if side in {"BUY", "SELL"} else "HOLD"


def _expiry_seconds(packet: Mapping[str, Any]) -> int:
    execution = _mapping(packet.get("execution"))
    time_sequence = _mapping(execution.get("time_sequence"))
    return int(_float(execution.get("expiry_seconds"), _float(time_sequence.get("target_seconds"), 0.0)))


def _default_decision(packet: Mapping[str, Any], decision: Mapping[str, Any] | None = None) -> dict[str, Any]:
    payload = _mapping(decision)
    payload.setdefault("will_click", True)
    payload.setdefault("reason", "PAPER_EXECUTION_ENGINE")
    payload.setdefault("side", _side(packet))
    payload.setdefault("expiry_seconds", _expiry_seconds(packet))
    return payload


def _entry_context(packet: Mapping[str, Any], entry_context: Mapping[str, Any] | None) -> dict[str, Any]:
    payload = dict(packet)
    explicit = _mapping(entry_context)
    payload.update(_mapping(packet.get("paper_entry")))
    payload.update(explicit)
    payload.setdefault("side", _side(packet))
    payload.setdefault("execution", _mapping(packet.get("execution")))
    payload.setdefault("market_context", _mapping(packet.get("market_context")))
    payload.setdefault("angle_context", _mapping(packet.get("angle_context")))
    payload.setdefault("model_council", _mapping(packet.get("model_council")))
    return payload


def _paper_execution_id(packet: Mapping[str, Any], entry: Mapping[str, Any], candles: Sequence[Mapping[str, Any]]) -> str:
    packet_id = _packet_id(packet)
    stable = _digest(
        {
            "packet_id": packet_id,
            "session_id": packet.get("session_id"),
            "entry": entry,
            "future_candles": list(candles),
        }
    )
    return f"paper-{packet_id}-{stable}" if packet_id else f"paper-{stable}"


def _package_reporter_result_dict(*, recorded: bool, reason: str, record_path: Path | None = None) -> dict[str, Any]:
    return {
        "mode": "PACKAGE_REPORTER",
        "recorded": recorded,
        "broker_click_allowed": False,
        "execution_removed": True,
        "reason": reason,
        "record_path": str(record_path) if record_path is not None else None,
    }


class PaperExecutionEngine:
    """Deterministic paper execution recorder for V3 model council packets."""

    def __init__(self, paths: PaperExecutionPaths | None = None) -> None:
        self.paths = paths or PaperExecutionPaths()

    def record_executable_packet(
        self,
        packet: Mapping[str, Any],
        future_candles: Sequence[Mapping[str, Any]] | None = None,
        *,
        entry_context: Mapping[str, Any] | None = None,
        decision: Mapping[str, Any] | None = None,
        now_epoch: float | None = None,
        expected_session_id: str | None = None,
        expected_symbol: str | None = None,
        expected_timeframe: str | None = None,
    ) -> dict[str, Any]:
        packet_payload = _mapping(packet)
        now = _resolve_now(packet_payload, now_epoch)
        validation = validate_execution_packet_v3(
            packet_payload,
            now_epoch=now,
            expected_session_id=expected_session_id,
            expected_symbol=expected_symbol,
            expected_timeframe=expected_timeframe,
            require_executable=True,
        )
        if not validation.ok or not validation.executable:
            return {
                "version": PAPER_EXECUTION_ENGINE_VERSION,
                "event": "paper_execution_packet_rejected",
                "timestamp_epoch": now,
                "recorded": False,
                "actual_clicked": False,
                "reason": validation.first_reason,
                "packet_id": _packet_id(packet_payload),
                "validation": validation.as_dict(),
            }

        future_rows = _rows(future_candles or [])
        entry = _entry_context(packet_payload, entry_context)
        outcome = track_candle_outcome(entry, future_rows)
        decision_payload = _default_decision(packet_payload, decision)
        package_record_path = _append_jsonl(
            self.paths.package_report_log,
            {
                "event": "package_report_recorded",
                "timestamp_epoch": now,
                "packet_id": _packet_id(packet_payload),
                "broker_click_allowed": False,
                "execution_removed": True,
                "decision": _json_clone(decision_payload),
            },
        )
        record: dict[str, Any] = {
            "version": PAPER_EXECUTION_ENGINE_VERSION,
            "event": "paper_execution_packet_recorded",
            "paper_execution_id": _paper_execution_id(packet_payload, entry, future_rows),
            "timestamp_epoch": now,
            "recorded": True,
            "actual_clicked": False,
            "broker_click_allowed": False,
            "reason": "PAPER_EXECUTION_RECORDED",
            "packet_id": _packet_id(packet_payload),
            "session_id": _text(packet_payload.get("session_id")),
            "symbol": _text(packet_payload.get("symbol")),
            "timeframe": _upper(packet_payload.get("timeframe")),
            "side": _side(packet_payload),
            "expiry_seconds": _expiry_seconds(packet_payload),
            "validation": validation.as_dict(),
            "decision": _json_clone(decision_payload),
            "packet": _json_clone(packet_payload),
            "entry_context": _json_clone(entry),
            "future_candle_count": len(future_rows),
            "outcome": _json_clone(outcome),
            "package_reporter_result": _package_reporter_result_dict(
                recorded=True,
                reason="PACKAGE_REPORT_RECORDED",
                record_path=package_record_path,
            ),
        }
        record_path = _append_jsonl(self.paths.packet_log, record)
        record["record_path"] = str(record_path)
        return record

    def rehearse_broker_demo(
        self,
        packet: Mapping[str, Any],
        decision: Mapping[str, Any] | None,
        boxes: Mapping[str, Any],
        window_bounds: tuple[int, int, int, int] | list[int],
        *,
        mode: str = "PACKAGE_REPORTER",
        latest_packet: Mapping[str, Any] | None = None,
        now_epoch: float | None = None,
        estimated_execution_latency_ms: float = 230.0,
        require_broker_click_safe: bool = True,
        execute_live_click: bool = False,
        broker_click_executor: BrokerClickExecutor | None = None,
    ) -> dict[str, Any]:
        packet_payload = _mapping(packet)
        now = _resolve_now(packet_payload, now_epoch)
        decision_payload = _default_decision(packet_payload, decision)
        reporter_mode = _upper(mode) or "PACKAGE_REPORTER"
        rehearsal = rehearse_execution(
            packet_payload,
            decision_payload,
            boxes,
            window_bounds,
            latest_packet=latest_packet,
            now_epoch=now,
            estimated_execution_latency_ms=estimated_execution_latency_ms,
            require_broker_click_safe=require_broker_click_safe,
        )
        coordinate_report = _mapping(rehearsal.get("coordinate_report"))

        live_click_report: dict[str, Any] = {
            "requested": bool(execute_live_click),
            "executor_present": broker_click_executor is not None,
            "attempted": False,
            "clicked": False,
            "reason": "SHOOTER_EXECUTION_RETIRED",
        }
        _ = broker_click_executor

        record: dict[str, Any] = {
            "version": PAPER_EXECUTION_ENGINE_VERSION,
            "event": "broker_demo_rehearsal_recorded",
            "timestamp_epoch": now,
            "mode": reporter_mode,
            "packet_id": _packet_id(packet_payload),
            "session_id": _text(packet_payload.get("session_id")),
            "side": _side(packet_payload),
            "expiry_seconds": _expiry_seconds(packet_payload),
            "rehearsal_ready": bool(rehearsal.get("ready")),
            "actual_clicked": bool(live_click_report.get("clicked")),
            "paper_engine_click_suppressed": not bool(live_click_report.get("clicked")),
            "live_click_report": _json_clone(live_click_report),
            "rehearsal": _json_clone(rehearsal),
            "coordinate_report": _json_clone(coordinate_report),
            "package_reporter_result": _package_reporter_result_dict(
                recorded=True,
                reason="PACKAGE_REPORTER_REHEARSAL_RECORDED",
            ),
        }
        record_path = _append_jsonl(self.paths.broker_demo_log, record)
        record["record_path"] = str(record_path)
        return record


def record_executable_paper_packet(
    packet: Mapping[str, Any],
    future_candles: Sequence[Mapping[str, Any]] | None = None,
    *,
    paths: PaperExecutionPaths | None = None,
    entry_context: Mapping[str, Any] | None = None,
    decision: Mapping[str, Any] | None = None,
    now_epoch: float | None = None,
    expected_session_id: str | None = None,
    expected_symbol: str | None = None,
    expected_timeframe: str | None = None,
) -> dict[str, Any]:
    return PaperExecutionEngine(paths).record_executable_packet(
        packet,
        future_candles,
        entry_context=entry_context,
        decision=decision,
        now_epoch=now_epoch,
        expected_session_id=expected_session_id,
        expected_symbol=expected_symbol,
        expected_timeframe=expected_timeframe,
    )


def run_broker_demo_rehearsal(
    packet: Mapping[str, Any],
    decision: Mapping[str, Any] | None,
    boxes: Mapping[str, Any],
    window_bounds: tuple[int, int, int, int] | list[int],
    *,
    paths: PaperExecutionPaths | None = None,
    mode: str = "PACKAGE_REPORTER",
    latest_packet: Mapping[str, Any] | None = None,
    now_epoch: float | None = None,
    estimated_execution_latency_ms: float = 230.0,
    require_broker_click_safe: bool = True,
    execute_live_click: bool = False,
    broker_click_executor: BrokerClickExecutor | None = None,
) -> dict[str, Any]:
    return PaperExecutionEngine(paths).rehearse_broker_demo(
        packet,
        decision,
        boxes,
        window_bounds,
        mode=mode,
        latest_packet=latest_packet,
        now_epoch=now_epoch,
        estimated_execution_latency_ms=estimated_execution_latency_ms,
        require_broker_click_safe=require_broker_click_safe,
        execute_live_click=execute_live_click,
        broker_click_executor=broker_click_executor,
    )


__all__ = [
    "DEFAULT_PAPER_EXECUTION_ROOT",
    "PAPER_EXECUTION_ENGINE_VERSION",
    "PaperExecutionEngine",
    "PaperExecutionPaths",
    "record_executable_paper_packet",
    "run_broker_demo_rehearsal",
]
