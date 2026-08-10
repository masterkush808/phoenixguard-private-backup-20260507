"""Leakage assertions for pure screenshot masked-future replay."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from phoenixguard.study.masked_image_region_v3 import mask_proof_passes_v3


PURE_LEAKAGE_AUDIT_SCHEMA_VERSION = "PG_PURE_MASKED_FUTURE_LEAKAGE_AUDIT_V3"
FORBIDDEN_FEATURE_KEYS: tuple[str, ...] = (
    "actual",
    "target",
    "future_suffix",
    "source_bucket",
    "folder_label",
    "trade_outcome",
)
FORBIDDEN_ARTIFACT_TOKENS: tuple[str, ...] = (
    "execution_packet",
    "allowance_package",
    "mt4_bridge",
    "shooter_action",
)


class PureMaskedFutureLeakageError(RuntimeError):
    pass


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _walk_keys(value: object) -> list[str]:
    output: list[str] = []
    if isinstance(value, Mapping):
        row = cast(Mapping[object, object], value)
        for key, item in row.items():
            output.append(str(key).lower())
            output.extend(_walk_keys(item))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in cast(Sequence[object], value):
            output.extend(_walk_keys(item))
    return output


def audit_pure_masked_future_v3(
    cases: Sequence[Mapping[str, Any]],
    *,
    run_dir: str | Path,
) -> dict[str, Any]:
    failures: list[str] = []
    family_folds: dict[str, set[int]] = defaultdict(set)
    image_folds: dict[str, set[int]] = defaultdict(set)
    mask_count = 0
    frozen_count = 0
    for case in cases:
        family = str(case.get("family_id") or "")
        image_hash = str(case.get("image_hash") or "")
        fold_value = case.get("fold", -1)
        fold = int(fold_value) if fold_value is not None else -1
        family_folds[family].add(fold)
        image_folds[image_hash].add(fold)
        proof = _mapping(case.get("mask_proof"))
        if mask_proof_passes_v3(proof):
            mask_count += 1
        else:
            failures.append(f"MASK_PROOF_FAILED:{case.get('cutoff_id')}")
        feature_keys = _walk_keys(case.get("prediction_context"))
        if any(token in key for key in feature_keys for token in FORBIDDEN_FEATURE_KEYS):
            failures.append(f"FORBIDDEN_FEATURE_KEY:{case.get('cutoff_id')}")
        prediction = _mapping(case.get("prediction"))
        prediction_keys = _walk_keys(prediction)
        if any(key.startswith("actual") or key == "target" for key in prediction_keys):
            failures.append(f"ACTUAL_VALUE_IN_FROZEN_PREDICTION:{case.get('cutoff_id')}")
        frozen = int(case.get("prediction_frozen_epoch_ms", 0) or 0)
        revealed = int(case.get("reveal_started_epoch_ms", 0) or 0)
        if frozen > 0 and revealed >= frozen:
            frozen_count += 1
        else:
            failures.append(f"PREDICTION_NOT_FROZEN_BEFORE_REVEAL:{case.get('cutoff_id')}")
        masked_path = Path(str(case.get("masked_path") or ""))
        prediction_path = Path(str(case.get("prediction_path") or ""))
        scorecard_path = Path(str(case.get("scorecard_path") or ""))
        if not masked_path.is_file() or not prediction_path.is_file() or not scorecard_path.is_file():
            failures.append(f"MISSING_CASE_ARTIFACT:{case.get('cutoff_id')}")
    grouped_families = all(
        len(values) == 1 and -1 not in values for values in family_folds.values()
    )
    grouped_images = all(
        len(values) == 1 and -1 not in values for values in image_folds.values()
    )
    if not grouped_families:
        failures.append("NEAR_DUPLICATE_FAMILY_CROSSED_FOLDS")
    if not grouped_images:
        failures.append("IMAGE_CUTOFFS_CROSSED_FOLDS")
    forbidden_artifacts = [
        str(path)
        for path in Path(run_dir).rglob("*")
        if path.is_file()
        and any(token in path.name.lower() for token in FORBIDDEN_ARTIFACT_TOKENS)
    ]
    if forbidden_artifacts:
        failures.append("FORBIDDEN_NON_RESEARCH_ARTIFACT_CREATED")
    checks = {
        "future_pixels_hidden_before_prediction": mask_count == len(cases),
        "future_candles_absent_from_features": not any(
            item.startswith("FORBIDDEN_FEATURE_KEY") for item in failures
        ),
        "future_overlays_absent_from_features": mask_count == len(cases),
        "folder_label_not_used_as_target": not any(
            "source_bucket" in key or "folder_label" in key
            for case in cases
            for key in _walk_keys(case.get("prediction_context"))
        ),
        "prediction_written_before_reveal": frozen_count == len(cases),
        "feature_digest_exists_before_target_reveal": all(
            bool(_mapping(case.get("prediction")).get("feature_digest")) for case in cases
        ),
        "all_cutoffs_from_one_family_in_one_fold": grouped_families,
        "all_cutoffs_from_one_image_in_one_fold": grouped_images,
        "masked_image_hash_differs_from_full_image_hash": all(
            str(_mapping(case.get("mask_proof")).get("original_pixel_hash"))
            != str(_mapping(case.get("mask_proof")).get("masked_pixel_hash"))
            for case in cases
        ),
        "hidden_region_is_uniformly_obscured": mask_count == len(cases),
        "forbidden_artifacts_absent": not forbidden_artifacts,
    }
    failures.extend(key for key, passed in checks.items() if not passed and key not in failures)
    unique_failures = list(dict.fromkeys(failures))
    return {
        "schema_version": PURE_LEAKAGE_AUDIT_SCHEMA_VERSION,
        "status": "PASS" if not unique_failures else "FAIL",
        "checks": checks,
        "failures": unique_failures,
        "case_count": len(cases),
        "family_count": len(family_folds),
        "image_count": len(image_folds),
        "forbidden_artifacts": forbidden_artifacts,
    }


def assert_pure_masked_future_leakage_v3(audit: Mapping[str, Any]) -> None:
    if str(audit.get("status") or "") != "PASS":
        failures = ", ".join(str(item) for item in audit.get("failures", []))
        raise PureMaskedFutureLeakageError(
            f"PG_PURE_MASKED_FUTURE_LEAKAGE_FAILED: {failures}"
        )


__all__ = [
    "PURE_LEAKAGE_AUDIT_SCHEMA_VERSION",
    "PureMaskedFutureLeakageError",
    "assert_pure_masked_future_leakage_v3",
    "audit_pure_masked_future_v3",
]
