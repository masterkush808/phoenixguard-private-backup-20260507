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
from phoenixguard.execution.shooter_modes import (
    ShooterMode,
    ShooterModeResult,
    build_coordinate_report,
    record_calibration_test,
    record_dry_run_click,
    record_live_disabled,
    record_live_ready,
    record_paper_execution,
    resolve_shooter_mode,
)


PAPER_EXECUTION_ENGINE_VERSION = "PG_SIM_PAPER_EXECUTION_ENGINE_V1"
DEFAULT_PAPER_EXECUTION_ROOT = Path("data") / "simulation" / "paper_execution"
BrokerClickExecutor = Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]], Mapping[str, Any] | bool]


@dataclass(frozen=True)
class PaperExecutionPaths:
    packet_log: Path = DEFAULT_PAPER_EXECUTION_ROOT / "executable_packets.jsonl"
    broker_demo_log: Path = DEFAULT_PAPER_EXECUTION_ROOT / "broker_demo_rehearsals.jsonl"
    shooter_paper_log: Path = DEFAULT_PAPER_EXECUTION_ROOT / "shooter_paper_executions.jsonl"
    shooter_dry_run_log: Path = DEFAULT_PAPER_EXECUTION_ROOT / "shooter_dry_run_clicks.jsonl"
    shooter_calibration_log: Path = DEFAULT_PAPER_EXECUTION_ROOT / "shooter_calibration_tests.jsonl"
    shooter_live_disabled_log: Path = DEFAULT_PAPER_EXECUTION_ROOT / "shooter_live_disabled.jsonl"
    shooter_live_ready_log: Path = DEFAULT_PAPER_EXECUTION_ROOT / "shooter_live_ready.jsonl"

    @classmethod
    def in_dir(cls, root: Path | str) -> "PaperExecutionPaths":
        base = Path(root)
        return cls(
            packet_log=base / "executable_packets.jsonl",
            broker_demo_log=base / "broker_demo_rehearsals.jsonl",
            shooter_paper_log=base / "shooter_paper_executions.jsonl",
            shooter_dry_run_log=base / "shooter_dry_run_clicks.jsonl",
            shooter_calibration_log=base / "shooter_calibration_tests.jsonl",
            shooter_live_disabled_log=base / "shooter_live_disabled.jsonl",
            shooter_live_ready_log=base / "shooter_live_ready.jsonl",
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


def _shooter_result_dict(result: ShooterModeResult | None) -> dict[str, Any]:
    if result is None:
        return {
            "mode": "",
            "recorded": False,
            "broker_click_allowed": False,
            "reason": "NO_SHOOTER_MODE_RECORD",
            "record_path": None,
        }
    return {
        "mode": result.mode.value,
        "recorded": bool(result.recorded),
        "broker_click_allowed": bool(result.broker_click_allowed),
        "reason": result.reason,
        "record_path": result.record_path,
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
        shooter_result = record_paper_execution(
            packet_payload,
            decision_payload,
            path=self.paths.shooter_paper_log,
            now=now,
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
            "shooter_mode_result": _shooter_result_dict(shooter_result),
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
        mode: ShooterMode | str = ShooterMode.DRY_RUN_CLICK,
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
        shooter_mode = resolve_shooter_mode(mode)
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
        if not coordinate_report:
            coordinate_report = build_coordinate_report(
                boxes,
                window_bounds,
                side=_side(packet_payload),
                expiry_seconds=_expiry_seconds(packet_payload),
            )

        live_click_report: dict[str, Any] = {
            "requested": bool(execute_live_click),
            "executor_present": broker_click_executor is not None,
            "attempted": False,
            "clicked": False,
            "reason": "LIVE_CLICK_NOT_REQUESTED",
        }
        shooter_result: ShooterModeResult | None
        if shooter_mode == ShooterMode.PAPER_EXECUTION:
            shooter_result = record_paper_execution(
                packet_payload,
                decision_payload,
                path=self.paths.shooter_paper_log,
                now=now,
            )
        elif shooter_mode == ShooterMode.DRY_RUN_CLICK:
            shooter_result = record_dry_run_click(
                packet_payload,
                decision_payload,
                coordinate_report,
                path=self.paths.shooter_dry_run_log,
                now=now,
            )
        elif shooter_mode == ShooterMode.CALIBRATION_TEST:
            shooter_result = record_calibration_test(
                packet_payload,
                decision_payload,
                coordinate_report,
                path=self.paths.shooter_calibration_log,
                now=now,
            )
        elif shooter_mode == ShooterMode.LIVE_DISABLED:
            shooter_result = record_live_disabled(
                packet_payload,
                decision_payload,
                path=self.paths.shooter_live_disabled_log,
                now=now,
            )
        elif shooter_mode == ShooterMode.LIVE_READY:
            if execute_live_click and broker_click_executor is not None and rehearsal.get("ready"):
                live_click_report["attempted"] = True
                raw_click_result = broker_click_executor(packet_payload, rehearsal, coordinate_report)
                if isinstance(raw_click_result, Mapping):
                    live_click_report.update(dict(raw_click_result))
                    live_click_report["clicked"] = bool(raw_click_result.get("clicked"))
                else:
                    live_click_report["clicked"] = bool(raw_click_result)
                live_click_report["reason"] = str(live_click_report.get("reason") or "LIVE_READY_DEMO_EXECUTOR_RETURNED")
            elif execute_live_click and broker_click_executor is None:
                live_click_report["reason"] = "LIVE_CLICK_EXECUTOR_MISSING"
            elif execute_live_click and not rehearsal.get("ready"):
                live_click_report["reason"] = f"LIVE_CLICK_REHEARSAL_BLOCKED:{rehearsal.get('reason')}"
            reason = (
                "LIVE_READY_DEMO_CLICK_RECORDED"
                if live_click_report.get("clicked")
                else "LIVE_READY_REHEARSAL_READY_NO_CLICK"
                if rehearsal.get("ready")
                else f"LIVE_READY_REHEARSAL_BLOCKED:{rehearsal.get('reason')}"
            )
            shooter_result = record_live_ready(
                packet_payload,
                decision_payload,
                clicked=bool(live_click_report.get("clicked")),
                reason=reason,
                rehearsal=rehearsal,
                path=self.paths.shooter_live_ready_log,
                now=now,
            )
        else:
            shooter_result = None

        record: dict[str, Any] = {
            "version": PAPER_EXECUTION_ENGINE_VERSION,
            "event": "broker_demo_rehearsal_recorded",
            "timestamp_epoch": now,
            "mode": shooter_mode.value,
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
            "shooter_mode_result": _shooter_result_dict(shooter_result),
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
    mode: ShooterMode | str = ShooterMode.DRY_RUN_CLICK,
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
