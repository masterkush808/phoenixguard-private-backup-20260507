"""Persistent, bounded per-pair behavioral memory for PhoenixGuard V3.

Pair DNA keeps cumulative aggregates while retaining only a bounded ring of
recent sequence identities.  Storage is atomically replaced under a
cross-process lock.  The profile is descriptive evidence only; no field can
grant entry or execution permission.  Lifelong candle and segment aggregates
are append-only: a closed timestamp must move past the stored high-water mark,
and a segment must have stable start/end/next boundaries.  Consequently,
out-of-order backfills and timestamp-free positional identities are audited but
not folded into the live cumulative profile.
"""

from __future__ import annotations

import base64
import binascii
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import math
from pathlib import Path
from typing import Any, cast

from phoenixguard.study._persistence_v3 import (
    StudyPersistenceError,
    exclusive_store_lock,
    read_json_document,
    write_json_atomic,
)
from phoenixguard.study.behavioral_sequence_v3 import BEHAVIORAL_SEQUENCE_SCHEMA_VERSION
from phoenixguard.study.candle_intelligence_v3 import CANDLE_INTELLIGENCE_SCHEMA_VERSION


PAIR_DNA_SCHEMA_VERSION = "PG_PAIR_DNA_STORE_V3"
DEFAULT_MAX_PAIR_PROFILES = 128
DEFAULT_RECENT_SEQUENCE_LIMIT = 512
# Whole-sequence idempotency uses a bounded, append-only segmented Bloom.  A
# segment is sealed at its design load instead of becoming progressively more
# saturated.  Twenty segments admit 10,240 sequence identities per pair with a
# union-bound false-positive ceiling below 1e-9.  Once full, the store refuses
# new sequences rather than silently forgetting old identities.
PAIR_DNA_DEDUPE_SEGMENT_BITS = 32_768
PAIR_DNA_DEDUPE_SEGMENT_HASHES = 16
PAIR_DNA_DEDUPE_SEGMENT_CAPACITY = 512
PAIR_DNA_DEDUPE_MAX_SEGMENTS = 20
PAIR_DNA_DEDUPE_CAPACITY = (
    PAIR_DNA_DEDUPE_SEGMENT_CAPACITY * PAIR_DNA_DEDUPE_MAX_SEGMENTS
)
PAIR_DNA_DEDUPE_FALSE_POSITIVE_CEILING = 1e-9
_TIME_ORDER_DOMAIN = "CLOSED_TIMESTAMP_V1"
_TRACKER_EVENT_ORDER_DOMAIN = "TRACKER_EVENT_SEQUENCE_V3"
_ORDER_DOMAINS = {_TIME_ORDER_DOMAIN, _TRACKER_EVENT_ORDER_DOMAIN}
_LEGACY_BLOOM_BITS = 16_384
_LEGACY_BLOOM_HASHES = 5
_LEGACY_FALSE_POSITIVE_CEILING = 1e-6
MAX_PAIR_DNA_ASSOCIATIONS = 2_048
MAX_PAIR_DNA_OBJECT_TYPES = 256
MAX_PAIR_DNA_REGIMES = 128


class PairDNAValidationError(ValueError):
    """Raised when pair identity or study evidence is not valid V3 data."""


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    result: list[dict[str, Any]] = []
    for item in cast(Sequence[object], value):
        if isinstance(item, Mapping):
            result.append(dict(cast(Mapping[str, Any], item)))
    return result


