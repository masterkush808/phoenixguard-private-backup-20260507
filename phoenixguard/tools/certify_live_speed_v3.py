from __future__ import annotations

import argparse
import json
import time

from certification_common_v3 import (
    DEFAULT_BASE_URL,
    DEFAULT_SESSION,
    gate_report,
    http_json,
    percentile,
    print_gate,
    quote_session,
    summarize_numbers,
    write_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify PhoenixGuard V3 live freshness and speed.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--duration-sec", type=float, default=600.0)
    parser.add_argument("--interval-sec", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    session_q = quote_session(args.session)
    deadline = time.time() + max(1.0, float(args.duration_sec))
    failures: list[str] = []
    warnings: list[str] = []
    samples: list[dict[str, object]] = []
    frame_ages: list[float] = []
    overlay_ages: list[float] = []
    model_ages: list[float] = []
    response_times: list[float] = []
    frontend_ages: list[float] = []
    queue_depths: list[float] = []
    stale_execution_packets = 0
    stale_overlays_visible = 0
    endpoint_failures = 0
    stale_frame_run = 0
    stale_overlay_run = 0
    frame_ids: set[int] = set()
    live_url = f"{base}/v1/mobile/live/state/v3/{session_q}?mode=CLEAN_LIVE&compact=1"

    warmup = http_json(live_url, timeout=args.timeout)
    if not warmup.ok:
        warnings.append(f"warmup_live_state_failed={warmup.error or warmup.status}")

    while time.time() < deadline:
        live = http_json(live_url, timeout=args.timeout)
        heartbeat = http_json(f"{base}/v1/mobile/frontend/heartbeat/v3?session_id={session_q}", timeout=min(args.timeout, 3.0))
        response_times.append(live.latency_ms)
        if not live.ok:
            endpoint_failures += 1
            samples.append({"live": live.as_dict(), "heartbeat": heartbeat.as_dict()})
            time.sleep(max(0.1, float(args.interval_sec)))
            continue
        live_payload = live.payload if isinstance(live.payload, dict) else {}
        perf_payload = live_payload.get("performance_trace_v3") if isinstance(live_payload.get("performance_trace_v3"), dict) else {}
        timing = live_payload.get("frame_timing_trace_v3") if isinstance(live_payload.get("frame_timing_trace_v3"), dict) else {}
        if not timing:
            timing = perf_payload.get("timing_trace") if isinstance(perf_payload.get("timing_trace"), dict) else {}
        model_state = live_payload.get("model_state") if isinstance(live_payload.get("model_state"), dict) else {}
        if not model_state:
            model_state = perf_payload.get("model_state") if isinstance(perf_payload.get("model_state"), dict) else {}
        frame_age = float(timing.get("frame_age_ms") or live_payload.get("frame_age_ms") or 0.0)
        overlay_age = float(timing.get("overlay_age_ms") or live_payload.get("overlay_age_ms") or 0.0)
        model_age = float(timing.get("model_vote_age_ms") or live_payload.get("model_vote_age_ms") or 0.0)
        frontend_age = float(timing.get("frontend_render_age_ms") or 0.0)
        queue_depth = float(model_state.get("queue_depth") or 0.0)
        frame_ages.append(frame_age)
        overlay_ages.append(overlay_age)
        model_ages.append(model_age)
        frontend_ages.append(frontend_age)
        queue_depths.append(queue_depth)
        display_frame_id = int(float(timing.get("display_frame_id") or live_payload.get("display_frame_id") or live_payload.get("frame_id") or 0))
        frame_ids.add(display_frame_id)
        execution_status = live_payload.get("execution_packet_status") if isinstance(live_payload.get("execution_packet_status"), dict) else {}
        if execution_status.get("exists") and execution_status.get("fresh") is False:
            stale_execution_packets += 1
        visual = live_payload.get("visual_health_v3") if isinstance(live_payload.get("visual_health_v3"), dict) else {}
        if not visual:
            visual = live_payload.get("visual_health") if isinstance(live_payload.get("visual_health"), dict) else {}
        if not visual:
            visual = perf_payload.get("visual_health") if isinstance(perf_payload.get("visual_health"), dict) else {}
        if visual and str(visual.get("status") or "").upper() not in {"PASS", "ALIVE", "OK"}:
            stale_overlays_visible += 1
        stale_frame_run = stale_frame_run + 1 if frame_age > 5000.0 else 0
        stale_overlay_run = stale_overlay_run + 1 if overlay_age > 5000.0 else 0
        if stale_frame_run > 3:
            failures.append("frame_age_ms exceeded 5000ms for more than 3 consecutive samples")
            break
        if stale_overlay_run > 3:
            failures.append("overlay_age_ms exceeded 5000ms for more than 3 consecutive samples")
            break
        model_health = model_state
        if model_health.get("models_total") == 0:
            failures.append("model health became 0/0")
            break
        samples.append(
            {
                "frame_id": live_payload.get("frame_id"),
                "display_frame_id": display_frame_id,
                "frame_age_ms": frame_age,
                "overlay_age_ms": overlay_age,
                "model_vote_age_ms": model_age,
                "frontend_render_age_ms": frontend_age,
                "queue_depth": queue_depth,
                "live_response_ms": live.latency_ms,
                "execution_packet_status": execution_status,
                "heartbeat_ok": heartbeat.ok,
            }
        )
        time.sleep(max(0.1, float(args.interval_sec)))

    p95_frame = percentile(frame_ages, 95)
    p95_overlay = percentile(overlay_ages, 95)
    p95_model = percentile(model_ages, 95)
    p95_response = percentile(response_times, 95)
    p95_frontend = percentile(frontend_ages, 95)
    p95_queue = percentile(queue_depths, 95)
    if len(frame_ids) <= 1:
        failures.append("capture loop did not publish multiple frame ids")
    if p95_frame >= 1200.0:
        failures.append(f"p95 frame_age_ms {p95_frame:.0f} >= 1200")
    if p95_overlay >= 1800.0:
        failures.append(f"p95 overlay_age_ms {p95_overlay:.0f} >= 1800")
    if p95_model >= 1800.0:
        failures.append(f"p95 model_vote_age_ms {p95_model:.0f} >= 1800")
    if p95_response >= 1000.0:
        failures.append(f"p95 live_state_response_ms {p95_response:.0f} >= 1000")
    if p95_frontend >= 2500.0:
        failures.append(f"p95 frontend_render_age_ms {p95_frontend:.0f} >= 2500")
    if p95_queue > 1.0:
        failures.append(f"queue_depth_p95 {p95_queue:.0f} > 1")
    if stale_execution_packets:
        failures.append(f"stale_execution_packets={stale_execution_packets}")
    if stale_overlays_visible:
        failures.append(f"stale_overlays_visible={stale_overlays_visible}")
    if endpoint_failures:
        failures.append(f"endpoint_failures={endpoint_failures}")

    report = gate_report(
        schema_version="PG_CERTIFY_LIVE_SPEED_V3",
        gate="Freshness and Speed",
        failures=failures,
        warnings=warnings,
        details={
            "session_id": args.session,
            "duration_sec": float(args.duration_sec),
            "sample_count": len(samples),
            "unique_frame_ids": len(frame_ids),
            "p95_frame_age_ms": p95_frame,
            "p95_overlay_age_ms": p95_overlay,
            "p95_model_vote_age_ms": p95_model,
            "p95_live_state_response_ms": p95_response,
            "p95_frontend_render_age_ms": p95_frontend,
            "queue_depth_p95": p95_queue,
            "stale_execution_packets": stale_execution_packets,
            "stale_overlays_visible": stale_overlays_visible,
            "endpoint_failures": endpoint_failures,
            "response_ms": summarize_numbers(response_times),
            "samples": samples[-300:],
        },
    )
    out = write_report("gate5_live_speed_v3.json", report)
    report["out_json"] = str(out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print("LIVE_SPEED: " + report["verdict"])
    print_gate("LIVE_SPEED", report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
