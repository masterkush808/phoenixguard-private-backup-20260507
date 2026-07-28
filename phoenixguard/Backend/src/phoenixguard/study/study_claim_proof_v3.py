"""Machine-checkable closed-candle proof certificates for V3 study claims.

Certificates are deterministic content-addressed envelopes.  They bind a
study claim to ordered closed-candle identities, coordinate space, order
domain, bounded inputs, and an explicit derivation description.  Verification
rebuilds every digest from supplied evidence.  A valid certificate proves
derivation integrity only; it does not prove causation, prediction quality, or
permission to trade.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
import hashlib
import hmac
import json
import math
from typing import Any, cast


STUDY_CLAIM_PROOF_SCHEMA_VERSION = "PG_STUDY_CLAIM_PROOF_V3"
STUDY_CLAIM_PROOF_VERIFICATION_SCHEMA_VERSION = (
    "PG_STUDY_CLAIM_PROOF_VERIFICATION_V3"
)
PUBLIC_STUDY_CANONICAL_PROJECTION_VERSION = "PG_PUBLIC_STUDY_CANONICAL_V3"
MAX_PROOF_CANDLES = 512
MAX_PROOF_DOCUMENT_BYTES = 128 * 1024
MAX_PROOF_DEPTH = 10
MAX_PROOF_COLLECTION_ITEMS = 1_024
ALLOWED_STUDY_CLAIM_TYPES = frozenset(
    {
        "BEHAVIORAL_SUMMARY",
        "CONCEPT_DRIFT",
        "CROSS_PAIR_ASSOCIATION",
        "EXPECTED_REST_DURATION",
        "FEATURE_PROMOTION",
        "HISTORICAL_SIMILARITY",
        "MOTIF_COMPOSITION",
        "MOTIF_MATCH",
        "PATH_RECONSTRUCTION",
        "REGIME_PARTITION",
        "RETRACEMENT_CONFLUENCE",
        "STUDY_ASSOCIATION",
        "TIME_TO_EVENT",
    }
)


class StudyClaimProofValidationError(ValueError):
    """Raised when proof inputs violate the closed-candle V3 contract."""


def _identity(value: object, *, field: str, maximum: int) -> str:
    text = " ".join(str(value or "").strip().upper().split())
    if not text:
        raise StudyClaimProofValidationError(f"{field} is required")
    if len(text) > maximum:
        raise StudyClaimProofValidationError(f"{field} exceeds {maximum} characters")
    return text


def _integer(value: object, *, field: str, minimum: int = 0) -> int:
    if isinstance(value, bool):
        raise StudyClaimProofValidationError(
            f"{field} must be an integer >= {minimum}"
        )
    try:
        numeric = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise StudyClaimProofValidationError(
            f"{field} must be an integer >= {minimum}"
        ) from exc
    if not math.isfinite(numeric) or not numeric.is_integer() or numeric < minimum:
        raise StudyClaimProofValidationError(
            f"{field} must be an integer >= {minimum}"
        )
    return int(numeric)


def _validate_json_value(value: object, *, field: str, depth: int = 0) -> Any:
    if depth > MAX_PROOF_DEPTH:
        raise StudyClaimProofValidationError(
            f"{field} exceeds maximum nesting depth {MAX_PROOF_DEPTH}"
        )
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value) > 8_192:
            raise StudyClaimProofValidationError(f"{field} string is too large")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise StudyClaimProofValidationError(f"{field} contains non-finite data")
        return value
    if isinstance(value, Mapping):
        mapping = dict(cast(Mapping[object, object], value))
        if len(mapping) > MAX_PROOF_COLLECTION_ITEMS:
            raise StudyClaimProofValidationError(f"{field} mapping is too large")
        result: dict[str, Any] = {}
        for raw_key, raw_value in mapping.items():
            if not isinstance(raw_key, str) or not raw_key or len(raw_key) > 256:
                raise StudyClaimProofValidationError(
                    f"{field} keys must be non-empty strings <= 256 characters"
                )
            result[raw_key] = _validate_json_value(
                raw_value,
                field=f"{field}.{raw_key}",
                depth=depth + 1,
            )
        return result
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        rows = list(cast(Sequence[object], value))
        if len(rows) > MAX_PROOF_COLLECTION_ITEMS:
            raise StudyClaimProofValidationError(f"{field} sequence is too large")
        return [
            _validate_json_value(
                row,
                field=f"{field}[{index}]",
                depth=depth + 1,
            )
            for index, row in enumerate(rows)
        ]
    raise StudyClaimProofValidationError(
        f"{field} contains a non-JSON value of type {type(value).__name__}"
    )


def _document(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise StudyClaimProofValidationError(f"{field} must be a mapping")
    source = dict(cast(Mapping[str, Any], value))
    canonical = cast(
        dict[str, Any], _validate_json_value(source, field=field, depth=0)
    )
    encoded = _encode(canonical)
    if len(encoded) > MAX_PROOF_DOCUMENT_BYTES:
        raise StudyClaimProofValidationError(
            f"{field} exceeds {MAX_PROOF_DOCUMENT_BYTES} bytes"
        )
    return canonical


def _encode(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise StudyClaimProofValidationError(
            "proof material must be finite canonical JSON"
        ) from exc


def _digest(value: object) -> str:
    return hashlib.sha256(_encode(value)).hexdigest()


_PROOF_REFERENCE_FIELDS = frozenset(
    {
        "claim_proof_id",
        "claim_bound_study_hash",
        "claim_bound_projection",
    }
)


def canonical_public_study_projection_v3(value: Mapping[str, Any]) -> dict[str, Any]:
    """Return the final-study projection covered by a claim certificate.

    Proof references are deliberately excluded because they are attached after
    certificate issuance and would otherwise create a circular hash.  Every
    other public field remains covered, including nested study data.  The
    projection is public and deterministic so a verifier can hash the final
    serialized study without relying on an undocumented pre-attachment object.
    """

    document = cast(
        dict[str, Any],
        _validate_json_value(dict(value), field="public_study", depth=0),
    )

    def project(node: object) -> Any:
        if isinstance(node, Mapping):
            source = cast(Mapping[str, Any], node)
            return {
                key: project(nested)
                for key, nested in source.items()
                if key not in _PROOF_REFERENCE_FIELDS
            }
        if isinstance(node, Sequence) and not isinstance(
            node, (str, bytes, bytearray)
        ):
            return [project(nested) for nested in cast(Sequence[object], node)]
        return node

    return cast(dict[str, Any], project(document))


def canonical_public_study_hash_v3(value: Mapping[str, Any]) -> str:
    """Hash the documented non-circular projection of a final public study."""

    return _digest(canonical_public_study_projection_v3(value))


def _safety_contract() -> dict[str, Any]:
    return {
        "study_only": True,
        "observation_only": True,
        "causal": False,
        "execution_authority": False,
        "grants_entry_permission": False,
        "grants_execution_permission": False,
    }


_FORBIDDEN_AUTHORITY_KEYS = frozenset(
    {
        "entry_permission",
        "execution_authority",
        "grants_entry_permission",
        "grants_execution_permission",
        "order_authority",
        "trade_authority",
    }
)


def _reject_authority(value: object, *, path: str = "claim_payload") -> None:
    if isinstance(value, Mapping):
        for raw_key, nested in cast(Mapping[object, object], value).items():
            key = str(raw_key).strip().lower()
            if key in _FORBIDDEN_AUTHORITY_KEYS and nested not in (
                False,
                None,
                "",
                "NONE",
                "UNAVAILABLE",
                "DENIED",
            ):
                raise StudyClaimProofValidationError(
                    f"{path}.{raw_key} attempts to grant trade authority"
                )
            _reject_authority(nested, path=f"{path}.{raw_key}")
    elif isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        for index, nested in enumerate(cast(Sequence[object], value)):
            _reject_authority(nested, path=f"{path}[{index}]")


def _closed_candle_evidence(
    value: object,
    *,
    coordinate_space: str,
    order_domain: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(
        value, (str, bytes, bytearray)
    ):
        raise StudyClaimProofValidationError("closed_candles must be a sequence")
    raw_rows = list(cast(Sequence[object], value))
    if not raw_rows or len(raw_rows) > MAX_PROOF_CANDLES:
        raise StudyClaimProofValidationError(
            f"closed_candles must contain between 1 and {MAX_PROOF_CANDLES} rows"
        )
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, Mapping):
            raise StudyClaimProofValidationError(
                f"closed_candles[{index}] must be a mapping"
            )
        source = dict(cast(Mapping[str, Any], raw))
        if source.get("is_closed") is not True:
            raise StudyClaimProofValidationError(
                f"closed_candles[{index}] is not closed"
            )
        row_coordinate = _identity(
            source.get("coordinate_space"),
            field=f"closed_candles[{index}].coordinate_space",
            maximum=64,
        )
        row_order_domain = _identity(
            source.get("order_domain"),
            field=f"closed_candles[{index}].order_domain",
            maximum=64,
        )
        if row_coordinate != coordinate_space:
            raise StudyClaimProofValidationError(
                "closed-candle coordinate space differs from the declared space"
            )
        if row_order_domain != order_domain:
            raise StudyClaimProofValidationError(
                "closed-candle order domain differs from the declared domain"
            )
        raw_closed_timestamp = source.get("closed_timestamp")
        if (
            raw_closed_timestamp is None
            or isinstance(raw_closed_timestamp, bool)
            or (
                isinstance(raw_closed_timestamp, str)
                and not raw_closed_timestamp.strip()
            )
        ):
            raise StudyClaimProofValidationError(
                f"closed_candles[{index}].closed_timestamp is required"
            )
        rows.append(
            {
                "candle_id": _identity(
                    source.get("candle_id"),
                    field=f"closed_candles[{index}].candle_id",
                    maximum=256,
                ),
                "order_index": _integer(
                    source.get("order_index"),
                    field=f"closed_candles[{index}].order_index",
                ),
                "closed_timestamp": _validate_json_value(
                    raw_closed_timestamp,
                    field=f"closed_candles[{index}].closed_timestamp",
                ),
                "coordinate_space": row_coordinate,
                "order_domain": row_order_domain,
                "is_closed": True,
            }
        )
    candle_ids = [str(row["candle_id"]) for row in rows]
    if len(set(candle_ids)) != len(candle_ids):
        raise StudyClaimProofValidationError(
            "closed-candle identities must be unique"
        )
    order_indices = [int(row["order_index"]) for row in rows]
    if any(
        order_indices[index] <= order_indices[index - 1]
        for index in range(1, len(order_indices))
    ):
        raise StudyClaimProofValidationError(
            "closed-candle order indices must increase strictly"
        )
    return rows


def issue_study_claim_certificate_v3(
    *,
    claim_type: object,
    claim_payload: Mapping[str, Any],
    closed_candles: Sequence[Mapping[str, Any]],
    coordinate_space: object,
    order_domain: object,
    inputs: Mapping[str, Any],
    derivation: Mapping[str, Any],
) -> dict[str, Any]:
    """Issue one deterministic content-addressed study proof certificate."""

    canonical_claim_type = _identity(
        claim_type, field="claim_type", maximum=64
    )
    if canonical_claim_type not in ALLOWED_STUDY_CLAIM_TYPES:
        raise StudyClaimProofValidationError(
            f"claim_type is not an allowed V3 study claim: {canonical_claim_type}"
        )
    coordinate = _identity(
        coordinate_space, field="coordinate_space", maximum=64
    )
    order = _identity(order_domain, field="order_domain", maximum=64)
    claim = _document(claim_payload, field="claim_payload")
    _reject_authority(claim)
    input_document = _document(inputs, field="inputs")
    derivation_document = _document(derivation, field="derivation")
    algorithm_id = _identity(
        derivation_document.get("algorithm_id"),
        field="derivation.algorithm_id",
        maximum=128,
    )
    algorithm_version = _identity(
        derivation_document.get("algorithm_version"),
        field="derivation.algorithm_version",
        maximum=64,
    )
    evidence = _closed_candle_evidence(
        closed_candles,
        coordinate_space=coordinate,
        order_domain=order,
    )
    evidence_digest = _digest(evidence)
    claim_digest = _digest(claim)
    input_digest = _digest(input_document)
    derivation_digest = _digest(derivation_document)
    core: dict[str, Any] = {
        "schema_version": STUDY_CLAIM_PROOF_SCHEMA_VERSION,
        "status": "ISSUED",
        "claim_type": canonical_claim_type,
        "claim_payload_hash": claim_digest,
        "input_hash": input_digest,
        "derivation_hash": derivation_digest,
        "derivation_identity": {
            "algorithm_id": algorithm_id,
            "algorithm_version": algorithm_version,
        },
        "evidence": {
            "closed_candle_count": len(evidence),
            "closed_candle_ids": [row["candle_id"] for row in evidence],
            "first_order_index": evidence[0]["order_index"],
            "last_order_index": evidence[-1]["order_index"],
            "coordinate_space": coordinate,
            "order_domain": order,
            "closed_candle_evidence_hash": evidence_digest,
        },
        "binding": {
            "canonicalization": "RFC8259_SORTED_KEYS_COMPACT_UTF8",
            "digest": "SHA256",
            "binds_claim_payload": True,
            "binds_inputs": True,
            "binds_derivation": True,
            "binds_ordered_closed_candle_evidence": True,
            "binds_coordinate_space": True,
            "binds_order_domain": True,
            "authenticates_market_source": False,
        },
        "interpretation": (
            "Integrity proof for a closed-candle study derivation only; validity "
            "does not establish causation, predictive accuracy, or trade permission."
        ),
        **_safety_contract(),
    }
    certificate_hash = _digest(core)
    return {
        **core,
        "certificate_id": f"PGPROOF-{certificate_hash[:24].upper()}",
        "certificate_hash": certificate_hash,
    }


def verify_study_claim_certificate_v3(
    certificate: Mapping[str, Any],
    *,
    claim_payload: Mapping[str, Any],
    closed_candles: Sequence[Mapping[str, Any]],
    inputs: Mapping[str, Any],
    derivation: Mapping[str, Any],
) -> dict[str, Any]:
    """Rebuild and compare a certificate against supplied proof material."""

    reasons: list[str] = []
    source = deepcopy(dict(certificate))
    try:
        claim_type = source.get("claim_type")
        raw_evidence = source.get("evidence")
        if not isinstance(raw_evidence, Mapping):
            raise StudyClaimProofValidationError("certificate.evidence is invalid")
        evidence = dict(cast(Mapping[str, Any], raw_evidence))
        expected = issue_study_claim_certificate_v3(
            claim_type=claim_type,
            claim_payload=claim_payload,
            closed_candles=closed_candles,
            coordinate_space=evidence.get("coordinate_space"),
            order_domain=evidence.get("order_domain"),
            inputs=inputs,
            derivation=derivation,
        )
    except StudyClaimProofValidationError as exc:
        reasons.append(str(exc))
        expected = None
    if source.get("schema_version") != STUDY_CLAIM_PROOF_SCHEMA_VERSION:
        reasons.append("certificate schema_version is invalid")
    for field, required in _safety_contract().items():
        if source.get(field) is not required:
            reasons.append(f"certificate safety field {field} is invalid")
    if expected is not None:
        for field in (
            "certificate_hash",
            "certificate_id",
            "claim_payload_hash",
            "input_hash",
            "derivation_hash",
        ):
            if not hmac.compare_digest(
                str(source.get(field) or ""), str(expected[field])
            ):
                reasons.append(f"certificate {field} mismatch")
        if source.get("evidence") != expected.get("evidence"):
            reasons.append("certificate closed-candle evidence binding mismatch")
        # Recompute the self-hash from the supplied envelope so mutation of any
        # contract or interpretation field is also rejected.
        envelope = deepcopy(source)
        supplied_hash = str(envelope.pop("certificate_hash", "") or "")
        envelope.pop("certificate_id", None)
        if not hmac.compare_digest(_digest(envelope), supplied_hash):
            reasons.append("certificate envelope digest mismatch")
    unique_reasons = list(dict.fromkeys(reasons))
    return {
        "schema_version": STUDY_CLAIM_PROOF_VERIFICATION_SCHEMA_VERSION,
        "status": "VALID" if not unique_reasons else "INVALID",
        "valid": not unique_reasons,
        "certificate_id": str(source.get("certificate_id") or ""),
        "reasons": unique_reasons,
        "verified_bindings": (
            deepcopy(expected["binding"]) if expected is not None and not unique_reasons else {}
        ),
        "interpretation": (
            "A valid digest proves evidence integrity, not causation, prediction, "
            "or permission to trade."
        ),
        **_safety_contract(),
    }


__all__ = [
    "ALLOWED_STUDY_CLAIM_TYPES",
    "MAX_PROOF_CANDLES",
    "MAX_PROOF_COLLECTION_ITEMS",
    "MAX_PROOF_DEPTH",
    "MAX_PROOF_DOCUMENT_BYTES",
    "PUBLIC_STUDY_CANONICAL_PROJECTION_VERSION",
    "STUDY_CLAIM_PROOF_SCHEMA_VERSION",
    "STUDY_CLAIM_PROOF_VERIFICATION_SCHEMA_VERSION",
    "StudyClaimProofValidationError",
    "canonical_public_study_hash_v3",
    "canonical_public_study_projection_v3",
    "issue_study_claim_certificate_v3",
    "verify_study_claim_certificate_v3",
]
