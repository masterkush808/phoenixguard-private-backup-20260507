from __future__ import annotations

import argparse
import json
import time
from typing import Mapping, cast

from certification_common_v3 import (
    DEFAULT_BASE_URL,
    DEFAULT_SESSION,
    extract_frame_fields,
    gate_report,
    http_json,
    percentile,
    print_gate,
    quote_session,
    summarize_numbers,
    write_report,
)


def _age_ms(epoch: float, *, now_epoch: float | None = None) -> float:
    observed_epoch = time.time() if now_epoch is None else now_epoch
    return max(0.0, (observed_epoch - float(epoch or 0.0)) * 1000.0) if epoch else 0.0


def _mapping(value: object) -> dict[str, object]:
    return dict(cast(Mapping[str, object], value)) if isinstance(value, Mapping) else {}


def _float_value(value: object, default: float = 0.0) -> float:
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_value(value: object, default: int = 0) -> int:
    return int(_float_value(value, float(default)))


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify PhoenixGuard V3 capture worker stability.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--count", type=int, default=20)
    parser.add_argument("--capture-timeout", type=float, default=3.0)
    parser.add_argument("--poll-timeout", type=float, default=3.0)
    parser.add_argument("--display-age-ms", type=float, default=1200.0)
    parser.add_argument("--allow-busy", action="store_true")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    session_q = quote_session(args.session)
    failures: list[str] = []
    warnings: list[str] = []
    samples: list[dict[str, object]] = []
    durations: list[float] = []
    advanced_count = 0
    connection_failures = 0
    previous_frame = 0
    live_url = f"{base}/v1/mobile/live/state/v3/{session_q}?mode=CLEAN_LIVE&compact=1"

    for index in range(max(1, int(args.count))):
        before = http_json(live_url, timeout=args.poll_timeout)
        before_fields = extract_frame_fields(_mapping(before.payload))
        response = http_json(
            f"{base}/v1/mobile/window-tracker/sessions/{session_q}/capture-once",
            method="POST",
            timeout=args.capture_timeout,
        )
        durations.append(response.latency_ms)
        if not response.ok:
            connection_failures += 1
        api_alive = http_json(f"{base}/v1/mobile/health", timeout=args.poll_timeout)
        after_live = http_json(live_url, timeout=args.poll_timeout)
        after_live_observed_epoch = time.time()
        perf = http_json(f"{base}/v1/mobile/performance/trace/v3/{session_q}", timeout=args.poll_timeout)
        payload = _mapping(response.payload)
        capture_result = _mapping(payload.get("capture_once_result"))
        after_fields = extract_frame_fields(_mapping(after_live.payload) or payload)
        result_after = _mapping(capture_result.get("after"))
        if result_after:
            result_display_frame = _int_value(result_after.get("display_frame_id"))
            result_display_epoch = _float_value(result_after.get("display_capture_epoch"))
            result_published_epoch = _float_value(result_after.get("display_published_epoch"))
            if result_display_frame > _int_value(after_fields.get("display_frame_id")):
                after_fields["display_frame_id"] = result_display_frame
            if result_display_epoch > _float_value(after_fields.get("display_capture_epoch")):
                after_fields["display_capture_epoch"] = result_display_epoch
            if result_published_epoch > _float_value(after_fields.get("display_published_epoch")):
                after_fields["display_published_epoch"] = result_published_epoch
            result_frame_index = _int_value(result_after.get("frame_index"))
            if result_frame_index > _int_value(after_fields.get("frame_id")):
                after_fields["frame_id"] = result_frame_index
        advanced = bool(
            capture_result.get("advanced")
            or after_fields["frame_id"] > before_fields["frame_id"]
            or after_fields["display_capture_epoch"] > before_fields["display_capture_epoch"] + 0.001
        )
        if advanced:
            advanced_count += 1
        display_epoch = max(
            float(after_fields.get("display_capture_epoch") or 0.0),
            float(after_fields.get("display_published_epoch") or 0.0),
        )
        display_age = _age_ms(display_epoch, now_epoch=after_live_observed_epoch)
        timing = _mapping(_mapping(perf.payload).get("timing_trace"))
        model_age = _float_value(timing.get("model_vote_age_ms"))
        sample: dict[str, object] = {
            "index": index + 1,
            "http_ok": response.ok,
            "status": response.status,
            "latency_ms": response.latency_ms,
            "capture_once_result": capture_result,
            "before_fields": before_fields,
            "after_fields": after_fields,
            "advanced": advanced,
            "api_alive": api_alive.ok,
            "display_age_ms": round(display_age, 3),
            "model_vote_age_ms": model_age,
            "perf_ok": perf.ok,
            "error": response.error,
        }
        samples.append(sample)
        if not response.ok:
            failures.append(f"capture-once #{index + 1} did not return HTTP 200: {response.error or response.status}")
        elif not capture_result:
            failures.append(f"capture-once #{index + 1} did not include capture_once_result")
        elif capture_result.get("status") == "busy" and args.allow_busy:
            warnings.append(f"capture-once #{index + 1} skipped because worker was busy")
        elif capture_result.get("ok") is not True:
            failures.append(f"capture-once #{index + 1} failed: {capture_result}")
        if not api_alive.ok:
            failures.append(f"API health failed after capture-once #{index + 1}: {api_alive.error or api_alive.status}")
        if display_age > float(args.display_age_ms):
            failures.append(
                f"display snapshot age {display_age:.0f}ms exceeded {args.display_age_ms:.0f}ms after capture #{index + 1}"
            )
        if previous_frame and after_fields["frame_id"] < previous_frame:
            failures.append(f"frame_id regressed from {previous_frame} to {after_fields['frame_id']}")
        previous_frame = int(after_fields["frame_id"] or previous_frame)

    p95_duration = percentile(durations, 95)
    if p95_duration > float(args.capture_timeout) * 1000.0:
        failures.append(f"capture-once p95 {p95_duration:.0f}ms exceeded timeout budget {args.capture_timeout * 1000.0:.0f}ms")
    if advanced_count < max(1, int(args.count)) and not args.allow_busy:
        failures.append(f"only {advanced_count}/{args.count} capture-once calls advanced the frame")

    report = gate_report(
        schema_version="PG_CERTIFY_CAPTURE_WORKER_V3",
        gate="Capture Worker",
        failures=failures,
        warnings=warnings,
        details={
            "session_id": args.session,
            "base_url": base,
            "capture_once_count": int(args.count),
            "advanced_count": advanced_count,
            "connection_failures": connection_failures,
            "duration_ms": summarize_numbers(durations),
            "p95_capture_once_ms": p95_duration,
            "samples": samples,
        },
    )
    out = write_report("gate2_capture_worker_v3.json", report)
    report["out_json"] = str(out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print("CAPTURE_WORKER: " + report["verdict"])
    print_gate("CAPTURE_WORKER", report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
