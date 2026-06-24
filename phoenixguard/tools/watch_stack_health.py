from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any, Mapping, cast
import urllib.error
import urllib.parse
import urllib.request


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _get_json(url: str, timeout: float) -> tuple[int, dict[str, Any], str, float]:
    started = time.perf_counter()
    req = urllib.request.Request(url=url, method="GET", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            payload: object = json.loads(raw) if raw.strip() else {}
            return int(resp.status), _mapping(payload) if isinstance(payload, Mapping) else {"raw": payload}, "", (time.perf_counter() - started) * 1000.0
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload: object = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            payload = {"detail": raw}
        return int(exc.code), _mapping(payload) if isinstance(payload, Mapping) else {"raw": payload}, "", (time.perf_counter() - started) * 1000.0
    except Exception as exc:
        return 0, {}, str(exc), (time.perf_counter() - started) * 1000.0


def _append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, allow_nan=False, separators=(",", ":"), sort_keys=True) + "\n")


def _nested(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    return _mapping(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor PhoenixGuard API/session stack health during burn runs.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8793")
    parser.add_argument("--session-id", default="pocket-live-8788")
    parser.add_argument("--out-dir", default="")
    parser.add_argument("--poll-sec", type=float, default=1.0)
    parser.add_argument("--timeout-sec", type=float, default=5.0)
    parser.add_argument("--duration-sec", type=float, default=0.0)
    parser.add_argument("--print-every", type=float, default=30.0)
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    session = urllib.parse.quote(str(args.session_id), safe="")
    urls = {
        "health": f"{base}/v1/mobile/health",
        "live": f"{base}/v1/mobile/live/state/v3/{session}?compact=1",
        "performance": f"{base}/v1/mobile/performance/trace/v3/{session}",
        "execution": f"{base}/v1/mobile/model-council/sessions/{session}/execution/latest",
    }
    out_dir = Path(args.out_dir) if args.out_dir else Path.cwd() / "reports" / "stack_health"
    log_path = out_dir / "stack_health.jsonl"
    start = time.time()
    last_print = 0.0
    samples = 0
    api_errors = 0
    stale_samples = 0

    print(f"PhoenixGuard stack health monitor base={base} session={args.session_id}", flush=True)
    print(f"PhoenixGuard stack health monitor log={log_path}", flush=True)

    while True:
        now = time.time()
        samples += 1
        health_status, health, health_error, health_ms = _get_json(urls["health"], args.timeout_sec)
        live_status, live, live_error, live_ms = _get_json(urls["live"], args.timeout_sec)
        perf_status, perf_payload, perf_error, perf_ms = _get_json(urls["performance"], args.timeout_sec)
        exec_status, execution, exec_error, exec_ms = _get_json(urls["execution"], args.timeout_sec)

        freshness = _nested(live, "freshness") or _nested(live, "runtime_freshness")
        perf = _nested(live, "performance")
        timing = _nested(perf_payload, "timing_trace")
        latest = _nested(live, "latest")
        frame_age_ms = (
            timing.get("frame_age_ms")
            or freshness.get("frame_age_ms")
            or perf.get("frame_age_ms")
            or latest.get("frame_age_ms")
            or live.get("frame_age_ms")
        )
        stale_status = str(timing.get("stale_status") or freshness.get("stale_status") or live.get("stale_status") or "")
        has_errors = health_status == 0 or live_status == 0 or perf_status == 0 or bool(health_error or live_error or perf_error)
        stale = stale_status.upper() in {"STALE", "FAIL", "FROZEN"} if stale_status else False
        if has_errors:
            api_errors += 1
        if stale:
            stale_samples += 1

        record: dict[str, Any] = {
            "at_epoch": now,
            "at_utc": _utc_now(),
            "sample": samples,
            "health": {"http_status": health_status, "ms": round(health_ms, 3), "error": health_error, "payload": health},
            "live": {
                "http_status": live_status,
                "ms": round(live_ms, 3),
                "error": live_error,
                "frame_age_ms": frame_age_ms,
                "stale_status": stale_status,
                "capture_count": live.get("capture_count") or latest.get("capture_count"),
                "state_version": live.get("state_version") or latest.get("state_version"),
                "has_study_packet": bool(live.get("study_packet") or live.get("latest_study_packet")),
            },
            "performance": {
                "http_status": perf_status,
                "ms": round(perf_ms, 3),
                "error": perf_error,
                "frame_id": perf_payload.get("frame_id"),
                "models_awake": _nested(perf_payload, "model_state").get("models_awake"),
                "models_total": _nested(perf_payload, "model_state").get("models_total"),
                "queue_depth": _nested(perf_payload, "model_state").get("queue_depth"),
                "packet_age_ms": timing.get("packet_age_ms"),
                "state_publish_age_ms": timing.get("state_publish_age_ms"),
                "stale_flags": timing.get("stale_flags") or [],
            },
            "execution": {
                "http_status": exec_status,
                "ms": round(exec_ms, 3),
                "error": exec_error,
                "schema_version": execution.get("schema_version"),
                "packet_id": execution.get("packet_id"),
                "symbol": execution.get("symbol"),
                "state": _nested(execution, "execution").get("state"),
                "side": _nested(execution, "execution").get("side"),
            },
            "summary": {"api_errors": api_errors, "stale_samples": stale_samples},
        }
        _append_jsonl(log_path, record)

        if now - last_print >= args.print_every:
            print(
                f"{time.strftime('%Y-%m-%d %H:%M:%S')} stack "
                f"health={health_status} live={live_status} perf={perf_status} exec={exec_status} "
                f"frame_age_ms={frame_age_ms} stale={stale_status or 'UNKNOWN'} "
                f"errors={api_errors}",
                flush=True,
            )
            last_print = now

        if args.duration_sec > 0 and now - start >= args.duration_sec:
            break
        time.sleep(max(0.2, args.poll_sec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
