from __future__ import annotations

from _bootstrap import ensure_backend_paths

ensure_backend_paths()

import argparse
import sys
from pathlib import Path

from phoenixguard.study.v3_causal_path_replay import (  # noqa: E402
    DiskReserveError,
    ReplayCaseOutcome,
    ReplayConfig,
    discover_screenshot_images,
    ensure_disk_reserve,
    format_replay_summary,
    output_size_bytes,
    prepare_fresh_output_root,
    run_replay_case,
    summarize_replay,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run leak-free screenshot masking through the actual PhoenixGuard V3 "
            "production study and score its frozen candle trajectory after reveal."
        )
    )
    parser.add_argument("--corpus-root", type=Path, default=PROJECT_ROOT / "808 Memory")
    parser.add_argument(
        "--output-root",
        type=Path,
        default=PROJECT_ROOT / ".codex_runtime" / "v3_causal_path_replay",
    )
    parser.add_argument("--mask-ratio", type=float, default=0.60)
    parser.add_argument("--min-visible-candles", type=int, default=24)
    parser.add_argument("--min-future-candles", type=int, default=4)
    parser.add_argument("--horizon-candles", type=int, default=12)
    parser.add_argument("--min-free-gb", type=float, default=45.0)
    parser.add_argument("--max-output-mb", type=int, default=512)
    parser.add_argument("--limit", type=int, default=0, help="Zero processes the full corpus.")
    parser.add_argument("--start-index", type=int, default=0)
    return parser


def main() -> int:
    args = _parser().parse_args()
    config = ReplayConfig(
        mask_ratio=args.mask_ratio,
        min_visible_candles=args.min_visible_candles,
        min_future_candles=args.min_future_candles,
        max_horizon_candles=args.horizon_candles,
        min_free_gb=args.min_free_gb,
        max_output_bytes=int(args.max_output_mb) * 1024 * 1024,
    )
    ensure_disk_reserve(PROJECT_ROOT, min_free_gb=config.min_free_gb)
    images = discover_screenshot_images(args.corpus_root)
    start = max(0, int(args.start_index))
    images = images[start:]
    if args.limit > 0:
        images = images[: int(args.limit)]
    if not images:
        print("No supported screenshots were found.")
        return 2
    run_root = prepare_fresh_output_root(args.output_root, PROJECT_ROOT)
    evidence_root = run_root / "evidence"
    state_parent = run_root / "case_state"
    evidence_root.mkdir(parents=True, exist_ok=True)
    state_parent.mkdir(parents=True, exist_ok=True)
    outcomes: list[ReplayCaseOutcome] = []
    output_bytes = 0
    total = len(images)
    try:
        for offset, source_path in enumerate(images, start=1):
            relative = source_path.relative_to(args.corpus_root)
            try:
                outcome, output_bytes = run_replay_case(
                    source_path,
                    source_label=str(relative),
                    case_index=start + offset,
                    state_parent=state_parent,
                    evidence_root=evidence_root,
                    existing_output_bytes=output_bytes,
                    config=config,
                )
            except DiskReserveError:
                raise
            except Exception as exc:
                outcome = ReplayCaseOutcome(
                    source_path=source_path,
                    category="UNKNOWN",
                    status="ERROR",
                    reason=f"{type(exc).__name__}: {exc}",
                )
            outcomes.append(outcome)
            metric = outcome.metrics
            terminal = (
                "HIT" if metric.get("terminal_direction_hit") else "MISS"
            ) if metric else "N/A"
            print(
                f"[{offset:03d}/{total:03d}] {outcome.status} "
                f"terminal={terminal} future={outcome.actual_future_candles} "
                f"{relative}"
            )
    except DiskReserveError as exc:
        print(f"DISK CONTRACT STOP: {exc}", file=sys.stderr)
        return 3
    finally:
        if state_parent.exists():
            try:
                state_parent.rmdir()
            except OSError:
                pass
    summary = summarize_replay(outcomes)
    print()
    print(format_replay_summary(summary))
    print(f"Evidence PNGs: {evidence_root}")
    print(f"Evidence size: {output_size_bytes(evidence_root) / (1024**2):.2f} MB")
    free_gb = ensure_disk_reserve(PROJECT_ROOT, min_free_gb=config.min_free_gb)
    print(f"Free disk after replay: {free_gb:.2f} GB")
    return 0 if summary.get("scored", 0) else 2


if __name__ == "__main__":
    raise SystemExit(main())
