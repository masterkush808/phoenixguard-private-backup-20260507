from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Mapping, cast

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from capture_dashboard_visual_v3 import build_capture  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture PhoenixGuard V3 visual evidence artifacts.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8793")
    parser.add_argument("--session", default="pocket-live-8788")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--out-dir", default="reports/certification/visual_evidence")
    parser.add_argument("--width", type=int, default=1440)
    parser.add_argument("--height", type=int, default=900)
    parser.add_argument("--skip-playwright", action="store_true")
    args = parser.parse_args()

    report = build_capture(
        args.base_url,
        args.session,
        args.timeout,
        Path(args.out_dir),
        args.width,
        args.height,
        args.skip_playwright,
    )
    out = ROOT / "reports" / "certification" / "visual_evidence_v3.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    capture = report.get("capture")
    capture_map = dict(cast(Mapping[str, object], capture)) if isinstance(capture, Mapping) else {}
    print(json.dumps({"verdict": report.get("verdict"), "out": str(out), "screenshot": capture_map.get("path")}, indent=2))
    return 0 if report.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
