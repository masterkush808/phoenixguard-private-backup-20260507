from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping, cast

from certification_common_v3 import (
    DEFAULT_BASE_URL,
    DEFAULT_SESSION,
    gate_report,
    print_gate,
    write_report,
)


ROOT = Path(__file__).resolve().parents[2]


def _python_executable() -> str:
    env_exe = os.getenv("PHOENIXGUARD_PYTHON_EXE", "").strip()
    if env_exe and Path(env_exe).exists():
        return env_exe
    repo_python = ROOT / ".venv" / "Scripts" / "python.exe"
    if repo_python.exists():
        return str(repo_python)
    return sys.executable


def _as_int(value: object, default: int = 0) -> int:
    if value is None:
        return default
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _run_tool(args: list[str], timeout: float) -> dict[str, object]:
    try:
        completed = subprocess.run(
            [_python_executable(), *args],
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
    except subprocess.TimeoutExpired as exc:
        return {
            "args": args,
            "returncode": 124,
            "stdout": str(exc.stdout or "")[-4000:],
            "stderr": f"timed out after {timeout:.1f}s",
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
            "Backend/tools/audit_overlay_precision_v3.py",
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
    evidence_args: list[str] = [
        "Backend/tools/capture_v3_visual_evidence.py",
        "--base-url",
        args.base_url,
        "--session",
        args.session,
        "--timeout",
        str(args.timeout),
    ]
    if args.skip_playwright:
        evidence_args.append("--skip-playwright")
    evidence = _run_tool(evidence_args, timeout=max(args.timeout + 90.0, args.timeout * 2.0))
    if _as_int(audit.get("returncode"), 0) != 0:
        failures.append(f"overlay precision audit failed: {audit.get('stderr') or audit.get('stdout')}")
    if _as_int(evidence.get("returncode"), 0) != 0:
        failures.append(f"visual evidence capture failed: {evidence.get('stderr') or evidence.get('stdout')}")
    audit_payload: dict[str, object] = {}
    try:
        raw_audit_payload: object = json.loads(audit_out.read_text(encoding="utf-8"))
        audit_payload = dict(cast(Mapping[str, object], raw_audit_payload)) if isinstance(raw_audit_payload, Mapping) else {}
    except Exception as exc:
        failures.append(f"unable to read overlay audit output: {exc}")
    raw_precision = audit_payload.get("precision_report")
    precision: dict[str, object] = dict(cast(Mapping[str, object], raw_precision)) if isinstance(raw_precision, Mapping) else {}
    for key in ("outside_plot_area", "missing_transform", "stale_frame_id", "unanchored_boxes", "label_collisions", "nesting_collisions"):
        count = _as_int(precision.get(key), 0)
        if count != 0:
            failures.append(f"{key}={count}")

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
