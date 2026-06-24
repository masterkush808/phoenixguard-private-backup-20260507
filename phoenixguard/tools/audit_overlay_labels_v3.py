from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence, cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from certification_common_v3 import DEFAULT_BASE_URL, DEFAULT_SESSION, gate_report, http_json, print_gate, quote_session, write_report

from phoenixguard.vision.v3_overlay_contract import (
    DIAGNOSTIC_OVERLAY_TYPES,
    DIAGNOSTIC_VIEW_MODES,
    approved_overlay_display_labels,
    is_approved_overlay_display_label,
    normalize_view_mode,
)
from phoenixguard.runtime.realtime_performance_v3 import OVERLAY_RENDER_BUDGETS


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(cast(Sequence[Any], value)) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def _labels(rows: Sequence[Any]) -> list[str]:
    labels: list[str] = []
    for row in rows:
        payload = _mapping(row)
        if payload.get("label_hidden") is True or str(payload.get("label_hidden") or "").lower() == "true":
            continue
        label = str(payload.get("display_label") or payload.get("label") or "").strip()
        if label:
            labels.append(label)
    return labels


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit PhoenixGuard V3 overlay labels for the approved visual dictionary.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--mode", default="CLEAN_LIVE")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    mode = normalize_view_mode(args.mode)
    base = args.base_url.rstrip("/")
    session_q = quote_session(args.session)
    response = http_json(f"{base}/v1/mobile/live/state/v3/{session_q}?mode={quote_session(mode)}&compact=1", timeout=args.timeout)
    payload = _mapping(response.payload)
    overlays = _mapping(payload.get("overlays"))
    objects = _sequence(payload.get("overlay_objects") or overlays.get("objects"))
    labels = _labels(objects)
    unknown_terms = _sequence(payload.get("unknown_or_unmapped_terms") or overlays.get("unknown_or_unmapped_terms"))
    vocabulary = _mapping(payload.get("overlay_vocabulary") or overlays.get("vocabulary"))
    remapped = _sequence(vocabulary.get("labels_remapped"))
    unapproved = sorted({label for label in labels if not is_approved_overlay_display_label(label)})
    diag_leaks = sorted(
        {
            str(_mapping(row).get("type") or "")
            for row in objects
            if mode not in DIAGNOSTIC_VIEW_MODES and str(_mapping(row).get("type") or "") in DIAGNOSTIC_OVERLAY_TYPES
        }
    )
    now_count = sum(1 for label in labels if label in {"NOW", "CURRENT"})

    failures: list[str] = []
    warnings: list[str] = []
    if not response.ok:
        failures.append(f"live state endpoint failed: {response.error or response.status}")
    if unapproved:
        failures.append(f"visible unapproved labels: {unapproved}")
    if diag_leaks:
        failures.append(f"diagnostic overlay leaked into {mode}: {diag_leaks}")
    if mode == "CLEAN_LIVE" and now_count > 1:
        failures.append(f"duplicate NOW/CURRENT labels visible: {now_count}")
    clean_live_budget = int(OVERLAY_RENDER_BUDGETS.get("CLEAN_LIVE", 0) or 0)
    if mode == "CLEAN_LIVE" and clean_live_budget > 0 and len(objects) > clean_live_budget:
        failures.append(f"CLEAN_LIVE overlay budget exceeded: {len(objects)}")
    if mode == "COUNCIL":
        spam = sorted(
            {
                str(_mapping(row).get("type") or "")
                for row in objects
                if str(_mapping(row).get("type") or "")
                not in {"MODEL_COUNCIL_MARKER", "REGIME_MARKER", "MARKET_PLAY_MARKER", "PRICE_LOCATION_MARKER"}
            }
        )
        if spam:
            failures.append(f"COUNCIL rendered chart box spam: {spam}")
    if unknown_terms and mode not in DIAGNOSTIC_VIEW_MODES:
        warnings.append(f"{len(unknown_terms)} unknown/unmapped terms hidden from live rendering")

    report = gate_report(
        schema_version="PG_AUDIT_OVERLAY_LABELS_V3",
        gate="Overlay Label Vocabulary",
        failures=failures,
        warnings=warnings,
        details={
            "session_id": args.session,
            "base_url": args.base_url,
            "mode": mode,
            "endpoint_status": response.status,
            "approved_labels": list(approved_overlay_display_labels()),
            "visible_labels": labels,
            "visible_label_count": len(labels),
            "visible_unapproved_labels": unapproved,
            "duplicate_now_count": now_count,
            "unknown_or_unmapped_terms": unknown_terms,
            "labels_remapped": remapped,
            "renderable_count": payload.get("renderable_count"),
            "overlay_count": payload.get("overlay_count"),
        },
    )
    out = write_report(f"gate_overlay_label_audit_v3_{mode.lower()}.json", report)
    report["out_json"] = str(out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print("OVERLAY_LABEL_AUDIT: " + report["verdict"])
    print_gate("OVERLAY_LABEL_AUDIT", report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
