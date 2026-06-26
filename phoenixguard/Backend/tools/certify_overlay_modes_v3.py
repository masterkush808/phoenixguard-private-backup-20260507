from __future__ import annotations

from _bootstrap import ensure_backend_paths

ensure_backend_paths()

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence, cast

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

from phoenixguard.vision.v3_overlay_contract import (
    DIAGNOSTIC_OVERLAY_TYPES,
    DIAGNOSTIC_VIEW_MODES,
    VIEW_MODES,
    is_approved_overlay_display_label,
    normalize_view_mode,
)
from phoenixguard.runtime.realtime_performance_v3 import OVERLAY_RENDER_BUDGETS


CORE_MODES: tuple[str, ...] = (
    "CLEAN_LIVE",
    "CHART_BOUNDS",
    "CANDLES",
    "GLOBAL",
    "LOCAL",
    "SUPPLY_DEMAND",
    "TRENDLINES",
    "TRIGGER",
    "TARGET",
    "PATH",
    "COUNCIL",
    "TWO_CANDLE_STUDY",
    "ACTIVE_CONTEXT",
    "FULL_HISTORY_READ",
    "REPLAY",
    "PREDICTION",
    "BROKER",
    "CALIBRATION",
    "DIAGNOSTICS",
    "DEBUG",
    "INSPECTOR",
)

COUNCIL_MARKER_TYPES: set[str] = {
    "MODEL_COUNCIL_MARKER",
    "REGIME_MARKER",
    "MARKET_PLAY_MARKER",
    "PRICE_LOCATION_MARKER",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(cast(Sequence[Any], value)) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def _bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "y", "on", "valid", "ok", "pass"}:
            return True
        if lowered in {"0", "false", "no", "n", "off", "invalid", "fail"}:
            return False
    return default


def _mode_list(raw: str, *, all_modes: bool) -> list[str]:
    if all_modes:
        return list(VIEW_MODES)
    if not raw.strip():
        return list(CORE_MODES)
    return [normalize_view_mode(part) for part in raw.replace(";", ",").split(",") if part.strip()]


