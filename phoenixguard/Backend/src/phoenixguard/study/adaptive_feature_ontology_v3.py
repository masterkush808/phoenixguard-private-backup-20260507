"""Bounded, auditable adaptive feature ontology for PhoenixGuard V3 studies.

Features are proposed in a shadow namespace and cannot become public study
features until a deterministic temporal-safety gate passes.  The gate is
deliberately named a causal *evaluation* gate because it checks closed-candle
ordering and leakage controls; passing it never establishes causation.

This module is intentionally integration-free.  It grants no decision, entry,
order, or execution authority.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import json
import math
from typing import Any, cast


ADAPTIVE_FEATURE_ONTOLOGY_SCHEMA_VERSION = "PG_ADAPTIVE_FEATURE_ONTOLOGY_V3"
FEATURE_PROMOTION_GATE_SCHEMA_VERSION = "PG_FEATURE_PROMOTION_GATE_V3"
DEFAULT_MAX_FEATURES = 1_000_000
DEFAULT_MAX_REVISIONS_PER_FEATURE = 1_000_000
MAX_FEATURE_DOCUMENT_BYTES = 32 * 1024
MAX_FEATURE_EVIDENCE_CANDLES = 1_000_000


class AdaptiveFeatureOntologyValidationError(ValueError):
    """Raised when an ontology transition would violate the V3 contract."""


def _identity(value: object, *, field: str, maximum: int) -> str:
    text = " ".join(str(value or "").strip().upper().split())
    if not text:
        raise AdaptiveFeatureOntologyValidationError(f"{field} is required")
    if len(text) > maximum:
        raise AdaptiveFeatureOntologyValidationError(
            f"{field} exceeds {maximum} characters"
        )
    return text


def _integer(value: object, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise AdaptiveFeatureOntologyValidationError(
            f"{field} must be an integer >= {minimum}"
        )
    try:
        numeric = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise AdaptiveFeatureOntologyValidationError(
            f"{field} must be an integer >= {minimum}"
        ) from exc
    if not math.isfinite(numeric) or not numeric.is_integer() or numeric < minimum:
        raise AdaptiveFeatureOntologyValidationError(
            f"{field} must be an integer >= {minimum}"
        )
    return int(numeric)


def _finite(value: object, *, field: str, low: float, high: float) -> float:
    if isinstance(value, bool):
        raise AdaptiveFeatureOntologyValidationError(
            f"{field} must be finite and in [{low}, {high}]"
        )
    try:
        numeric = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise AdaptiveFeatureOntologyValidationError(
            f"{field} must be finite and in [{low}, {high}]"
        ) from exc
    if not math.isfinite(numeric) or numeric < low or numeric > high:
        raise AdaptiveFeatureOntologyValidationError(
            f"{field} must be finite and in [{low}, {high}]"
        )
    return numeric


def _bounded_document(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise AdaptiveFeatureOntologyValidationError(f"{field} must be a mapping")
    document = deepcopy(dict(cast(Mapping[str, Any], value)))
    try:
        encoded = json.dumps(
            document,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise AdaptiveFeatureOntologyValidationError(
            f"{field} must contain finite JSON values"
        ) from exc
    if len(encoded) > MAX_FEATURE_DOCUMENT_BYTES:
        raise AdaptiveFeatureOntologyValidationError(
            f"{field} exceeds {MAX_FEATURE_DOCUMENT_BYTES} bytes"
        )
    return document


def _candle_ids(value: object) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        raise AdaptiveFeatureOntologyValidationError(
            "closed_candle_ids must be a sequence"
        )
    rows = list(cast(Sequence[object], value))
    if not rows or len(rows) > MAX_FEATURE_EVIDENCE_CANDLES:
        raise AdaptiveFeatureOntologyValidationError(
            "closed_candle_ids must contain between 1 and "
            f"{MAX_FEATURE_EVIDENCE_CANDLES} identities"
        )
    result = [
        _identity(row, field=f"closed_candle_ids[{index}]", maximum=256)
        for index, row in enumerate(rows)
    ]
    if len(set(result)) != len(result):
        raise AdaptiveFeatureOntologyValidationError(
            "closed_candle_ids must be unique"
        )
    return result


def _safety_contract() -> dict[str, Any]:
    return {
        "study_only": True,
        "observation_only": True,
        "causal": False,
        "execution_authority": False,
        "grants_entry_permission": False,
        "grants_execution_permission": False,
    }


class AdaptiveFeatureOntologyV3:
    """In-memory bounded feature registry with immutable revision history."""

    def __init__(
        self,
        *,
        symbol: object = "UNSCOPED",
        timeframe: object = "UNSCOPED",
        max_features: int = DEFAULT_MAX_FEATURES,
        max_revisions_per_feature: int = DEFAULT_MAX_REVISIONS_PER_FEATURE,
        minimum_support: int = 64,
        minimum_holdout_support: int = 24,
        minimum_independent_partitions: int = 3,
        minimum_stability_score: float = 0.70,
        minimum_effect_size: float = 0.15,
        maximum_adjusted_p_value: float = 0.05,
    ) -> None:
        self.symbol = _identity(symbol, field="symbol", maximum=64)
        self.timeframe = _identity(timeframe, field="timeframe", maximum=32)
        self.max_features = _integer(
            max_features, field="max_features", minimum=1
        )
        self.max_revisions_per_feature = _integer(
            max_revisions_per_feature,
            field="max_revisions_per_feature",
            minimum=2,
        )
        if self.max_features > DEFAULT_MAX_FEATURES:
            raise AdaptiveFeatureOntologyValidationError(
                f"max_features cannot exceed {DEFAULT_MAX_FEATURES}"
            )
        if self.max_revisions_per_feature > 1_000_000:
            raise AdaptiveFeatureOntologyValidationError(
                "max_revisions_per_feature cannot exceed 1000000"
            )
        self.minimum_support = _integer(
            minimum_support, field="minimum_support", minimum=1
        )
        self.minimum_holdout_support = _integer(
            minimum_holdout_support,
            field="minimum_holdout_support",
            minimum=1,
        )
        self.minimum_independent_partitions = _integer(
            minimum_independent_partitions,
            field="minimum_independent_partitions",
            minimum=1,
        )
        self.minimum_stability_score = _finite(
            minimum_stability_score,
            field="minimum_stability_score",
            low=0.0,
            high=1.0,
        )
        self.minimum_effect_size = _finite(
            minimum_effect_size,
            field="minimum_effect_size",
            low=0.0,
            high=1.0,
        )
        self.maximum_adjusted_p_value = _finite(
            maximum_adjusted_p_value,
            field="maximum_adjusted_p_value",
            low=0.0,
            high=1.0,
        )
        if self.maximum_adjusted_p_value <= 0.0:
            raise AdaptiveFeatureOntologyValidationError(
                "maximum_adjusted_p_value must be greater than zero"
            )
        self._features: dict[str, dict[str, Any]] = {}
        self._ontology_version = 0

    def _next_version(self) -> int:
        self._ontology_version += 1
        return self._ontology_version

    @classmethod
    def from_snapshot(
        cls,
        snapshot: Mapping[str, Any],
        *,
        symbol: object,
        timeframe: object,
    ) -> "AdaptiveFeatureOntologyV3":
        """Restore one bounded pair-scoped ontology from its audit snapshot."""

        source: dict[str, Any] = deepcopy(dict(snapshot))
        if source.get("schema_version") != ADAPTIVE_FEATURE_ONTOLOGY_SCHEMA_VERSION:
            raise AdaptiveFeatureOntologyValidationError(
                "ontology snapshot schema is not PhoenixGuard V3"
            )
        scope = source.get("scope")
        if not isinstance(scope, Mapping):
            raise AdaptiveFeatureOntologyValidationError("ontology snapshot scope is required")
        scope_row = cast(Mapping[str, Any], scope)
        expected_symbol = _identity(symbol, field="symbol", maximum=64)
        expected_timeframe = _identity(timeframe, field="timeframe", maximum=32)
        if _identity(scope_row.get("symbol"), field="scope.symbol", maximum=64) != expected_symbol:
            raise AdaptiveFeatureOntologyValidationError("ontology snapshot symbol mismatch")
        if _identity(scope_row.get("timeframe"), field="scope.timeframe", maximum=32) != expected_timeframe:
            raise AdaptiveFeatureOntologyValidationError("ontology snapshot timeframe mismatch")
        capacity = source.get("capacity")
        policy = source.get("gate_policy")
        if not isinstance(capacity, Mapping) or not isinstance(policy, Mapping):
            raise AdaptiveFeatureOntologyValidationError(
                "ontology snapshot capacity and gate policy are required"
            )
        capacity_row = cast(Mapping[str, Any], capacity)
        policy_row = cast(Mapping[str, Any], policy)
        ontology = cls(
            symbol=expected_symbol,
            timeframe=expected_timeframe,
            max_features=_integer(
                capacity_row.get("max_features"),
                field="capacity.max_features",
                minimum=1,
            ),
            max_revisions_per_feature=_integer(
                capacity_row.get("max_revisions_per_feature"),
                field="capacity.max_revisions_per_feature",
                minimum=2,
            ),
            minimum_support=_integer(
                policy_row.get("minimum_support"), field="minimum_support", minimum=1
            ),
            minimum_holdout_support=_integer(
                policy_row.get("minimum_holdout_support"),
                field="minimum_holdout_support",
                minimum=1,
            ),
            minimum_independent_partitions=_integer(
                policy_row.get("minimum_independent_partitions"),
                field="minimum_independent_partitions",
                minimum=1,
            ),
            minimum_stability_score=_finite(
                policy_row.get("minimum_stability_score"),
                field="minimum_stability_score",
                low=0.0,
                high=1.0,
            ),
            minimum_effect_size=_finite(
                policy_row.get("minimum_effect_size"),
                field="minimum_effect_size",
                low=0.0,
                high=1.0,
            ),
            maximum_adjusted_p_value=_finite(
                policy_row.get("maximum_adjusted_p_value"),
                field="maximum_adjusted_p_value",
                low=0.0,
                high=1.0,
            ),
        )
        raw_features = source.get("features")
        if not isinstance(raw_features, Sequence) or isinstance(
            raw_features, (str, bytes, bytearray)
        ):
            raise AdaptiveFeatureOntologyValidationError(
                "ontology snapshot features must be a sequence"
            )
        features: dict[str, dict[str, Any]] = {}
        maximum_version = 0
        for raw_feature in cast(Sequence[object], raw_features):
            if not isinstance(raw_feature, Mapping):
                raise AdaptiveFeatureOntologyValidationError(
                    "ontology snapshot feature must be a mapping"
                )
            feature: dict[str, Any] = deepcopy(
                dict(cast(Mapping[str, Any], raw_feature))
            )
            feature_id = _identity(
                feature.get("feature_id"), field="feature_id", maximum=128
            )
            if feature_id in features:
                raise AdaptiveFeatureOntologyValidationError(
                    "ontology snapshot contains duplicate feature ids"
                )
            revisions = feature.get("revisions")
            if not isinstance(revisions, Sequence) or isinstance(
                revisions, (str, bytes, bytearray)
            ):
                raise AdaptiveFeatureOntologyValidationError(
                    "ontology snapshot revisions must be a sequence"
                )
            revision_rows: list[dict[str, Any]] = []
            for row in cast(Sequence[object], revisions):
                if not isinstance(row, Mapping):
                    raise AdaptiveFeatureOntologyValidationError(
                        "ontology snapshot revision must be a mapping"
                    )
                revision_rows.append(
                    deepcopy(dict(cast(Mapping[str, Any], row)))
                )
            if not revision_rows or len(revision_rows) > ontology.max_revisions_per_feature:
                raise AdaptiveFeatureOntologyValidationError(
                    "ontology snapshot revision capacity is invalid"
                )
            for index, revision in enumerate(revision_rows, start=1):
                if _integer(revision.get("revision"), field="revision", minimum=1) != index:
                    raise AdaptiveFeatureOntologyValidationError(
                        "ontology snapshot revisions are not contiguous"
                    )
                version = _integer(
                    revision.get("ontology_version"),
                    field="ontology_version",
                    minimum=1,
                )
                maximum_version = max(maximum_version, version)
                if revision.get("study_only") is not True or revision.get(
                    "execution_authority"
                ) is not False:
                    raise AdaptiveFeatureOntologyValidationError(
                        "ontology snapshot revision violates the study-only contract"
                    )
                evidence = revision.get("evidence")
                if not isinstance(evidence, Mapping):
                    raise AdaptiveFeatureOntologyValidationError(
                        "ontology snapshot revision evidence is required"
                    )
                evidence_row = cast(Mapping[str, Any], evidence)
                if _identity(
                    evidence_row.get("symbol"), field="evidence.symbol", maximum=64
                ) != expected_symbol or _identity(
                    evidence_row.get("timeframe"),
                    field="evidence.timeframe",
                    maximum=32,
                ) != expected_timeframe:
                    raise AdaptiveFeatureOntologyValidationError(
                        "ontology snapshot revision scope mismatch"
                    )
                _candle_ids(evidence_row.get("closed_candle_ids"))
            current_revision = _integer(
                feature.get("current_revision"),
                field="current_revision",
                minimum=1,
            )
            if current_revision != len(revision_rows):
                raise AdaptiveFeatureOntologyValidationError(
                    "ontology snapshot current revision is invalid"
                )
            current = revision_rows[-1]
            if feature.get("status") != current.get("status"):
                raise AdaptiveFeatureOntologyValidationError(
                    "ontology snapshot feature status is inconsistent"
                )
            feature["feature_id"] = feature_id
            feature["revisions"] = revision_rows
            features[feature_id] = feature
        declared_version = _integer(
            source.get("ontology_version"),
            field="ontology_version",
            minimum=0,
        )
        if declared_version != maximum_version:
            raise AdaptiveFeatureOntologyValidationError(
                "ontology snapshot version does not match revisions"
            )
        if len(features) > ontology.max_features:
            raise AdaptiveFeatureOntologyValidationError(
                "ontology snapshot exceeds feature capacity"
            )
        ontology._features = features
        ontology._ontology_version = declared_version
        return ontology

    def _feature(self, feature_id: object) -> tuple[str, dict[str, Any]]:
        canonical = _identity(feature_id, field="feature_id", maximum=128)
        feature = self._features.get(canonical)
        if feature is None:
            raise AdaptiveFeatureOntologyValidationError(
                f"unknown feature_id: {canonical}"
            )
        return canonical, feature

    def _append_revision(
        self,
        feature: dict[str, Any],
        revision: dict[str, Any],
    ) -> None:
        revisions = cast(list[dict[str, Any]], feature["revisions"])
        if len(revisions) >= self.max_revisions_per_feature:
            raise AdaptiveFeatureOntologyValidationError(
                "feature revision capacity reached; archive explicitly before "
                "continuing"
            )
        revision["revision"] = len(revisions) + 1
        revision["ontology_version"] = self._next_version()
        revisions.append(revision)
        feature["current_revision"] = revision["revision"]
        feature["status"] = revision["status"]

    def propose_shadow_feature(
        self,
        *,
        feature_id: object,
        definition: Mapping[str, Any],
        derivation: Mapping[str, Any],
        closed_candle_ids: Sequence[object],
        coordinate_space: object,
        order_domain: object,
    ) -> dict[str, Any]:
        """Create revision one in the non-public shadow namespace."""

        canonical_id = _identity(feature_id, field="feature_id", maximum=128)
        if canonical_id in self._features:
            raise AdaptiveFeatureOntologyValidationError(
                f"feature_id already exists: {canonical_id}"
            )
        if len(self._features) >= self.max_features:
            raise AdaptiveFeatureOntologyValidationError(
                "feature capacity reached; ontology does not evict audit history"
            )
        definition_row = _bounded_document(definition, field="definition")
        derivation_row = _bounded_document(derivation, field="derivation")
        if not str(derivation_row.get("algorithm_id") or "").strip():
            raise AdaptiveFeatureOntologyValidationError(
                "derivation.algorithm_id is required"
            )
        if not str(derivation_row.get("algorithm_version") or "").strip():
            raise AdaptiveFeatureOntologyValidationError(
                "derivation.algorithm_version is required"
            )
        evidence_ids = _candle_ids(closed_candle_ids)
        coordinate = _identity(
            coordinate_space, field="coordinate_space", maximum=64
        )
        order = _identity(order_domain, field="order_domain", maximum=64)
        feature: dict[str, Any] = {
            "feature_id": canonical_id,
            "namespace": "SHADOW",
            "status": "SHADOW",
            "current_revision": 0,
            "revisions": [],
        }
        revision = {
            "transition": "PROPOSED",
            "status": "SHADOW",
            "namespace": "SHADOW",
            "definition": definition_row,
            "derivation": derivation_row,
            "evidence": {
                "closed_candle_ids": evidence_ids,
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "coordinate_space": coordinate,
                "order_domain": order,
                "closed_candle_only": True,
            },
            "promotion_gate": None,
            **_safety_contract(),
        }
        self._append_revision(feature, revision)
        self._features[canonical_id] = feature
        return self.get_feature(canonical_id)

    def evaluate_promotion_gate(
        self,
        feature_id: object,
        *,
        evaluation: Mapping[str, Any],
        closed_candle_ids: Sequence[object] | None = None,
        coordinate_space: object | None = None,
        order_domain: object | None = None,
    ) -> dict[str, Any]:
        """Record an auditable temporal-safety and holdout evaluation."""

        canonical_id, feature = self._feature(feature_id)
        if feature["status"] != "SHADOW":
            raise AdaptiveFeatureOntologyValidationError(
                "only a shadow feature can be evaluated for promotion"
            )
        source = _bounded_document(evaluation, field="evaluation")
        support = _integer(
            source.get("support_count"), field="support_count", minimum=0
        )
        holdout_support = _integer(
            source.get("holdout_support_count"),
            field="holdout_support_count",
            minimum=0,
        )
        partition_count = _integer(
            source.get("independent_partition_count"),
            field="independent_partition_count",
            minimum=0,
        )
        stability = _finite(
            source.get("stability_score"),
            field="stability_score",
            low=0.0,
            high=1.0,
        )
        adjusted_p = _finite(
            source.get("adjusted_p_value"),
            field="adjusted_p_value",
            low=0.0,
            high=1.0,
        )
        effect_size = _finite(
            source.get("effect_size"),
            field="effect_size",
            low=0.0,
            high=1.0,
        )
        checks = {
            "minimum_support": support >= self.minimum_support,
            "minimum_holdout_support": (
                holdout_support >= self.minimum_holdout_support
            ),
            "independent_partition_support": (
                partition_count >= self.minimum_independent_partitions
            ),
            "closed_candle_only": source.get("closed_candle_only") is True,
            "temporal_precedence_verified": (
                source.get("temporal_precedence_verified") is True
            ),
            "future_leakage_absent": (
                source.get("future_leakage_detected") is False
            ),
            "deterministic_derivation": (
                source.get("deterministic_derivation") is True
            ),
            "coordinate_space_preserved": (
                source.get("coordinate_space_preserved") is True
            ),
            "order_domain_preserved": (
                source.get("order_domain_preserved") is True
            ),
            "minimum_stability": stability >= self.minimum_stability_score,
            "minimum_effect_size": effect_size >= self.minimum_effect_size,
            "multiplicity_adjusted_significance": (
                adjusted_p <= self.maximum_adjusted_p_value
            ),
        }
        gate = {
            "schema_version": FEATURE_PROMOTION_GATE_SCHEMA_VERSION,
            "gate_id": "CAUSAL_EVALUATION_SAFETY_GATE_V3",
            "passed": all(checks.values()),
            "checks": checks,
            "measurements": {
                "support_count": support,
                "holdout_support_count": holdout_support,
                "independent_partition_count": partition_count,
                "stability_score": round(stability, 8),
                "effect_size": round(effect_size, 8),
                "adjusted_p_value": round(adjusted_p, 8),
            },
            "contract": {
                "tests_temporal_safety_and_association_stability": True,
                "establishes_causation": False,
                "authorizes_prediction": False,
                **_safety_contract(),
            },
        }
        current = deepcopy(cast(list[dict[str, Any]], feature["revisions"])[-1])
        current.pop("revision", None)
        current.pop("ontology_version", None)
        current["transition"] = "EVALUATED"
        if closed_candle_ids is not None:
            evidence = dict(cast(Mapping[str, Any], current["evidence"]))
            evidence["closed_candle_ids"] = _candle_ids(closed_candle_ids)
            if coordinate_space is not None:
                supplied_coordinate = _identity(
                    coordinate_space,
                    field="coordinate_space",
                    maximum=64,
                )
                if supplied_coordinate != evidence.get("coordinate_space"):
                    raise AdaptiveFeatureOntologyValidationError(
                        "evaluation coordinate_space differs from proposed feature"
                    )
            if order_domain is not None:
                supplied_order = _identity(
                    order_domain,
                    field="order_domain",
                    maximum=64,
                )
                if supplied_order != evidence.get("order_domain"):
                    raise AdaptiveFeatureOntologyValidationError(
                        "evaluation order_domain differs from proposed feature"
                    )
            current["evidence"] = evidence
        current["promotion_gate"] = gate
        self._append_revision(feature, current)
        self._features[canonical_id] = feature
        return self.get_feature(canonical_id)

    def promote(self, feature_id: object) -> dict[str, Any]:
        """Promote only the latest shadow revision whose gate passed."""

        canonical_id, feature = self._feature(feature_id)
        if feature["status"] != "SHADOW":
            raise AdaptiveFeatureOntologyValidationError(
                "only a shadow feature can be promoted"
            )
        current = deepcopy(cast(list[dict[str, Any]], feature["revisions"])[-1])
        raw_gate = current.get("promotion_gate")
        gate = (
            dict(cast(Mapping[str, Any], raw_gate))
            if isinstance(raw_gate, Mapping)
            else {}
        )
        if gate.get("passed") is not True:
            raise AdaptiveFeatureOntologyValidationError(
                "promotion requires a passing latest promotion gate"
            )
        current.pop("revision", None)
        current.pop("ontology_version", None)
        current["transition"] = "PROMOTED"
        current["status"] = "PROMOTED"
        current["namespace"] = "PUBLIC_STUDY"
        self._append_revision(feature, current)
        feature["namespace"] = "PUBLIC_STUDY"
        self._features[canonical_id] = feature
        return self.get_feature(canonical_id)

    def rollback(
        self,
        feature_id: object,
        *,
        target_revision: object,
        reason: object,
    ) -> dict[str, Any]:
        """Append a revision restoring any earlier state without deleting history."""

        canonical_id, feature = self._feature(feature_id)
        target = _integer(target_revision, field="target_revision", minimum=1)
        revisions = cast(list[dict[str, Any]], feature["revisions"])
        if target > len(revisions):
            raise AdaptiveFeatureOntologyValidationError(
                "target_revision does not exist"
            )
        if target == int(feature["current_revision"]):
            raise AdaptiveFeatureOntologyValidationError(
                "target_revision is already current"
            )
        reason_text = " ".join(str(reason or "").strip().split())
        if not reason_text or len(reason_text) > 512:
            raise AdaptiveFeatureOntologyValidationError(
                "rollback reason is required and must not exceed 512 characters"
            )
        restored = deepcopy(revisions[target - 1])
        restored.pop("revision", None)
        restored.pop("ontology_version", None)
        restored["transition"] = "ROLLED_BACK"
        restored["rollback"] = {
            "target_revision": target,
            "reason": reason_text,
        }
        self._append_revision(feature, restored)
        feature["namespace"] = restored["namespace"]
        self._features[canonical_id] = feature
        return self.get_feature(canonical_id)

    def get_feature(self, feature_id: object) -> dict[str, Any]:
        canonical_id, feature = self._feature(feature_id)
        return {
            "schema_version": ADAPTIVE_FEATURE_ONTOLOGY_SCHEMA_VERSION,
            "status": "READY",
            "ontology_version": self._ontology_version,
            "feature": deepcopy(feature),
            "feature_id": canonical_id,
            **_safety_contract(),
        }

    def snapshot(self) -> dict[str, Any]:
        """Return a detached, bounded audit snapshot including shadows."""

        return {
            "schema_version": ADAPTIVE_FEATURE_ONTOLOGY_SCHEMA_VERSION,
            "status": "READY",
            "ontology_version": self._ontology_version,
            "scope": {"symbol": self.symbol, "timeframe": self.timeframe},
            "gate_policy": {
                "minimum_support": self.minimum_support,
                "minimum_holdout_support": self.minimum_holdout_support,
                "minimum_independent_partitions": self.minimum_independent_partitions,
                "minimum_stability_score": self.minimum_stability_score,
                "minimum_effect_size": self.minimum_effect_size,
                "maximum_adjusted_p_value": self.maximum_adjusted_p_value,
            },
            "capacity": {
                "feature_count": len(self._features),
                "max_features": self.max_features,
                "max_revisions_per_feature": self.max_revisions_per_feature,
            },
            "features": [
                deepcopy(self._features[key]) for key in sorted(self._features)
            ],
            **_safety_contract(),
        }

    def public_study_snapshot(self) -> dict[str, Any]:
        """Expose promoted definitions only; shadow features cannot leak out."""

        public_features: list[dict[str, Any]] = []
        for feature_id in sorted(self._features):
            feature = self._features[feature_id]
            if feature.get("status") != "PROMOTED":
                continue
            revisions = cast(list[dict[str, Any]], feature["revisions"])
            current = deepcopy(revisions[int(feature["current_revision"]) - 1])
            public_features.append(
                {
                    "feature_id": feature_id,
                    "revision": current["revision"],
                    "ontology_version": current["ontology_version"],
                    "symbol": self.symbol,
                    "timeframe": self.timeframe,
                    "definition": current["definition"],
                    "derivation": current["derivation"],
                    "coordinate_space": current["evidence"]["coordinate_space"],
                    "order_domain": current["evidence"]["order_domain"],
                    **_safety_contract(),
                }
            )
        return {
            "schema_version": ADAPTIVE_FEATURE_ONTOLOGY_SCHEMA_VERSION,
            "status": "READY",
            "ontology_version": self._ontology_version,
            "scope": {"symbol": self.symbol, "timeframe": self.timeframe},
            "namespace": "PUBLIC_STUDY",
            "features": public_features,
            "shadow_features_excluded": True,
            **_safety_contract(),
        }


__all__ = [
    "ADAPTIVE_FEATURE_ONTOLOGY_SCHEMA_VERSION",
    "DEFAULT_MAX_FEATURES",
    "DEFAULT_MAX_REVISIONS_PER_FEATURE",
    "FEATURE_PROMOTION_GATE_SCHEMA_VERSION",
    "AdaptiveFeatureOntologyV3",
    "AdaptiveFeatureOntologyValidationError",
]
