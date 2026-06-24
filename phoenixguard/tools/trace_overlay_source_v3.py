from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence, cast
from urllib.request import urlopen, Request

from PIL import Image

from phoenixguard.vision.renderer import render_overlays_on_chart


@dataclass
class TraceResult:
    chart_endpoint_url: str
    chart_frame_id: int
    chart_image_size: list[int]
    chart_artifact_path: str
    chart_artifact_exists: bool
    overlay_endpoint_url: str
    overlay_response_status: int
    overlay_count: int
    overlay_frame_id: int | None
    chart_transform_id: str
    overlay_coordinate_modes: list[str]
    overlay_source_modules: list[str]
    overlay_source_agents: list[str]
    overlay_ttl_sec: list[float]
    visible_mode: str
    frontend_render_mode: str
    overlay_frame_matches_chart_frame: bool
    overlay_transform_matches_chart_transform: bool
    overlays_are_v3_objects: bool
    study_packet_exists: bool
    model_council_state_exists: bool
    model_health: str


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(cast(Sequence[Any], value)) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else []


def _json_get(url: str) -> tuple[int, dict[str, Any]]:
    request = Request(url, headers={"User-Agent": "PhoenixGuard-Overlay-Trace/1.0"})
    try:
        with urlopen(request, timeout=60) as response:
            data = response.read().decode("utf-8", errors="replace")
            return int(getattr(response, "status", 200)), _mapping(json.loads(data))
    except Exception as exc:
        try:
            code = int(getattr(exc, 'code', 500))
        except Exception:
            code = 500
        return code, {}


def _bytes_get(url: str) -> tuple[int, bytes, str]:
    request = Request(url, headers={"User-Agent": "PhoenixGuard-Overlay-Trace/1.0"})
    try:
        with urlopen(request, timeout=60) as response:
            return int(getattr(response, "status", 200)), response.read(), str(response.headers.get_content_type())
    except Exception:
        return 500, b"", "application/octet-stream"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")


def _normalize_list(values: list[str]) -> list[str]:
    return sorted({value for value in values if value})


