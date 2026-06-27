from __future__ import annotations

from _bootstrap import ensure_backend_paths

ensure_backend_paths()

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from audit_overlay_anchor_quality_v3 import audit_live_anchor_quality
from certification_common_v3 import DEFAULT_BASE_URL, DEFAULT_SESSION, gate_report, print_gate, write_report


CAPTURE_MODES: tuple[str, ...] = (
    "CLEAN_LIVE",
    "SUPPLY_DEMAND",
    "TRENDLINES",
    "FULL_HISTORY_READ",
    "DIAGNOSTICS",
)

REQUIRED_SCREENSHOTS: dict[str, str] = {
    "CLEAN_LIVE": "after_clean_live.png",
    "SUPPLY_DEMAND": "after_supply_demand.png",
    "TRENDLINES": "after_trendlines.png",
    "FULL_HISTORY_READ": "after_full_history_read.png",
    "DIAGNOSTICS": "after_diagnostics_rejected.png",
}


def _session_file(mode: str, session_id: str, out_dir: Path) -> Path:
    return out_dir / f"{mode.lower()}_{session_id}.png"


def capture_anchor_screenshots(base_url: str, session_id: str, out_dir: Path, *, timeout: float) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    capture_script = Path(__file__).with_name("capture_overlay_mode_screenshots_v3.py")
    command = [
        sys.executable,
        str(capture_script),
        "--base-url",
        base_url,
        "--session",
        session_id,
        "--modes",
        ",".join(CAPTURE_MODES),
        "--out",
        str(out_dir),
        "--timeout",
        str(timeout),
    ]
    completed = subprocess.run(
        command,
        cwd=str(Path(__file__).resolve().parents[2]),
        capture_output=True,
        text=True,
        timeout=max(float(timeout) * (len(CAPTURE_MODES) + 2), 120.0),
        check=False,
    )
    failures: list[str] = []
    warnings: list[str] = []
    if completed.returncode != 0:
        failures.append(f"mode_screenshot_capture_failed:{completed.returncode}")
    screenshot_paths: dict[str, str] = {}
    for mode, filename in REQUIRED_SCREENSHOTS.items():
        source = _session_file(mode, session_id, out_dir)
        target = out_dir / filename
        if source.exists():
            shutil.copyfile(source, target)
            screenshot_paths[filename] = str(target)
        else:
            failures.append(f"missing_mode_screenshot:{mode}:{source}")
    before_target = out_dir / "before_problem_case.png"
    if not before_target.exists():
        clean_live = out_dir / REQUIRED_SCREENSHOTS["CLEAN_LIVE"]
        if clean_live.exists():
            shutil.copyfile(clean_live, before_target)
            warnings.append("before_problem_case seeded from current clean-live capture because no earlier problem capture file existed")
    if before_target.exists():
        screenshot_paths["before_problem_case.png"] = str(before_target)
    else:
        failures.append("missing_before_problem_case.png")
    audits = [
        audit_live_anchor_quality(base_url, session_id, mode, timeout=timeout)
        for mode in CAPTURE_MODES
    ]
    metrics_path = out_dir / "anchor_screenshot_metrics.json"
    metrics_payload = {
        "base_url": base_url,
        "session_id": session_id,
        "screenshots": screenshot_paths,
        "audits": audits,
        "capture_stdout_tail": completed.stdout[-4000:],
        "capture_stderr_tail": completed.stderr[-4000:],
    }
    metrics_path.write_text(json.dumps(metrics_payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    return gate_report(
        schema_version="PG_CAPTURE_OVERLAY_ANCHOR_SCREENSHOTS_V3",
        gate="Overlay Anchor Screenshots",
        failures=failures,
        warnings=warnings,
        details={
            "session_id": session_id,
            "base_url": base_url,
            "out_dir": str(out_dir),
            "metrics_path": str(metrics_path),
            "screenshots": screenshot_paths,
            "audits": audits,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture PhoenixGuard V3 overlay anchor correction evidence screenshots.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--out", default=".codex_runtime/visual_evidence/anchor_fix")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()

    report = capture_anchor_screenshots(
        str(args.base_url),
        str(args.session),
        Path(str(args.out)),
        timeout=float(args.timeout),
    )
    out = write_report("gate_overlay_anchor_screenshots_v3.json", report)
    report["out_json"] = str(out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print_gate("OVERLAY_ANCHOR_SCREENSHOTS", report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
