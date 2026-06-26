from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Mapping, Sequence, cast


def _default_common_files_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        raise RuntimeError("APPDATA is not set; cannot resolve MetaQuotes common Files directory.")
    return Path(appdata) / "MetaQuotes" / "Terminal" / "Common" / "Files"


SLOT_BYTES = 65536


def _json_safe(value: object) -> object:
    if isinstance(value, dict):
        return {str(key): _json_safe(child) for key, child in cast(Mapping[object, object], value).items()}
    if isinstance(value, list):
        return [_json_safe(child) for child in cast(Sequence[object], value)]
    if isinstance(value, tuple):
        return [_json_safe(child) for child in cast(Sequence[object], value)]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return value


def _json_dumps(value: object, *, sort_keys: bool = False) -> str:
    return json.dumps(
        _json_safe(value),
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=sort_keys,
    )


def _write_text_atomic(path: Path, text: str) -> float:
    """Publish a small MT4 command file with temp-file + atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (text.rstrip("\r\n") + "\n").encode("utf-8")
    if len(payload) > SLOT_BYTES:
        raise ValueError(f"MT4 bridge payload too large: {len(payload)} bytes")
    started = time.perf_counter()
    last_error: Exception | None = None
    temp_name = ""
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        for _ in range(20):
            try:
                os.replace(temp_name, path)
                return (time.perf_counter() - started) * 1000.0
            except (PermissionError, OSError) as exc:
                last_error = exc
                time.sleep(0.025)
    finally:
        if temp_name:
            try:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
            except OSError:
                pass
    raise last_error or PermissionError(path)


def _write_text_shared(path: Path, text: str) -> float:
    """Backward-compatible wrapper; now uses atomic publish semantics."""
    return _write_text_atomic(path, text)


def _append_jsonl(path: Path, record: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = _json_dumps(record, sort_keys=True) + "\n"
    last_error: Exception | None = None
    for _ in range(5):
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line)
            return
        except OSError as exc:
            last_error = exc
            time.sleep(0.025)
    if last_error:
        raise last_error


def _get_json(url: str, timeout: float) -> tuple[int, dict[str, object]]:
    req = urllib.request.Request(url=url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            payload: object = json.loads(raw) if raw.strip() else {}
            return int(resp.status), cast(dict[str, object], payload) if isinstance(payload, dict) else {"raw": payload}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload: object = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            payload = {"detail": raw}
        return int(exc.code), cast(dict[str, object], payload) if isinstance(payload, dict) else {"raw": payload}


def _status(
    status: str,
    *,
    detail: str = "",
    http_status: int = 0,
    bridge_sequence: int = 0,
    error: str = "",
) -> dict[str, object]:
    written_epoch = time.time()
    return {
        "schema_version": "PG_MT4_BRIDGE_STATUS_V1",
        "bridge_status": status,
        "detail": detail,
        "http_status": http_status,
        "bridge_sequence": bridge_sequence,
        "written_epoch": written_epoch,
        "timestamp_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(written_epoch)),
        "heartbeat": {
            "alive": status != "BRIDGE_ERROR",
            "bridge_sequence": bridge_sequence,
            "written_epoch": written_epoch,
        },
        "error": error,
    }


def _nested(payload: dict[str, object], key: str) -> dict[str, object]:
    value = payload.get(key)
    return dict(cast(Mapping[str, object], value)) if isinstance(value, dict) else {}


def _collect_reason_codes(*sources: dict[str, object]) -> list[str]:
    reasons: list[str] = []
    for source in sources:
        for key in ("reason_codes", "reasons", "blockers", "deny_reasons"):
            raw = source.get(key)
            if isinstance(raw, list):
                reasons.extend(str(item) for item in cast(Sequence[object], raw) if str(item))
            elif isinstance(raw, str) and raw:
                reasons.append(raw)
    return list(dict.fromkeys(reasons))


def _allowance_source(payload: dict[str, object], execution: dict[str, object], council: dict[str, object]) -> dict[str, object]:
    promotion_trace = _nested(council, "promotion_trace") or _nested(payload, "promotion_trace")
    for source in (
        _nested(payload, "allowance_package"),
        _nested(execution, "allowance_package"),
        _nested(council, "allowance_package"),
        _nested(promotion_trace, "allowance_package"),
    ):
        if source:
            return source
    return {}


def _compact_allowance_package(
    payload: dict[str, object],
    *,
    execution: dict[str, object],
    council: dict[str, object],
    eligible: bool,
    side: str,
) -> dict[str, object]:
    source = _allowance_source(payload, execution, council)
    source_present = bool(source)
    timing_decision = _nested(payload, "timing_decision") or _nested(council, "timing_decision")
    timing_mode = str(
        source.get("timing_mode")
        or timing_decision.get("timing_mode")
        or execution.get("timing_mode")
        or ""
    ).upper()
    entry_now_allowed = (
        source.get("entry_now_allowed") is True
        or timing_decision.get("entry_now_allowed") is True
    )
    package_type = str(source.get("package_type") or "").upper()
    if not package_type:
        package_type = "INTRADAY_ENTER_NOW" if entry_now_allowed and timing_mode == "ENTER_NOW" else "SWING"
    allowance_family = str(source.get("allowance_family") or "").upper()
    if not allowance_family:
        allowance_family = "INTRADAY" if package_type == "INTRADAY_ENTER_NOW" else "SWING"
    return {
        "schema_version": str(source.get("schema_version") or "PG_ALLOWANCE_PACKAGE_V1"),
        "package_type": package_type,
        "allowance_family": allowance_family,
        "execution_authority": str(source.get("execution_authority") or "PG_EXECUTION_PACKET_V3"),
        "source_present": source_present,
        "inferred": not source_present,
        "side": str(source.get("side") or side),
        "accepted": bool(source.get("accepted", eligible)),
        "decision_accepted": bool(source.get("decision_accepted", source.get("accepted", eligible))),
        "execution_ready": bool(source.get("execution_ready", eligible)),
        "executable": bool(source.get("executable", eligible)),
        "tracking_active": bool(source.get("tracking_active", False)) and not eligible,
        "intraday_capture_active": bool(source.get("intraday_capture_active", package_type == "INTRADAY_ENTER_NOW" and eligible)),
        "entry_now_allowed": bool(entry_now_allowed),
        "timing_mode": timing_mode,
        "path_class": str(source.get("path_class") or timing_decision.get("path_class") or ""),
        "selected_lane": str(source.get("selected_lane") or council.get("selected_execution_lane") or council.get("selected_lane") or payload.get("selected_execution_lane") or ""),
        "score": source.get("score", council.get("final_execution_score", payload.get("final_score", 0.0))),
        "threshold": source.get("threshold", council.get("threshold", payload.get("threshold", 0.0))),
        "true_blocker": source.get("true_blocker", ""),
        "next_required": source.get("next_required", ""),
    }


def _compact_command(payload: dict[str, object], *, bridge_sequence: int = 0) -> dict[str, object]:
    execution = _nested(payload, "execution")
    council = _nested(payload, "model_council")
    live = _nested(payload, "live_integrity")
    health = _nested(payload, "runtime_model_health")
    sequence = _nested(council, "sequence_context")
    permission = _nested(payload, "trade_permission") or _nested(council, "trade_permission")
    time_sequence = _nested(execution, "time_sequence")
    created_epoch = payload.get("created_epoch_sec") or payload.get("created_epoch", 0)
    valid_until = payload.get("valid_until_epoch_sec") or payload.get("valid_until_epoch", 0)
    execution_state = str(execution.get("state", ""))
    side = str(execution.get("side") or council.get("final_side") or "")
    executable_allowed = permission.get("executable_allowed") is True if permission else False
    eligible = bool(execution.get("enabled", False)) and execution_state == "EXECUTABLE" and bool(executable_allowed)
    allowance_package = _compact_allowance_package(
        payload,
        execution=execution,
        council=council,
        eligible=eligible,
        side=side,
    )
    bridge_written_epoch = time.time()
    return {
        "schema_version": "PG_MT4_EXECUTION_COMMAND_V1",
        "source_schema_version": payload.get("schema_version", ""),
        "bridge_sequence": bridge_sequence,
        "packet_id": payload.get("packet_id", ""),
        "session_id": payload.get("session_id", ""),
        "symbol": payload.get("symbol", ""),
        "timeframe": payload.get("timeframe", ""),
        "frame_id": payload.get("frame_id", 0),
        "capture_count": payload.get("capture_count", 0),
        "state_version": payload.get("state_version", 0),
        "created_epoch_sec": created_epoch,
        "valid_until_epoch_sec": valid_until,
        "signal_state": {
            "state": execution_state or str(council.get("final_state", "")),
            "side": side,
            "source": "model_council_execution_latest",
            "allowance_package_type": allowance_package["package_type"],
        },
        "permission_state": {
            "execution_enabled": bool(execution.get("enabled", False)),
            "execution_state": execution_state,
            "trade_executable_allowed": bool(executable_allowed),
            "entry_eligible": eligible,
        },
        "entry_eligibility": {
            "eligible": eligible,
            "side": side,
            "state": execution_state,
            "valid_until_epoch_sec": valid_until,
            "allowance_package_type": allowance_package["package_type"],
            "allowance_family": allowance_package["allowance_family"],
        },
        "allowance_package": allowance_package,
        "reason_codes": _collect_reason_codes(payload, execution, council, permission),
        "confidence_score": council.get("confidence")
        or council.get("final_confidence")
        or council.get("dominance_margin")
        or payload.get("confidence")
        or 0.0,
        "execution": {
            "enabled": execution.get("enabled", False),
            "state": execution_state,
            "side": side,
            "expiry_seconds": execution.get("expiry_seconds", 0),
            "amount_action": execution.get("amount_action", ""),
            "allowance_package_type": allowance_package["package_type"],
            "time_sequence": {
                "target_seconds": time_sequence.get("target_seconds") or execution.get("expiry_seconds", 0),
                "target_text": time_sequence.get("target_text", ""),
            },
        },
        "model_council": {
            "final_state": council.get("final_state", ""),
            "final_side": council.get("final_side", ""),
            "dominance_margin": council.get("dominance_margin", 0.0),
            "allowance_package_type": allowance_package["package_type"],
            "sequence_context": {
                "sequence_status": sequence.get("sequence_status") or sequence.get("status", ""),
                "status": sequence.get("status") or sequence.get("sequence_status", ""),
                "sequence_length": sequence.get("sequence_length", 0),
                "sequence_confidence": sequence.get("sequence_confidence", 1.0),
            },
        },
        "live_integrity": {
            "is_live": live.get("is_live") is True,
            "frame_advancing": live.get("frame_advancing") is True,
            "capture_advancing": live.get("capture_advancing") is True,
            "state_advancing": live.get("state_advancing") is True,
            "source": live.get("source", ""),
            "cache_status": live.get("cache_status", ""),
            "input_frame_hash": live.get("input_frame_hash", ""),
        },
        "runtime_model_health": {
            "all_required_models_awake": health.get("all_required_models_awake") is True,
        },
        "trade_permission": {
            "executable_allowed": executable_allowed,
        },
        "heartbeat": {
            "alive": True,
            "bridge_sequence": bridge_sequence,
            "source_created_epoch_sec": created_epoch,
            "valid_until_epoch_sec": valid_until,
            "bridge_written_epoch": bridge_written_epoch,
        },
        "error": "",
        "bridge_compacted": True,
        "bridge_written_epoch": bridge_written_epoch,
    }


def _validate_command(command: dict[str, object]) -> None:
    required_top = (
        "schema_version",
        "packet_id",
        "symbol",
        "created_epoch_sec",
        "valid_until_epoch_sec",
        "execution",
        "allowance_package",
        "model_council",
        "live_integrity",
        "trade_permission",
        "bridge_sequence",
        "heartbeat",
    )
    missing = [key for key in required_top if key not in command]
    if missing:
        raise ValueError(f"MT4 command missing required fields: {missing}")
    if command.get("schema_version") != "PG_MT4_EXECUTION_COMMAND_V1":
        raise ValueError("MT4 command schema_version mismatch")
    if not str(command.get("packet_id") or ""):
        raise ValueError("MT4 command packet_id is empty")
    if not str(command.get("symbol") or ""):
        raise ValueError("MT4 command symbol is empty")
    execution = _nested(command, "execution")
    for key in ("enabled", "state", "side", "expiry_seconds", "amount_action", "time_sequence"):
        if key not in execution:
            raise ValueError(f"MT4 command execution missing {key}")
    if execution.get("amount_action") != "DO_NOT_CHANGE_AMOUNT":
        raise ValueError("MT4 command execution.amount_action must be DO_NOT_CHANGE_AMOUNT")
    allowance = _nested(command, "allowance_package")
    if allowance.get("schema_version") != "PG_ALLOWANCE_PACKAGE_V1":
        raise ValueError("MT4 command allowance_package schema_version mismatch")
    if allowance.get("source_present") is not True or allowance.get("inferred") is True:
        raise ValueError("MT4 command allowance_package must be explicit from Model Council")
    if allowance.get("package_type") not in {"SWING", "INTRADAY_ENTER_NOW"}:
        raise ValueError("MT4 command allowance_package.package_type must be SWING or INTRADAY_ENTER_NOW")
    if allowance.get("execution_authority") != "PG_EXECUTION_PACKET_V3":
        raise ValueError("MT4 command allowance_package.execution_authority must be PG_EXECUTION_PACKET_V3")
    if allowance.get("accepted") is not True:
        raise ValueError("MT4 command allowance_package.accepted must be true")
    if allowance.get("execution_ready") is not True:
        raise ValueError("MT4 command allowance_package.execution_ready must be true")
    live = _nested(command, "live_integrity")
    if live.get("is_live") is not True:
        raise ValueError("MT4 command live_integrity.is_live must be true")
    for key in ("frame_advancing", "capture_advancing", "state_advancing"):
        if live.get(key) is not True:
            raise ValueError(f"MT4 command live_integrity.{key} must be true")
    if live.get("cache_status") != "fresh":
        raise ValueError("MT4 command live_integrity.cache_status must be fresh")
    if live.get("source") != "model_council":
        raise ValueError("MT4 command live_integrity.source must be model_council")
    if not str(live.get("input_frame_hash") or ""):
        raise ValueError("MT4 command live_integrity.input_frame_hash is empty")
    health = _nested(command, "runtime_model_health")
    if health.get("all_required_models_awake") is not True:
        raise ValueError("MT4 command runtime_model_health.all_required_models_awake must be true")
    trade_permission = _nested(command, "trade_permission")
    if trade_permission.get("executable_allowed") is not True:
        raise ValueError("MT4 command trade_permission.executable_allowed must be true")
    _json_dumps(command)


def main() -> int:
    parser = argparse.ArgumentParser(description="Bridge PhoenixGuard execution packets into MT4 FILE_COMMON.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8793")
    parser.add_argument("--session-id", default="pocket-live-8788")
    parser.add_argument("--common-files-dir", default="")
    parser.add_argument("--signal-file", default=r"PhoenixGuard\mt4_execution_command.json")
    parser.add_argument("--status-file", default=r"PhoenixGuard\mt4_bridge_status.json")
    parser.add_argument("--metrics-file", default=r"PhoenixGuard\mt4_bridge_metrics.jsonl")
    parser.add_argument("--poll-sec", type=float, default=15.0)
    parser.add_argument("--timeout-sec", type=float, default=8.0)
    parser.add_argument("--print-every", type=float, default=30.0)
    parser.add_argument("--metrics-every", type=float, default=15.0)
    args = parser.parse_args()

    common_root = Path(args.common_files_dir) if args.common_files_dir else _default_common_files_dir()
    signal_path = common_root / args.signal_file
    status_path = common_root / args.status_file
    metrics_path = common_root / args.metrics_file if args.metrics_file else None
    url = (
        args.base_url.rstrip("/")
        + "/v1/mobile/model-council/sessions/"
        + urllib.parse.quote(str(args.session_id), safe="")
        + "/execution/latest"
    )

    print(f"PhoenixGuard MT4 bridge polling: {url}", flush=True)
    print(f"MT4 signal file: {signal_path}", flush=True)
    print(f"MT4 status file: {status_path}", flush=True)
    if metrics_path:
        print(f"MT4 metrics file: {metrics_path}", flush=True)

    last_print = 0.0
    last_metrics = 0.0
    last_status = ""
    last_packet_id = ""
    bridge_sequence = 0
    while True:
        now = time.time()
        bridge_sequence += 1
        http_ms = 0.0
        write_ms = 0.0
        metric_status = "UNKNOWN"
        metric_error = ""
        metric_http_status = 0
        try:
            http_started = time.perf_counter()
            status, payload = _get_json(url, args.timeout_sec)
            http_ms = (time.perf_counter() - http_started) * 1000.0
            metric_http_status = status
            if status == 200 and payload.get("schema_version") == "PG_EXECUTION_PACKET_V3":
                packet_id = str(payload.get("packet_id") or "")
                command = _compact_command(payload, bridge_sequence=bridge_sequence)
                _validate_command(command)
                body = _json_dumps(command, sort_keys=False)
                write_ms += _write_text_shared(signal_path, body)
                write_ms += _write_text_shared(
                    status_path,
                    _json_dumps(
                        _status(
                            "EXECUTION_PACKET",
                            detail=packet_id,
                            http_status=status,
                            bridge_sequence=bridge_sequence,
                        ),
                        sort_keys=True,
                    ),
                )
                last_status = f"EXECUTION_PACKET {packet_id}"
                last_packet_id = packet_id
                metric_status = "EXECUTION_PACKET"
            elif status == 404:
                detail = str(payload.get("detail") or "Model Council executable packet not found.")
                status_body = _json_dumps(
                    _status("NO_EXECUTION_PACKET", detail=detail, http_status=status, bridge_sequence=bridge_sequence),
                    sort_keys=True,
                )
                write_ms += _write_text_shared(signal_path, status_body)
                write_ms += _write_text_shared(status_path, status_body)
                last_status = "NO_EXECUTION_PACKET"
                metric_status = "NO_EXECUTION_PACKET"
            else:
                detail = str(payload.get("detail") or payload)[:500]
                status_body = _json_dumps(
                    _status(
                        "BRIDGE_ERROR",
                        detail=detail,
                        http_status=status,
                        bridge_sequence=bridge_sequence,
                        error=detail,
                    ),
                    sort_keys=True,
                )
                write_ms += _write_text_shared(signal_path, status_body)
                write_ms += _write_text_shared(status_path, status_body)
                last_status = f"BRIDGE_ERROR HTTP {status}"
                metric_status = "BRIDGE_ERROR"
                metric_error = detail
        except Exception as exc:
            detail = str(exc)[:500]
            metric_error = detail
            metric_status = "BRIDGE_ERROR"
            try:
                status_body = _json_dumps(
                    _status("BRIDGE_ERROR", detail=detail, bridge_sequence=bridge_sequence, error=detail),
                    sort_keys=True,
                )
                write_ms += _write_text_shared(signal_path, status_body)
                write_ms += _write_text_shared(status_path, status_body)
            except Exception as write_exc:
                metric_error = f"{detail}; status_write_failed={write_exc}"
            last_status = f"BRIDGE_ERROR {detail}"

        if metrics_path and (time.time() - last_metrics >= max(0.1, args.metrics_every) or metric_status == "EXECUTION_PACKET"):
            try:
                _append_jsonl(
                    metrics_path,
                    {
                        "at_epoch": time.time(),
                        "at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        "bridge_sequence": bridge_sequence,
                        "status": metric_status,
                        "http_status": metric_http_status,
                        "http_ms": round(http_ms, 3),
                        "write_ms": round(write_ms, 3),
                        "last_packet_id": last_packet_id,
                        "error": metric_error,
                    },
                )
            except Exception as metrics_exc:
                print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} METRICS_WRITE_ERROR {metrics_exc}", flush=True)
            last_metrics = time.time()

        if now - last_print >= args.print_every:
            suffix = f" last_packet={last_packet_id}" if last_packet_id else ""
            print(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} {last_status}{suffix} "
                f"seq={bridge_sequence} http_ms={http_ms:.1f} write_ms={write_ms:.1f}",
                flush=True,
            )
            last_print = now
        time.sleep(max(0.05, args.poll_sec))


if __name__ == "__main__":
    raise SystemExit(main())
