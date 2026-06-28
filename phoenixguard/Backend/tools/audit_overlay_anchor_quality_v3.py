from __future__ import annotations

from _bootstrap import ensure_backend_paths

ensure_backend_paths()

import argparse
from collections.abc import Mapping, Sequence
from typing import Any, cast

from certification_common_v3 import DEFAULT_BASE_URL, DEFAULT_SESSION, gate_report, http_json, print_gate, quote_session, write_report

from phoenixguard.vision.v3_overlay_contract import DIAGNOSTIC_OVERLAY_TYPES, HARD_ANCHOR_REQUIRED_TYPES, normalize_v3_overlay_object


def _mapping(value: object) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _sequence_of_mappings(value: object) -> list[dict[str, Any]]:
    if isinstance(value, Mapping):
        mapping_value = cast(Mapping[str, object], value)
        for key in ("objects", "items", "rows", "overlays", "renderable"):
            nested = mapping_value.get(key)
            if isinstance(nested, Sequence) and not isinstance(nested, (str, bytes, bytearray)):
                nested_items = cast(Sequence[object], nested)
                return [dict(cast(Mapping[str, Any], item)) for item in nested_items if isinstance(item, Mapping)]
        return [dict(cast(Mapping[str, Any], mapping_value))]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = cast(Sequence[object], value)
        return [dict(cast(Mapping[str, Any], item)) for item in items if isinstance(item, Mapping)]
    return []


def _float(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float(default)
    if number != number:
        return float(default)
    return float(number)


def _int(value: object, default: int = 0) -> int:
    return int(_float(value, float(default)))


def _overlay_rows(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("overlay_objects", "renderable_overlays", "overlays", "objects"):
        rows.extend(_sequence_of_mappings(payload.get(key)))
    if not rows:
        overlay_summary = _mapping(payload.get("overlay_summary"))
        rows.extend(_sequence_of_mappings(overlay_summary.get("objects")))
    unique: dict[str, dict[str, Any]] = {}
    for index, raw in enumerate(rows):
        try:
            normalized = normalize_v3_overlay_object(raw, strict=False, fallback_index=index)
        except Exception:
            normalized = dict(raw)
        overlay_id = str(normalized.get("overlay_id") or normalized.get("id") or f"overlay_{index}")
        unique[overlay_id] = dict(normalized)
    return list(unique.values())


def _quality_score(row: Mapping[str, Any]) -> float:
    quality = _mapping(row.get("anchor_quality"))
    return _float(quality.get("score"), 0.0)


def _live_anchor_issue(row: Mapping[str, Any]) -> bool:
    overlay_type = str(row.get("type") or "")
    if overlay_type in DIAGNOSTIC_OVERLAY_TYPES:
        return False
    if overlay_type not in HARD_ANCHOR_REQUIRED_TYPES:
        return False
    evidence_status = str(row.get("anchor_evidence_status") or "").upper()
    if evidence_status != "VALID":
        return True
    return _quality_score(row) < 0.65


def build_anchor_quality_audit(payload: Mapping[str, Any], *, mode: str) -> dict[str, Any]:
    rows = _overlay_rows(payload)
    display_frame_id = _int(payload.get("frame_id", payload.get("display_frame_id", 0)))
    overlay_object_frame_id = _int(
        payload.get(
            "overlay_object_frame_id",
            payload.get("overlay_frame_id", payload.get("full_overlay_frame_id", 0)),
        )
    )
    frame_id = overlay_object_frame_id or display_frame_id
    symbol = str(payload.get("symbol") or "").strip()
    timeframe = str(payload.get("timeframe") or "").strip()
    live_rows = [row for row in rows if str(row.get("type") or "") not in DIAGNOSTIC_OVERLAY_TYPES]
    quality_scores = [_quality_score(row) for row in live_rows if _mapping(row.get("anchor_quality"))]
    unanchored = [row for row in live_rows if _live_anchor_issue(row)]
    wrong_frame = [
        row
        for row in live_rows
        if frame_id > 0 and _int(row.get("frame_id"), frame_id) not in (0, frame_id)
    ]
    wrong_pair = [
        row
        for row in live_rows
        if (symbol and str(row.get("symbol") or "").strip() not in {"", symbol})
        or (timeframe and str(row.get("timeframe") or "").strip() not in {"", timeframe})
    ]
    current_candle_markers = sum(1 for row in live_rows if str(row.get("type") or "") == "CURRENT_CANDLE")
    avg = sum(quality_scores) / len(quality_scores) if quality_scores else 0.0
    min_score = min(quality_scores) if quality_scores else 0.0
    report = {
        "mode": mode,
        "frame_id": frame_id,
        "display_frame_id": display_frame_id,
        "overlay_object_frame_id": overlay_object_frame_id,
        "state_version": payload.get("state_version", 0),
        "symbol": symbol,
        "timeframe": timeframe,
        "overlay_count": len(rows),
        "live_overlay_count": len(live_rows),
        "floating_boxes": len(unanchored),
        "unanchored_live_overlays": len(unanchored),
        "wrong_frame_overlays": len(wrong_frame),
        "wrong_pair_overlays": len(wrong_pair),
        "current_candle_markers": current_candle_markers,
        "anchor_quality_avg": round(float(avg), 4),
        "anchor_quality_min": round(float(min_score), 4),
        "low_quality_overlay_ids": [str(row.get("overlay_id") or row.get("id") or "") for row in unanchored],
    }
    return report


def audit_live_anchor_quality(base_url: str, session_id: str, mode: str, *, timeout: float) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/v1/mobile/live/state/v3/{quote_session(session_id)}?mode={mode}&compact=1"
    result = http_json(url, timeout=timeout)
    payload = _mapping(result.payload)
    audit = build_anchor_quality_audit(payload, mode=mode)
    audit["api_ok"] = bool(result.ok)
    audit["api_status"] = int(result.status)
    audit["api_latency_ms"] = round(float(result.latency_ms), 3)
    audit["api_error"] = result.error
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit PhoenixGuard V3 live overlay anchor quality.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--mode", default="CLEAN_LIVE")
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    mode = str(args.mode or "CLEAN_LIVE").strip().upper()
    audit = audit_live_anchor_quality(str(args.base_url), str(args.session), mode, timeout=float(args.timeout))
    failures: list[str] = []
    if not audit.get("api_ok"):
        failures.append(f"live_state_api_failed:{audit.get('api_status')}:{audit.get('api_error')}")
    if int(audit.get("live_overlay_count") or 0) <= 0:
        failures.append("no_live_overlays_returned")
    for key in ("floating_boxes", "unanchored_live_overlays", "wrong_frame_overlays", "wrong_pair_overlays"):
        if int(audit.get(key) or 0) > 0:
            failures.append(f"{key}:{audit.get(key)}")
    if mode == "CLEAN_LIVE" and int(audit.get("current_candle_markers") or 0) != 1:
        failures.append(f"current_candle_markers:{audit.get('current_candle_markers')}")
    report = gate_report(
        schema_version="PG_AUDIT_OVERLAY_ANCHOR_QUALITY_V3",
        gate="Overlay Anchor Quality",
        failures=failures,
        details={"session_id": args.session, "base_url": args.base_url, "audit": audit},
    )
    out = write_report(f"gate_overlay_anchor_quality_v3_{mode.lower()}.json", report)
    report["out_json"] = str(out)
    out.write_text(__import__("json").dumps(report, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print_gate("OVERLAY_ANCHOR_QUALITY", report)
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
