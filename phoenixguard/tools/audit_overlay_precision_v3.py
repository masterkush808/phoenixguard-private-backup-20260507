from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import urllib.parse
import urllib.request
from typing import Any, Mapping, Sequence, cast


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from phoenixguard.mobile_api.live_state_v3 import build_live_state_v3  # noqa: E402
from phoenixguard.vision.broker_scene_graph_v3 import build_broker_scene_graph_v3  # noqa: E402
from phoenixguard.vision.box_refinement_v3 import resolve_precision_overlays_v3  # noqa: E402
from phoenixguard.tracking.market_object_tracker_v3 import build_market_object_registry_v3  # noqa: E402


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _sequence_of_mappings(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    items = cast(Sequence[object], value)
    return [dict(cast(Mapping[str, Any], item)) for item in items if isinstance(item, Mapping)]


def _text(value: Any, default: str = "") -> str:
    text = str(value or "").strip()
    return text or default


def _http_json(url: str, timeout: float) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "PhoenixGuard-V3-OverlayPrecisionAudit/1.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8", errors="replace"))
    return dict(cast(Mapping[str, Any], payload)) if isinstance(payload, Mapping) else {}


def _load_session_file(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _default_session_path(session_id: str) -> Path:
    return ROOT / ".codex_runtime" / "data_live" / "mobile_api" / "window_tracker" / "sessions" / session_id / "session.json"


def _artifact_refs_from_session(session: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    refs: dict[str, dict[str, Any]] = {}
    for kind, key in {
        "window": "last_window_path",
        "chart": "last_chart_path",
        "overlay": "last_overlay_path",
        "full-overlay": "last_full_overlay_path",
    }.items():
        path = Path(_text(session.get(key))) if _text(session.get(key)) else None
        refs[kind] = {
            "kind": kind,
            "path": str(path) if path else "",
            "exists": bool(path and path.exists()),
            "width": 0,
            "height": 0,
        }
    return refs


def _audit_from_live_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    audit = _mapping(payload.get("overlay_precision_audit"))
    if audit:
        return audit
    scene = _mapping(payload.get("scene_graph") or payload.get("broker_scene_graph_v3"))
    overlays = _sequence_of_mappings(payload.get("overlay_objects"))
    current_side = _text(_mapping(payload.get("model_council")).get("side") or _mapping(payload.get("latest_signal")).get("action"))
    _resolved, audit = resolve_precision_overlays_v3(
        overlays,
        scene_graph=scene,
        current_side=current_side,
        frame_id=int(payload.get("frame_id") or payload.get("frame_index") or 0),
    )
    return audit


def _audit_from_session(session: Mapping[str, Any]) -> dict[str, Any]:
    artifacts = _artifact_refs_from_session(session)
    scene = build_broker_scene_graph_v3(session, artifacts=artifacts).as_dict()["scene_graph"]
    registry = build_market_object_registry_v3(session)
    current_side = _text(_mapping(session.get("latest_signal")).get("action") or _mapping(session.get("latest_signal")).get("side"))
    _resolved, audit = resolve_precision_overlays_v3(
        registry.overlays,
        scene_graph=scene,
        current_side=current_side,
        frame_id=registry.frame_id,
    )
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit PhoenixGuard V3 overlay precision.")
    parser.add_argument("--base-url", default="", help="Live API base URL, e.g. http://127.0.0.1:8793")
    parser.add_argument("--session", default="pocket-live-8788", help="Session id to audit.")
    parser.add_argument("--session-json", default="", help="Optional local session.json path.")
    parser.add_argument("--mode", default="CLEAN_LIVE", help="Overlay mode to audit.")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--out", default="", help="Optional output JSON path.")
    args = parser.parse_args()

    if args.base_url:
        base = args.base_url.rstrip("/")
        session_q = urllib.parse.quote(args.session, safe="")
        mode_q = urllib.parse.quote(args.mode, safe="")
        payload = _http_json(f"{base}/v1/mobile/live/state/v3/{session_q}?mode={mode_q}", args.timeout)
        audit = _audit_from_live_payload(payload)
    else:
        session_path = Path(args.session_json) if args.session_json else _default_session_path(args.session)
        session = _load_session_file(session_path)
        live_state = build_live_state_v3(session, artifacts={}, overlay_mode=args.mode)
        audit = _mapping(live_state.get("overlay_precision_audit")) or _audit_from_session(session)

    out_path = Path(args.out) if args.out else ROOT / "reports" / "overlay_precision_audit_v3.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(audit, indent=2, sort_keys=True))
    report = _mapping(audit.get("precision_report"))
    hard_failures = [
        key
        for key in (
            "unanchored_boxes",
            "outside_plot_area",
            "stale_frame_id",
            "missing_transform",
            "label_collisions",
            "nesting_collisions",
        )
        if int(report.get(key) or 0) != 0
    ]
    return 1 if hard_failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
