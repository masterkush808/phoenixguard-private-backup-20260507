from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import time
from typing import Mapping, cast
import urllib.error
import urllib.parse
import urllib.request


def _read_json(url: str, timeout_sec: float) -> tuple[bool, int, dict[str, object], str, int]:
    started = time.perf_counter()
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "PhoenixGuardCloudWatchdog/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, timeout_sec)) as response:  # noqa: S310 - operator-owned endpoint
            raw = response.read().decode("utf-8", errors="replace")
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            parsed: object = json.loads(raw) if raw.strip() else {}
            payload = dict(cast(Mapping[str, object], parsed)) if isinstance(parsed, Mapping) else {"value": parsed}
            return 200 <= int(response.status) < 300, int(response.status), payload, "", elapsed_ms
    except urllib.error.HTTPError as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return False, int(exc.code), {}, exc.read().decode("utf-8", errors="replace")[:500], elapsed_ms
    except Exception as exc:
        elapsed_ms = int((time.perf_counter() - started) * 1000)
        return False, 0, {}, f"{type(exc).__name__}: {exc}", elapsed_ms


def _write_log(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    row = dict(payload)
    row.setdefault("epoch_ms", int(time.time() * 1000))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _restart_service(service_name: str, restart_command: str, timeout_sec: int) -> tuple[bool, str]:
    if restart_command.strip():
        command = shlex.split(restart_command)
    elif service_name.strip():
        command = ["systemctl", "restart", service_name.strip()]
    else:
        return False, "restart disabled: no service name or command configured"
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_sec, check=False)
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"
    output = "\n".join(part for part in (completed.stdout.strip(), completed.stderr.strip()) if part)
    return completed.returncode == 0, output[-1200:]


def _check_once(base_url: str, session_id: str, timeout_sec: float) -> dict[str, object]:
    base = base_url.rstrip("/")
    checks: list[dict[str, object]] = []

    health_ok, health_status, health_payload, health_error, health_ms = _read_json(f"{base}/v1/mobile/health", timeout_sec)
    checks.append(
        {
            "name": "api_health",
            "ok": health_ok and health_payload.get("status") == "ok",
            "status": health_status,
            "latency_ms": health_ms,
            "error": health_error,
        }
    )

    ready_ok, ready_status, ready_payload, ready_error, ready_ms = _read_json(f"{base}/v1/mobile/frame-ingest/readiness", timeout_sec)
    checks.append(
        {
            "name": "frame_ingest_readiness",
            "ok": ready_ok and ready_payload.get("armed") is True,
            "status": ready_status,
            "latency_ms": ready_ms,
            "error": ready_error,
            "active_feed_count": ready_payload.get("active_feed_count", 0),
        }
    )

    if session_id.strip():
        quoted = urllib.parse.quote(session_id.strip(), safe="")
        live_ok, live_status, live_payload, live_error, live_ms = _read_json(
            f"{base}/v1/mobile/live/state/v3/{quoted}?compact=1&monitor=1",
            timeout_sec,
        )
        checks.append(
            {
                "name": "compact_live_state",
                "ok": live_ok,
                "status": live_status,
                "latency_ms": live_ms,
                "error": live_error,
                "capture_count": live_payload.get("capture_count", live_payload.get("frame_index", 0)),
                "state_version": live_payload.get("state_version", 0),
                "renderable_count": live_payload.get("renderable_count", 0),
            }
        )

    ok = all(bool(row.get("ok")) for row in checks)
    return {
        "schema_version": "PG_CLOUD_WATCHDOG_SAMPLE_V1",
        "ok": ok,
        "base_url": base,
        "session_id": session_id,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="PhoenixGuard deployed cloud-brain watchdog.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8793")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--log-path", default="runtime/live/logs_live/cloud_watchdog.jsonl")
    parser.add_argument("--interval-sec", type=float, default=30.0)
    parser.add_argument("--timeout-sec", type=float, default=10.0)
    parser.add_argument("--failure-threshold", type=int, default=3)
    parser.add_argument("--service-name", default="phoenixguard-cloud-brain.service")
    parser.add_argument("--restart-command", default="")
    parser.add_argument("--restart-timeout-sec", type=int, default=90)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--no-restart", action="store_true")
    args = parser.parse_args()

    log_path = Path(args.log_path)
    consecutive_failures = 0
    while True:
        sample = _check_once(str(args.base_url), str(args.session_id), float(args.timeout_sec))
        if bool(sample["ok"]):
            consecutive_failures = 0
            sample["watchdog_action"] = "none"
        else:
            consecutive_failures += 1
            sample["consecutive_failures"] = consecutive_failures
            if consecutive_failures >= max(1, int(args.failure_threshold)) and not bool(args.no_restart):
                restarted, detail = _restart_service(str(args.service_name), str(args.restart_command), int(args.restart_timeout_sec))
                sample["watchdog_action"] = "restart"
                sample["restart_ok"] = restarted
                sample["restart_detail"] = detail
                consecutive_failures = 0 if restarted else consecutive_failures
            else:
                sample["watchdog_action"] = "observe"
        _write_log(log_path, cast(Mapping[str, object], sample))
        print(json.dumps(sample, sort_keys=True, default=str))
        if bool(args.once):
            return 0 if bool(sample["ok"]) else 1
        time.sleep(max(5.0, float(args.interval_sec)))


if __name__ == "__main__":
    raise SystemExit(main())
