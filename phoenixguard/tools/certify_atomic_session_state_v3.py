from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, cast

from certification_common_v3 import (
    DEFAULT_BASE_URL,
    DEFAULT_SESSION,
    extract_frame_fields,
    gate_report,
    http_json,
    print_gate,
    quote_session,
    write_report,
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phoenixguard.runtime.realtime_performance_v3 import (  # noqa: E402
    SessionAtomicWriterV3,
    SessionFreshnessValidatorV3,
)


def _mapping(value: object) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify PhoenixGuard V3 atomic session state.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()

    failures: list[str] = []
    warnings: list[str] = []
    validator = SessionFreshnessValidatorV3()

    previous: dict[str, Any] = {
        "session_id": args.session,
        "frame_index": 10,
        "display_frame_id": 10,
        "display_capture_epoch": 100.0,
        "chart_frame_id": 10,
        "overlay_frame_id": 10,
        "model_vote_frame_id": 10,
        "model_capture_epoch": 100.0,
        "state_version": 10010,
        "source_capture_id": "capture:test:10",
        "updated_at": "old",
    }
    touch_only: dict[str, Any] = dict(previous, updated_at="new", state_version=10011)
    touch_result = _mapping(validator.validate(previous, touch_only, now_epoch=101.0))
    if touch_result.get("status") != "TOUCH_ONLY_STALE":
        failures.append(f"touch-only stale sample was not rejected: {touch_result}")

    prepared = _mapping(SessionAtomicWriterV3.prepare_payload(
        {
            "session_id": args.session,
            "capture_count": 11,
            "frame_index": 11,
            "last_capture_started_epoch": 102.0,
            "last_capture_epoch": 103.0,
            "updated_at": "fresh",
        },
        previous=previous,
    ))
    raw_prepared_result = prepared.get("session_freshness_v3")
    prepared_result = _mapping(raw_prepared_result)
    if prepared_result.get("missing_fields"):
        failures.append(f"atomic writer left missing frame fields: {prepared_result.get('missing_fields')}")
    if not prepared.get("source_capture_id"):
        failures.append("atomic writer did not create source_capture_id")

    partial = _mapping(validator.validate({}, {"session_id": args.session, "frame_index": 12, "state_version": 12}, now_epoch=104.0))
    if partial.get("status") != "PARTIAL_SESSION":
        failures.append(f"partial sample was not rejected: {partial}")

    mismatch = _mapping(validator.validate(
        previous,
        dict(previous, frame_index=11, display_frame_id=11, chart_frame_id=11, overlay_frame_id=11, model_vote_frame_id=11),
        now_epoch=101.0,
    ))
    if mismatch.get("status") != "FRAME_EPOCH_MISMATCH":
        failures.append(f"frame/epoch mismatch sample was not rejected: {mismatch}")

    base = args.base_url.rstrip("/")
    live = http_json(f"{base}/v1/mobile/live/state/v3/{quote_session(args.session)}", timeout=args.timeout)
    session = http_json(f"{base}/v1/mobile/window-tracker/sessions/{quote_session(args.session)}", timeout=args.timeout)
    live_fields = extract_frame_fields(_mapping(live.payload))
    session_payload = _mapping(session.payload)
    session_freshness = _mapping(session_payload.get("session_freshness_v3"))
    required = [
        "frame_id",
        "display_frame_id",
        "display_capture_epoch",
        "chart_frame_id",
        "overlay_frame_id",
        "model_vote_frame_id",
        "state_version",
        "source_capture_id",
    ]
    missing_live = [key for key in required if live_fields.get(key) in ("", 0, 0.0, None)]
    if not live.ok:
        failures.append(f"live state unavailable: {live.error or live.status}")
    elif missing_live:
        failures.append(f"live state missing required atomic fields: {missing_live}")
    elif live_fields["display_capture_epoch"] and (time.time() - live_fields["display_capture_epoch"]) * 1000.0 > 5000.0:
        failures.append("live state atomic fields exist but display_capture_epoch is stale")
    if not session.ok:
        warnings.append(f"session endpoint unavailable: {session.error or session.status}")

    report = gate_report(
        schema_version="PG_CERTIFY_ATOMIC_SESSION_STATE_V3",
        gate="Atomic Session State",
        failures=failures,
        warnings=warnings,
        details={
            "session_id": args.session,
            "sample_results": {
                "touch_without_capture": touch_result,
                "prepared_payload_freshness": prepared_result,
                "partial_session": partial,
                "frame_epoch_mismatch": mismatch,
            },
            "live_fields": live_fields,
            "session_freshness_v3": session_freshness,
        },
    )
    out = write_report("gate3_atomic_session_state_v3.json", report)
    report["out_json"] = str(out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print("ATOMIC_SESSION_STATE: " + report["verdict"])
    print_gate("ATOMIC_SESSION_STATE", report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
