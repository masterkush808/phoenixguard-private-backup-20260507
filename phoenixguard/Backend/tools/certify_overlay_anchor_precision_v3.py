from __future__ import annotations

from _bootstrap import ensure_backend_paths

ensure_backend_paths()

import argparse
import json
from typing import Any

from audit_overlay_anchor_quality_v3 import audit_live_anchor_quality
from certification_common_v3 import DEFAULT_BASE_URL, DEFAULT_SESSION, gate_report, print_gate, write_report


DEFAULT_MODES: tuple[str, ...] = (
    "CLEAN_LIVE",
    "SUPPLY_DEMAND",
    "TRENDLINES",
    "FULL_HISTORY_READ",
    "REPLAY",
    "DIAGNOSTICS",
)


def _mode_list(raw: str) -> list[str]:
    if not raw.strip():
        return list(DEFAULT_MODES)
    return [item.strip().upper() for item in raw.replace(";", ",").split(",") if item.strip()]


def certify_anchor_precision(base_url: str, session_id: str, modes: list[str], *, timeout: float) -> dict[str, Any]:
    audits = [
        audit_live_anchor_quality(base_url, session_id, mode, timeout=timeout)
        for mode in modes
    ]
    failures: list[str] = []
    warnings: list[str] = []
    for audit in audits:
        mode = str(audit.get("mode") or "UNKNOWN")
        if not audit.get("api_ok"):
            failures.append(f"{mode}:api_failed:{audit.get('api_status')}:{audit.get('api_error')}")
            continue
        if int(audit.get("live_overlay_count") or 0) <= 0 and mode != "DIAGNOSTICS":
            failures.append(f"{mode}:no_live_overlays")
        for key in ("floating_boxes", "unanchored_live_overlays", "wrong_frame_overlays", "wrong_pair_overlays"):
            count = int(audit.get(key) or 0)
            if count > 0:
                failures.append(f"{mode}:{key}:{count}")
        if mode == "CLEAN_LIVE" and int(audit.get("current_candle_markers") or 0) != 1:
            failures.append(f"{mode}:current_candle_markers:{audit.get('current_candle_markers')}")
        min_quality = float(audit.get("anchor_quality_min") or 0.0)
        if min_quality and min_quality < 0.65:
            failures.append(f"{mode}:anchor_quality_min:{min_quality:.2f}")
        elif min_quality and min_quality < 0.85:
            warnings.append(f"{mode}:anchor_quality_soft:{min_quality:.2f}")
    return gate_report(
        schema_version="PG_CERTIFY_OVERLAY_ANCHOR_PRECISION_V3",
        gate="Overlay Anchor Precision",
        failures=failures,
        warnings=warnings,
        details={
            "session_id": session_id,
            "base_url": base_url,
            "modes": modes,
            "audits": audits,
        },
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify PhoenixGuard V3 overlay anchor precision across modes.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--modes", default="")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    report = certify_anchor_precision(
        str(args.base_url),
        str(args.session),
        _mode_list(str(args.modes)),
        timeout=float(args.timeout),
    )
    out = write_report("gate_overlay_anchor_precision_v3.json", report)
    report["out_json"] = str(out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print_gate("OVERLAY_ANCHOR_PRECISION", report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
