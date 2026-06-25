from __future__ import annotations

import argparse
from http.client import HTTPException
from io import BytesIO
import json
from pathlib import Path
import socket
import statistics
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError, URLError
from typing import Any, Mapping, cast

from PIL import Image, ImageFilter, ImageStat


def _bytes(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "PhoenixGuard-LiveClarityTestV3/1.0", "Connection": "close"})
    with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - local operator endpoint
        return response.read()


def _error(exc: BaseException) -> str:
    if isinstance(exc, HTTPError):
        return f"HTTP {exc.code}: {exc.reason}"
    if isinstance(exc, URLError):
        return f"URL error: {exc.reason}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return f"timeout: {exc}"
    if isinstance(exc, HTTPException):
        return f"HTTP error: {exc}"
    return f"{type(exc).__name__}: {exc}"


def _json(url: str, timeout: float) -> dict[str, Any]:
    return _mapping(json.loads(_bytes(url, timeout).decode("utf-8", errors="replace")))


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _avg(values: list[float]) -> float:
    return round(float(statistics.mean(values)), 4) if values else 0.0


def _image_scores(blob: bytes) -> dict[str, Any]:
    with Image.open(BytesIO(blob)) as image:
        rgb = image.convert("RGB")
        gray = rgb.convert("L")
        stat = ImageStat.Stat(gray)
        edges = gray.filter(ImageFilter.FIND_EDGES)
        edge_stat = ImageStat.Stat(edges)
        contrast = float(stat.stddev[0] or 0.0) / 128.0
        sharpness = float(edge_stat.mean[0] or 0.0) / 64.0
        readability = min(1.0, max(0.0, (contrast * 0.55) + (sharpness * 0.45)))
        return {
            "width": int(image.width),
            "height": int(image.height),
            "bytes": len(blob),
            "candle_contrast_score": round(min(1.0, contrast), 4),
            "edge_sharpness_score": round(min(1.0, sharpness), 4),
            "chart_text_readability_proxy": round(readability, 4),
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PhoenixGuard V3 live clarity sampling.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8793")
    parser.add_argument("--session", default="pocket-live-8788")
    parser.add_argument("--duration-sec", type=float, default=60.0)
    parser.add_argument("--interval-sec", type=float, default=2.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--out", default=".codex_runtime/clarity_test")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    session = urllib.parse.quote(args.session, safe="")
    deadline = time.time() + max(1.0, float(args.duration_sec))
    samples: list[dict[str, Any]] = []
    endpoint_failures = 0
    while time.time() < deadline:
        try:
            live = _json(f"{base}/v1/mobile/live/state/v3/{session}", args.timeout)
        except Exception as exc:
            endpoint_failures += 1
            samples.append({
                "error": _error(exc),
                "stage": "live_state",
                "sample_epoch_ms": round(time.time() * 1000.0, 3),
            })
            time.sleep(max(0.1, float(args.interval_sec)))
            continue
        chart_frame = _mapping(live.get("chart_frame"))
        broker_surface = _mapping(live.get("broker_surface"))
        artifact = _mapping(chart_frame.get("display_artifact") or broker_surface.get("frame"))
        url = str(artifact.get("url") or broker_surface.get("url") or "")
        if not url:
            endpoint_failures += 1
            samples.append({
                "error": "missing_display_artifact_url",
                "stage": "artifact_lookup",
                "frame_id": live.get("frame_id"),
                "sample_epoch_ms": round(time.time() * 1000.0, 3),
            })
            time.sleep(max(0.1, float(args.interval_sec)))
            continue
        try:
            blob = _bytes(f"{base}{url}" if url.startswith("/") else url, args.timeout)
            scores = _image_scores(blob)
        except Exception as exc:
            endpoint_failures += 1
            samples.append({
                "error": _error(exc),
                "stage": "artifact_download",
                "frame_id": live.get("frame_id"),
                "artifact_url": url,
                "sample_epoch_ms": round(time.time() * 1000.0, 3),
            })
            time.sleep(max(0.1, float(args.interval_sec)))
            continue
        audit = _mapping(live.get("overlay_precision_audit"))
        precision_report = _mapping(audit.get("precision_report"))
        alignment = 1.0
        if int(precision_report.get("outside_plot_area") or 0) or int(precision_report.get("missing_transform") or 0):
            alignment = 0.0
        elif int(precision_report.get("label_collisions") or 0):
            alignment = 0.5
        performance_trace = _mapping(live.get("performance_trace_v3"))
        adaptive_performance = _mapping(performance_trace.get("adaptive_performance"))
        scores.update({
            "frame_id": live.get("frame_id"),
            "overlay_alignment_score": alignment,
            "display_profile": adaptive_performance.get("profile", "BALANCED"),
        })
        samples.append(scores)
        time.sleep(max(0.1, float(args.interval_sec)))

    good_samples = [item for item in samples if "width" in item]
    report: dict[str, Any] = {
        "schema_version": "PG_LIVE_CLARITY_TEST_V3",
        "session_id": args.session,
        "duration_sec": float(args.duration_sec),
        "sample_count": len(samples),
        "valid_sample_count": len(good_samples),
        "endpoint_failures": endpoint_failures,
        "frame_resolution": [good_samples[-1]["width"], good_samples[-1]["height"]] if good_samples else [0, 0],
        "average_image_bytes": _avg([float(item["bytes"]) for item in good_samples]),
        "chart_text_readability_proxy": _avg([float(item["chart_text_readability_proxy"]) for item in good_samples]),
        "candle_contrast_score": _avg([float(item["candle_contrast_score"]) for item in good_samples]),
        "edge_sharpness_score": _avg([float(item["edge_sharpness_score"]) for item in good_samples]),
        "overlay_alignment_score": _avg([float(item["overlay_alignment_score"]) for item in good_samples]),
        "samples": samples,
    }
    report["verdict"] = "PASS" if good_samples and endpoint_failures == 0 and report["candle_contrast_score"] >= 0.08 and report["edge_sharpness_score"] >= 0.02 and report["overlay_alignment_score"] >= 0.9 else "FAIL"
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_json = out_dir / "live_clarity_test_v3.json"
    out_json.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("verdict", "valid_sample_count", "endpoint_failures", "frame_resolution", "average_image_bytes", "chart_text_readability_proxy", "candle_contrast_score", "edge_sharpness_score", "overlay_alignment_score")}, indent=2))
    print(str(out_json))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
