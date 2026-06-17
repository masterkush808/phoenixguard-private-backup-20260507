from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from certification_common_v3 import (
    DEFAULT_BASE_URL,
    DEFAULT_SESSION,
    gate_report,
    print_gate,
    write_report,
)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from capture_dashboard_visual_v3 import build_capture  # noqa: E402


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return int(default)
    try:
        return int(str(raw).strip())
    except Exception:
        return int(default)


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify PhoenixGuard V3 dashboard canonical hydration.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--skip-playwright", action="store_true")
    parser.add_argument(
        "--max-capture-sets",
        type=int,
        default=_env_int("PHOENIXGUARD_DASHBOARD_HYDRATION_MAX_CAPTURE_SETS", 3),
        help="Keep only the newest N dashboard hydration evidence bundles. Set 0 to disable pruning.",
    )
    args = parser.parse_args()

    failures: list[str] = []
    warnings: list[str] = []
    samples: list[dict[str, object]] = []
    retention_samples: list[dict[str, object]] = []
    out_dir = ROOT / "reports" / "certification" / "dashboard_hydration"
    if args.skip_playwright:
        failures.append("--skip-playwright is non-certifying for dashboard hydration")

    for index in range(max(1, int(args.count))):
        report = build_capture(
            args.base_url,
            args.session,
            args.timeout,
            out_dir,
            args.width,
            args.height,
            args.skip_playwright,
            max_capture_sets=args.max_capture_sets,
        )
        retention = report.get("evidence_retention") if isinstance(report.get("evidence_retention"), dict) else {}
        retention_samples.append(
            {
                "index": index + 1,
                "max_capture_sets": retention.get("max_capture_sets"),
                "removed_files": retention.get("removed_files"),
                "removed_mb": retention.get("removed_mb", 0.0),
                "errors": retention.get("errors", []),
            }
        )
        ready = ((report.get("capture") or {}).get("ready_state") or {}) if isinstance(report.get("capture"), dict) else {}
        live_payload = ((report.get("live_state") or {}).get("payload") or {}) if isinstance(report.get("live_state"), dict) else {}
        overlay_count = int(((live_payload.get("overlays") or {}).get("count") or len(live_payload.get("overlay_objects") or [])) if isinstance(live_payload, dict) else 0)
        sample = {
            "index": index + 1,
            "verdict": report.get("verdict"),
            "hard_mismatches": report.get("hard_mismatches"),
            "warnings": report.get("warnings"),
            "ready_state": ready,
            "backend_frame_id": live_payload.get("frame_id") if isinstance(live_payload, dict) else 0,
            "overlay_count": overlay_count,
            "screenshot": (report.get("capture") or {}).get("path") if isinstance(report.get("capture"), dict) else "",
        }
        samples.append(sample)
        if report.get("verdict") != "PASS":
            failures.append(f"dashboard load #{index + 1} failed: {report.get('hard_mismatches')}")
        if ready:
            if ready.get("live_state") is not True:
                failures.append(f"dashboard load #{index + 1} did not load canonical live/state/v3")
            if ready.get("legacy_state"):
                failures.append(f"dashboard load #{index + 1} used legacy fallback")
            if ready.get("visible_image") is not True:
                failures.append(f"dashboard load #{index + 1} did not show broker surface")
            if overlay_count > 0 and int(ready.get("hotspot_count") or 0) <= 0:
                failures.append(f"dashboard load #{index + 1} rendered no overlays even though backend has overlays")
        time.sleep(0.3)

    report = gate_report(
        schema_version="PG_CERTIFY_DASHBOARD_HYDRATION_V3",
        gate="Dashboard Hydration",
        failures=failures,
        warnings=warnings,
        details={
            "session_id": args.session,
            "base_url": args.base_url,
            "checks": int(args.count),
            "evidence_retention_policy": {
                "out_dir": str(out_dir),
                "max_capture_sets": int(args.max_capture_sets),
                "disabled": int(args.max_capture_sets) <= 0,
                "retention_samples": retention_samples,
            },
            "samples": samples,
        },
    )
    out = write_report("gate6_dashboard_hydration_v3.json", report)
    report["out_json"] = str(out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print("DASHBOARD_HYDRATION: " + report["verdict"])
    print_gate("DASHBOARD_HYDRATION", report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
