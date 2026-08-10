from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, cast


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    row = cast(Mapping[object, Any], value)
    return {str(key): item for key, item in row.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-summary", default=".codex_runtime/masked_future_v3/final/summary.json")
    parser.add_argument("--new-run", default=".codex_runtime/optimized_masked_future")
    parser.add_argument("--output", default="reports/MASKED_FUTURE_MODEL_COMPARISON_V3.md")
    args = parser.parse_args()
    old = _mapping(json.loads(Path(args.old_summary).read_text(encoding="utf-8")))
    new = _mapping(json.loads((Path(args.new_run) / "summary.json").read_text(encoding="utf-8")))
    old_cv = _mapping(old.get("cross_validation"))
    old_promotion = _mapping(old_cv.get("promotion"))
    calibration = _mapping(new.get("calibration"))
    promotion = _mapping(new.get("promotion"))
    report = f"""# Masked-Future V3 Model Comparison

| Measure | Empirical V3 | Optimized selective V3 |
|---|---:|---:|
| Broad 13/21 accuracy | {100 * float(old_promotion.get("primary_accuracy") or 0):.2f}% | Event conditioned |
| Visible pullback | {100 * float(old_promotion.get("visible_pullback_accuracy") or 0):.2f}% | {100 * float(new.get("visible_pullback_accuracy") or 0):.2f}% |
| Target-before-invalidation precision | Not labelled | {100 * float(new.get("target_before_invalidation_precision") or 0):.2f}% |
| High-confidence precision | Not measured | {100 * float(new.get("high_confidence_selective_precision") or 0):.2f}% |
| High-confidence coverage | Not measured | {100 * float(new.get("high_confidence_coverage") or 0):.2f}% |
| Brier | broad horizons | {float(calibration.get("brier") or 0):.6f} |
| Recommendation | Hidden-state prior | {promotion.get("reason", "UNKNOWN")} |

The optimized score cannot replace the broad empirical model. It is a calibrated
event and trade-path contributor layered on top of that prior.
"""
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report, encoding="utf-8")
    print(json.dumps({"status": "COMPARED", "output": str(output)}, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
