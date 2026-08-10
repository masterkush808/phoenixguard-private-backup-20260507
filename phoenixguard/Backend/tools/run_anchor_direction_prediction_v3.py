from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from phoenixguard.study.anchor_direction_replay_v3 import (
    run_anchor_direction_replay_v3,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Test only whether every hidden candle closes above or below one "
            "fixed final-visible-candle anchor."
        )
    )
    parser.add_argument(
        "--corpus-cache",
        type=Path,
        default=(
            REPOSITORY_ROOT
            / ".codex_runtime"
            / "pure_masked_future"
            / "corpus_cache.jsonl"
        ),
    )
    parser.add_argument(
        "--mask-run-dir",
        type=Path,
        default=REPOSITORY_ROOT / ".codex_runtime" / "pure_masked_future",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPOSITORY_ROOT / ".codex_runtime" / "anchor_direction_only_v3",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=(
            REPOSITORY_ROOT
            / "reports"
            / "FINAL_FIXED_ANCHOR_DIRECTION_PREDICTION_REPORT.md"
        ),
    )
    parser.add_argument("--workers", type=_positive_int, default=2)
    parser.add_argument("--neighbors", type=_positive_int, default=15)
    parser.add_argument("--minimum-free-gb", type=float, default=45.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    summary = run_anchor_direction_replay_v3(
        corpus_cache_path=args.corpus_cache,
        mask_run_dir=args.mask_run_dir,
        output_dir=args.output_dir,
        report_path=args.report,
        workers=args.workers,
        neighbors=args.neighbors,
        minimum_free_gb=args.minimum_free_gb,
    )
    metrics = summary.get("metrics")
    print(
        json.dumps(
            {
                "status": summary.get("status"),
                "source_screenshots": summary.get("source_screenshot_count"),
                "prepared_cases": summary.get("prepared_case_count"),
                "failures": summary.get("preparation_failure_count"),
                "metrics": metrics,
                "audit": summary.get("audit"),
                "report": summary.get("report_path"),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if summary.get("status") == "COMPLETE" else 1


if __name__ == "__main__":
    raise SystemExit(main())
