"""Closed-candle online concept-drift partitions for PhoenixGuard V3.

The detector compares two adjacent bounded windows with a two-sample
Kolmogorov-Smirnov statistic and a standardized mean-shift floor.  A detected
distribution change creates a new deterministic regime partition identifier;
it does not predict direction and cannot authorize a trade.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import json
import math
from typing import Any, cast


CONCEPT_DRIFT_STUDY_SCHEMA_VERSION = "PG_CONCEPT_DRIFT_STUDY_V3"
CONCEPT_DRIFT_STATE_SCHEMA_VERSION = "PG_CONCEPT_DRIFT_DETECTOR_STATE_V3"
DEFAULT_DRIFT_WINDOW_SIZE = 24
DEFAULT_MAX_REGIME_PARTITIONS = 1_000_000
MAX_DRIFT_WINDOW_SIZE = 256
MAX_DRIFT_FEATURES = 64


class ConceptDriftValidationError(ValueError):
    """Raised when drift evidence is unclosed, unordered, or incompatible."""


def _identity(value: object, *, field: str, maximum: int) -> str:
    text = " ".join(str(value or "").strip().upper().split())
    if not text:
        raise ConceptDriftValidationError(f"{field} is required")
    if len(text) > maximum:
        raise ConceptDriftValidationError(f"{field} exceeds {maximum} characters")
    return text


def _integer(value: object, *, field: str, minimum: int) -> int:
    if isinstance(value, bool):
        raise ConceptDriftValidationError(
            f"{field} must be an integer >= {minimum}"
        )
    try:
        numeric = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise ConceptDriftValidationError(
            f"{field} must be an integer >= {minimum}"
        ) from exc
    if not math.isfinite(numeric) or not numeric.is_integer() or numeric < minimum:
        raise ConceptDriftValidationError(
            f"{field} must be an integer >= {minimum}"
        )
    return int(numeric)


def _finite(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise ConceptDriftValidationError(f"{field} must be finite")
    try:
        numeric = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise ConceptDriftValidationError(f"{field} must be finite") from exc
    if not math.isfinite(numeric):
        raise ConceptDriftValidationError(f"{field} must be finite")
    return numeric


def _optional_identity(value: object, *, field: str, maximum: int) -> str | None:
    if value is None:
        return None
    return _identity(value, field=field, maximum=maximum)


def _optional_integer(value: object, *, field: str) -> int | None:
    if value is None:
        return None
    return _integer(value, field=field, minimum=0)


def _state_digest(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _safety_contract() -> dict[str, Any]:
    return {
        "study_only": True,
        "observation_only": True,
        "causal": False,
        "predicts_direction": False,
        "execution_authority": False,
        "grants_entry_permission": False,
        "grants_execution_permission": False,
    }


def _ks_statistic(left: Sequence[float], right: Sequence[float]) -> float:
    left_sorted = sorted(float(value) for value in left)
    right_sorted = sorted(float(value) for value in right)
    if not left_sorted or not right_sorted:
        return 0.0
    left_index = 0
    right_index = 0
    maximum = 0.0
    for value in sorted(set(left_sorted + right_sorted)):
        while left_index < len(left_sorted) and left_sorted[left_index] <= value:
            left_index += 1
        while right_index < len(right_sorted) and right_sorted[right_index] <= value:
            right_index += 1
        difference = abs(
            left_index / len(left_sorted) - right_index / len(right_sorted)
        )
        maximum = max(maximum, difference)
    return maximum


def _mean(values: Sequence[float]) -> float:
    return sum(values) / max(1, len(values))


def _variance(values: Sequence[float], average: float) -> float:
    return sum((value - average) ** 2 for value in values) / max(1, len(values))


class OnlineConceptDriftDetectorV3:
    """Deterministic bounded detector scoped to one pair/timeframe stream."""

    def __init__(
        self,
        *,
        symbol: object,
        timeframe: object,
        coordinate_space: object,
        order_domain: object,
        feature_names: Sequence[object],
        window_size: int = DEFAULT_DRIFT_WINDOW_SIZE,
        significance_alpha: float = 0.01,
        minimum_standardized_mean_shift: float = 0.50,
        max_regime_partitions: int = DEFAULT_MAX_REGIME_PARTITIONS,
    ) -> None:
        self.symbol = _identity(symbol, field="symbol", maximum=64)
        self.timeframe = _identity(timeframe, field="timeframe", maximum=32)
        self.coordinate_space = _identity(
            coordinate_space, field="coordinate_space", maximum=64
        )
        self.order_domain = _identity(
            order_domain, field="order_domain", maximum=64
        )
        if isinstance(feature_names, (str, bytes, bytearray)):
            raise ConceptDriftValidationError("feature_names must be a sequence")
        features = [
            _identity(value, field=f"feature_names[{index}]", maximum=128)
            for index, value in enumerate(feature_names)
        ]
        if not features or len(features) > MAX_DRIFT_FEATURES:
            raise ConceptDriftValidationError(
                f"feature_names must contain between 1 and {MAX_DRIFT_FEATURES} names"
            )
        if len(set(features)) != len(features):
            raise ConceptDriftValidationError("feature_names must be unique")
        self.feature_names = features
        self.window_size = _integer(
            window_size, field="window_size", minimum=4
        )
        if self.window_size > MAX_DRIFT_WINDOW_SIZE:
            raise ConceptDriftValidationError(
                f"window_size cannot exceed {MAX_DRIFT_WINDOW_SIZE}"
            )
        self.significance_alpha = _finite(
            significance_alpha, field="significance_alpha"
        )
        if not 0.0 < self.significance_alpha <= 0.10:
            raise ConceptDriftValidationError(
                "significance_alpha must be in (0, 0.10]"
            )
        self.minimum_standardized_mean_shift = _finite(
            minimum_standardized_mean_shift,
            field="minimum_standardized_mean_shift",
        )
        if not 0.0 <= self.minimum_standardized_mean_shift <= 100.0:
            raise ConceptDriftValidationError(
                "minimum_standardized_mean_shift must be in [0, 100]"
            )
        self.max_regime_partitions = _integer(
            max_regime_partitions,
            field="max_regime_partitions",
            minimum=1,
        )
        if self.max_regime_partitions > 1_024:
            raise ConceptDriftValidationError(
                "max_regime_partitions cannot exceed 1024"
            )
        self._rows: list[dict[str, Any]] = []
        self._last_order_index: int | None = None
        self._stream_digest = hashlib.sha256(
            "|".join(
                (
                    self.symbol,
                    self.timeframe,
                    self.coordinate_space,
                    self.order_domain,
                    *self.feature_names,
                )
            ).encode("utf-8")
        ).hexdigest()
        initial_id = self._regime_id(ordinal=1, anchor="GENESIS")
        self._partitions: list[dict[str, Any]] = [
            {
                "regime_partition_id": initial_id,
                "ordinal": 1,
                "status": "ACTIVE",
                "start_candle_id": None,
                "start_order_index": None,
                "end_candle_id": None,
                "end_order_index": None,
                "created_by": "INITIAL_PARTITION",
                "drift_evidence_digest": None,
            }
        ]

    @classmethod
    def from_snapshot(
        cls,
        snapshot: object,
        *,
        symbol: object | None = None,
        timeframe: object | None = None,
    ) -> OnlineConceptDriftDetectorV3:
        """Restore one validated bounded detector state.

        ``symbol`` and ``timeframe`` are optional caller-side scope locks.  A
        service restoring Pair DNA state should always provide both so a state
        document cannot be attached to another pair accidentally.
        """

        if not isinstance(snapshot, Mapping):
            raise ConceptDriftValidationError("snapshot must be a mapping")
        source = dict(cast(Mapping[str, Any], snapshot))
        if source.get("schema_version") != CONCEPT_DRIFT_STATE_SCHEMA_VERSION:
            raise ConceptDriftValidationError(
                "snapshot schema_version is not a concept-drift detector state"
            )
        if (
            source.get("private_state") is not True
            or source.get("study_only") is not True
            or source.get("execution_authority") is not False
        ):
            raise ConceptDriftValidationError(
                "snapshot must be private study-only state"
            )
        supplied_digest = str(source.get("state_digest") or "").lower()
        if len(supplied_digest) != 64:
            raise ConceptDriftValidationError("snapshot state_digest is invalid")
        digest_core = deepcopy(source)
        digest_core.pop("state_digest", None)
        if _state_digest(digest_core) != supplied_digest:
            raise ConceptDriftValidationError("snapshot state_digest does not match")

        stream = source.get("stream")
        configuration = source.get("configuration")
        if not isinstance(stream, Mapping) or not isinstance(configuration, Mapping):
            raise ConceptDriftValidationError(
                "snapshot stream and configuration must be mappings"
            )
        stream_row = dict(cast(Mapping[str, Any], stream))
        config_row = dict(cast(Mapping[str, Any], configuration))
        snapshot_symbol = _identity(
            stream_row.get("symbol"), field="stream.symbol", maximum=64
        )
        snapshot_timeframe = _identity(
            stream_row.get("timeframe"), field="stream.timeframe", maximum=32
        )
        if symbol is not None and _identity(
            symbol, field="symbol", maximum=64
        ) != snapshot_symbol:
            raise ConceptDriftValidationError("snapshot symbol mismatch")
        if timeframe is not None and _identity(
            timeframe, field="timeframe", maximum=32
        ) != snapshot_timeframe:
            raise ConceptDriftValidationError("snapshot timeframe mismatch")
        feature_names = stream_row.get("feature_names")
        if not isinstance(feature_names, list):
            raise ConceptDriftValidationError(
                "snapshot stream.feature_names must be a list"
            )
        detector = cls(
            symbol=snapshot_symbol,
            timeframe=snapshot_timeframe,
            coordinate_space=stream_row.get("coordinate_space"),
            order_domain=stream_row.get("order_domain"),
            feature_names=cast(list[object], feature_names),
            window_size=_integer(
                config_row.get("window_size"),
                field="configuration.window_size",
                minimum=4,
            ),
            significance_alpha=_finite(
                config_row.get("significance_alpha"),
                field="configuration.significance_alpha",
            ),
            minimum_standardized_mean_shift=_finite(
                config_row.get("minimum_standardized_mean_shift"),
                field="configuration.minimum_standardized_mean_shift",
            ),
            max_regime_partitions=_integer(
                config_row.get("max_regime_partitions"),
                field="configuration.max_regime_partitions",
                minimum=1,
            ),
        )
        if str(source.get("stream_digest") or "") != detector._stream_digest:
            raise ConceptDriftValidationError("snapshot stream_digest does not match")

        raw_rows = source.get("rows")
        if not isinstance(raw_rows, list):
            raise ConceptDriftValidationError("snapshot rows must be a list")
        raw_row_items = cast(list[object], raw_rows)
        if len(raw_row_items) > 2 * detector.window_size:
            raise ConceptDriftValidationError("snapshot row bound was exceeded")
        restored_rows: list[dict[str, Any]] = []
        prior_order: int | None = None
        candle_ids: set[str] = set()
        for index, raw in enumerate(raw_row_items):
            if not isinstance(raw, Mapping):
                raise ConceptDriftValidationError(
                    f"snapshot rows[{index}] must be a mapping"
                )
            row = detector._canonical_row(cast(Mapping[str, Any], raw))
            order_index = int(row["order_index"])
            if prior_order is not None and order_index <= prior_order:
                raise ConceptDriftValidationError(
                    "snapshot row order_index must increase strictly"
                )
            if str(row["candle_id"]) in candle_ids:
                raise ConceptDriftValidationError(
                    "snapshot contains a duplicate candle_id"
                )
            restored_rows.append(row)
            candle_ids.add(str(row["candle_id"]))
            prior_order = order_index
        restored_last = _optional_integer(
            source.get("last_order_index"), field="last_order_index"
        )
        expected_last = (
            int(restored_rows[-1]["order_index"]) if restored_rows else None
        )
        if restored_last != expected_last:
            raise ConceptDriftValidationError(
                "snapshot last_order_index does not match its bounded rows"
            )
        detector._rows = restored_rows
        detector._last_order_index = restored_last
        detector._partitions = detector._validate_snapshot_partitions(
            source.get("partitions")
        )
        return detector

    def _validate_snapshot_partitions(self, value: object) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            raise ConceptDriftValidationError("snapshot partitions must be a list")
        raw_partitions = cast(list[object], value)
        if not 1 <= len(raw_partitions) <= self.max_regime_partitions:
            raise ConceptDriftValidationError(
                "snapshot partition count is outside the configured bound"
            )
        partitions: list[dict[str, Any]] = []
        identifiers: set[str] = set()
        for index, raw in enumerate(raw_partitions):
            if not isinstance(raw, Mapping):
                raise ConceptDriftValidationError(
                    f"snapshot partitions[{index}] must be a mapping"
                )
            row = dict(cast(Mapping[str, Any], raw))
            ordinal = _integer(
                row.get("ordinal"),
                field=f"partitions[{index}].ordinal",
                minimum=1,
            )
            if ordinal != index + 1:
                raise ConceptDriftValidationError(
                    "snapshot partition ordinals must be contiguous"
                )
            partition_id = _identity(
                row.get("regime_partition_id"),
                field=f"partitions[{index}].regime_partition_id",
                maximum=64,
            )
            if partition_id in identifiers or not partition_id.startswith("PGREG-"):
                raise ConceptDriftValidationError(
                    "snapshot partition identifiers must be unique V3 identifiers"
                )
            status = _identity(
                row.get("status"),
                field=f"partitions[{index}].status",
                maximum=16,
            )
            expected_status = "ACTIVE" if index == len(raw_partitions) - 1 else "CLOSED"
            if status != expected_status:
                raise ConceptDriftValidationError(
                    "snapshot must have one final ACTIVE partition"
                )
            start_candle_id = _optional_identity(
                row.get("start_candle_id"),
                field=f"partitions[{index}].start_candle_id",
                maximum=256,
            )
            start_order_index = _optional_integer(
                row.get("start_order_index"),
                field=f"partitions[{index}].start_order_index",
            )
            end_candle_id = _optional_identity(
                row.get("end_candle_id"),
                field=f"partitions[{index}].end_candle_id",
                maximum=256,
            )
            end_order_index = _optional_integer(
                row.get("end_order_index"),
                field=f"partitions[{index}].end_order_index",
            )
            if (start_candle_id is None) != (start_order_index is None):
                raise ConceptDriftValidationError(
                    "snapshot partition start boundary is incomplete"
                )
            if (end_candle_id is None) != (end_order_index is None):
                raise ConceptDriftValidationError(
                    "snapshot partition end boundary is incomplete"
                )
            if status == "CLOSED" and end_order_index is None:
                raise ConceptDriftValidationError(
                    "snapshot closed partition requires an end boundary"
                )
            if status == "ACTIVE" and end_order_index is not None:
                raise ConceptDriftValidationError(
                    "snapshot active partition cannot have an end boundary"
                )
            if (
                start_order_index is not None
                and end_order_index is not None
                and end_order_index < start_order_index
            ):
                raise ConceptDriftValidationError(
                    "snapshot partition end precedes its start"
                )
            if partitions:
                previous_end = cast(int, partitions[-1]["end_order_index"])
                if start_order_index is None or start_order_index <= previous_end:
                    raise ConceptDriftValidationError(
                        "snapshot partition boundaries must increase strictly"
                    )
            created_by = _identity(
                row.get("created_by"),
                field=f"partitions[{index}].created_by",
                maximum=64,
            )
            if index == 0 and created_by != "INITIAL_PARTITION":
                raise ConceptDriftValidationError(
                    "snapshot first partition must be the initial partition"
                )
            if index > 0 and created_by != "STATISTICALLY_SIGNIFICANT_CONCEPT_DRIFT":
                raise ConceptDriftValidationError(
                    "snapshot appended partitions must come from concept drift"
                )
            evidence_digest = row.get("drift_evidence_digest")
            if index == 0:
                if evidence_digest is not None:
                    raise ConceptDriftValidationError(
                        "snapshot initial partition cannot have drift evidence"
                    )
                canonical_digest = None
            else:
                canonical_digest = str(evidence_digest or "").lower()
                if len(canonical_digest) != 64:
                    raise ConceptDriftValidationError(
                        "snapshot drift evidence digest is invalid"
                    )
            partitions.append(
                {
                    "regime_partition_id": partition_id,
                    "ordinal": ordinal,
                    "status": status,
                    "start_candle_id": start_candle_id,
                    "start_order_index": start_order_index,
                    "end_candle_id": end_candle_id,
                    "end_order_index": end_order_index,
                    "created_by": created_by,
                    "drift_evidence_digest": canonical_digest,
                }
            )
            identifiers.add(partition_id)
        if self._rows and partitions[-1]["start_order_index"] is None:
            raise ConceptDriftValidationError(
                "snapshot active partition requires a start boundary"
            )
        if (
            self._last_order_index is not None
            and cast(int, partitions[-1]["start_order_index"])
            > self._last_order_index
        ):
            raise ConceptDriftValidationError(
                "snapshot active partition starts after the high-water mark"
            )
        return partitions

    def _regime_id(self, *, ordinal: int, anchor: str) -> str:
        digest = hashlib.sha256(
            f"{self._stream_digest}|{ordinal}|{anchor}".encode("utf-8")
        ).hexdigest()[:16]
        return f"PGREG-{ordinal:04d}-{digest.upper()}"

    def _result(
        self,
        *,
        status: str,
        metrics: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "schema_version": CONCEPT_DRIFT_STUDY_SCHEMA_VERSION,
            "status": status,
            "stream": {
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "coordinate_space": self.coordinate_space,
                "order_domain": self.order_domain,
                "feature_names": list(self.feature_names),
            },
            "current_regime_partition_id": self._partitions[-1][
                "regime_partition_id"
            ],
            "partition_count": len(self._partitions),
            "partitions": deepcopy(self._partitions),
            "buffered_closed_candles": len(self._rows),
            "required_for_comparison": 2 * self.window_size,
            "metrics": deepcopy(dict(metrics or {})),
            **_safety_contract(),
        }

    def _canonical_row(self, observation: Mapping[str, Any]) -> dict[str, Any]:
        source = dict(observation)
        if source.get("is_closed") is not True:
            raise ConceptDriftValidationError(
                "concept drift accepts completed closed candles only"
            )
        candle_id = _identity(
            source.get("candle_id"), field="candle_id", maximum=256
        )
        coordinate = _identity(
            source.get("coordinate_space"),
            field="coordinate_space",
            maximum=64,
        )
        order_domain = _identity(
            source.get("order_domain"), field="order_domain", maximum=64
        )
        if coordinate != self.coordinate_space:
            raise ConceptDriftValidationError(
                "observation coordinate_space does not match detector"
            )
        if order_domain != self.order_domain:
            raise ConceptDriftValidationError(
                "observation order_domain does not match detector"
            )
        order_index = _integer(
            source.get("order_index"), field="order_index", minimum=0
        )
        feature_source = source.get("features")
        if not isinstance(feature_source, Mapping):
            raise ConceptDriftValidationError("features must be a mapping")
        feature_map = dict(cast(Mapping[str, Any], feature_source))
        normalized_source = {
            _identity(key, field="features key", maximum=128): value
            for key, value in feature_map.items()
        }
        if set(normalized_source) != set(self.feature_names):
            raise ConceptDriftValidationError(
                "features must exactly match the declared feature_names"
            )
        return {
            "candle_id": candle_id,
            "order_index": order_index,
            "features": {
                name: _finite(
                    normalized_source[name], field=f"features.{name}"
                )
                for name in self.feature_names
            },
        }

    def _measure(self) -> dict[str, Any]:
        baseline = self._rows[: self.window_size]
        recent = self._rows[self.window_size : 2 * self.window_size]
        alpha_per_feature = self.significance_alpha / len(self.feature_names)
        critical = math.sqrt(
            -0.5
            * math.log(alpha_per_feature / 2.0)
            * (len(baseline) + len(recent))
            / (len(baseline) * len(recent))
        )
        feature_metrics: list[dict[str, Any]] = []
        trigger_features: list[str] = []
        for name in self.feature_names:
            left = [float(row["features"][name]) for row in baseline]
            right = [float(row["features"][name]) for row in recent]
            left_mean = _mean(left)
            right_mean = _mean(right)
            pooled_deviation = math.sqrt(
                (_variance(left, left_mean) + _variance(right, right_mean)) / 2.0
            )
            mean_difference = abs(right_mean - left_mean)
            standardized_shift = (
                0.0
                if mean_difference <= 1e-12
                else min(1_000_000.0, mean_difference / max(1e-12, pooled_deviation))
            )
            ks = _ks_statistic(left, right)
            triggered = (
                ks > critical
                and standardized_shift >= self.minimum_standardized_mean_shift
            )
            if triggered:
                trigger_features.append(name)
            feature_metrics.append(
                {
                    "feature_name": name,
                    "ks_statistic": round(ks, 8),
                    "standardized_mean_shift": round(standardized_shift, 8),
                    "baseline_mean": round(left_mean, 8),
                    "recent_mean": round(right_mean, 8),
                    "triggered": triggered,
                }
            )
        evidence_core = {
            "baseline_ids": [row["candle_id"] for row in baseline],
            "recent_ids": [row["candle_id"] for row in recent],
            "feature_metrics": feature_metrics,
            "critical": round(critical, 8),
        }
        evidence_digest = hashlib.sha256(
            json.dumps(
                evidence_core,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            ).encode("utf-8")
        ).hexdigest()
        return {
            "test": "TWO_SAMPLE_KS_WITH_BONFERRONI_DKW_BOUND",
            "significance_alpha": self.significance_alpha,
            "alpha_per_feature": round(alpha_per_feature, 10),
            "ks_critical_value": round(critical, 8),
            "minimum_standardized_mean_shift": (
                self.minimum_standardized_mean_shift
            ),
            "statistically_significant_drift": bool(trigger_features),
            "trigger_features": trigger_features,
            "feature_metrics": feature_metrics,
            "baseline_closed_candle_ids": evidence_core["baseline_ids"],
            "recent_closed_candle_ids": evidence_core["recent_ids"],
            "evidence_digest": evidence_digest,
            "interpretation": (
                "A distribution partition boundary, not directional evidence "
                "or proof of causation."
            ),
        }

    def update(self, observation: Mapping[str, Any]) -> dict[str, Any]:
        """Ingest one strictly ordered closed-candle fingerprint."""

        row = self._canonical_row(observation)
        if (
            self._last_order_index is not None
            and int(row["order_index"]) <= self._last_order_index
        ):
            raise ConceptDriftValidationError(
                "closed-candle order_index must increase strictly"
            )
        if any(existing["candle_id"] == row["candle_id"] for existing in self._rows):
            raise ConceptDriftValidationError(
                "candle_id is duplicated inside the bounded evidence window"
            )
        self._rows.append(row)
        self._last_order_index = int(row["order_index"])
        if self._partitions[-1]["start_candle_id"] is None:
            self._partitions[-1]["start_candle_id"] = row["candle_id"]
            self._partitions[-1]["start_order_index"] = row["order_index"]
        if len(self._rows) > 2 * self.window_size:
            self._rows = self._rows[-2 * self.window_size :]
        if len(self._rows) < 2 * self.window_size:
            return self._result(status="WARMING")

        metrics = self._measure()
        if not metrics["statistically_significant_drift"]:
            return self._result(status="STABLE", metrics=metrics)
        if len(self._partitions) >= self.max_regime_partitions:
            return self._result(
                status="DRIFT_PARTITION_CAPACITY_REACHED",
                metrics=metrics,
            )

        previous = self._partitions[-1]
        previous["status"] = "CLOSED"
        previous["end_candle_id"] = self._rows[self.window_size - 1]["candle_id"]
        previous["end_order_index"] = self._rows[self.window_size - 1][
            "order_index"
        ]
        recent = self._rows[self.window_size :]
        ordinal = len(self._partitions) + 1
        anchor = "|".join(
            (
                str(recent[0]["candle_id"]),
                str(recent[-1]["candle_id"]),
                str(metrics["evidence_digest"]),
            )
        )
        self._partitions.append(
            {
                "regime_partition_id": self._regime_id(
                    ordinal=ordinal,
                    anchor=anchor,
                ),
                "ordinal": ordinal,
                "status": "ACTIVE",
                "start_candle_id": recent[0]["candle_id"],
                "start_order_index": recent[0]["order_index"],
                "end_candle_id": None,
                "end_order_index": None,
                "created_by": "STATISTICALLY_SIGNIFICANT_CONCEPT_DRIFT",
                "drift_evidence_digest": metrics["evidence_digest"],
            }
        )
        # The recent comparison window becomes the new baseline.  This avoids
        # repeatedly declaring the same distribution change while retaining a
        # bounded and deterministic warm-up path for the next partition.
        self._rows = recent
        return self._result(status="DRIFT_DETECTED", metrics=metrics)

    def replay_retained_history(
        self,
        observations: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        """Idempotently append the unseen suffix of one retained history.

        Rows at or below the persisted high-water mark cannot create another
        partition.  Any replayed row still present in the bounded detector
        window must match byte-for-byte after canonicalization; divergent
        overlap fails closed.  Older rows outside the private two-window buffer
        are immutable history and are ignored.
        """

        if isinstance(observations, (str, bytes, bytearray)):
            raise ConceptDriftValidationError(
                "retained history must be a sequence of mappings"
            )
        canonical: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
        previous_order: int | None = None
        replay_ids: set[str] = set()
        for index, raw in enumerate(cast(Sequence[object], observations)):
            if not isinstance(raw, Mapping):
                raise ConceptDriftValidationError(
                    f"retained_history[{index}] must be a mapping"
                )
            row = self._canonical_row(cast(Mapping[str, Any], raw))
            order_index = int(row["order_index"])
            if previous_order is not None and order_index <= previous_order:
                raise ConceptDriftValidationError(
                    "retained history order_index must increase strictly"
                )
            candle_id = str(row["candle_id"])
            if candle_id in replay_ids:
                raise ConceptDriftValidationError(
                    "retained history contains a duplicate candle_id"
                )
            canonical.append((cast(Mapping[str, Any], raw), row))
            previous_order = order_index
            replay_ids.add(candle_id)

        buffered_by_order = {
            int(row["order_index"]): row for row in self._rows
        }
        result: dict[str, Any] | None = None
        for raw, row in canonical:
            order_index = int(row["order_index"])
            if (
                self._last_order_index is not None
                and order_index <= self._last_order_index
            ):
                buffered = buffered_by_order.get(order_index)
                if buffered is not None and buffered != row:
                    raise ConceptDriftValidationError(
                        "retained history conflicts with persisted detector evidence"
                    )
                continue
            result = self.update(raw)
        if result is None:
            return self._result(status="REPLAY_UNCHANGED")
        return result

    def persistence_snapshot(self) -> dict[str, Any]:
        """Return bounded private state suitable for atomic persistence."""

        payload: dict[str, Any] = {
            "schema_version": CONCEPT_DRIFT_STATE_SCHEMA_VERSION,
            "private_state": True,
            "study_only": True,
            "execution_authority": False,
            "stream": {
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "coordinate_space": self.coordinate_space,
                "order_domain": self.order_domain,
                "feature_names": list(self.feature_names),
            },
            "configuration": {
                "window_size": self.window_size,
                "significance_alpha": self.significance_alpha,
                "minimum_standardized_mean_shift": (
                    self.minimum_standardized_mean_shift
                ),
                "max_regime_partitions": self.max_regime_partitions,
            },
            "stream_digest": self._stream_digest,
            "last_order_index": self._last_order_index,
            "rows": [
                {
                    "candle_id": row["candle_id"],
                    "order_index": row["order_index"],
                    "is_closed": True,
                    "coordinate_space": self.coordinate_space,
                    "order_domain": self.order_domain,
                    "features": deepcopy(row["features"]),
                }
                for row in self._rows
            ],
            "partitions": deepcopy(self._partitions),
        }
        payload["state_digest"] = _state_digest(payload)
        return payload

    def snapshot(self) -> dict[str, Any]:
        """Return metadata only; raw feature windows stay private."""

        return self._result(status="READY")


__all__ = [
    "CONCEPT_DRIFT_STATE_SCHEMA_VERSION",
    "CONCEPT_DRIFT_STUDY_SCHEMA_VERSION",
    "DEFAULT_DRIFT_WINDOW_SIZE",
    "DEFAULT_MAX_REGIME_PARTITIONS",
    "MAX_DRIFT_FEATURES",
    "MAX_DRIFT_WINDOW_SIZE",
    "ConceptDriftValidationError",
    "OnlineConceptDriftDetectorV3",
]
