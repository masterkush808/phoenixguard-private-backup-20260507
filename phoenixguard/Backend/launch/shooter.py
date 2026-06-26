#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path
import sys

_PROJECT_ROOT_BOOTSTRAP = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_BOOTSTRAP))

from _pg_bootstrap import ensure_project_paths
PROJECT_ROOT = ensure_project_paths()

"""
PhoenixGuard shooter package reporter.

The old calibrated broker-click shooter has been retired. This process now has
one job: report fresh Model Council allowance packages that are already accepted
for either INTRADAY_ENTER_NOW or SWING handling. It does not read calibration
files, move the mouse, click the broker, set time controls, or execute trades.
"""

import argparse
import json
import logging
import os
import time
from typing import Mapping, Sequence, TypedDict, cast
import urllib.error
import urllib.parse
import urllib.request

from phoenixguard.execution.packet_v3 import validate_execution_packet_v3
from phoenixguard.runtime.singleton_guard_v3 import PhoenixRuntimeSingletonGuardV3, guard_from_environment


JsonDict = dict[str, object]
ALLOWED_PACKAGE_TYPES = frozenset({"INTRADAY_ENTER_NOW", "SWING"})
REPORT_SCHEMA_VERSION = "PG_SHOOTER_PACKAGE_REPORT_V1"
PACKAGE_SCHEMA_VERSION = "PG_ALLOWANCE_PACKAGE_V1"
EXECUTION_AUTHORITY = "PG_EXECUTION_PACKET_V3"
DEFAULT_BASE_URL = "http://127.0.0.1:8793"
DEFAULT_SESSION_ID = "pocket-live-8788"
DEFAULT_POLL_SECONDS = 15.0
DEFAULT_TIMEOUT_SECONDS = 5.0
DEFAULT_HEARTBEAT_SECONDS = 4.0
REPORT_TTL_SECONDS = 8.0
_RUNTIME_DIR = PROJECT_ROOT / ".codex_runtime"
_SHOOTER_HANDSHAKE_PATH = _RUNTIME_DIR / "shooter_handshake.json"

LOGGER = logging.getLogger("shooter_package_reporter")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S",
)


class AllowanceReview(TypedDict):
    allowed: bool
    reason: str
    allowance_package: JsonDict


def _as_mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def _as_dict(value: object) -> JsonDict:
    return dict(cast(Mapping[str, object], value)) if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _upper(value: object) -> str:
    return _text(value).upper()


def _float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _json_ready(value: object) -> object:
    if isinstance(value, Mapping):
        mapping_value = cast(Mapping[object, object], value)
        return {str(key): _json_ready(item) for key, item in mapping_value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sequence_value = cast(Sequence[object], value)
        return [_json_ready(item) for item in sequence_value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _read_json_url(url: str, *, timeout_sec: float) -> JsonDict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout_sec) as response:
        raw = response.read().decode("utf-8")
    parsed: object = json.loads(raw)
    if not isinstance(parsed, Mapping):
        raise ValueError("endpoint did not return a JSON object")
    return dict(cast(Mapping[str, object], parsed))


def _execution_url(base_url: str, session_id: str) -> str:
    return (
        base_url.rstrip("/")
        + "/v1/mobile/model-council/sessions/"
        + urllib.parse.quote(session_id, safe="")
        + "/execution/latest"
    )


def _allowance_package_from_packet(packet: Mapping[str, object]) -> JsonDict:
    direct = _as_dict(packet.get("allowance_package"))
    if direct:
        return direct
    council = _as_mapping(packet.get("model_council"))
    return _as_dict(council.get("allowance_package"))


def review_allowed_package(packet: Mapping[str, object], *, now_epoch: float | None = None) -> AllowanceReview:
    validation = validate_execution_packet_v3(packet, now_epoch=now_epoch)
    if validation.rejected:
        return {
            "allowed": False,
            "reason": "PACKET_VALIDATION_REJECTED:" + ",".join(validation.reason_codes),
            "allowance_package": {},
        }

    allowance = _allowance_package_from_packet(packet)
    if not allowance:
        return {"allowed": False, "reason": "MISSING_ALLOWANCE_PACKAGE", "allowance_package": {}}

    package_type = _upper(allowance.get("package_type"))
    if allowance.get("schema_version") != PACKAGE_SCHEMA_VERSION:
        return {"allowed": False, "reason": "INVALID_ALLOWANCE_PACKAGE_SCHEMA", "allowance_package": allowance}
    if package_type not in ALLOWED_PACKAGE_TYPES:
        return {"allowed": False, "reason": "UNKNOWN_ALLOWANCE_PACKAGE", "allowance_package": allowance}
    if allowance.get("execution_authority") != EXECUTION_AUTHORITY:
        return {"allowed": False, "reason": "INVALID_ALLOWANCE_EXECUTION_AUTHORITY", "allowance_package": allowance}
    if allowance.get("accepted") is not True:
        return {"allowed": False, "reason": "ALLOWANCE_PACKAGE_NOT_ACCEPTED", "allowance_package": allowance}
    if allowance.get("execution_ready") is not True:
        return {"allowed": False, "reason": "ALLOWANCE_PACKAGE_NOT_EXECUTION_READY", "allowance_package": allowance}
    if package_type == "INTRADAY_ENTER_NOW" and allowance.get("entry_now_allowed") is not True:
        return {"allowed": False, "reason": "INTRADAY_PACKAGE_NOT_ENTRY_NOW_ALLOWED", "allowance_package": allowance}

    execution = _as_mapping(packet.get("execution"))
    execution_side = _upper(execution.get("side"))
    package_side = _upper(allowance.get("side"))
    if package_side and execution_side and package_side != execution_side:
        return {"allowed": False, "reason": "ALLOWANCE_SIDE_EXECUTION_SIDE_MISMATCH", "allowance_package": allowance}

    return {"allowed": True, "reason": "ALLOWED_PACKAGE_READY", "allowance_package": allowance}


def build_allowed_package_report(
    packet: Mapping[str, object],
    review: AllowanceReview,
    *,
    source_url: str,
    now_epoch: float | None = None,
) -> JsonDict:
    if not review["allowed"]:
        raise ValueError(f"Cannot report a disallowed package: {review['reason']}")
    now_value = time.time() if now_epoch is None else float(now_epoch)
    allowance = review["allowance_package"]
    execution = _as_mapping(packet.get("execution"))
    package_type = _upper(allowance.get("package_type"))
    side = _upper(execution.get("side") or allowance.get("side"))
    packet_id = _text(packet.get("packet_id"))
    session_id = _text(packet.get("session_id"))
    valid_until = _float(packet.get("valid_until_epoch_sec") or packet.get("valid_until_epoch"), now_value + REPORT_TTL_SECONDS)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "status": "PASS",
        "state": "ALLOWED_PACKAGE_REPORTED",
        "mode": "PACKAGE_REPORTER",
        "available": True,
        "execution_removed": True,
        "broker_click_allowed": False,
        "will_click": False,
        "action": "REPORT_ALLOWED_PACKAGE",
        "reason": review["reason"],
        "session_id": session_id,
        "packet_id": packet_id,
        "package_type": package_type,
        "allowance_family": _upper(allowance.get("allowance_family")),
        "side": side,
        "timing_mode": _upper(allowance.get("timing_mode")),
        "selected_lane": _upper(allowance.get("selected_lane")),
        "updated_epoch_sec": now_value,
        "valid_until_epoch_sec": valid_until,
        "source": "model_council_execution_latest",
        "source_url": source_url,
        "allowed_package": _as_dict(_json_ready(allowance)),
        "allowance_package": _as_dict(_json_ready(allowance)),
        "payload": _as_dict(_json_ready(packet)),
    }


