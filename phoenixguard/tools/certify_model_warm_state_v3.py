from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from typing import cast

from certification_common_v3 import (
    DEFAULT_BASE_URL,
    DEFAULT_SESSION,
    gate_report,
    http_json,
    print_gate,
    quote_session,
    write_report,
)


def _mapping(value: object) -> dict[str, object]:
    return dict(cast(Mapping[str, object], value)) if isinstance(value, Mapping) else {}


def _row_list(value: object) -> list[dict[str, object]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return [dict(cast(Mapping[str, object], row)) for row in cast(Sequence[object], value) if isinstance(row, Mapping)]


def _float(value: object, default: float = 0.0) -> float:
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return float(value)
    except ValueError:
        return default


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify PhoenixGuard V3 model warm-state.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--expected-models", type=int, default=7)
    parser.add_argument("--max-age-ms", type=float, default=3000.0)
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    live = http_json(f"{base}/v1/mobile/live/state/v3/{quote_session(args.session)}", timeout=args.timeout)
    perf = http_json(f"{base}/v1/mobile/performance/trace/v3/{quote_session(args.session)}", timeout=args.timeout)
    failures: list[str] = []
    warnings: list[str] = []
    if not live.ok:
        failures.append(f"live state unavailable: {live.error or live.status}")
    if not perf.ok:
        failures.append(f"performance trace unavailable: {perf.error or perf.status}")
    live_payload = _mapping(live.payload)
    perf_payload = _mapping(perf.payload)
    states = _row_list(live_payload.get("model_warm_state_v3"))
    if not states:
        states = _row_list(perf_payload.get("model_warm_state_v3"))
    awake = 0
    stale_rows: list[dict[str, object]] = []
    queue_rows: list[dict[str, object]] = []
    for raw in states:
        status = str(raw.get("status") or "").upper()
        warm = bool(raw.get("warm")) or status in {"AWAKE", "BUSY", "IDLE_BUT_LOADED", "STAGGERED_FRESH"}
        if warm:
            awake += 1
        if _float(raw.get("last_inference_age_ms")) > float(args.max_age_ms) and status != "STAGGERED_FRESH":
            stale_rows.append(raw)
        if int(_float(raw.get("queue_depth"))) > 1:
            queue_rows.append(raw)
        for key in ("status", "last_inference_frame_id", "last_inference_age_ms", "p95_inference_ms", "queue_depth", "role_name", "device"):
            if key not in raw:
                failures.append(f"model {raw.get('model_name') or raw.get('role_name') or 'unknown'} missing {key}")
    if len(states or []) != int(args.expected_models):
        failures.append(f"models_total expected {args.expected_models}, got {len(states or [])}")
    if awake != int(args.expected_models):
        failures.append(f"models_awake expected {args.expected_models}, got {awake}")
    if stale_rows:
        failures.append(f"{len(stale_rows)} model(s) exceeded {args.max_age_ms:.0f}ms age")
    if queue_rows:
        failures.append(f"{len(queue_rows)} model(s) had queue_depth > 1")

    report = gate_report(
        schema_version="PG_CERTIFY_MODEL_WARM_STATE_V3",
        gate="Model Warm-State",
        failures=failures,
        warnings=warnings,
        details={
            "session_id": args.session,
            "models_awake": awake,
            "models_total": len(states or []),
            "expected_models": int(args.expected_models),
            "states": states,
            "stale_rows": stale_rows,
            "queue_rows": queue_rows,
        },
    )
    out = write_report("gate7_model_warm_state_v3.json", report)
    report["out_json"] = str(out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print("MODEL_WARM_STATE: " + report["verdict"])
    print_gate("MODEL_WARM_STATE", report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
