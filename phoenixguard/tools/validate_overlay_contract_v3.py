from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phoenixguard.vision.v3_overlay_contract import (
    COORDINATE_MODES,
    OVERLAY_TYPES,
    REQUIRED_FIELDS,
    VIEW_MODES,
    is_approved_overlay_display_label,
    validate_v3_overlay_object,
)


DEFAULT_BASE_URL = "http://127.0.0.1:8793"
DEFAULT_SESSION = "pocket-live-8788"

ALLOWED_TYPES = set(OVERLAY_TYPES)
ALLOWED_MODES = set(VIEW_MODES)
ALLOWED_COORDINATE_MODES = set(COORDINATE_MODES)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def _float_value(value: object) -> float:
    if not isinstance(value, (int, float, str)):
        raise TypeError(f"expected numeric value, got {type(value).__name__}")
    return float(value)


def _http_json(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "PhoenixGuard-V3-OverlayContract/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - local operator tool
            parsed = json.loads(response.read().decode("utf-8", errors="replace"))
            payload = _mapping(parsed) if isinstance(parsed, Mapping) else {"value": parsed}
            return {"ok": 200 <= int(response.status) < 300, "status": int(response.status), "payload": payload}
    except urllib.error.HTTPError as exc:
        return {"ok": False, "status": int(exc.code), "error": str(exc), "payload": {}}
    except Exception as exc:
        return {"ok": False, "status": 0, "error": str(exc), "payload": {}}


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, sort_keys=True, default=str), encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _to_overlay_list(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    direct_overlays = _mapping(payload.get("overlays"))
    if direct_overlays:
        rows = _sequence(direct_overlays.get("objects"))
        if rows:
            return [_mapping(row) for row in rows if isinstance(row, Mapping)]
    for key in ("overlay_objects", "overlays"):
        rows = _sequence(payload.get(key))
        if rows:
            return [_mapping(row) for row in rows if isinstance(row, Mapping)]
    visual = _mapping(payload.get("visual"))
    for key in ("overlay_objects", "overlays"):
        rows = _sequence(visual.get(key))
        if rows:
            return [_mapping(row) for row in rows if isinstance(row, Mapping)]
    registry = _mapping(payload.get("market_object_registry"))
    rows = _sequence(registry.get("active_overlays"))
    if rows:
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            overlay = _mapping(row.get("overlay"))
            if overlay:
                merged = dict(overlay)
                for field in ("overlay_id", "object_id", "track_id", "lifecycle_state", "truth_score", "frame_id", "chart_transform_id"):
                    if field not in merged and row.get(field) not in (None, ""):
                        merged[field] = row.get(field)
                out.append(merged)
            else:
                out.append(_mapping(row))
        return out
    active = _sequence(payload.get("active_overlays"))
    if active:
        out = []
        for row in active:
            if isinstance(row, Mapping):
                overlay = _mapping(row.get("overlay"))
                out.append(overlay or _mapping(row))
        return out
    return []


def load_overlays(base_url: str, session_id: str, timeout: float, input_path: str | None = None, mode: str = "DIAGNOSTICS") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if input_path:
        raw = json.loads(Path(input_path).read_text(encoding="utf-8"))
        payload = _mapping(raw) if isinstance(raw, Mapping) else {"overlay_objects": raw}
        return _to_overlay_list(payload), {"source": str(input_path), "payload": payload}

    base = base_url.rstrip("/")
    session_q = urllib.parse.quote(session_id, safe="")
    mode_q = urllib.parse.quote(mode, safe="")
    live_url = f"{base}/v1/mobile/live/state/v3/{session_q}?mode={mode_q}&compact=1"
    live = _http_json(live_url, timeout)
    if live.get("ok"):
        overlays = _to_overlay_list(_mapping(live.get("payload")))
        return overlays, {"source": "live_state_v3", "mode": mode, "endpoint": live_url, "endpoint_status": live.get("status")}

    registry = _http_json(f"{base}/v1/mobile/registry/sessions/{session_q}/active?min_truth_score=0.0", timeout)
    overlays = _to_overlay_list(_mapping(registry.get("payload")))
    return overlays, {
        "source": "registry_active_fallback",
        "endpoint": f"{base}/v1/mobile/registry/sessions/{session_q}/active?min_truth_score=0.0",
        "endpoint_status": registry.get("status"),
        "live_state_error": live.get("error") or live.get("status"),
    }


def _valid_bounds(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key in ("bbox", "pixel_bbox", "normalized_bbox", "xyxy"):
            if key in value and _valid_bounds(value.get(key)):
                return True
        try:
            x = _float_value(value.get("x", value.get("left")))
            y = _float_value(value.get("y", value.get("top")))
            width = _float_value(value.get("width", value.get("w")))
            height = _float_value(value.get("height", value.get("h")))
            return width > 0 and height > 0 and x == x and y == y
        except Exception:
            pass
        try:
            left = _float_value(value.get("left", value.get("x")))
            top = _float_value(value.get("top", value.get("y")))
            right = _float_value(value.get("right"))
            bottom = _float_value(value.get("bottom"))
            return right > left and bottom > top
        except Exception:
            return False
    items = _sequence(value)
    if len(items) != 4:
        return False
    try:
        x1, y1, x2, y2 = [float(v) for v in items]
    except Exception:
        return False
    return x2 > x1 and y2 > y1


def validate_overlay(overlay: Mapping[str, Any], index: int) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    contract_result = validate_v3_overlay_object(overlay)
    for issue in contract_result.errors:
        errors.append(f"{issue.field}: {issue.reason}")
    for field in REQUIRED_FIELDS:
        if field == "anchor_candles" and isinstance(overlay.get(field), Sequence) and not isinstance(overlay.get(field), (str, bytes, bytearray)):
            continue
        if field in {"source_version", "broker_source_lock_id"} and field in overlay and overlay.get(field) in ("", None):
            continue
        if overlay.get(field) in (None, "", [], {}):
            errors.append(f"missing required field: {field}")
    typ = str(overlay.get("type") or "")
    if typ and typ not in ALLOWED_TYPES:
        errors.append(f"unsupported type: {typ}")
    coord = str(overlay.get("coordinate_mode") or "")
    if coord and coord not in ALLOWED_COORDINATE_MODES:
        errors.append(f"unsupported coordinate_mode: {coord}")
    if overlay.get("bounds") not in (None, "") and not _valid_bounds(overlay.get("bounds")):
        errors.append("bounds must be [x1, y1, x2, y2] with positive width/height")
    for field in ("truth_score", "confidence"):
        try:
            value = _float_value(overlay.get(field))
            if not 0.0 <= value <= 1.0:
                errors.append(f"{field} out of range: {value}")
        except Exception:
            if overlay.get(field) not in (None, ""):
                errors.append(f"{field} is not numeric")
    modes = _sequence(overlay.get("visible_modes"))
    if modes:
        invalid = [str(mode) for mode in modes if str(mode) not in ALLOWED_MODES]
        if invalid:
            errors.append(f"invalid visible_modes: {', '.join(invalid)}")
    if overlay.get("bbox") and not overlay.get("bounds"):
        warnings.append("legacy bbox present but V3 bounds missing")
    label_hidden = overlay.get("label_hidden") is True or str(overlay.get("label_hidden") or "").lower() == "true"
    if not label_hidden:
        display_label = str(overlay.get("display_label") or "").strip()
        public_label = str(overlay.get("label") or "").strip()
        if display_label and not is_approved_overlay_display_label(display_label):
            errors.append(f"display_label not approved: {display_label}")
        if public_label and not is_approved_overlay_display_label(public_label):
            errors.append(f"public label not approved: {public_label}")
    return {
        "index": index,
        "overlay_id": str(overlay.get("overlay_id") or overlay.get("id") or f"overlay_{index}"),
        "type": typ,
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
    }


def validate_contract(overlays: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    rows = [validate_overlay(overlay, index) for index, overlay in enumerate(overlays)]
    duplicate_ids: list[str] = []
    seen: set[str] = set()
    for row in rows:
        overlay_id = str(row["overlay_id"])
        if overlay_id in seen and overlay_id not in duplicate_ids:
            duplicate_ids.append(overlay_id)
        seen.add(overlay_id)
    hard_mismatches = [f"{row['overlay_id']}: {error}" for row in rows for error in row["errors"]]
    hard_mismatches.extend(f"duplicate overlay_id: {overlay_id}" for overlay_id in duplicate_ids)
    if not rows:
        hard_mismatches.append("no overlay objects were returned")
    return {
        "overlay_count": len(rows),
        "valid_count": sum(1 for row in rows if row["valid"]),
        "invalid_count": sum(1 for row in rows if not row["valid"]),
        "duplicate_overlay_ids": duplicate_ids,
        "rows": rows,
        "hard_mismatches": hard_mismatches,
        "ok": not hard_mismatches and bool(rows),
    }


def _render_markdown(report: Mapping[str, Any]) -> str:
    lines = [
        "# PhoenixGuard V3 Overlay Contract Report",
        "",
        f"- Session: {report['session_id']}",
        f"- Source: {report['source'].get('source')}",
        f"- Verdict: {report['verdict']}",
        f"- Overlay count: {report['summary']['overlay_count']}",
        f"- Invalid overlays: {report['summary']['invalid_count']}",
        "",
        "## Invalid Objects",
    ]
    invalid = [row for row in report["summary"]["rows"] if not row["valid"]]
    if invalid:
        for row in invalid:
            lines.append(f"- {row['overlay_id']} ({row.get('type') or 'unknown'}): {'; '.join(row['errors'])}")
    else:
        lines.append("- none")
    lines.extend(["", "## Warnings"])
    warnings = [f"{row['overlay_id']}: {warning}" for row in report["summary"]["rows"] for warning in row["warnings"]]
    if warnings:
        lines.extend(f"- {warning}" for warning in warnings)
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate PhoenixGuard V3 overlay object contracts.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", "--session-id", dest="session_id", default=DEFAULT_SESSION)
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--mode", default="DIAGNOSTICS", help="Live-state overlay mode to validate when --input is not used.")
    parser.add_argument("--input", help="Optional JSON file containing live_state, overlay_objects, overlays, or registry payload.")
    parser.add_argument("--out-json", default="reports/FINAL_OVERLAY_CONTRACT_REPORT.json")
    parser.add_argument("--out-md", default="reports/FINAL_OVERLAY_CONTRACT_REPORT.md")
    parser.add_argument("--soft", action="store_true", help="Always exit 0 after writing reports.")
    args = parser.parse_args(argv)

    overlays, source = load_overlays(args.base_url, args.session_id, args.timeout, args.input, args.mode)
    summary = validate_contract(overlays)
    verdict = "PASS" if summary["ok"] else "FAIL"
    report = {
        "schema_version": "PG_OVERLAY_CONTRACT_VALIDATION_V3",
        "session_id": args.session_id,
        "base_url": args.base_url.rstrip("/"),
        "generated_epoch": time.time(),
        "verdict": verdict,
        "ok": verdict == "PASS",
        "source": source,
        "summary": summary,
    }
    _write_json(Path(args.out_json), report)
    _write_text(Path(args.out_md), _render_markdown(report))
    print(json.dumps({"verdict": verdict, "overlay_count": summary["overlay_count"], "invalid_count": summary["invalid_count"], "out_json": args.out_json, "out_md": args.out_md}, indent=2))
    return 0 if args.soft or report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