def _validate_payload(requested_mode: str, payload: Mapping[str, Any]) -> list[str]:
    failures: list[str] = []
    expected_active = normalize_view_mode(requested_mode)
    overlays = _mapping(payload.get("overlays"))
    overlay_mode = _mapping(payload.get("overlay_mode"))
    overlay_objects = _sequence(payload.get("overlay_objects"))
    broker_source = _mapping(payload.get("broker_source"))
    visible_layers = _sequence(payload.get("visible_layers"))
    overlay_mode_layers = _sequence(overlay_mode.get("visible_layers"))
    active_mode = str(payload.get("active_mode") or "")
    renderable_count = int(payload.get("renderable_count") or 0)
    overlay_count = int(payload.get("overlay_count") or overlays.get("total_count") or 0)
    overlays_renderable = int(overlays.get("renderable_count") or overlays.get("count") or 0)
    unknown_terms = _sequence(payload.get("unknown_or_unmapped_terms") or overlays.get("unknown_or_unmapped_terms"))

    if active_mode != expected_active:
        failures.append(f"{requested_mode}: active_mode={active_mode}, expected {expected_active}")
    if str(overlay_mode.get("active") or "") != expected_active:
        failures.append(f"{requested_mode}: overlay_mode.active={overlay_mode.get('active')}, expected {expected_active}")
    if str(payload.get("requested_mode") or "") != requested_mode:
        failures.append(f"{requested_mode}: requested_mode={payload.get('requested_mode')}")
    if str(overlay_mode.get("requested") or "") != requested_mode:
        failures.append(f"{requested_mode}: overlay_mode.requested={overlay_mode.get('requested')}")
    if list(visible_layers) != list(overlay_mode_layers):
        failures.append(f"{requested_mode}: visible_layers mismatch between top-level and overlay_mode")
    if renderable_count != len(overlay_objects):
        failures.append(f"{requested_mode}: renderable_count={renderable_count}, overlay_objects={len(overlay_objects)}")
    if overlays_renderable != renderable_count:
        failures.append(f"{requested_mode}: overlays.renderable_count={overlays_renderable}, top-level={renderable_count}")
    if renderable_count == 0 and not str(payload.get("reason_if_empty") or overlay_mode.get("reason_if_empty") or "").strip():
        failures.append(f"{requested_mode}: empty mode has no reason_if_empty")
    if overlay_count < renderable_count:
        failures.append(f"{requested_mode}: overlay_count={overlay_count} < renderable_count={renderable_count}")
    if broker_source and not _bool(broker_source.get("valid"), True) and renderable_count > 0:
        failures.append(f"{requested_mode}: invalid broker_source rendered {renderable_count} overlays")
    typed_overlay_rows = [_mapping(row) for row in overlay_objects if isinstance(row, Mapping)]
    visible_label_rows = [
        row
        for row in typed_overlay_rows
        if row.get("label_hidden") is not True and str(row.get("label_hidden") or "").lower() != "true"
    ]
    visible_labels = [str(row.get("display_label") or row.get("label") or "").strip() for row in visible_label_rows]
    raw_visible_labels = [str(row.get("label") or "").strip() for row in visible_label_rows]
    unapproved_labels = [label for label in visible_labels if label and not is_approved_overlay_display_label(label)]
    unapproved_raw_labels = [label for label in raw_visible_labels if label and not is_approved_overlay_display_label(label)]
    if unapproved_labels:
        failures.append(f"{requested_mode}: visible unapproved labels={sorted(set(unapproved_labels))}")
    if unapproved_raw_labels:
        failures.append(f"{requested_mode}: visible raw labels are not dictionary labels={sorted(set(unapproved_raw_labels))}")
    if expected_active not in DIAGNOSTIC_VIEW_MODES:
        leaked_diag = [
            str(row.get("type") or "")
            for row in typed_overlay_rows
            if str(row.get("type") or "") in DIAGNOSTIC_OVERLAY_TYPES
        ]
        if leaked_diag:
            failures.append(f"{requested_mode}: diagnostics overlay leaked into live mode={sorted(set(leaked_diag))}")
    if expected_active == "CLEAN_LIVE":
        now_count = sum(1 for label in visible_labels if label in {"NOW", "CURRENT"})
        if now_count > 1:
            failures.append(f"{requested_mode}: duplicate NOW/CURRENT labels visible={now_count}")
        clean_live_budget = int(OVERLAY_RENDER_BUDGETS.get("CLEAN_LIVE", 0) or 0)
        if clean_live_budget > 0 and renderable_count > clean_live_budget:
            failures.append(f"{requested_mode}: clean live overlay budget exceeded={renderable_count}")
    if expected_active == "COUNCIL":
        spam_types = [
            str(row.get("type") or "")
            for row in typed_overlay_rows
            if str(row.get("type") or "") not in COUNCIL_MARKER_TYPES
        ]
        if spam_types:
            failures.append(f"{requested_mode}: council mode rendered box spam={sorted(set(spam_types))}")
    if expected_active in DIAGNOSTIC_VIEW_MODES and unknown_terms and renderable_count == 0:
        failures.append(f"{requested_mode}: diagnostics has unmapped terms but no diagnostic overlays")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Certify backend overlay-mode wiring for PhoenixGuard V3.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--modes", default="", help="Comma-separated overlay modes. Defaults to core operator modes.")
    parser.add_argument("--all-modes", action="store_true")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    session_q = quote_session(args.session)
    failures: list[str] = []
    warnings: list[str] = []
    samples: list[dict[str, Any]] = []
    modes = _mode_list(args.modes, all_modes=args.all_modes)
    for mode in modes:
        response = http_json(f"{base}/v1/mobile/live/state/v3/{session_q}?mode={quote_session(mode)}", timeout=args.timeout)
        payload = _mapping(response.payload)
        sample: dict[str, Any] = {
            "mode": mode,
            "ok": response.ok,
            "status": response.status,
            "latency_ms": response.latency_ms,
            "active_mode": payload.get("active_mode"),
            "requested_mode": payload.get("requested_mode"),
            "visible_layers": payload.get("visible_layers"),
            "overlay_count": payload.get("overlay_count"),
            "renderable_count": payload.get("renderable_count"),
            "reason_if_empty": payload.get("reason_if_empty"),
            "unknown_or_unmapped_terms": payload.get("unknown_or_unmapped_terms"),
            "overlay_vocabulary": payload.get("overlay_vocabulary"),
            "broker_source": payload.get("broker_source"),
        }
        samples.append(sample)
        if not response.ok:
            failures.append(f"{mode}: live state endpoint failed: {response.error or response.status}")
            continue
        failures.extend(_validate_payload(mode, payload))

    report = gate_report(
        schema_version="PG_CERTIFY_OVERLAY_MODES_V3",
        gate="Overlay Mode Wiring",
        failures=failures,
        warnings=warnings,
        details={
            "session_id": args.session,
            "base_url": args.base_url,
            "modes": modes,
            "samples": samples,
        },
    )
    out = write_report("gate13_overlay_modes_v3.json", report)
    report["out_json"] = str(out)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print("OVERLAY_MODE_WIRING: " + report["verdict"])
    print_gate("OVERLAY_MODE_WIRING", report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
