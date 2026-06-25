from __future__ import annotations

from _bootstrap import ensure_backend_paths

ensure_backend_paths()

import argparse
import json
from typing import Any, Mapping, cast
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from certification_common_v3 import (
    DEFAULT_BASE_URL,
    DEFAULT_SESSION,
    gate_report,
    http_json,
    print_gate,
    quote_session,
    write_report,
)

from phoenixguard.mobile_api.live_state_v3 import build_live_state_v3
from phoenixguard.vision.broker_source_lock_v3 import build_broker_source_lock_v3


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _synthetic_wrong_surface_session() -> dict[str, Any]:
    return {
        "session_id": "wrong-surface-cert",
        "frame_index": 9001,
        "state_version": 1,
        "locked_window": {
            "hwnd": 808,
            "title": "The Most Innovative Trading Platform - Pocket Option",
        },
        "broker_source": {
            "lock_id": "synthetic-wrong-surface",
            "valid": False,
            "status": "TITLE_MATCH_PIXEL_MISMATCH",
            "wrong_surface": True,
            "url_valid": True,
            "title_valid": True,
            "pixel_fingerprint_valid": False,
        },
        "tracking_summary": {
            "support_resistance_zones": [
                {
                    "type": "SUPPORT_ZONE",
                    "side": "BUY",
                    "pixel_bbox": [80, 180, 240, 230],
                    "confidence": 0.86,
                }
            ],
            "timing_signal": {
                "type": "SNIPER_ENTRY_BOX",
                "side": "BUY",
                "pixel_bbox": [250, 170, 290, 230],
                "confidence": 0.91,
            },
        },
        "latest_signal": {"side": "BUY", "confidence": 0.9},
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify PhoenixGuard V3 rejects market overlays on wrong surfaces.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    failures: list[str] = []
    warnings: list[str] = []
    base = args.base_url.rstrip("/")
    live = http_json(f"{base}/v1/mobile/live/state/v3/{quote_session(args.session)}?mode=CLEAN_LIVE", timeout=args.timeout)
    live_payload = _mapping(live.payload)
    broker_source = _mapping(live_payload.get("broker_source"))
    if not live.ok:
        warnings.append(f"live endpoint unavailable during wrong-surface certification: {live.error or live.status}")
    elif not broker_source:
        warnings.append("live endpoint did not publish broker_source while synthetic rejection passed")

    synthetic_session = _synthetic_wrong_surface_session()
    state = build_live_state_v3(synthetic_session, artifacts={}, now_epoch=1_000.0, overlay_mode="CLEAN_LIVE")
    if state.get("renderable_count") != 0:
        failures.append(f"wrong surface rendered overlays: renderable_count={state.get('renderable_count')}")
    if state.get("overlay_objects"):
        failures.append(f"wrong surface exposed overlay_objects: {state.get('overlay_objects')}")
    if state.get("reason_if_empty") != "broker source rejected: wrong surface":
        failures.append(f"unexpected empty reason: {state.get('reason_if_empty')}")
    if _mapping(state.get("overlay_precision_audit")).get("source_block_reason") != "broker source rejected: wrong surface":
        failures.append("precision audit did not carry source_block_reason")

    lock = build_broker_source_lock_v3(
        {
            "title": "The Most Innovative Trading Platform - Pocket Option",
            "process_name": "Code.exe",
            "window_image_width": 1200,
            "window_image_height": 800,
        },
        image=None,
    ).as_dict()
    if bool(lock.get("valid")):
        failures.append(f"broker-source lock accepted synthetic wrong process: {lock}")
    if str(lock.get("status") or "").upper() not in {
        "TITLE_MATCH_PIXEL_MISMATCH",
        "WRONG_SURFACE",
        "BROKER_NOT_FOUND",
        "INVALID_BROWSER",
    }:
        failures.append(f"unexpected synthetic wrong-process status: {lock.get('status')}")

    report = gate_report(
        schema_version="PG_CERTIFY_WRONG_SURFACE_REJECTION_V3",
        gate="Wrong Surface Rejection",
        failures=failures,
        warnings=warnings,
        details={
            "session_id": args.session,
            "live": live.as_dict(),
            "live_broker_source": broker_source,
            "synthetic_state": {
                "renderable_count": state.get("renderable_count"),
                "overlay_count": state.get("overlay_count"),
                "reason_if_empty": state.get("reason_if_empty"),
                "broker_source": state.get("broker_source"),
                "overlay_precision_audit": state.get("overlay_precision_audit"),
            },
            "synthetic_lock": lock,
        },
    )
    out = write_report("gate12_wrong_surface_rejection_v3.json", report)
    report["out_json"] = str(out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print("WRONG_SURFACE_REJECTION: " + report["verdict"])
    print_gate("WRONG_SURFACE_REJECTION", report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
