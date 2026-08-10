from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Mapping, Sequence, cast

from phoenixguard.study.masked_image_region_v3 import MaskRectangleV3
from phoenixguard.study.pure_masked_future_replay_v3 import (
    DEFAULT_PURE_HORIZONS,
    run_manual_masked_future_replay_v3,
    run_pure_masked_future_replay_v3,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BUY_ROOT = (
    REPOSITORY_ROOT / "808 Memory" / "BUYS-20260224T225615Z-1-001" / "BUYS"
)
DEFAULT_SELL_ROOT = (
    REPOSITORY_ROOT / "808 Memory" / "SELLS-20260224T225719Z-1-001" / "SELLS"
)
DEFAULT_OUTPUT = REPOSITORY_ROOT / ".codex_runtime" / "pure_masked_future"
DEFAULT_REPORT = (
    REPOSITORY_ROOT / "reports" / "FINAL_PURE_MASKED_FUTURE_PREDICTION_REPORT.md"
)


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("value must be greater than zero")
    return parsed


def _horizons(value: str) -> tuple[int, ...]:
    try:
        parsed = tuple(sorted({_positive_int(item.strip()) for item in value.split(",")}))
    except ValueError as error:
        raise argparse.ArgumentTypeError("horizons must be comma-separated integers") from error
    if not parsed:
        raise argparse.ArgumentTypeError("at least one horizon is required")
    return parsed


def _mask_rectangle(value: str) -> MaskRectangleV3:
    try:
        values = [int(item.strip()) for item in value.split(",")]
    except ValueError as error:
        raise argparse.ArgumentTypeError("mask rectangle must be x1,y1,x2,y2") from error
    if len(values) != 4:
        raise argparse.ArgumentTypeError("mask rectangle must be x1,y1,x2,y2")
    rectangle = MaskRectangleV3(*values)
    if rectangle.x2 <= rectangle.x1 or rectangle.y2 <= rectangle.y1:
        raise argparse.ArgumentTypeError("mask rectangle must have positive area")
    return rectangle


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run leakage-safe screenshot-only masked-future prediction replay. "
            "Folder names are provenance and never prediction labels."
        )
    )
    parser.add_argument("--memory-buy", type=Path, default=DEFAULT_BUY_ROOT)
    parser.add_argument("--memory-sell", type=Path, default=DEFAULT_SELL_ROOT)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--mask-rect", type=_mask_rectangle)
    parser.add_argument(
        "--horizons",
        type=_horizons,
        default=DEFAULT_PURE_HORIZONS,
        help="Comma-separated candle horizons, default: 1,2,3,5,8,13,21,34",
    )
    parser.add_argument("--minimum-prefix-candles", type=_positive_int, default=32)
    parser.add_argument("--minimum-hidden-candles", type=_positive_int, default=8)
    parser.add_argument("--cutoff-stride", type=_positive_int, default=2)
    parser.add_argument("--maximum-cutoffs-per-image", type=_positive_int, default=4)
    parser.add_argument("--folds", type=_positive_int, default=5)
    parser.add_argument("--workers", type=_positive_int, default=2)
    parser.add_argument("--maximum-width", type=_positive_int, default=1200)
    parser.add_argument("--minimum-free-gb", type=float, default=45.0)
    parser.add_argument(
        "--grouped-validation",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Keep every cutoff from one screenshot family in one held-out fold.",
    )
    parser.add_argument(
        "--leakage-audit",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fail the run when physical masking, freeze order, or family grouping fails.",
    )
    parser.add_argument(
        "--render-gallery",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser


def _compact_summary(summary: dict[str, object]) -> dict[str, object]:
    raw_metrics = summary.get("metrics")
    metrics: Mapping[str, object] = (
        cast(Mapping[str, object], raw_metrics)
        if isinstance(raw_metrics, Mapping)
        else dict[str, object]()
    )
    return {
        "schema_version": summary.get("schema_version"),
        "status": summary.get("status"),
        "scope": summary.get("scope"),
        "images_discovered": summary.get("images_discovered"),
        "images_extracted": summary.get("images_extracted"),
        "images_with_predictions": summary.get("images_with_predictions"),
        "independent_family_count": summary.get("independent_family_count"),
        "masked_cutoff_count": summary.get("masked_cutoff_count"),
        "preparation_failure_count": summary.get("preparation_failure_count"),
        "frozen_prediction_count": summary.get("frozen_prediction_count"),
        "scorecard_count": metrics.get("scorecard_count"),
        "metrics": raw_metrics,
        "leakage_audit": summary.get("leakage_audit"),
        "gallery_path": summary.get("gallery_path"),
        "free_gb_after": summary.get("free_gb_after"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.grouped_validation or not args.leakage_audit:
        raise SystemExit(
            "Grouped validation and leakage audit are mandatory for a causal replay."
        )
    if (args.image is None) != (args.mask_rect is None):
        raise SystemExit("--image and --mask-rect must be supplied together")
    if args.image is not None:
        summary = run_manual_masked_future_replay_v3(
            image_path=args.image,
            mask_rectangle=args.mask_rect,
            output_dir=args.output_dir,
            report_path=args.report,
            horizons=args.horizons,
            minimum_prefix_candles=args.minimum_prefix_candles,
            minimum_free_gb=args.minimum_free_gb,
            render_gallery=args.render_gallery,
        )
    else:
        summary = run_pure_masked_future_replay_v3(
            roots=(args.memory_buy, args.memory_sell),
            output_dir=args.output_dir,
            report_path=args.report,
            horizons=args.horizons,
            minimum_prefix_candles=args.minimum_prefix_candles,
            minimum_hidden_candles=args.minimum_hidden_candles,
            cutoff_stride=args.cutoff_stride,
            maximum_cutoffs_per_image=args.maximum_cutoffs_per_image,
            folds=args.folds,
            workers=args.workers,
            maximum_width=args.maximum_width,
            minimum_free_gb=args.minimum_free_gb,
            render_gallery=args.render_gallery,
        )
    print(json.dumps(_compact_summary(summary), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