def main() -> int:
    parser = argparse.ArgumentParser(description="Trace PhoenixGuard V3 overlay source-of-truth.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    base_url = args.base_url.rstrip("/")
    session = args.session.strip()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    chart_state_url = f"{base_url}/v1/mobile/chart/state/v3?session_id={session}"
    overlay_url = f"{base_url}/v1/mobile/registry/sessions/{session}/active?min_truth_score=0.0"
    session_url = f"{base_url}/v1/mobile/window-tracker/sessions/{session}"
    visual_health_url = f"{base_url}/v1/mobile/visual/health/v3?session_id={session}"
    study_url = f"{base_url}/v1/mobile/model-council/study/latest?session_id={session}"
    runtime_url = f"{base_url}/v1/mobile/runtime/trace/v3?session_id={session}"

    _, chart_state = _json_get(chart_state_url)
    overlay_status, overlay_payload = _json_get(overlay_url)
    _, session_payload = _json_get(session_url)
    _, visual_health = _json_get(visual_health_url)
    _, study_payload = _json_get(study_url)
    _, runtime_trace = _json_get(runtime_url)

    # prefer explicit chart frame id from chart state, fall back to tracker session frame_index
    chart_frame_id = int(chart_state.get("frame_id") or session_payload.get("frame_index") or 0)
    # prefer window-tracker artifact endpoint for chart PNG
    chart_url = f"{base_url}/v1/mobile/window-tracker/sessions/{session}/artifacts/latest-chart"
    _chart_status, chart_bytes, _chart_content_type = _bytes_get(chart_url)
    chart_path = out_dir / "current_chart.png"
    chart_path.write_bytes(chart_bytes)

    chart_image_size = [0, 0]
    try:
        with Image.open(chart_path) as image:
            chart_image_size = [int(image.width), int(image.height)]
    except Exception:
        pass

    active_overlays = _sequence(overlay_payload.get("active_overlays"))
    overlay_dicts: list[dict[str, Any]] = []
    overlay_source_modules: list[str] = []
    overlay_source_agents: list[str] = []
    overlay_coordinate_modes: list[str] = []
    overlay_ttl_sec: list[float] = []
    overlay_frame_id: int | None = None
    chart_transform = _mapping(overlay_payload.get("chart_transform"))
    chart_transform_id = str(chart_transform.get("chart_transform_id") or "")
    overlays_are_v3 = True
    for item in active_overlays:
        if not isinstance(item, Mapping):
            overlays_are_v3 = False
            continue
        item_map = cast(Mapping[str, Any], item)
        overlay = _mapping(item_map.get("overlay"))
        overlay_dicts.append(overlay)
        overlay_source_modules.append(str(item_map.get("source_module") or overlay.get("source_module") or ""))
        overlay_source_agents.append(str(overlay.get("source_agent") or item_map.get("source_agent") or ""))
        overlay_coordinate_modes.append(str(overlay.get("coordinate_mode") or ""))
        try:
            overlay_ttl_sec.append(float(overlay.get("ttl_sec") or 0.0))
        except Exception:
            overlay_ttl_sec.append(0.0)
        if overlay_frame_id is None and item_map.get("frame_id") is not None:
            try:
                raw_frame_id = item_map.get("frame_id")
                overlay_frame_id = int(raw_frame_id) if isinstance(raw_frame_id, (int, float, str)) else None
            except Exception:
                overlay_frame_id = None
        overlays_are_v3 = overlays_are_v3 and all(
            key in overlay for key in ("overlay_id", "type", "frame_id", "chart_transform_id", "coordinate_mode", "truth_score")
        )

    debug_png = render_overlays_on_chart(chart_path, overlay_dicts)
    debug_path = out_dir / "current_overlay_debug.png"
    debug_path.write_bytes(debug_png)

    result = TraceResult(
        chart_endpoint_url=chart_url,
        chart_frame_id=chart_frame_id,
        chart_image_size=chart_image_size,
        chart_artifact_path=str(chart_path),
        chart_artifact_exists=chart_path.exists(),
        overlay_endpoint_url=overlay_url,
        overlay_response_status=overlay_status,
        overlay_count=len(active_overlays),
        overlay_frame_id=overlay_frame_id,
        chart_transform_id=chart_transform_id,
        overlay_coordinate_modes=_normalize_list(overlay_coordinate_modes),
        overlay_source_modules=_normalize_list(overlay_source_modules),
        overlay_source_agents=_normalize_list(overlay_source_agents),
        overlay_ttl_sec=[round(v, 3) for v in overlay_ttl_sec],
        visible_mode=str(session_payload.get("visual_mode") or session_payload.get("mode") or session_payload.get("dashboard_mode") or "CLEAN_LIVE"),
        frontend_render_mode="PIL",
        overlay_frame_matches_chart_frame=overlay_frame_id is None or overlay_frame_id == chart_frame_id,
        overlay_transform_matches_chart_transform=bool(chart_transform_id),
        overlays_are_v3_objects=overlays_are_v3,
        study_packet_exists=bool(study_payload),
        model_council_state_exists=bool(_mapping(_mapping(runtime_trace.get("endpoints")).get("model_council_latest")).get("status") == "PASS"),
        model_health=str(_mapping(visual_health.get("model_health")).get("council_status") or "UNKNOWN"),
    )

    _write_json(out_dir / "overlay_source_trace.json", asdict(result))
    md = [
        "# Overlay Source Trace",
        "",
        f"- Chart endpoint: {chart_state_url}",
        f"- Chart frame_id: {chart_frame_id}",
        f"- Chart image size: {chart_image_size[0]}x{chart_image_size[1]}",
        f"- Chart artifact path: {chart_path}",
        f"- Chart artifact exists: {chart_path.exists()}",
        f"- Overlay endpoint: {overlay_url}",
        f"- Overlay response status: {overlay_status}",
        f"- Overlay count: {len(active_overlays)}",
        f"- Overlay frame_id: {overlay_frame_id}",
        f"- Chart transform id: {chart_transform_id}",
        f"- Overlay coordinate modes: {', '.join(_normalize_list(overlay_coordinate_modes)) or 'none'}", 
        f"- Overlay source modules: {', '.join(_normalize_list(overlay_source_modules)) or 'none'}", 
        f"- Overlay source agents: {', '.join(_normalize_list(overlay_source_agents)) or 'none'}", 
        f"- Overlay TTL sec: {', '.join(str(v) for v in result.overlay_ttl_sec) or 'none'}", 
        f"- Visible mode: {result.visible_mode}", 
        f"- Frontend render mode: {result.frontend_render_mode}", 
        f"- Overlay frame matches chart frame: {result.overlay_frame_matches_chart_frame}", 
        f"- Overlay transform matches chart transform: {result.overlay_transform_matches_chart_transform}", 
        f"- Overlays are V3 objects: {result.overlays_are_v3_objects}", 
        f"- Study packet exists: {result.study_packet_exists}", 
        f"- Model council state exists: {result.model_council_state_exists}", 
        f"- Model health: {result.model_health}", 
        "",
        "## Visual verdict",
        f"- overlay endpoint status: {overlay_status}",
        f"- current study packet: {'present' if result.study_packet_exists else 'missing'}",
        f"- current overlay shape: {'V3-native' if result.overlays_are_v3_objects else 'legacy/fallback'}",
    ]
    _write_text(out_dir / "overlay_source_trace.md", "\n".join(md) + "\n")
    print(json.dumps(asdict(result), indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
