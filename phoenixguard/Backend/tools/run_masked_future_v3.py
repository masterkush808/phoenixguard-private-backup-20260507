from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Sequence

from phoenixguard.simulation.masked_future_v3 import DEFAULT_RESERVE_GB, run_masked_future_replay_v3
from phoenixguard.study.masked_future_behavior_v3 import DEFAULT_HORIZONS, DEFAULT_MASKED_FUTURE_MODEL_NAME


DEFAULT_ROOTS = (
    Path("808 Memory/BUYS-20260224T225615Z-1-001/BUYS"),
    Path("808 Memory/SELLS-20260224T225719Z-1-001/SELLS"),
)


def _horizons(value: str) -> tuple[int, ...]:
    return tuple(sorted({max(1, int(item.strip())) for item in value.split(",") if item.strip()}))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run grouped leak-free V3 masked-future replay over every historical chart image.")
    parser.add_argument("--root", action="append", default=[], help="Historical image root. Repeat for multiple roots.")
    parser.add_argument("--output-dir", default=".codex_runtime/masked_future_v3/final")
    parser.add_argument("--cache", default=".codex_runtime/masked_future_v3/corpus_cache.jsonl")
    parser.add_argument("--model-path", default=str(Path("Backend/src/phoenixguard") / DEFAULT_MASKED_FUTURE_MODEL_NAME))
    parser.add_argument("--horizons", default=",".join(str(value) for value in DEFAULT_HORIZONS))
    parser.add_argument("--minimum-prefix", type=int, default=24)
    parser.add_argument("--stride", type=int, default=2)
    parser.add_argument("--folds", type=int, default=5)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--maximum-width", type=int, default=1600)
    parser.add_argument("--minimum-free-gb", type=float, default=DEFAULT_RESERVE_GB)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    roots = [Path(value) for value in args.root] if args.root else list(DEFAULT_ROOTS)
    summary = run_masked_future_replay_v3(
        roots=roots,
        output_dir=Path(args.output_dir),
        cache_path=Path(args.cache),
        model_path=Path(args.model_path),
        horizons=_horizons(args.horizons),
        minimum_prefix=max(4, args.minimum_prefix),
        stride=max(1, args.stride),
        folds=max(2, args.folds),
        workers=max(1, args.workers),
        minimum_free_gb=max(0.0, args.minimum_free_gb),
        maximum_width=max(0, args.maximum_width),
    )
    print(json.dumps(summary, indent=2, ensure_ascii=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
