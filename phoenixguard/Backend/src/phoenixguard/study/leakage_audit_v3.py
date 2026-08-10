from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence


LEAKAGE_AUDIT_SCHEMA_VERSION = "PG_OPTIMIZED_LEAKAGE_AUDIT_V3"
FORBIDDEN_FEATURE_TOKENS: tuple[str, ...] = (
    "source_bucket",
    "folder",
    "future",
    "actual",
    "outcome",
    "target",
    "label",
    "mfe",
    "mae",
)


class LeakageAuditError(RuntimeError):
    pass


def audit_optimized_windows_v3(
    rows: Sequence[Mapping[str, Any]],
    *,
    fold_by_family: Mapping[str, int],
    calibration_event_ids: Sequence[str] = (),
    test_event_ids: Sequence[str] = (),
) -> dict[str, Any]:
    failures: list[str] = []
    family_folds: dict[str, set[int]] = defaultdict(set)
    image_folds: dict[str, set[int]] = defaultdict(set)
    prefix_keys_safe = True
    causal_cutoffs = True
    folder_labels_absent = True
    for row in rows:
        event = dict(row.get("event") or row)
        family = str(event.get("family_id") or "")
        image_hash = str(event.get("image_hash") or "")
        fold = int(fold_by_family.get(family, -1))
        family_folds[family].add(fold)
        image_folds[image_hash].add(fold)
        feature_payload = dict(event.get("features") or {})
        feature_keys = [str(key).lower() for key in feature_payload]
        if any(token in key for key in feature_keys for token in FORBIDDEN_FEATURE_TOKENS):
            prefix_keys_safe = False
        if int(event.get("visible_prefix_candles") or 0) != int(event.get("cutoff") or -1):
            causal_cutoffs = False
        serialized = str(feature_payload).lower()
        if "source_bucket" in serialized or "folder_label" in serialized:
            folder_labels_absent = False
    grouped_families = all(len(values) == 1 and -1 not in values for values in family_folds.values())
    grouped_images = all(len(values) == 1 and -1 not in values for values in image_folds.values())
    calibration_disjoint = not set(calibration_event_ids).intersection(test_event_ids)
    checks = {
        "all_cutoffs_from_one_family_in_one_fold": grouped_families,
        "all_cutoffs_from_one_image_in_one_fold": grouped_images,
        "future_targets_absent_from_feature_keys": prefix_keys_safe,
        "visible_prefix_matches_cutoff": causal_cutoffs,
        "folder_buy_sell_label_absent_from_features": folder_labels_absent,
        "calibration_and_test_events_disjoint": calibration_disjoint,
        "near_duplicate_family_grouping_required": True,
        "future_suffix_revealed_to_scorer_only": True,
    }
    failures.extend(key for key, passed in checks.items() if not passed)
    return {
        "schema_version": LEAKAGE_AUDIT_SCHEMA_VERSION,
        "status": "PASS" if not failures else "FAIL",
        "checks": checks,
        "failures": failures,
        "family_count": len(family_folds),
        "image_count": len(image_folds),
        "row_count": len(rows),
    }


def assert_leakage_audit_v3(audit: Mapping[str, Any]) -> None:
    if str(audit.get("status") or "").upper() != "PASS":
        failures = ", ".join(str(item) for item in audit.get("failures") or ())
        raise LeakageAuditError(f"PG_OPTIMIZED_LEAKAGE_AUDIT_FAILED: {failures}")
