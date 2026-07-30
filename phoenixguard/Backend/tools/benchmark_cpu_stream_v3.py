from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import statistics
import time
from typing import Any, cast

from _bootstrap import ensure_backend_paths

ensure_backend_paths()

from PIL import Image, ImageDraw  # noqa: E402

from phoenixguard.vision.cpu_stream_v3 import CPUStreamConfig, CPUStreamObserver  # noqa: E402


def _mapping(value: object) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = int(round((len(ordered) - 1) * percentile / 100.0))
    return float(ordered[min(len(ordered) - 1, max(0, position))])


def _frames(width: int, height: int) -> tuple[Image.Image, ...]:
    base = Image.new("RGB", (width, height), (24, 29, 43))

    rest = base.copy()
    rest_draw = ImageDraw.Draw(rest)
    rest_draw.rectangle(
        (max(0, width - 32), 8, max(0, width - 8), 24),
        fill=(26, 31, 45),
    )

    material_up = base.copy()
    up_draw = ImageDraw.Draw(material_up)
    up_draw.rectangle(
        (width // 2, height // 5, width - 1, height // 2),
        fill=(36, 150, 90),
    )

    material_down = base.copy()
    down_draw = ImageDraw.Draw(material_down)
    down_draw.rectangle(
        (width // 2, height // 2, width - 1, (height * 4) // 5),
        fill=(178, 46, 70),
    )
    return base, rest, material_up, material_down


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Benchmark the bounded PhoenixGuard V3 CPU stream observer.",
    )
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--target-fps", type=float, default=4.0)
    parser.add_argument("--p95-budget-ms", type=float, default=100.0)
    parser.add_argument("--memory-budget-mb", type=float, default=64.0)
    args = parser.parse_args()

    frame_count = max(8, int(args.frames))
    width = max(64, int(args.width))
    height = max(64, int(args.height))
    target_fps = min(8.0, max(1.0, float(args.target_fps)))
    config = CPUStreamConfig()
    observer = CPUStreamObserver(config, stream_id="cpu-stream-benchmark-v3")
    base, rest, material_up, material_down = _frames(width, height)
    identity: dict[str, Any] = {
        "session_id": "benchmark",
        "window_handle": "synthetic",
        "symbol": "EUR/USD OTC",
        "timeframe": "M5",
        "geometry_hash": f"{width}x{height}",
    }

    durations_ms: list[float] = []
    accepted = 0
    reasons: dict[str, int] = {}
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    epoch = 1_000_000.0
    for index in range(frame_count):
        phase = index % 40
        if phase == 20:
            image = material_up
        elif phase == 21:
            image = material_down
        elif phase in {5, 6}:
            image = rest
        else:
            image = base
        started = time.perf_counter()
        decision = observer.push(
            image,
            captured_epoch=epoch + index / target_fps,
            identity=identity,
        )
        durations_ms.append((time.perf_counter() - started) * 1000.0)
        accepted += int(decision.accepted_for_study)
        reasons[decision.reason] = reasons.get(decision.reason, 0) + 1

    wall_sec = max(1e-9, time.perf_counter() - wall_started)
    cpu_sec = max(0.0, time.process_time() - cpu_started)
    snapshot = observer.snapshot()
    memory = _mapping(snapshot.get("memory", {}))
    rings = _mapping(snapshot.get("rings", {}))
    estimated_bytes = int(memory.get("current_estimated_pixel_bytes", 0) or 0)
    p95_ms = _percentile(durations_ms, 95.0)
    report: dict[str, Any] = {
        "schema_version": "PG_CPU_STREAM_BENCHMARK_V3",
        "cpu_only": True,
        "input": {
            "frames": frame_count,
            "width": width,
            "height": height,
            "target_fps": target_fps,
            "input_megapixels": round(width * height / 1_000_000.0, 3),
        },
        "timing": {
            "wall_sec": round(wall_sec, 6),
            "process_cpu_sec": round(cpu_sec, 6),
            "observed_frames_per_wall_sec": round(frame_count / wall_sec, 3),
            "mean_push_ms": round(statistics.mean(durations_ms), 3),
            "p50_push_ms": round(_percentile(durations_ms, 50.0), 3),
            "p95_push_ms": round(p95_ms, 3),
            "max_push_ms": round(max(durations_ms), 3),
        },
        "selection": {
            "accepted_keyframes": accepted,
            "accepted_ratio": round(accepted / frame_count, 6),
            "reasons": dict(sorted(reasons.items())),
        },
        "bounded_state": {
            "rings": rings,
            "estimated_pixel_memory_bytes": estimated_bytes,
            "estimated_pixel_memory_mb": round(estimated_bytes / (1024.0 * 1024.0), 3),
        },
        "budgets": {
            "p95_push_ms_lte": float(args.p95_budget_ms),
            "estimated_pixel_memory_mb_lte": float(args.memory_budget_mb),
        },
    }
    report["pass"] = bool(
        p95_ms <= float(args.p95_budget_ms)
        and estimated_bytes <= float(args.memory_budget_mb) * 1024.0 * 1024.0
        and int(_mapping(rings.get("full_frames", {})).get("size", 0) or 0)
        <= config.full_frame_capacity
        and int(_mapping(rings.get("downsamples", {})).get("size", 0) or 0)
        <= config.downsample_ring_capacity
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if bool(report["pass"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