def _required_mapping(value: object, *, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise PairDNAValidationError(f"{field} must be a mapping")
    return dict(cast(Mapping[str, Any], value))


def _required_rows(value: object, *, field: str) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise PairDNAValidationError(f"{field} must be a list of mappings")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(cast(list[object], value)):
        if not isinstance(item, Mapping):
            raise PairDNAValidationError(f"{field}[{index}] must be a mapping")
        result.append(dict(cast(Mapping[str, Any], item)))
    return result


def _required_strings(value: object, *, field: str, maximum_length: int) -> list[str]:
    if not isinstance(value, list):
        raise PairDNAValidationError(f"{field} must be a list of strings")
    result: list[str] = []
    for index, item in enumerate(cast(list[object], value)):
        if not isinstance(item, str) or not item:
            raise PairDNAValidationError(f"{field}[{index}] must be a non-empty string")
        if len(item) > maximum_length:
            raise PairDNAValidationError(
                f"{field}[{index}] exceeds {maximum_length} characters"
            )
        result.append(item)
    return result


def _canonical_identity(value: object, *, field: str, maximum: int) -> str:
    text = " ".join(str(value or "").strip().upper().split())
    if not text:
        raise PairDNAValidationError(f"{field} is required")
    if len(text) > maximum:
        raise PairDNAValidationError(f"{field} exceeds {maximum} characters")
    return text


def pair_profile_key_v3(symbol: object, timeframe: object) -> str:
    canonical_symbol = _canonical_identity(symbol, field="symbol", maximum=64)
    canonical_timeframe = _canonical_identity(timeframe, field="timeframe", maximum=32)
    digest = hashlib.sha256(f"{canonical_symbol}|{canonical_timeframe}".encode("utf-8")).hexdigest()[:24]
    return f"pair-{digest}"


def _finite(value: object, *, field: str, default: float | None = None) -> float:
    if value is None and default is not None:
        return default
    if isinstance(value, bool):
        raise PairDNAValidationError(f"{field} must be a finite number")
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise PairDNAValidationError(f"{field} must be a finite number") from exc
    if not math.isfinite(parsed):
        raise PairDNAValidationError(f"{field} must be a finite number")
    return parsed


def _integer(value: object, *, field: str, default: int = 0) -> int:
    if value is None:
        return default
    if isinstance(value, bool):
        raise PairDNAValidationError(f"{field} must be a non-negative integer")
    try:
        parsed = int(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise PairDNAValidationError(f"{field} must be a non-negative integer") from exc
    if parsed < 0 or str(value).strip() not in {str(parsed), f"{parsed}.0"}:
        raise PairDNAValidationError(f"{field} must be a non-negative integer")
    return parsed


def _order_number(value: object, *, field: str) -> int | float:
    if isinstance(value, bool):
        raise PairDNAValidationError(f"{field} must be a finite ordered number")
    if isinstance(value, int):
        return value
    return _finite(value, field=field)


def _increment(counter: dict[str, Any], key: object, amount: int = 1) -> None:
    token = str(key or "UNKNOWN").strip().upper() or "UNKNOWN"
    counter[token] = int(counter.get(token, 0)) + int(amount)


def _increment_bounded(
    counter: dict[str, Any],
    key: object,
    *,
    amount: int = 1,
    maximum_keys: int,
) -> None:
    token = str(key or "UNKNOWN").strip().upper() or "UNKNOWN"
    if token not in counter and len(counter) >= maximum_keys:
        token = "__OTHER__"
    _increment(counter, token, amount)


def _counter(value: object, *, field: str) -> dict[str, int]:
    source = _required_mapping(value, field=field)
    result: dict[str, int] = {}
    for key, raw in source.items():
        token = str(key).strip().upper()
        if not token:
            raise PairDNAValidationError(f"{field} contains an empty key")
        result[token] = _integer(raw, field=f"{field}.{token}")
    return dict(sorted(result.items()))


def _empty_profile(symbol: str, timeframe: str, pair_id: str) -> dict[str, Any]:
    return {
        "pair_id": pair_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "first_observed_at": "",
        "last_observed_at": "",
        "updated_ordinal": 0,
        "observation_count": 0,
        "candle_count": 0,
        "candle": {
            "direction_counts": {},
            "type_counts": {},
            "personality_counts": {},
            "body_ratio_sum": 0.0,
            "upper_wick_ratio_sum": 0.0,
            "lower_wick_ratio_sum": 0.0,
            "rejection_count": 0,
            "acceptance_count": 0,
            "upper_sweep_count": 0,
            "lower_sweep_count": 0,
        },
        "behavior": {
            "state_candle_counts": {},
            "segment_counts": {},
            "segment_candle_sum": {},
            "segment_duration_sum": {},
            "segment_normalized_change_sum": {},
            "transition_counts": {},
            "major_trend_counts": {},
            "inner_trend_counts": {},
        },
        "regime_counts": {},
        "coordinate_space_counts": {},
        "object_type_counts": {},
        "outcome_correlations": {},
        "association_overflow_count": 0,
        "recent_sequences": [],
        "seen_sequence_ids": [],
        "identity_ledger": {
            "algorithm": "MONOTONIC_CLOSED_BOUNDARY_V1",
            "baseline_initialized": False,
            "candle_order_domain": "",
            "candle_high_watermark": None,
            "completed_boundary_high_watermark": None,
            "open_segment": None,
            "accepted_candles": 0,
            "accepted_completed_segments": 0,
            "skipped_overlapping_candles": 0,
            "skipped_unstable_candles": 0,
            "skipped_order_domain_conflicts": 0,
            "skipped_incomplete_segments": 0,
            "skipped_overlapping_segments": 0,
        },
        "sequence_dedupe_bloom": {
            "algorithm": "SHA256_SEGMENTED_BLOOM_V2",
            "insertions": 0,
            "capacity": PAIR_DNA_DEDUPE_CAPACITY,
            "false_positive_ceiling": PAIR_DNA_DEDUPE_FALSE_POSITIVE_CEILING,
            "segment_bits": PAIR_DNA_DEDUPE_SEGMENT_BITS,
            "segment_hashes": PAIR_DNA_DEDUPE_SEGMENT_HASHES,
            "segment_capacity": PAIR_DNA_DEDUPE_SEGMENT_CAPACITY,
            "max_segments": PAIR_DNA_DEDUPE_MAX_SEGMENTS,
            "segments": [],
            "legacy_sha256_bloom_v1": None,
        },
    }


def _empty_state() -> dict[str, Any]:
    return {
        "schema_version": PAIR_DNA_SCHEMA_VERSION,
        "study_only": True,
        "execution_authority": False,
        "next_ordinal": 1,
        "profiles": {},
    }


def _empty_identity_ledger() -> dict[str, Any]:
    return {
        "algorithm": "MONOTONIC_CLOSED_BOUNDARY_V1",
        "baseline_initialized": False,
        "candle_order_domain": "",
        "candle_high_watermark": None,
        "completed_boundary_high_watermark": None,
        "open_segment": None,
        "accepted_candles": 0,
        "accepted_completed_segments": 0,
        "skipped_overlapping_candles": 0,
        "skipped_unstable_candles": 0,
        "skipped_order_domain_conflicts": 0,
        "skipped_incomplete_segments": 0,
        "skipped_overlapping_segments": 0,
    }


def _validate_watermark(value: object, *, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    source = _required_mapping(value, field=field)
    identity = str(source.get("identity") or "").strip()
    if not identity or len(identity) > 512:
        raise PairDNAValidationError(f"{field}.identity must be 1..512 characters")
    return {
        "identity": identity,
        "order": _order_number(source.get("order"), field=f"{field}.order"),
    }


def _validate_open_segment(value: object, *, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    source = _required_mapping(value, field=field)
    state = str(source.get("state") or "").strip().upper()
    start_identity = str(source.get("start_identity") or "").strip()
    if not state or not start_identity or len(start_identity) > 256:
        raise PairDNAValidationError(f"{field} has an invalid stable start boundary")
    return {
        "state": state,
        "start_identity": start_identity,
        "start_order": _order_number(
            source.get("start_order"), field=f"{field}.start_order"
        ),
        "already_counted": source.get("already_counted") is True,
    }


def _validate_identity_ledger(value: object, *, field: str) -> dict[str, Any]:
    source = _mapping(value)
    if not source:
        return _empty_identity_ledger()
    if source.get("algorithm") != "MONOTONIC_CLOSED_BOUNDARY_V1":
        raise PairDNAValidationError(f"{field}.algorithm is not supported")
    candle_high_watermark = _validate_watermark(
        source.get("candle_high_watermark"),
        field=f"{field}.candle_high_watermark",
    )
    order_domain = str(source.get("candle_order_domain") or "").strip().upper()
    if not order_domain and candle_high_watermark:
        identity = str(candle_high_watermark.get("identity") or "").upper()
        if "TIME:" in identity:
            order_domain = _TIME_ORDER_DOMAIN
        elif "RESOLVER_EVENT:" in identity:
            order_domain = _TRACKER_EVENT_ORDER_DOMAIN
    if order_domain and order_domain not in _ORDER_DOMAINS:
        raise PairDNAValidationError(
            f"{field}.candle_order_domain is not supported"
        )
    return {
        "algorithm": "MONOTONIC_CLOSED_BOUNDARY_V1",
        "baseline_initialized": source.get("baseline_initialized") is True,
        "candle_order_domain": order_domain,
        "candle_high_watermark": candle_high_watermark,
        "completed_boundary_high_watermark": _validate_watermark(
            source.get("completed_boundary_high_watermark"),
            field=f"{field}.completed_boundary_high_watermark",
        ),
        "open_segment": _validate_open_segment(
            source.get("open_segment"),
            field=f"{field}.open_segment",
        ),
        "accepted_candles": _integer(
            source.get("accepted_candles"), field=f"{field}.accepted_candles"
        ),
        "accepted_completed_segments": _integer(
            source.get("accepted_completed_segments"),
            field=f"{field}.accepted_completed_segments",
        ),
        "skipped_overlapping_candles": _integer(
            source.get("skipped_overlapping_candles"),
            field=f"{field}.skipped_overlapping_candles",
        ),
        "skipped_unstable_candles": _integer(
            source.get("skipped_unstable_candles"),
            field=f"{field}.skipped_unstable_candles",
        ),
        "skipped_order_domain_conflicts": _integer(
            source.get("skipped_order_domain_conflicts"),
            field=f"{field}.skipped_order_domain_conflicts",
        ),
        "skipped_incomplete_segments": _integer(
            source.get("skipped_incomplete_segments"),
            field=f"{field}.skipped_incomplete_segments",
        ),
        "skipped_overlapping_segments": _integer(
            source.get("skipped_overlapping_segments"),
            field=f"{field}.skipped_overlapping_segments",
        ),
    }


def _stable_timestamp(value: object) -> tuple[str, int | float] | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return f"TIME:{value}", value
    if isinstance(value, float):
        parsed = float(value)
        if not math.isfinite(parsed):
            return None
        return f"TIME:{format(parsed, '.17g')}", parsed
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed_datetime = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed_integer = int(text)
        except ValueError:
            parsed_integer = None
        if parsed_integer is not None:
            return f"TIME:{parsed_integer}", parsed_integer
        try:
            parsed_number = float(text)
        except ValueError:
            return None
        if not math.isfinite(parsed_number):
            return None
        return f"TIME:{format(parsed_number, '.17g')}", parsed_number
    if parsed_datetime.tzinfo is None:
        parsed_datetime = parsed_datetime.replace(tzinfo=timezone.utc)
    utc_value = parsed_datetime.astimezone(timezone.utc)
    return f"TIME:{utc_value.isoformat()}", utc_value.timestamp()


def _stable_candle_marker(row: Mapping[str, Any]) -> dict[str, Any] | None:
    if row.get("closed") is not True:
        return None
    timestamp = _stable_timestamp(row.get("timestamp"))
    candle_id = str(row.get("candle_id") or "").strip()
    if timestamp is None:
        stable_identity = str(
            row.get("stable_candle_identity") or ""
        ).strip()
        sequence = row.get("closed_candle_sequence")
        if not (
            row.get("identity_stable") is True
            and row.get("identity_proof_source")
            == "PG_CLOSED_CANDLE_IDENTITY_STATE_V3"
            and stable_identity
            and isinstance(sequence, int)
            and not isinstance(sequence, bool)
            and sequence >= 0
        ):
            # Positional identities restart at zero for every rolling window.
            # Only the closed-candle resolver may replace a missing source
            # timestamp, and it must supply both immutable key and event order.
            return None
        return {
            "identity": f"RESOLVER_EVENT:{stable_identity}",
            "order": sequence,
            "order_domain": _TRACKER_EVENT_ORDER_DOMAIN,
            "candle_id": candle_id,
        }
    identity, order = timestamp
    return {
        "identity": identity,
        "order": order,
        "order_domain": _TIME_ORDER_DOMAIN,
        "candle_id": candle_id,
    }


def _bloom_probability(*, bits: int, hashes: int, insertions: int) -> float:
    if insertions <= 0:
        return 0.0
    return (1.0 - math.exp(-hashes * insertions / bits)) ** hashes


def _segmented_false_positive_ceiling() -> float:
    one = _bloom_probability(
        bits=PAIR_DNA_DEDUPE_SEGMENT_BITS,
        hashes=PAIR_DNA_DEDUPE_SEGMENT_HASHES,
        insertions=PAIR_DNA_DEDUPE_SEGMENT_CAPACITY,
    )
    return 1.0 - (1.0 - one) ** PAIR_DNA_DEDUPE_MAX_SEGMENTS


def _bloom_positions(
    sequence_id: str,
    *,
    bits: int,
    hashes: int,
) -> tuple[int, ...]:
    digest = hashlib.sha256(sequence_id.encode("utf-8")).digest()
    first = int.from_bytes(digest[:16], "big")
    second = int.from_bytes(digest[16:], "big") | 1
    return tuple((first + index * second) % bits for index in range(hashes))


def _legacy_bloom_positions(sequence_id: str) -> tuple[int, ...]:
    digest = hashlib.sha256(sequence_id.encode("utf-8")).digest()
    return tuple(
        int.from_bytes(digest[index * 4 : index * 4 + 4], "big")
        % _LEGACY_BLOOM_BITS
        for index in range(_LEGACY_BLOOM_HASHES)
    )


def _empty_dedupe_segment() -> dict[str, Any]:
    bitmap_bytes = bytes(PAIR_DNA_DEDUPE_SEGMENT_BITS // 8)
    return {
        "insertions": 0,
        "bitmap_b64": base64.b64encode(bitmap_bytes).decode("ascii"),
    }


def _validate_segment(value: object, *, field: str) -> dict[str, Any]:
    source = _required_mapping(value, field=field)
    insertions = _integer(source.get("insertions"), field=f"{field}.insertions")
    if insertions > PAIR_DNA_DEDUPE_SEGMENT_CAPACITY:
        raise PairDNAValidationError(f"{field} exceeds its sealed insertion capacity")
    encoded = str(source.get("bitmap_b64") or "")
    try:
        bitmap = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise PairDNAValidationError(f"{field}.bitmap_b64 is malformed") from exc
    if len(bitmap) != PAIR_DNA_DEDUPE_SEGMENT_BITS // 8:
        raise PairDNAValidationError(f"{field}.bitmap_b64 has the wrong size")
    return {"insertions": insertions, "bitmap_b64": encoded}


def _validate_legacy_bloom(value: object, *, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    source = _required_mapping(value, field=field)
    if source.get("algorithm") != "SHA256_BLOOM_V1":
        raise PairDNAValidationError(f"{field}.algorithm is not supported")
    if _integer(source.get("bits"), field=f"{field}.bits") != _LEGACY_BLOOM_BITS:
        raise PairDNAValidationError(f"{field}.bits does not match the legacy contract")
    if _integer(source.get("hashes"), field=f"{field}.hashes") != _LEGACY_BLOOM_HASHES:
        raise PairDNAValidationError(f"{field}.hashes does not match the legacy contract")
    insertions = _integer(source.get("insertions"), field=f"{field}.insertions")
    probability = _bloom_probability(
        bits=_LEGACY_BLOOM_BITS,
        hashes=_LEGACY_BLOOM_HASHES,
        insertions=insertions,
    )
    if probability > _LEGACY_FALSE_POSITIVE_CEILING:
        raise PairDNAValidationError(
            f"{field} exceeds the safe legacy false-positive ceiling; rebuild this profile"
        )
    bitmap = str(source.get("bitmap_hex") or "").lower()
    if len(bitmap) != _LEGACY_BLOOM_BITS // 4 or any(
        character not in "0123456789abcdef" for character in bitmap
    ):
        raise PairDNAValidationError(f"{field}.bitmap_hex is malformed")
    return {
        "algorithm": "SHA256_BLOOM_V1",
        "bits": _LEGACY_BLOOM_BITS,
        "hashes": _LEGACY_BLOOM_HASHES,
        "insertions": insertions,
        "bitmap_hex": bitmap,
        "false_positive_ceiling": probability,
    }


def _validate_bloom(value: object, *, field: str) -> dict[str, Any]:
    design_ceiling = _segmented_false_positive_ceiling()
    if design_ceiling > PAIR_DNA_DEDUPE_FALSE_POSITIVE_CEILING:
        raise PairDNAValidationError(
            f"{field} design exceeds its declared false-positive ceiling"
        )
    source = _required_mapping(value, field=field)
    if source.get("algorithm") == "SHA256_BLOOM_V1":
        legacy = _validate_legacy_bloom(source, field=field)
        return {
            "algorithm": "SHA256_SEGMENTED_BLOOM_V2",
            "insertions": int(source.get("insertions", 0)),
            "capacity": PAIR_DNA_DEDUPE_CAPACITY,
            "false_positive_ceiling": float(
                _mapping(legacy).get("false_positive_ceiling", 0.0)
            ),
            "design_false_positive_ceiling": design_ceiling,
            "segment_bits": PAIR_DNA_DEDUPE_SEGMENT_BITS,
            "segment_hashes": PAIR_DNA_DEDUPE_SEGMENT_HASHES,
            "segment_capacity": PAIR_DNA_DEDUPE_SEGMENT_CAPACITY,
            "max_segments": PAIR_DNA_DEDUPE_MAX_SEGMENTS,
            "segments": [],
            "legacy_sha256_bloom_v1": legacy,
        }
    if source.get("algorithm") != "SHA256_SEGMENTED_BLOOM_V2":
        raise PairDNAValidationError(f"{field}.algorithm is not supported")
    segments_raw = source.get("segments")
    if not isinstance(segments_raw, list):
        raise PairDNAValidationError(f"{field}.segments must be a list")
    segment_values = cast(list[object], segments_raw)
    if len(segment_values) > PAIR_DNA_DEDUPE_MAX_SEGMENTS:
        raise PairDNAValidationError(f"{field}.segments exceeds its bounded capacity")
    segments = [
        _validate_segment(raw, field=f"{field}.segments[{index}]")
        for index, raw in enumerate(segment_values)
    ]
    new_insertions = sum(int(row["insertions"]) for row in segments)
    legacy = _validate_legacy_bloom(
        source.get("legacy_sha256_bloom_v1"),
        field=f"{field}.legacy_sha256_bloom_v1",
    )
    legacy_insertions = int(_mapping(legacy).get("insertions", 0))
    insertions = _integer(source.get("insertions"), field=f"{field}.insertions")
    if insertions != new_insertions + legacy_insertions:
        raise PairDNAValidationError(f"{field}.insertions does not match segment totals")
    new_probability = 1.0
    for row in segments:
        probability = _bloom_probability(
            bits=PAIR_DNA_DEDUPE_SEGMENT_BITS,
            hashes=PAIR_DNA_DEDUPE_SEGMENT_HASHES,
            insertions=int(row["insertions"]),
        )
        new_probability *= 1.0 - probability
    combined = 1.0 - new_probability
    legacy_probability = float(_mapping(legacy).get("false_positive_ceiling", 0.0))
    combined = 1.0 - (1.0 - combined) * (1.0 - legacy_probability)
    if combined > _LEGACY_FALSE_POSITIVE_CEILING:
        raise PairDNAValidationError(f"{field} exceeds its false-positive safety ceiling")
    return {
        "algorithm": "SHA256_SEGMENTED_BLOOM_V2",
        "insertions": insertions,
        "capacity": PAIR_DNA_DEDUPE_CAPACITY,
        "false_positive_ceiling": combined,
        "design_false_positive_ceiling": design_ceiling,
        "segment_bits": PAIR_DNA_DEDUPE_SEGMENT_BITS,
        "segment_hashes": PAIR_DNA_DEDUPE_SEGMENT_HASHES,
        "segment_capacity": PAIR_DNA_DEDUPE_SEGMENT_CAPACITY,
        "max_segments": PAIR_DNA_DEDUPE_MAX_SEGMENTS,
        "segments": segments,
        "legacy_sha256_bloom_v1": legacy,
    }


def _bloom_contains(bloom: Mapping[str, Any], sequence_id: str) -> bool:
    canonical = _validate_bloom(bloom, field="sequence_dedupe_bloom")
    legacy = _mapping(canonical.get("legacy_sha256_bloom_v1"))
    if legacy:
        bitmap = int(str(legacy.get("bitmap_hex") or "0"), 16)
        positions = _legacy_bloom_positions(sequence_id)
        if all(bitmap & (1 << position) for position in positions):
            return True
    for row in cast(list[dict[str, Any]], canonical["segments"]):
        bitmap = int.from_bytes(base64.b64decode(str(row["bitmap_b64"])), "big")
        positions = _bloom_positions(
            sequence_id,
            bits=PAIR_DNA_DEDUPE_SEGMENT_BITS,
            hashes=PAIR_DNA_DEDUPE_SEGMENT_HASHES,
        )
        if all(bitmap & (1 << position) for position in positions):
            return True
    return False


def _bloom_add(bloom: Mapping[str, Any], sequence_id: str) -> dict[str, Any]:
    canonical = _validate_bloom(bloom, field="sequence_dedupe_bloom")
    segments = cast(list[dict[str, Any]], canonical["segments"])
    if not segments or int(segments[-1]["insertions"]) >= PAIR_DNA_DEDUPE_SEGMENT_CAPACITY:
        if len(segments) >= PAIR_DNA_DEDUPE_MAX_SEGMENTS:
            raise PairDNAValidationError(
                "sequence identity capacity reached; shard the Pair DNA store before recording more"
            )
        segments.append(_empty_dedupe_segment())
    active = segments[-1]
    bitmap = int.from_bytes(base64.b64decode(str(active["bitmap_b64"])), "big")
    for position in _bloom_positions(
        sequence_id,
        bits=PAIR_DNA_DEDUPE_SEGMENT_BITS,
        hashes=PAIR_DNA_DEDUPE_SEGMENT_HASHES,
    ):
        bitmap |= 1 << position
    active["bitmap_b64"] = base64.b64encode(
        bitmap.to_bytes(PAIR_DNA_DEDUPE_SEGMENT_BITS // 8, "big")
    ).decode("ascii")
    active["insertions"] = int(active["insertions"]) + 1
    canonical["segments"] = segments
    canonical["insertions"] = int(canonical["insertions"]) + 1
    return _validate_bloom(canonical, field="sequence_dedupe_bloom")


def _validate_profile(profile: Mapping[str, Any], *, key: str) -> dict[str, Any]:
    pair_id = str(profile.get("pair_id") or "")
    if pair_id != key:
        raise PairDNAValidationError(f"profile {key} pair_id mismatch")
    symbol = _canonical_identity(profile.get("symbol"), field=f"profiles.{key}.symbol", maximum=64)
    timeframe = _canonical_identity(profile.get("timeframe"), field=f"profiles.{key}.timeframe", maximum=32)
    if pair_profile_key_v3(symbol, timeframe) != key:
        raise PairDNAValidationError(f"profile {key} identity digest mismatch")
    candle = _required_mapping(profile.get("candle"), field=f"profiles.{key}.candle")
    behavior = _required_mapping(profile.get("behavior"), field=f"profiles.{key}.behavior")
    correlations = _required_mapping(
        profile.get("outcome_correlations"),
        field=f"profiles.{key}.outcome_correlations",
    )
    if len(correlations) > MAX_PAIR_DNA_ASSOCIATIONS:
        raise PairDNAValidationError(f"profile {key} exceeds the outcome association bound")
    canonical_correlations: dict[str, Any] = {}
    for feature, raw_row in correlations.items():
        row = _required_mapping(
            raw_row,
            field=f"profiles.{key}.outcome_correlations.{feature}",
        )
        canonical_correlations[str(feature)] = {
            "support": _integer(row.get("support"), field=f"correlations.{feature}.support"),
            "direction_counts": _counter(row.get("direction_counts"), field=f"correlations.{feature}.direction_counts"),
            "success_count": _integer(row.get("success_count"), field=f"correlations.{feature}.success_count"),
            "realized_return_sum": _finite(row.get("realized_return_sum"), field=f"correlations.{feature}.realized_return_sum", default=0.0),
        }
    recent_sequences = _required_rows(
        profile.get("recent_sequences"),
        field=f"profiles.{key}.recent_sequences",
    )
    seen_sequence_ids = _required_strings(
        profile.get("seen_sequence_ids"),
        field=f"profiles.{key}.seen_sequence_ids",
        maximum_length=256,
    )
    if len(seen_sequence_ids) != len(set(seen_sequence_ids)):
        raise PairDNAValidationError(f"profile {key} has duplicate seen sequence ids")
    regime_counts = _counter(profile.get("regime_counts"), field="regime_counts")
    if len(regime_counts) > MAX_PAIR_DNA_REGIMES + 1:
        raise PairDNAValidationError(f"profile {key} exceeds the regime bound")
    object_type_counts = _counter(profile.get("object_type_counts"), field="object_type_counts")
    if len(object_type_counts) > MAX_PAIR_DNA_OBJECT_TYPES + 1:
        raise PairDNAValidationError(f"profile {key} exceeds the object-type bound")
    return {
        "pair_id": pair_id,
        "symbol": symbol,
        "timeframe": timeframe,
        "first_observed_at": str(profile.get("first_observed_at") or ""),
        "last_observed_at": str(profile.get("last_observed_at") or ""),
        "updated_ordinal": _integer(profile.get("updated_ordinal"), field=f"profiles.{key}.updated_ordinal"),
        "observation_count": _integer(profile.get("observation_count"), field=f"profiles.{key}.observation_count"),
        "candle_count": _integer(profile.get("candle_count"), field=f"profiles.{key}.candle_count"),
        "candle": {
            "direction_counts": _counter(candle.get("direction_counts"), field=f"profiles.{key}.candle.direction_counts"),
            "type_counts": _counter(candle.get("type_counts"), field=f"profiles.{key}.candle.type_counts"),
            "personality_counts": _counter(candle.get("personality_counts"), field=f"profiles.{key}.candle.personality_counts"),
            "body_ratio_sum": _finite(candle.get("body_ratio_sum"), field="body_ratio_sum", default=0.0),
            "upper_wick_ratio_sum": _finite(candle.get("upper_wick_ratio_sum"), field="upper_wick_ratio_sum", default=0.0),
            "lower_wick_ratio_sum": _finite(candle.get("lower_wick_ratio_sum"), field="lower_wick_ratio_sum", default=0.0),
            "rejection_count": _integer(candle.get("rejection_count"), field="rejection_count"),
            "acceptance_count": _integer(candle.get("acceptance_count"), field="acceptance_count"),
            "upper_sweep_count": _integer(candle.get("upper_sweep_count"), field="upper_sweep_count"),
            "lower_sweep_count": _integer(candle.get("lower_sweep_count"), field="lower_sweep_count"),
        },
        "behavior": {
            "state_candle_counts": _counter(behavior.get("state_candle_counts"), field="state_candle_counts"),
            "segment_counts": _counter(behavior.get("segment_counts"), field="segment_counts"),
            "segment_candle_sum": _counter(behavior.get("segment_candle_sum"), field="segment_candle_sum"),
            "segment_duration_sum": _counter(behavior.get("segment_duration_sum"), field="segment_duration_sum"),
            "segment_normalized_change_sum": {
                str(name): _finite(raw, field=f"segment_normalized_change_sum.{name}")
                for name, raw in _required_mapping(
                    behavior.get("segment_normalized_change_sum"),
                    field="segment_normalized_change_sum",
                ).items()
            },
            "transition_counts": _counter(behavior.get("transition_counts"), field="transition_counts"),
            "major_trend_counts": _counter(behavior.get("major_trend_counts"), field="major_trend_counts"),
            "inner_trend_counts": _counter(behavior.get("inner_trend_counts"), field="inner_trend_counts"),
        },
        "regime_counts": regime_counts,
        "coordinate_space_counts": _counter(profile.get("coordinate_space_counts"), field="coordinate_space_counts"),
        "object_type_counts": object_type_counts,
        "outcome_correlations": canonical_correlations,
        "association_overflow_count": _integer(
            profile.get("association_overflow_count"),
            field=f"profiles.{key}.association_overflow_count",
        ),
        "recent_sequences": recent_sequences,
        "seen_sequence_ids": seen_sequence_ids,
        "identity_ledger": _validate_identity_ledger(
            profile.get("identity_ledger"),
            field=f"profiles.{key}.identity_ledger",
        ),
        "sequence_dedupe_bloom": _validate_bloom(
            profile.get("sequence_dedupe_bloom"),
            field=f"profiles.{key}.sequence_dedupe_bloom",
        ),
    }


def _validate_state(
    raw: Mapping[str, Any],
    *,
    max_pairs: int,
    recent_sequence_limit: int,
) -> dict[str, Any]:
    if raw.get("schema_version") != PAIR_DNA_SCHEMA_VERSION:
        raise PairDNAValidationError(f"pair DNA schema must be {PAIR_DNA_SCHEMA_VERSION}")
    if raw.get("study_only") is not True or raw.get("execution_authority") is not False:
        raise PairDNAValidationError("pair DNA store must be study-only")
    profiles_raw = _required_mapping(raw.get("profiles"), field="profiles")
    if len(profiles_raw) > max_pairs:
        raise PairDNAValidationError("pair DNA store exceeds configured pair bound")
    profiles: dict[str, Any] = {}
    for key, value in profiles_raw.items():
        profile = _validate_profile(_mapping(value), key=str(key))
        if len(cast(list[object], profile["recent_sequences"])) > recent_sequence_limit:
            raise PairDNAValidationError("pair DNA recent sequence bound was exceeded")
        if len(cast(list[str], profile["seen_sequence_ids"])) > recent_sequence_limit:
            raise PairDNAValidationError("pair DNA sequence identity bound was exceeded")
        profiles[str(key)] = profile
    return {
        "schema_version": PAIR_DNA_SCHEMA_VERSION,
        "study_only": True,
        "execution_authority": False,
        "next_ordinal": max(1, _integer(raw.get("next_ordinal"), field="next_ordinal", default=1)),
        "profiles": profiles,
    }


def _validate_studies(
    candle_study: Mapping[str, Any],
    behavior_study: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if candle_study.get("schema_version") != CANDLE_INTELLIGENCE_SCHEMA_VERSION:
        raise PairDNAValidationError("candle study schema is not PhoenixGuard V3")
    if behavior_study.get("schema_version") != BEHAVIORAL_SEQUENCE_SCHEMA_VERSION:
        raise PairDNAValidationError("behavior study schema is not PhoenixGuard V3")
    if candle_study.get("status") != "STUDIED" or behavior_study.get("status") != "STUDIED":
        raise PairDNAValidationError("only completed studies can update Pair DNA")
    if candle_study.get("execution_authority") is not False or behavior_study.get("execution_authority") is not False:
        raise PairDNAValidationError("study evidence must not have execution authority")
    candles = _required_rows(candle_study.get("candles"), field="candle_study.candles")
    segments = _required_rows(behavior_study.get("segments"), field="behavior_study.segments")
    if not candles or not segments:
        raise PairDNAValidationError("completed studies require candle and behavior rows")
    coordinate_spaces = {str(row.get("coordinate_space") or "UNKNOWN").upper() for row in candles}
    if len(coordinate_spaces) != 1:
        raise PairDNAValidationError("one Pair DNA study cannot mix coordinate spaces")
    return candles, segments


def _outcome_direction(outcome: Mapping[str, Any]) -> str:
    for key in ("direction", "outcome_direction", "next_direction", "actual_direction"):
        text = str(outcome.get(key) or "").strip().upper()
        if text in {"BUY", "BULL", "BULLISH", "UP", "UP_SWING"}:
            return "UP"
        if text in {"SELL", "BEAR", "BEARISH", "DOWN", "DOWN_SWING"}:
            return "DOWN"
        if text in {"REST", "SIDEWAYS", "FLAT", "TIE", "HOLD"}:
            return "REST"
    # P&L sign cannot identify market direction: a profitable SELL produces a
    # positive return while price moved down.  Direction therefore requires a
    # separate observed market-direction field.
    return "UNKNOWN"


def _outcome_success(outcome: Mapping[str, Any]) -> bool:
    explicit = outcome.get("success")
    if isinstance(explicit, bool):
        return explicit
    result = str(outcome.get("result") or outcome.get("status") or "").strip().upper()
    return result in {"WIN", "WON", "SUCCESS", "SUCCESSFUL", "MATCHED", "CORRECT"}


def _correlation_features(
    candles: Sequence[Mapping[str, Any]],
    behavior: Mapping[str, Any],
    objects: Sequence[Mapping[str, Any]],
) -> list[str]:
    latest = candles[-1]
    candle_type = str(latest.get("type") or "UNKNOWN").upper()
    personality = str(latest.get("personality") or "UNKNOWN").upper()
    coordinate_space = str(latest.get("coordinate_space") or "UNKNOWN").upper()
    features = {
        f"CANDLE_TYPE:{candle_type}",
        f"PERSONALITY:{personality}",
        f"REGIME:{str(latest.get('regime') or 'UNKNOWN').upper()}",
        f"CURRENT_STATE:{str(_mapping(behavior.get('current_state')).get('state') or 'UNKNOWN').upper()}",
        f"COORDINATE_SPACE:{coordinate_space}",
    }
    object_types: set[str] = set()
    for row in objects:
        object_type = str(row.get("object_type") or row.get("type") or "").strip().upper()
        if object_type:
            features.add(f"OBJECT:{object_type}")
            object_types.add(object_type)
    ordered_objects = sorted(object_types)[:32]
    for object_type in ordered_objects:
        features.add(f"PAIR:CANDLE_TYPE={candle_type}&OBJECT={object_type}")
        features.add(f"PAIR:PERSONALITY={personality}&OBJECT={object_type}")
    for index, first in enumerate(ordered_objects):
        for second in ordered_objects[index + 1 :]:
            features.add(f"PAIR:OBJECT={first}&OBJECT={second}")
    return sorted(features)[:768]


def _apply_outcome_correlation(
    profile: dict[str, Any],
    features: Sequence[str],
    outcome: Mapping[str, Any],
) -> None:
    direction = _outcome_direction(outcome)
    if direction == "UNKNOWN":
        return
    realized_return = _finite(outcome.get("realized_return"), field="outcome.realized_return", default=0.0)
    correlations = _mapping(profile.get("outcome_correlations"))
    for feature in features:
        if feature not in correlations and len(correlations) >= MAX_PAIR_DNA_ASSOCIATIONS:
            profile["association_overflow_count"] = int(
                profile.get("association_overflow_count", 0)
            ) + 1
            continue
        row: dict[str, Any] = _mapping(correlations.get(feature))
        if not row:
            row = {
                "support": 0,
                "direction_counts": {},
                "success_count": 0,
                "realized_return_sum": 0.0,
            }
        row["support"] = _integer(row.get("support"), field=f"correlations.{feature}.support") + 1
        direction_counts: dict[str, Any] = _mapping(row.get("direction_counts"))
        _increment(direction_counts, direction)
        row["direction_counts"] = direction_counts
        row["success_count"] = _integer(row.get("success_count"), field=f"correlations.{feature}.success_count") + int(_outcome_success(outcome))
        row["realized_return_sum"] = _finite(
            row.get("realized_return_sum"),
            field=f"correlations.{feature}.realized_return_sum",
            default=0.0,
        ) + realized_return
        correlations[feature] = row
    profile["outcome_correlations"] = correlations


def _incremental_evidence(
    profile: Mapping[str, Any],
    *,
    candles: Sequence[Mapping[str, Any]],
    segments: Sequence[Mapping[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[tuple[dict[str, Any], dict[str, Any]]],
    dict[str, Any],
]:
    """Select only causally new candles and completed segment boundaries.

    Closed timestamps or resolver event sequences form one locked monotonic
    order domain per pair.  Domains are never numerically compared or mixed.
    This intentionally ignores backfills/out-of-order rows; accepting them
    would make a bounded store unable to distinguish a backfill from
    rolling-window replay.  Segment evidence is admitted only when a following
    segment proves the boundary and both sides resolve in that same domain.
    """

    ledger = _validate_identity_ledger(
        profile.get("identity_ledger"), field="identity_ledger"
    )
    stable_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
    marker_by_id: dict[str, dict[str, Any]] = {}
    ambiguous_candle_ids: set[str] = set()
    previous_window_order = -math.inf
    order_domain = str(ledger.get("candle_order_domain") or "")
    for raw in candles:
        row = dict(raw)
        marker = _stable_candle_marker(row)
        if marker is None:
            ledger["skipped_unstable_candles"] = (
                int(ledger["skipped_unstable_candles"]) + 1
            )
            continue
        marker_domain = str(marker.get("order_domain") or "")
        if order_domain and marker_domain != order_domain:
            ledger["skipped_order_domain_conflicts"] = (
                int(ledger["skipped_order_domain_conflicts"]) + 1
            )
            continue
        if not order_domain:
            order_domain = marker_domain
        marker_order = _order_number(
            marker["order"], field="candle.timestamp_order"
        )
        if marker_order <= previous_window_order:
            ledger["skipped_unstable_candles"] = (
                int(ledger["skipped_unstable_candles"]) + 1
            )
            continue
        previous_window_order = marker_order
        stable_rows.append((row, marker))
        candle_id = str(marker.get("candle_id") or "")
        if candle_id and candle_id not in marker_by_id:
            marker_by_id[candle_id] = marker
        elif candle_id:
            ambiguous_candle_ids.add(candle_id)
            marker_by_id.pop(candle_id, None)

    for candle_id in ambiguous_candle_ids:
        marker_by_id.pop(candle_id, None)
    if order_domain:
        ledger["candle_order_domain"] = order_domain

    prior_candle = _mapping(ledger.get("candle_high_watermark"))
    prior_candle_order: int | float = (
        _order_number(
            prior_candle.get("order"), field="candle_high_watermark.order"
        )
        if prior_candle
        else -math.inf
    )
    # An old V3 profile has aggregates but no boundary ledger.  Establish a
    # baseline without replaying its current window.  One live candle may be
    # conservatively missed during migration; historical totals are never
    # duplicated or corrupted.
    migrating_legacy_profile = (
        ledger.get("baseline_initialized") is not True
        and int(profile.get("candle_count", 0)) > 0
    )
    new_candles: list[dict[str, Any]] = []
    for row, marker in stable_rows:
        marker_order = _order_number(marker["order"], field="candle.timestamp_order")
        if not migrating_legacy_profile and marker_order > prior_candle_order:
            new_candles.append(row)
        else:
            ledger["skipped_overlapping_candles"] = (
                int(ledger["skipped_overlapping_candles"]) + 1
            )
    if stable_rows:
        latest_marker = max(
            stable_rows,
            key=lambda item: _order_number(
                item[1]["order"], field="candle.timestamp_order"
            ),
        )[1]
        latest_order = _order_number(
            latest_marker["order"], field="candle.timestamp_order"
        )
        if latest_order > prior_candle_order:
            ledger["candle_high_watermark"] = {
                "identity": str(latest_marker["identity"]),
                "order": latest_order,
            }
    ledger["baseline_initialized"] = True
    ledger["accepted_candles"] = int(ledger["accepted_candles"]) + len(new_candles)

    prior_open = _mapping(ledger.get("open_segment"))
    prior_boundary = _mapping(ledger.get("completed_boundary_high_watermark"))
    prior_boundary_order: int | float = (
        _order_number(
            prior_boundary.get("order"),
            field="completed_boundary_high_watermark.order",
        )
        if prior_boundary
        else -math.inf
    )
    if migrating_legacy_profile and stable_rows:
        latest_marker = max(
            stable_rows,
            key=lambda item: _order_number(
                item[1]["order"], field="candle.timestamp_order"
            ),
        )[1]
        prior_boundary_order = _order_number(
            latest_marker["order"], field="candle.timestamp_order"
        )
        ledger["completed_boundary_high_watermark"] = {
            "identity": f"MIGRATION_BASELINE:{latest_marker['identity']}",
            "order": prior_boundary_order,
        }
    completed: list[tuple[dict[str, Any], dict[str, Any]]] = []
    prior_open_completed = False
    for index, raw_segment in enumerate(segments[:-1]):
        segment = dict(raw_segment)
        following = dict(segments[index + 1])
        state = str(segment.get("state") or "UNKNOWN").upper()
        start = marker_by_id.get(str(segment.get("start_candle_id") or ""))
        end = marker_by_id.get(str(segment.get("end_candle_id") or ""))
        next_start = marker_by_id.get(str(following.get("start_candle_id") or ""))
        prior_matches = bool(
            prior_open
            and state == str(prior_open.get("state") or "").upper()
            and start
            and str(start["identity"]) == str(prior_open.get("start_identity") or "")
        )
        # The first segment in a rolling window is left-censored unless it is
        # the exact segment that was persisted as open on the previous update.
        fully_observed = index > 0 or prior_matches
        if not fully_observed or start is None or end is None or next_start is None:
            ledger["skipped_incomplete_segments"] = (
                int(ledger["skipped_incomplete_segments"]) + 1
            )
            continue
        boundary_identity = (
            f"SEG:{state}:{start['identity']}->{end['identity']}|"
            f"NEXT:{str(following.get('state') or 'UNKNOWN').upper()}:{next_start['identity']}"
        )
        boundary_order = _order_number(
            next_start["order"], field="segment.next_start_order"
        )
        if boundary_order <= prior_boundary_order:
            ledger["skipped_overlapping_segments"] = (
                int(ledger["skipped_overlapping_segments"]) + 1
            )
            continue
        if prior_matches and prior_open.get("already_counted") is True:
            # Legacy V3 counted its then-open segment.  Advance past that one
            # boundary without counting it again when it eventually closes.
            ledger["skipped_overlapping_segments"] = (
                int(ledger["skipped_overlapping_segments"]) + 1
            )
            prior_boundary_order = boundary_order
            ledger["completed_boundary_high_watermark"] = {
                "identity": f"MIGRATION_SUPPRESSED:{boundary_identity}",
                "order": boundary_order,
            }
            prior_open_completed = True
            continue
        completed.append((segment, following))
        prior_boundary_order = boundary_order
        ledger["completed_boundary_high_watermark"] = {
            "identity": boundary_identity,
            "order": boundary_order,
        }
        prior_open_completed = prior_open_completed or prior_matches

    ledger["accepted_completed_segments"] = int(
        ledger["accepted_completed_segments"]
    ) + len(completed)
    if segments:
        current = dict(segments[-1])
        current_state = str(current.get("state") or "UNKNOWN").upper()
        current_start = marker_by_id.get(str(current.get("start_candle_id") or ""))
        if current_start is not None:
            retain_prior = bool(
                prior_open
                and not prior_open_completed
                and current_state == str(prior_open.get("state") or "").upper()
                and _order_number(
                    prior_open.get("start_order"),
                    field="open_segment.start_order",
                )
                <= _order_number(
                    current_start["order"], field="segment.start_order"
                )
            )
            if not retain_prior:
                ledger["open_segment"] = {
                    "state": current_state,
                    "start_identity": str(current_start["identity"]),
                    "start_order": _order_number(
                        current_start["order"], field="segment.start_order"
                    ),
                    "already_counted": migrating_legacy_profile,
                }
    return new_candles, completed, ledger


def _apply_study(
    profile: dict[str, Any],
    *,
    candle_study: Mapping[str, Any],
    behavior_study: Mapping[str, Any],
    candles: Sequence[Mapping[str, Any]],
    segments: Sequence[Mapping[str, Any]],
    objects: Sequence[Mapping[str, Any]],
    outcome: Mapping[str, Any],
    sequence_id: str,
    observed_at: str,
    ordinal: int,
    recent_sequence_limit: int,
) -> None:
    new_candles, completed_segments, identity_ledger = _incremental_evidence(
        profile,
        candles=candles,
        segments=segments,
    )
    has_stable_evidence = bool(new_candles or completed_segments)
    # observation_count describes unique study envelopes.  Candle/segment
    # aggregates below are independently gated by stable boundary evidence.
    profile["observation_count"] = int(profile["observation_count"]) + 1
    profile["candle_count"] = int(profile["candle_count"]) + len(new_candles)
    profile["updated_ordinal"] = ordinal
    profile["first_observed_at"] = str(profile.get("first_observed_at") or observed_at)
    profile["last_observed_at"] = observed_at

    candle_aggregate = _mapping(profile.get("candle"))
    for row in new_candles:
        direction_counts = _mapping(candle_aggregate.get("direction_counts"))
        _increment(direction_counts, row.get("direction"))
        candle_aggregate["direction_counts"] = direction_counts
        type_counts = _mapping(candle_aggregate.get("type_counts"))
        _increment(type_counts, row.get("type"))
        candle_aggregate["type_counts"] = type_counts
        personality_counts = _mapping(candle_aggregate.get("personality_counts"))
        _increment(personality_counts, row.get("personality"))
        candle_aggregate["personality_counts"] = personality_counts
        ratios = _mapping(row.get("ratios"))
        candle_aggregate["body_ratio_sum"] = float(candle_aggregate.get("body_ratio_sum", 0.0)) + _finite(ratios.get("body_to_range"), field="body_to_range")
        candle_aggregate["upper_wick_ratio_sum"] = float(candle_aggregate.get("upper_wick_ratio_sum", 0.0)) + _finite(ratios.get("upper_wick_to_range"), field="upper_wick_to_range")
        candle_aggregate["lower_wick_ratio_sum"] = float(candle_aggregate.get("lower_wick_ratio_sum", 0.0)) + _finite(ratios.get("lower_wick_to_range"), field="lower_wick_to_range")
        interaction = _mapping(row.get("interaction"))
        rejection = _mapping(interaction.get("rejection"))
        acceptance = _mapping(interaction.get("acceptance"))
        candle_aggregate["rejection_count"] = int(candle_aggregate.get("rejection_count", 0)) + int(bool(rejection.get("detected")))
        candle_aggregate["acceptance_count"] = int(candle_aggregate.get("acceptance_count", 0)) + int(bool(acceptance.get("detected")))
        candle_aggregate["upper_sweep_count"] = int(candle_aggregate.get("upper_sweep_count", 0)) + int(bool(rejection.get("upper_wick_swept_previous_high")))
        candle_aggregate["lower_sweep_count"] = int(candle_aggregate.get("lower_sweep_count", 0)) + int(bool(rejection.get("lower_wick_swept_previous_low")))
    profile["candle"] = candle_aggregate

    behavior_aggregate = _mapping(profile.get("behavior"))
    coordinate_space = str(candles[-1].get("coordinate_space") or "UNKNOWN").upper()
    state_counts = _mapping(behavior_aggregate.get("state_candle_counts"))
    candle_id_counts = Counter(str(row.get("candle_id") or "") for row in candles)
    new_candle_ids = {
        str(row.get("candle_id") or "")
        for row in new_candles
        if candle_id_counts[str(row.get("candle_id") or "")] == 1
    }
    for row in _rows(behavior_study.get("states")):
        if str(row.get("candle_id") or "") in new_candle_ids:
            _increment(
                state_counts,
                f"{coordinate_space}|{str(row.get('state') or 'UNKNOWN').upper()}",
            )
    behavior_aggregate["state_candle_counts"] = state_counts
    transition_counts = _mapping(behavior_aggregate.get("transition_counts"))
    for segment, following in completed_segments:
        state = str(segment.get("state") or "UNKNOWN").upper()
        partition = f"{coordinate_space}|{state}"
        for field, value_name in (
            ("segment_counts", None),
            ("segment_candle_sum", "candle_count"),
            ("segment_duration_sum", "duration_seconds"),
        ):
            counter = _mapping(behavior_aggregate.get(field))
            amount = 1 if value_name is None else _integer(segment.get(value_name), field=f"segment.{value_name}")
            _increment(counter, partition, amount)
            behavior_aggregate[field] = counter
        change_sums = _mapping(behavior_aggregate.get("segment_normalized_change_sum"))
        change_sums[partition] = float(change_sums.get(partition, 0.0)) + _finite(
            segment.get("absolute_change_in_median_ranges"),
            field="segment.absolute_change_in_median_ranges",
        )
        behavior_aggregate["segment_normalized_change_sum"] = change_sums
        token = (
            f"{coordinate_space}|{state}->"
            f"{str(following.get('state') or 'UNKNOWN').upper()}"
        )
        _increment(transition_counts, token)
    behavior_aggregate["transition_counts"] = transition_counts
    for field, source_field in (("major_trend_counts", "major_trend"), ("inner_trend_counts", "inner_trend")):
        counter = _mapping(behavior_aggregate.get(field))
        label = str(_mapping(behavior_study.get(source_field)).get("label") or "UNKNOWN").upper()
        if has_stable_evidence:
            _increment(counter, f"{coordinate_space}|{label}")
        behavior_aggregate[field] = counter
    profile["behavior"] = behavior_aggregate

    regimes = _mapping(profile.get("regime_counts"))
    for candle in new_candles:
        _increment_bounded(
            regimes,
            candle.get("regime"),
            maximum_keys=MAX_PAIR_DNA_REGIMES,
        )
    profile["regime_counts"] = regimes
    coordinate_counts = _mapping(profile.get("coordinate_space_counts"))
    if new_candles:
        _increment(coordinate_counts, coordinate_space, len(new_candles))
    profile["coordinate_space_counts"] = coordinate_counts
    object_counts = _mapping(profile.get("object_type_counts"))
    if has_stable_evidence:
        for row in objects:
            object_type = str(
                row.get("object_type") or row.get("type") or ""
            ).strip().upper()
            if object_type:
                _increment_bounded(
                    object_counts,
                    object_type,
                    maximum_keys=MAX_PAIR_DNA_OBJECT_TYPES,
                )
    profile["object_type_counts"] = object_counts
    profile["identity_ledger"] = identity_ledger
    if has_stable_evidence:
        _apply_outcome_correlation(
            profile,
            _correlation_features(candles, behavior_study, objects),
            outcome,
        )

    recent = _rows(profile.get("recent_sequences"))
    recent.append(
        {
            "sequence_id": sequence_id,
            "observed_at": observed_at,
            "ordinal": ordinal,
            "sequence_signature": str(candle_study.get("sequence_signature") or ""),
            "candle_count": len(new_candles),
            "window_candle_count": len(candles),
            "completed_segment_count": len(completed_segments),
            "major_trend": str(_mapping(behavior_study.get("major_trend")).get("label") or "UNKNOWN"),
            "inner_trend": str(_mapping(behavior_study.get("inner_trend")).get("label") or "UNKNOWN"),
            "current_state": str(_mapping(behavior_study.get("current_state")).get("state") or "UNKNOWN"),
            "coordinate_space": coordinate_space,
        }
    )
    profile["recent_sequences"] = recent[-recent_sequence_limit:]
    seen = [str(value) for value in cast(Sequence[object], profile.get("seen_sequence_ids", []))]
    seen.append(sequence_id)
    profile["seen_sequence_ids"] = seen[-recent_sequence_limit:]
    profile["sequence_dedupe_bloom"] = _bloom_add(
        _mapping(profile.get("sequence_dedupe_bloom")),
        sequence_id,
    )


def _derived_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    result = deepcopy(dict(profile))
    candle_count = int(result.get("candle_count", 0))
    candle = _mapping(result.get("candle"))
    denominator = max(1, candle_count)
    candle["averages"] = {
        "body_to_range": round(float(candle.get("body_ratio_sum", 0.0)) / denominator, 6),
        "upper_wick_to_range": round(float(candle.get("upper_wick_ratio_sum", 0.0)) / denominator, 6),
        "lower_wick_to_range": round(float(candle.get("lower_wick_ratio_sum", 0.0)) / denominator, 6),
    }
    candle["rates"] = {
        "rejection": round(int(candle.get("rejection_count", 0)) / denominator, 6),
        "acceptance": round(int(candle.get("acceptance_count", 0)) / denominator, 6),
        "upper_sweep": round(int(candle.get("upper_sweep_count", 0)) / denominator, 6),
        "lower_sweep": round(int(candle.get("lower_sweep_count", 0)) / denominator, 6),
    }
    result["candle"] = candle
    behavior = _mapping(result.get("behavior"))
    transition_counts = _counter(behavior.get("transition_counts"), field="transition_counts")
    outgoing: Counter[str] = Counter()
    for token, count in transition_counts.items():
        source = token.split("->", maxsplit=1)[0]
        outgoing[source] += count
    behavior["transition_probabilities"] = {
        token: round(count / outgoing[token.split("->", maxsplit=1)[0]], 6)
        for token, count in sorted(transition_counts.items())
        if outgoing[token.split("->", maxsplit=1)[0]]
    }
    segment_counts = _counter(behavior.get("segment_counts"), field="segment_counts")
    behavior["segment_averages"] = {
        state: {
            "candles": round(int(_mapping(behavior.get("segment_candle_sum")).get(state, 0)) / count, 4),
            "duration_seconds": round(int(_mapping(behavior.get("segment_duration_sum")).get(state, 0)) / count, 2),
            "absolute_change_in_median_ranges": round(
                float(_mapping(behavior.get("segment_normalized_change_sum")).get(state, 0.0)) / count,
                8,
            ),
        }
        for state, count in sorted(segment_counts.items())
        if count > 0
    }
    result["behavior"] = behavior
    correlations = _mapping(result.get("outcome_correlations"))
    result["outcome_association_contract"] = {
        "analysis_kind": "MARGINAL_AND_PAIRWISE_FEATURE_ASSOCIATION",
        "causal": False,
        "note": "Counts describe historical association and do not prove causation.",
    }
    result["marginal_and_pairwise_outcome_associations"] = [
        {
            "feature": feature,
            "support": int(row.get("support", 0)),
            "direction_probabilities": {
                direction: round((int(_mapping(row.get("direction_counts")).get(direction, 0)) + 1) / (int(row.get("support", 0)) + 3), 6)
                for direction in ("UP", "DOWN", "REST")
            },
            "success_rate": round(int(row.get("success_count", 0)) / max(1, int(row.get("support", 0))), 6),
            "average_realized_return": round(float(row.get("realized_return_sum", 0.0)) / max(1, int(row.get("support", 0))), 8),
        }
        for feature, raw in sorted(correlations.items())
        for row in [_mapping(raw)]
    ]
    result["study_only"] = True
    result["execution_authority"] = False
    return result


class PairDNAStoreV3:
    """Transactional JSON store for bounded per-pair cumulative studies."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_pairs: int = DEFAULT_MAX_PAIR_PROFILES,
        recent_sequence_limit: int = DEFAULT_RECENT_SEQUENCE_LIMIT,
        lock_timeout_seconds: float = 5.0,
    ) -> None:
        self.path = Path(path)
        self.max_pairs = int(max_pairs)
        self.recent_sequence_limit = int(recent_sequence_limit)
        self.lock_timeout_seconds = float(lock_timeout_seconds)
        if not 1 <= self.max_pairs <= 4096:
            raise PairDNAValidationError("max_pairs must be in [1, 4096]")
        if not 1 <= self.recent_sequence_limit <= 4096:
            raise PairDNAValidationError("recent_sequence_limit must be in [1, 4096]")
        if not 0.0 < self.lock_timeout_seconds <= 60.0:
            raise PairDNAValidationError("lock_timeout_seconds must be in (0, 60]")

    def _load(self) -> dict[str, Any]:
        raw = read_json_document(self.path)
        if raw is None:
            return _empty_state()
        return _validate_state(
            raw,
            max_pairs=self.max_pairs,
            recent_sequence_limit=self.recent_sequence_limit,
        )

    def record_study(
        self,
        *,
        symbol: object,
        timeframe: object,
        candle_study: Mapping[str, Any],
        behavior_study: Mapping[str, Any],
        sequence_id: object | None = None,
        observed_at: object | None = None,
        objects: Sequence[Mapping[str, Any]] = (),
        outcome: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Atomically merge one completed sequence into its Pair DNA profile."""

        canonical_symbol = _canonical_identity(symbol, field="symbol", maximum=64)
        canonical_timeframe = _canonical_identity(timeframe, field="timeframe", maximum=32)
        pair_id = pair_profile_key_v3(canonical_symbol, canonical_timeframe)
        candles, segments = _validate_studies(candle_study, behavior_study)
        resolved_sequence_id = str(sequence_id or candle_study.get("sequence_signature") or "").strip()
        if not resolved_sequence_id or len(resolved_sequence_id) > 256:
            raise PairDNAValidationError("sequence_id is required and must not exceed 256 characters")
        resolved_observed_at = str(observed_at or candles[-1].get("timestamp") or "").strip()
        if isinstance(objects, (str, bytes, bytearray)):
            raise PairDNAValidationError("objects must be a sequence of mappings")
        object_rows: list[dict[str, Any]] = []
        for index, raw_row in enumerate(cast(Sequence[object], objects)):
            if not isinstance(raw_row, Mapping):
                raise PairDNAValidationError(f"objects[{index}] must be a mapping")
            object_rows.append(dict(cast(Mapping[str, Any], raw_row)))
        outcome_row = _mapping(outcome)

        try:
            with exclusive_store_lock(self.path, timeout_seconds=self.lock_timeout_seconds):
                state = self._load()
                profiles = _mapping(state.get("profiles"))
                if pair_id not in profiles and len(profiles) >= self.max_pairs:
                    raise PairDNAValidationError(
                        "Pair DNA capacity reached; shard or raise max_pairs without evicting lifelong profiles"
                    )
                profile = _mapping(profiles.get(pair_id)) or _empty_profile(canonical_symbol, canonical_timeframe, pair_id)
                seen = {str(value) for value in cast(Sequence[object], profile.get("seen_sequence_ids", []))}
                bloom = _validate_bloom(
                    profile.get("sequence_dedupe_bloom"),
                    field="sequence_dedupe_bloom",
                )
                if _bloom_contains(bloom, resolved_sequence_id):
                    duplicate_status = (
                        "DUPLICATE_IGNORED"
                        if resolved_sequence_id in seen
                        else "POSSIBLE_DUPLICATE_IGNORED"
                    )
                    return {
                        "schema_version": PAIR_DNA_SCHEMA_VERSION,
                        "status": duplicate_status,
                        "study_only": True,
                        "execution_authority": False,
                        "pair_id": pair_id,
                        "profile": _derived_profile(profile),
                    }
                ordinal = int(state.get("next_ordinal", 1))
                _apply_study(
                    profile,
                    candle_study=candle_study,
                    behavior_study=behavior_study,
                    candles=candles,
                    segments=segments,
                    objects=object_rows,
                    outcome=outcome_row,
                    sequence_id=resolved_sequence_id,
                    observed_at=resolved_observed_at,
                    ordinal=ordinal,
                    recent_sequence_limit=self.recent_sequence_limit,
                )
                profiles[pair_id] = profile
                state["profiles"] = profiles
                state["next_ordinal"] = ordinal + 1
                canonical = _validate_state(
                    state,
                    max_pairs=self.max_pairs,
                    recent_sequence_limit=self.recent_sequence_limit,
                )
                write_json_atomic(self.path, canonical)
        except StudyPersistenceError:
            raise
        return {
            "schema_version": PAIR_DNA_SCHEMA_VERSION,
            "status": "RECORDED",
            "study_only": True,
            "execution_authority": False,
            "pair_id": pair_id,
            "profile": _derived_profile(profile),
        }

    def get_profile(self, symbol: object, timeframe: object) -> dict[str, Any]:
        pair_id = pair_profile_key_v3(symbol, timeframe)
        with exclusive_store_lock(self.path, timeout_seconds=self.lock_timeout_seconds):
            state = self._load()
        profile = _mapping(_mapping(state.get("profiles")).get(pair_id))
        if not profile:
            return {
                "schema_version": PAIR_DNA_SCHEMA_VERSION,
                "status": "NOT_FOUND",
                "study_only": True,
                "execution_authority": False,
                "pair_id": pair_id,
                "profile": None,
            }
        return {
            "schema_version": PAIR_DNA_SCHEMA_VERSION,
            "status": "READY",
            "study_only": True,
            "execution_authority": False,
            "pair_id": pair_id,
            "profile": _derived_profile(profile),
        }

    def list_profiles(self) -> list[dict[str, Any]]:
        with exclusive_store_lock(self.path, timeout_seconds=self.lock_timeout_seconds):
            state = self._load()
        profiles = [_derived_profile(_mapping(value)) for value in _mapping(state.get("profiles")).values()]
        return sorted(profiles, key=lambda row: (str(row.get("symbol")), str(row.get("timeframe"))))


def update_pair_dna_v3(
    path: str | Path,
    *,
    symbol: object,
    timeframe: object,
    candle_study: Mapping[str, Any],
    behavior_study: Mapping[str, Any],
    sequence_id: object | None = None,
    observed_at: object | None = None,
    objects: Sequence[Mapping[str, Any]] = (),
    outcome: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Functional convenience wrapper around :class:`PairDNAStoreV3`."""

    return PairDNAStoreV3(path).record_study(
        symbol=symbol,
        timeframe=timeframe,
        candle_study=candle_study,
        behavior_study=behavior_study,
        sequence_id=sequence_id,
        observed_at=observed_at,
        objects=objects,
        outcome=outcome,
    )


__all__ = [
    "DEFAULT_MAX_PAIR_PROFILES",
    "DEFAULT_RECENT_SEQUENCE_LIMIT",
    "PAIR_DNA_DEDUPE_CAPACITY",
    "PAIR_DNA_DEDUPE_FALSE_POSITIVE_CEILING",
    "PAIR_DNA_DEDUPE_MAX_SEGMENTS",
    "PAIR_DNA_DEDUPE_SEGMENT_BITS",
    "PAIR_DNA_DEDUPE_SEGMENT_CAPACITY",
    "PAIR_DNA_DEDUPE_SEGMENT_HASHES",
    "PAIR_DNA_SCHEMA_VERSION",
    "PairDNAStoreV3",
    "PairDNAValidationError",
    "pair_profile_key_v3",
    "update_pair_dna_v3",
]
