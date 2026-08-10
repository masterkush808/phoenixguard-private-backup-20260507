from __future__ import annotations

import argparse
import json
from pathlib import Path


KEEP_NAMES = {
    "audit.json",
    "predictions.jsonl.gz",
    "summary.json",
    "PG_OPTIMIZED_HIDDEN_STATE_MODEL_V3.json.gz",
}
REMOVABLE_PREFIXES = ("fold_", "failed_", "checkpoint_", ".tmp", "tmp_")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default=".codex_runtime/optimized_masked_future")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--keep-final-artifacts", action="store_true")
    args = parser.parse_args()
    root = Path(args.output_dir).resolve()
    expected = (Path.cwd() / ".codex_runtime" / "optimized_masked_future").resolve()
    if root != expected and expected not in root.parents:
        raise SystemExit(f"PG_CLEANUP_REFUSED_OUTSIDE_RUNTIME: {root}")
    candidates = [
        path for path in root.rglob("*")
        if path.is_file()
        and path.name not in KEEP_NAMES
        and path.name.startswith(REMOVABLE_PREFIXES)
    ]
    removed: list[str] = []
    if args.apply:
        for path in candidates:
            path.unlink(missing_ok=True)
            removed.append(str(path))
    print(json.dumps({
        "status": "APPLIED" if args.apply else "DRY_RUN",
        "root": str(root),
        "candidate_count": len(candidates),
        "removed_count": len(removed),
        "protected": sorted(KEEP_NAMES),
    }, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
