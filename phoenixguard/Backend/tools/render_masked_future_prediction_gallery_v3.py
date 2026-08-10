from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from phoenixguard.study.pure_masked_future_gallery_v3 import (
    render_pure_masked_future_gallery_v3,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render the static masked-prediction versus revealed-actual gallery."
    )
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=REPOSITORY_ROOT / ".codex_runtime" / "pure_masked_future",
    )
    parser.add_argument("--output", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = args.output or args.run_dir / "gallery" / "index.html"
    rendered = render_pure_masked_future_gallery_v3(args.run_dir, output)
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