def _write_shooter_handshake(payload: Mapping[str, object], path: Path = _SHOOTER_HANDSHAKE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(_json_ready(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(encoded, encoding="utf-8")
    temp_path.replace(path)


def build_waiting_report(
    *,
    session_id: str,
    base_url: str,
    source_url: str,
    reason: str,
    now_epoch: float | None = None,
) -> JsonDict:
    now_value = float(now_epoch if now_epoch is not None else time.time())
    return {
        "schema_version": "PG_SHOOTER_PACKAGE_REPORTER_HEARTBEAT_V1",
        "status": "WAITING",
        "state": "WAITING",
        "mode": "PACKAGE_REPORTER",
        "available": True,
        "execution_removed": True,
        "broker_click_allowed": False,
        "will_click": False,
        "action": "WAIT_FOR_ALLOWED_PACKAGE",
        "reason": reason,
        "session_id": session_id,
        "base_url": base_url,
        "source": "model_council_execution_latest",
        "source_url": source_url,
        "updated_epoch_sec": now_value,
        "next_required": "fresh accepted intraday or swing allowance package",
        "package_type": "",
        "packet_id": "",
    }


def publish_waiting_report(
    *,
    session_id: str,
    base_url: str,
    source_url: str,
    reason: str,
    path: Path = _SHOOTER_HANDSHAKE_PATH,
    now_epoch: float | None = None,
) -> JsonDict:
    report = build_waiting_report(
        session_id=session_id,
        base_url=base_url,
        source_url=source_url,
        reason=reason,
        now_epoch=now_epoch,
    )
    _write_shooter_handshake(report, path)
    return report


def publish_allowed_package_report(
    packet: Mapping[str, object],
    *,
    source_url: str,
    path: Path = _SHOOTER_HANDSHAKE_PATH,
    now_epoch: float | None = None,
) -> JsonDict | None:
    review = review_allowed_package(packet, now_epoch=now_epoch)
    if not review["allowed"]:
        return None
    report = build_allowed_package_report(packet, review, source_url=source_url, now_epoch=now_epoch)
    _write_shooter_handshake(report, path)
    return report


def run_reporter(
    *,
    base_url: str,
    session_id: str,
    poll_sec: float,
    timeout_sec: float,
    heartbeat_sec: float,
    once: bool,
    handshake_path: Path,
    runtime_guard: PhoenixRuntimeSingletonGuardV3 | None = None,
    runtime_owner_token: str = "",
) -> int:
    url = _execution_url(base_url, session_id)
    LOGGER.info("Shooter execution is retired; reporting allowed packages only.")
    LOGGER.info("Polling %s", url)
    last_packet_id = ""
    last_waiting_write_epoch = 0.0
    last_waiting_reason = "WAITING_FOR_EXECUTION_PACKET"
    last_report_was_waiting = True

    def publish_waiting(reason: str, *, force: bool = False) -> None:
        nonlocal last_waiting_reason, last_waiting_write_epoch, last_report_was_waiting
        now_value = time.time()
        if not force and now_value - last_waiting_write_epoch < 1.0:
            return
        last_waiting_reason = reason
        publish_waiting_report(
            session_id=session_id,
            base_url=base_url,
            source_url=url,
            reason=reason,
            path=handshake_path,
            now_epoch=now_value,
        )
        last_waiting_write_epoch = now_value
        last_report_was_waiting = True

    publish_waiting("WAITING_FOR_EXECUTION_PACKET", force=True)
    while True:
        if runtime_guard is not None and runtime_owner_token:
            heartbeat = runtime_guard.heartbeat(
                owner_token=runtime_owner_token,
                updates={"session_id": session_id, "base_url": base_url, "shooter_pid": os.getpid()},
            )
            if not heartbeat.ok:
                LOGGER.debug("Runtime singleton heartbeat degraded: %s", heartbeat.reason)
        try:
            packet = _read_json_url(url, timeout_sec=timeout_sec)
            report = publish_allowed_package_report(packet, source_url=url, path=handshake_path)
            if report is not None:
                last_report_was_waiting = False
                packet_id = _text(report.get("packet_id"))
                if packet_id != last_packet_id:
                    LOGGER.info(
                        "Reported allowed %s package packet_id=%s side=%s lane=%s",
                        report.get("package_type"),
                        packet_id,
                        report.get("side"),
                        report.get("selected_lane"),
                    )
                    last_packet_id = packet_id
            else:
                publish_waiting("NO_ALLOWED_PACKAGE")
                if once:
                    LOGGER.info("No allowed package available; waiting handshake was updated.")
        except urllib.error.HTTPError as exc:
            publish_waiting(f"HTTP_{exc.code}_NO_EXECUTION_PACKET")
            if once:
                LOGGER.info("No executable package endpoint available: HTTP %s", exc.code)
            else:
                LOGGER.debug("Package endpoint unavailable: HTTP %s", exc.code)
        except Exception as exc:
            publish_waiting(f"WAITING_FOR_EXECUTION_PACKET: {exc}")
            if once:
                LOGGER.warning("Package reporter failed: %s", exc)
            else:
                LOGGER.debug("Package reporter waiting: %s", exc)
        if once:
            return 0
        sleep_deadline = time.time() + max(0.05, poll_sec)
        heartbeat_interval = max(1.0, min(float(heartbeat_sec), REPORT_TTL_SECONDS / 2.0))
        while time.time() < sleep_deadline:
            if last_report_was_waiting:
                publish_waiting(last_waiting_reason)
            remaining = max(0.05, sleep_deadline - time.time())
            time.sleep(min(heartbeat_interval, remaining))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=r".\.venv\Scripts\python.exe Backend\launch\shooter.py",
        description="Report allowed PhoenixGuard intraday/swing packages; broker execution is retired.",
    )
    parser.add_argument("command", nargs="?", default="signal")
    parser.add_argument("legacy_args", nargs="*")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session-id", default=DEFAULT_SESSION_ID)
    parser.add_argument("--poll", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--heartbeat", type=float, default=DEFAULT_HEARTBEAT_SECONDS)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--handshake-path", default=str(_SHOOTER_HANDSHAKE_PATH))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args, _unknown = parser.parse_known_args(argv)
    command = _text(args.command).lower()
    if command in {"list-windows", "preview"}:
        LOGGER.info("%s is retired because shooter no longer inspects broker windows.", command)
        return 0
    if command == "manual":
        LOGGER.error("Manual broker execution is retired; shooter only reports allowed packages.")
        return 2
    guard = guard_from_environment(PROJECT_ROOT)
    registration = guard.register_component(
        "shooter",
        pid=os.getpid(),
        session_id=str(args.session_id),
        base_url=str(args.base_url),
        owner_token=str(os.getenv("PHOENIXGUARD_RUNTIME_LOCK_TOKEN", "") or "").strip() or None,
    )
    if not registration.ok:
        LOGGER.error("Shooter reporter singleton registration refused: %s", registration.reason)
        return 19
    release_on_exit = registration.reason == "stack_lock_acquired"
    try:
        return run_reporter(
            base_url=str(args.base_url),
            session_id=str(args.session_id),
            poll_sec=float(args.poll),
            timeout_sec=float(args.timeout),
            heartbeat_sec=float(args.heartbeat),
            once=bool(args.once or command == "once"),
            handshake_path=Path(str(args.handshake_path)),
            runtime_guard=guard,
            runtime_owner_token=registration.owner_token,
        )
    finally:
        if release_on_exit:
            guard.release(owner_token=registration.owner_token)


if __name__ == "__main__":
    raise SystemExit(main())
