from __future__ import annotations

from _bootstrap import ensure_backend_paths

ensure_backend_paths()

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, cast

PROJECT_ROOT_BOOTSTRAP = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT_BOOTSTRAP))

from phoenixguard.decision.lstm_candle_sequence_contributor_v3 import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_METRICS_PATH,
    DEFAULT_MODEL_PATH,
    DEFAULT_SEQUENCE_LENGTH,
    FEATURE_SCHEMA,
    create_lstm_candle_sequence_model,
)


def _load(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return dict(cast(Mapping[str, Any], payload)) if isinstance(payload, Mapping) else {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate staged LSTM candle-sequence V3 artifacts.")
    parser.add_argument("--model-path", type=Path, default=DEFAULT_MODEL_PATH)
    parser.add_argument("--config-path", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--metrics-path", type=Path, default=DEFAULT_METRICS_PATH)
    args = parser.parse_args()

    config = _load(args.config_path)
    metrics = _load(args.metrics_path)
    inference_ok = False
    inference_error = ""
    if args.model_path.exists() and config:
        try:
            import torch

            model = create_lstm_candle_sequence_model(
                input_dim=int(config.get("input_dim", len(FEATURE_SCHEMA)) or len(FEATURE_SCHEMA)),
                hidden_dim=int(config.get("hidden_dim", 48) or 48),
                num_layers=int(config.get("num_layers", 1) or 1),
                dropout=float(config.get("dropout", 0.0) or 0.0),
            )
            loaded: object = torch.load(args.model_path, map_location="cpu", weights_only=False)
            loaded_map = dict(cast(Mapping[str, Any], loaded)) if isinstance(loaded, Mapping) else {}
            state_dict = loaded_map.get("state_dict", loaded)
            model.load_state_dict(cast(Mapping[str, Any], state_dict))
            model.eval()
            sequence_length = int(config.get("sequence_length", DEFAULT_SEQUENCE_LENGTH) or DEFAULT_SEQUENCE_LENGTH)
            with torch.inference_mode():
                outputs = model(torch.zeros((1, sequence_length, len(FEATURE_SCHEMA)), dtype=torch.float32))
            inference_ok = bool(outputs["next_1_logits"].shape[-1] == 2 and outputs["next_2_logits"].shape[-1] == 2)
        except Exception as exc:
            inference_error = str(exc)
    checks: dict[str, bool] = {
        "model_exists": args.model_path.exists(),
        "config_exists": bool(config),
        "metrics_exists": bool(metrics),
        "feature_schema_exists": bool(config.get("feature_schema")),
        "model_version_exists": bool(config.get("model_version") or metrics.get("model_version")),
        "inference_test_passes": inference_ok,
        "evaluation_metrics_exist": all(
            key in metrics
            for key in (
                "next_1_direction_accuracy",
                "next_2_direction_accuracy",
                "continuation_auc",
                "reversal_auc",
                "calibration_error",
            )
        ),
        "production_ready": bool(config.get("production_ready") and metrics.get("production_ready")),
    }
    ok = all(value for key, value in checks.items() if key != "production_ready")
    payload: dict[str, object] = {
        "ok": ok,
        "production_ready": checks["production_ready"],
        "checks": checks,
        "config_path": str(args.config_path),
        "inference_error": inference_error,
        "metrics_path": str(args.metrics_path),
        "model_path": str(args.model_path),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
