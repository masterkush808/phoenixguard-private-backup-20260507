from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from certification_common_v3 import (
    DEFAULT_BASE_URL,
    DEFAULT_SESSION,
    gate_report,
    print_gate,
    write_report,
)


ROOT = Path(__file__).resolve().parents[1]


def _run_tool(args: list[str], timeout: float) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, *args],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    return {
        "args": args,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify PhoenixGuard V3 overlay visual truth.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--skip-playwright", action="store_true")
    args = parser.parse_args()

    failures: list[str] = []
    warnings: list[str] = []
    audit_out = ROOT / "reports" / "certification" / "gate9_overlay_precision_audit_v3.json"
    audit = _run_tool(
        [
            "tools/audit_overlay_precision_v3.py",
            "--base-url",
            args.base_url,
            "--session",
            args.session,
            "--timeout",
            str(args.timeout),
            "--out",
            str(audit_out),
        ],
        timeout=args.timeout + 10.0,
    )
    evidence_args = [
        "tools/capture_v3_visual_evidence.py",
        "--base-url",
        args.base_url,
        "--session",
        args.session,
        "--timeout",
        str(args.timeout),
    ]
    if args.skip_playwright:
        evidence_args.append("--skip-playwright")
    evidence = _run_tool(evidence_args, timeout=args.timeout + 15.0)
    if int(audit.get("returncode") or 0) != 0:
        failures.append(f"overlay precision audit failed: {audit.get('stderr') or audit.get('stdout')}")
    if int(evidence.get("returncode") or 0) != 0:
        failures.append(f"visual evidence capture failed: {evidence.get('stderr') or evidence.get('stdout')}")
    audit_payload: dict[str, object] = {}
    try:
        audit_payload = json.loads(audit_out.read_text(encoding="utf-8"))
    except Exception as exc:
        failures.append(f"unable to read overlay audit output: {exc}")
    precision = audit_payload.get("precision_report") if isinstance(audit_payload.get("precision_report"), dict) else {}
    for key in ("outside_plot_area", "missing_transform", "stale_frame_id", "unanchored_boxes", "label_collisions", "nesting_collisions"):
        if int(precision.get(key) or 0) != 0:
            failures.append(f"{key}={precision.get(key)}")

    report = gate_report(
        schema_version="PG_CERTIFY_OVERLAY_VISUAL_TRUTH_V3",
        gate="Overlay Visual Truth",
        failures=failures,
        warnings=warnings,
        details={
            "session_id": args.session,
            "audit": audit,
            "evidence": evidence,
            "precision_report": precision,
            "audit_payload": audit_payload,
        },
    )
    out = write_report("gate9_overlay_visual_truth_v3.json", report)
    report["out_json"] = str(out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print("OVERLAY_VISUAL_TRUTH: " + report["verdict"])
    print_gate("OVERLAY_VISUAL_TRUTH", report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
