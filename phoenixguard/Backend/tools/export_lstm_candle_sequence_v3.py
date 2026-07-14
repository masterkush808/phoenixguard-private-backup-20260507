from __future__ import annotations

from _bootstrap import ensure_backend_paths

ensure_backend_paths()

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT_BOOTSTRAP = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_BOOTSTRAP))

from phoenixguard.decision.lstm_candle_sequence_contributor_v3 import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_METRICS_PATH,
    DEFAULT_MODEL_PATH,
)


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Export the production-ready PhoenixGuard V3 computer-vision LSTM bundle.")
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--metrics-path", type=Path, default=DEFAULT_METRICS_PATH)
    parser.add_argument("--export-dir", type=Path, default=Path("models/exports/lstm_candle_sequence_v3"))
    parser.add_argument("--allow-untrained", action="store_true", help="Export non-production artifacts for diagnostics only.")
    args = parser.parse_args()

    config = _load(args.config_path)
    metrics = _load(args.metrics_path)
    missing = [
        str(path)
        for path in (args.model_path, args.config_path, args.metrics_path)
        if not path.exists()
    ]
    production_ready = bool(config.get("production_ready") and metrics.get("production_ready"))
    if missing or (not production_ready and not args.allow_untrained):
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "lstm_artifacts_not_exportable",
                    "missing": missing,
                    "production_ready": production_ready,
                    "next_step": "Train/evaluate a real model, or pass --allow-untrained for diagnostics-only export.",
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 2

    args.export_dir.mkdir(parents=True, exist_ok=True)
    for path in (args.model_path, args.config_path, args.metrics_path):
        shutil.copy2(path, args.export_dir / path.name)
    manifest: dict[str, Any] = {
        "schema_version": "PG_LSTM_CANDLE_PATH_EXPORT_V3",
        "stack_version": "PHOENIXGUARD_V3",
        "modality": "COMPUTER_VISION",
        "production_ready": production_ready,
        "model_path": str(args.export_dir / args.model_path.name),
        "config_path": str(args.export_dir / args.config_path.name),
        "metrics_path": str(args.export_dir / args.metrics_path.name),
        "mode": "production" if production_ready else "diagnostics_only",
    }
    manifest_path = args.export_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({"ok": True, "manifest_path": str(manifest_path), **manifest}, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
