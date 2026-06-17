from __future__ import annotations

import argparse
import json
from typing import Any, Mapping

from certification_common_v3 import (
    DEFAULT_BASE_URL,
    DEFAULT_SESSION,
    gate_report,
    http_json,
    print_gate,
    quote_session,
    write_report,
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on", "valid", "ok", "pass", "locked"}:
            return True
        if lowered in {"0", "false", "no", "n", "off", "invalid", "fail", "wrong_surface"}:
            return False
    return default


def _first_mapping(*values: Any) -> dict[str, Any]:
    for value in values:
        candidate = _mapping(value)
        if candidate:
            return candidate
    return {}


def _source_valid(payload: Mapping[str, Any]) -> bool:
    status = _text(payload.get("status")).upper()
    return _bool(payload.get("valid"), status in {"VALID", "OK", "PASS", "LOCKED"})


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify PhoenixGuard V3 broker-source lock on the live state.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--allow-empty-lock-id", action="store_true")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    session_q = quote_session(args.session)
    live = http_json(f"{base}/v1/mobile/live/state/v3/{session_q}?mode=DIAGNOSTICS", timeout=args.timeout)
    session = http_json(f"{base}/v1/mobile/window-tracker/sessions/{session_q}", timeout=args.timeout)
    failures: list[str] = []
    warnings: list[str] = []
    live_payload = _mapping(live.payload)
    session_payload = _mapping(session.payload)
    broker_source = _mapping(live_payload.get("broker_source"))
    session_lock = _mapping(session_payload.get("broker_source_lock"))
    tracking = _mapping(session_payload.get("tracking_summary"))
    tracking_lock = _mapping(tracking.get("broker_source_lock"))
    latest_signal = _mapping(session_payload.get("latest_signal"))
    session_surface = _mapping(session_payload.get("broker_surface"))
    tracking_surface = _mapping(tracking.get("broker_surface"))
    live_surface = _mapping(live_payload.get("broker_surface"))
    explicit_source_lock = _first_mapping(
        session_lock,
        _mapping(session_payload.get("broker_source")),
        tracking_lock,
        _mapping(tracking.get("broker_source")),
        _mapping(latest_signal.get("broker_source_lock")),
        _mapping(latest_signal.get("broker_source")),
        _mapping(session_surface.get("broker_source_lock")),
        _mapping(session_surface.get("broker_source")),
        _mapping(tracking_surface.get("broker_source_lock")),
        _mapping(tracking_surface.get("broker_source")),
        _mapping(live_payload.get("broker_source_lock")),
        _mapping(live_surface.get("broker_source_lock")),
        _mapping(live_surface.get("broker_source")),
    )

    if not live.ok:
        failures.append(f"live state endpoint failed: {live.error or live.status}")
    if not session.ok:
        failures.append(f"session endpoint failed: {session.error or session.status}")
    if not explicit_source_lock:
        failures.append("session/live payload did not expose explicit broker_source_lock or raw broker_source evidence")
    elif not _source_valid(explicit_source_lock):
        failures.append(f"explicit broker source lock is not valid: {explicit_source_lock}")
    if not broker_source:
        failures.append("live state did not publish broker_source")
    else:
        status = _text(broker_source.get("status")).upper()
        lock_id = _text(broker_source.get("lock_id"))
        if not _bool(broker_source.get("valid"), False):
            failures.append(f"broker_source.valid is false: {broker_source}")
        if _bool(broker_source.get("wrong_surface"), False):
            failures.append(f"broker_source reports wrong_surface: {broker_source}")
        if status and status not in {"VALID", "OK", "PASS", "LOCKED"}:
            failures.append(f"broker_source status is not valid: {status}")
        if not lock_id and not args.allow_empty_lock_id:
            failures.append("broker_source.lock_id is empty")
        for key in ("url_valid", "title_valid", "pixel_fingerprint_valid"):
            if not _bool(broker_source.get(key), True):
                failures.append(f"broker_source.{key} is false")

    if not session_lock and not tracking_lock:
        warnings.append("raw broker_source_lock is not top-level; using nested explicit broker-source evidence")

    report = gate_report(
        schema_version="PG_CERTIFY_BROKER_SOURCE_LOCK_V3",
        gate="Broker Source Lock",
        failures=failures,
        warnings=warnings,
        details={
            "session_id": args.session,
            "base_url": args.base_url,
            "live": live.as_dict(),
            "session_endpoint": session.as_dict(),
            "broker_source": broker_source,
            "explicit_source_lock": explicit_source_lock,
            "session_broker_source_lock": session_lock,
            "tracking_broker_source_lock": tracking_lock,
            "session_broker_surface": session_surface,
            "tracking_broker_surface": tracking_surface,
        },
    )
    out = write_report("gate11_broker_source_lock_v3.json", report)
    report["out_json"] = str(out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print("BROKER_SOURCE_LOCK: " + report["verdict"])
    print_gate("BROKER_SOURCE_LOCK", report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
