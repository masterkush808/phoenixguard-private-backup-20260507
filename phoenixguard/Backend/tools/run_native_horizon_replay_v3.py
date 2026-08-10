from __future__ import annotations

from _bootstrap import ensure_backend_paths

ensure_backend_paths()

import argparse
import sys
from pathlib import Path

from phoenixguard.study.native_horizon_replay_v3 import (  # noqa: E402
    CaseOutcome,
    DiskReserveError,
    NativeReplayConfig,
    RapidChartIdentityResolver,
    discover_images,
    ensure_disk_reserve,
    format_summary,
    output_bytes,
    prepare_output_root,
    run_case,
    summarize,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run PhoenixGuard V3 native 72-horizon screenshot replay.")
    parser.add_argument("--corpus-root", type=Path, default=PROJECT_ROOT / "808 Memory")
    parser.add_argument("--output-root", type=Path, default=PROJECT_ROOT / ".codex_runtime" / "native_horizon_replay_v3")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Preserve an existing replay directory and continue at --start-index.",
    )
    parser.add_argument("--min-free-gb", type=float, default=45.0)
    parser.add_argument("--max-output-mb", type=int, default=512)
    args = parser.parse_args()
    config = NativeReplayConfig(
        min_free_gb=args.min_free_gb,
        max_output_bytes=args.max_output_mb * 1024 * 1024,
    )
    ensure_disk_reserve(PROJECT_ROOT, minimum_gb=config.min_free_gb)
    images = discover_images(args.corpus_root)
    start = max(0, args.start_index)
    images = images[start:]
    if args.limit > 0:
        images = images[: args.limit]
    if not images:
        print("No screenshot images found.")
        return 2
    if args.resume:
        runtime_root = (PROJECT_ROOT / ".codex_runtime").resolve()
        run_root = args.output_root.resolve()
        if run_root == runtime_root or runtime_root not in run_root.parents:
            print("Resume refused: output must remain below workspace .codex_runtime.", file=sys.stderr)
            return 4
        run_root.mkdir(parents=True, exist_ok=True)
    else:
        run_root = prepare_output_root(args.output_root, PROJECT_ROOT)
    evidence_root = run_root / "evidence"
    state_root = run_root / "state"
    evidence_root.mkdir(parents=True, exist_ok=True)
    state_root.mkdir(parents=True, exist_ok=True)
    identity_resolver = RapidChartIdentityResolver()
    outcomes: list[CaseOutcome] = []
    bytes_written = output_bytes(evidence_root) if args.resume else 0
    try:
        for offset, source in enumerate(images, start=1):
            relative = source.relative_to(args.corpus_root)
            try:
                outcome, bytes_written = run_case(
                    source,
                    source_label=str(relative),
                    index=start + offset,
                    state_root=state_root,
                    evidence_root=evidence_root,
                    bytes_written=bytes_written,
                    identity_resolver=identity_resolver,
                    config=config,
                )
            except DiskReserveError:
                raise
            except Exception as exc:
                outcome = CaseOutcome(
                    source_path=source,
                    category="UNKNOWN",
                    status="ERROR",
                    reason=f"{type(exc).__name__}: {exc}",
                )
            outcomes.append(outcome)
            terminal = (
                "HIT" if outcome.metrics.get("terminal_direction_hit") else "MISS"
            ) if outcome.metrics else "N/A"
            print(
                f"[{offset:03d}/{len(images):03d}] {outcome.status} terminal={terminal} "
                f"h={outcome.available_future}/{outcome.horizon} {outcome.pair} {outcome.timeframe} {relative}",
                flush=True,
            )
    except DiskReserveError as exc:
        print(f"DISK CONTRACT STOP: {exc}", file=sys.stderr)
        return 3
    finally:
        try:
            state_root.rmdir()
        except OSError:
            pass
    result = summarize(outcomes)
    print()
    print(format_summary(result))
    print(f"Evidence PNGs: {evidence_root}")
    print(f"Evidence size: {output_bytes(evidence_root) / (1024**2):.2f} MB")
    print(f"Free disk after replay: {ensure_disk_reserve(PROJECT_ROOT, minimum_gb=config.min_free_gb):.2f} GB")
    return 0 if result.get("scored") else 2


if __name__ == "__main__":
    raise SystemExit(main())
