"""Deterministic, observation-only candlestick micro analysis for PhoenixGuard V3.

The live tracker can provide either price OHLC values or authoritative pixel
geometry.  This module accepts both representations, validates the complete
candle envelope, and emits one canonical study record per closed candle.  It
does not score entries, issue trade actions, or grant execution permission.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
import hashlib
import json
import math
from statistics import median
from typing import Any, cast


CANDLE_INTELLIGENCE_SCHEMA_VERSION = "PG_CANDLE_INTELLIGENCE_V3"
MAX_STUDY_CANDLES = 512


class CandleStudyValidationError(ValueError):
    """Raised when candle geometry is incomplete, non-finite, or contradictory."""


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _finite(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise CandleStudyValidationError(f"{field} must be a finite number")
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError) as exc:
        raise CandleStudyValidationError(f"{field} must be a finite number") from exc
    if not math.isfinite(parsed):
        raise CandleStudyValidationError(f"{field} must be a finite number")
    return parsed


def _first_number(row: Mapping[str, Any], names: Sequence[str], *, field: str) -> float:
    for name in names:
        if name in row and row.get(name) is not None:
            return _finite(row.get(name), field=field)
    raise CandleStudyValidationError(f"missing required candle field: {field}")


def _explicit_closed(row: Mapping[str, Any]) -> bool | None:
    for name in ("is_closed", "closed", "complete", "is_complete"):
        if name not in row:
            continue
        value = row.get(name)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and value in {0, 1}:
            return bool(value)
        if isinstance(value, str):
            text = value.strip().lower()
            if text in {"true", "yes", "closed", "complete", "1"}:
                return True
            if text in {"false", "no", "forming", "open", "0"}:
                return False
        raise CandleStudyValidationError(f"{name} must explicitly describe candle closure")
    return None


def _canonical_regime(value: object) -> str:
    text = str(value or "UNKNOWN").strip().upper().replace(" ", "_").replace("-", "_")
    canonical = text or "UNKNOWN"
    if len(canonical) > 64:
        raise CandleStudyValidationError("regime exceeds 64 characters")
    return canonical


def _tracker_direction(value: object) -> str:
    text = str(value or "").strip().upper()
    if text in {"BUY", "BULL", "BULLISH", "UP", "GREEN"}:
        return "BUY"
    if text in {"SELL", "BEAR", "BEARISH", "DOWN", "RED", "MAGENTA"}:
        return "SELL"
    return "UNKNOWN"


def _canonical_timestamp(value: object) -> int | float | str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise CandleStudyValidationError("timestamp cannot be boolean")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CandleStudyValidationError("timestamp must be finite")
        return value
    text = str(value).strip()
    return text or None


_IMMUTABLE_CANDLE_ID_FIELDS = (
    "candle_id",
    "bar_id",
    "source_candle_id",
    "source_bar_id",
)
_STABLE_CANDLE_TIMESTAMP_FIELDS = (
    "timestamp",
    "time",
    "bar_open_time",
    "open_time",
    "open_timestamp",
    "candle_open_epoch",
    "closed_candle_epoch",
    "close_time",
)
_POSITIONAL_CANDLE_ID_FIELDS = ("track_id", "id")


def _identity_text(value: object) -> str:
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return str(value).strip()


def _candle_identity_evidence(
    row: Mapping[str, Any],
    index: int,
) -> dict[str, Any]:
    """Separate a display id from evidence that can prove continuity.

    The vision tracker numbers candles by their position in each visible
    window.  Those ``track_id`` values are useful labels, but they are not an
    immutable market-candle identity and must never mature an outcome.  Stable
    source ids and ordered source timestamps are carried explicitly instead.
    """

    timestamp: int | float | str | None = None
    timestamp_source = ""
    for name in _STABLE_CANDLE_TIMESTAMP_FIELDS:
        if row.get(name) is None:
            continue
        candidate = _canonical_timestamp(row.get(name))
        if candidate is not None:
            timestamp = candidate
            timestamp_source = name
            break

    explicit_identity = _identity_text(row.get("stable_candle_identity"))
    if row.get("identity_stable") is True and explicit_identity:
        display_id = _identity_text(row.get("candle_id")) or explicit_identity
        proof_source = _identity_text(row.get("identity_proof_source"))
        raw_sequence = row.get("closed_candle_sequence")
        closed_sequence = (
            raw_sequence
            if isinstance(raw_sequence, int)
            and not isinstance(raw_sequence, bool)
            and raw_sequence >= 0
            else None
        )
        return {
            "candle_id": display_id,
            "timestamp": timestamp,
            "identity_stable": True,
            "stable_candle_identity": f"EXPLICIT:{explicit_identity}",
            "identity_source": "stable_candle_identity",
            "identity_proof_source": proof_source,
            "closed_candle_sequence": closed_sequence,
        }

    for name in _IMMUTABLE_CANDLE_ID_FIELDS:
        identity = _identity_text(row.get(name))
        if identity:
            return {
                "candle_id": identity,
                "timestamp": timestamp,
                "identity_stable": True,
                "stable_candle_identity": f"{name.upper()}:{identity}",
                "identity_source": name,
                "identity_proof_source": "",
                "closed_candle_sequence": None,
            }

    if timestamp is not None:
        timestamp_identity = _identity_text(timestamp)
        return {
            "candle_id": timestamp_identity,
            "timestamp": timestamp,
            "identity_stable": True,
            "stable_candle_identity": (
                f"{timestamp_source.upper()}:{timestamp_identity}"
            ),
            "identity_source": timestamp_source,
            "identity_proof_source": "",
            "closed_candle_sequence": None,
        }

    for name in _POSITIONAL_CANDLE_ID_FIELDS:
        identity = _identity_text(row.get(name))
        if identity:
            return {
                "candle_id": identity,
                "timestamp": None,
                "identity_stable": False,
                "stable_candle_identity": "",
                "identity_source": name,
                "identity_proof_source": "",
                "closed_candle_sequence": None,
            }

    return {
        "candle_id": f"candle-{index:06d}",
        "timestamp": None,
        "identity_stable": False,
        "stable_candle_identity": "",
        "identity_source": "sequence_index",
        "identity_proof_source": "",
        "closed_candle_sequence": None,
    }


def _geometry(row: Mapping[str, Any]) -> dict[str, Any]:
    has_price_ohlc = all(
        any(name in row and row.get(name) is not None for name in aliases)
        for aliases in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"))
    )
    if has_price_ohlc:
        open_value = _first_number(row, ("open", "o"), field="open")
        high_value = _first_number(row, ("high", "h"), field="high")
        low_value = _first_number(row, ("low", "l"), field="low")
        close_value = _first_number(row, ("close", "c"), field="close")
        coordinate_space = "PRICE"
        source_values = {
            "open": open_value,
            "high": high_value,
            "low": low_value,
            "close": close_value,
        }
    else:
        has_proxy_ohlc = all(
            any(name in row and row.get(name) is not None for name in aliases)
            for aliases in (
                ("open_proxy",),
                ("high_proxy",),
                ("low_proxy",),
                ("close_proxy", "price_proxy"),
            )
        )
        if has_proxy_ohlc:
            open_value = _first_number(row, ("open_proxy",), field="open_proxy")
            high_value = _first_number(row, ("high_proxy",), field="high_proxy")
            low_value = _first_number(row, ("low_proxy",), field="low_proxy")
            close_value = _first_number(row, ("close_proxy", "price_proxy"), field="close_proxy")
            coordinate_space = "NORMALIZED_PRICE_PROXY"
            source_values = {
                "open_proxy": open_value,
                "high_proxy": high_value,
                "low_proxy": low_value,
                "close_proxy": close_value,
            }
        else:
            wick_top = _first_number(row, ("wick_top_px", "high_y_px"), field="wick_top_px")
            wick_bottom = _first_number(row, ("wick_bottom_px", "low_y_px"), field="wick_bottom_px")
            has_open_close_y = all(
                any(name in row and row.get(name) is not None for name in aliases)
                for aliases in (("open_y_px", "open_y"), ("close_y_px", "close_y"))
            )
            if has_open_close_y:
                open_y = _first_number(row, ("open_y_px", "open_y"), field="open_y_px")
                close_y = _first_number(row, ("close_y_px", "close_y"), field="close_y_px")
            else:
                body_top = _first_number(row, ("body_top_px", "body_top"), field="body_top_px")
                body_bottom = _first_number(row, ("body_bottom_px", "body_bottom"), field="body_bottom_px")
                if body_top > body_bottom:
                    raise CandleStudyValidationError("body_top_px cannot be below body_bottom_px")
                side = _tracker_direction(row.get("direction") or row.get("side") or row.get("color"))
                if side == "BUY":
                    open_y, close_y = body_bottom, body_top
                elif side == "SELL":
                    open_y, close_y = body_top, body_bottom
                else:
                    raise CandleStudyValidationError(
                        "pixel body geometry requires a measured BUY or SELL direction"
                    )
            # Pixel Y increases downward. Negating Y creates a local
            # price-like axis without pretending pixels are broker prices.
            open_value = -open_y
            high_value = -wick_top
            low_value = -wick_bottom
            close_value = -close_y
            coordinate_space = "PIXEL_PRICE_PROXY"
            source_values = {
                "open_y_px": open_y,
                "wick_top_px": wick_top,
                "wick_bottom_px": wick_bottom,
                "close_y_px": close_y,
            }

    tolerance = max(1e-12, abs(high_value - low_value) * 1e-9)
    if high_value + tolerance < low_value:
        raise CandleStudyValidationError("high must be greater than or equal to low")
    if high_value + tolerance < max(open_value, close_value):
        raise CandleStudyValidationError("high cannot be below the candle body")
    if low_value - tolerance > min(open_value, close_value):
        raise CandleStudyValidationError("low cannot be above the candle body")

    range_size = high_value - low_value
    if range_size <= tolerance:
        raise CandleStudyValidationError("candle range must be positive")
    body_high = max(open_value, close_value)
    body_low = min(open_value, close_value)
    body_size = body_high - body_low
    upper_wick = max(0.0, high_value - body_high)
    lower_wick = max(0.0, body_low - low_value)
    return {
        "open": open_value,
        "high": high_value,
        "low": low_value,
        "close": close_value,
        "range": range_size,
        "body": body_size,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "coordinate_space": coordinate_space,
        "source_values": source_values,
    }


def adapt_tracker_candle_v3(
    candle: Mapping[str, Any],
    *,
    closure_proof: Mapping[str, Any],
) -> dict[str, Any]:
    """Adapt one active tracker row only when its closed-bar proof is explicit.

    The caller must source ``event_key`` from V3's closed-candle identity
    resolver and set ``proven_closed=True`` for this exact row.  This adapter
    never infers closure from list position or pixel geometry.
    """

    row = _mapping(candle)
    proof = _mapping(closure_proof)
    if not row:
        raise CandleStudyValidationError("tracker candle must be a non-empty mapping")
    if proof.get("proven_closed") is not True:
        raise CandleStudyValidationError("tracker candle requires proven_closed=True")
    event_key = str(proof.get("event_key") or proof.get("closed_candle_key") or "").strip()
    if not event_key:
        raise CandleStudyValidationError("tracker candle requires a closed-candle event key")
    proof_id = str(proof.get("candle_id") or proof.get("track_id") or "").strip()
    row_id = str(row.get("candle_id") or row.get("track_id") or row.get("id") or "").strip()
    if proof_id and row_id and proof_id != row_id:
        raise CandleStudyValidationError("closure proof does not identify this tracker candle")
    adapted = dict(row)
    adapted["is_closed"] = True
    adapted["closed_candle_identity"] = event_key
    adapted["closure_proof_source"] = "PG_CLOSED_CANDLE_IDENTITY_STATE_V3"
    # Validate the tracker geometry now so malformed rows cannot be marked as
    # trustworthy merely because they carried a valid event identity.
    _geometry(adapted)
    return adapted


def _direction(open_value: float, close_value: float, range_size: float) -> str:
    delta = close_value - open_value
    if abs(delta) <= range_size * 1e-6:
        return "NEUTRAL"
    return "BULLISH" if delta > 0.0 else "BEARISH"


def _candle_type(
    *,
    direction: str,
    body_ratio: float,
    upper_ratio: float,
    lower_ratio: float,
) -> str:
    if body_ratio <= 0.03:
        if upper_ratio >= 0.32 and lower_ratio >= 0.32:
            return "LONG_LEGGED_DOJI"
        return "DOJI"
    if body_ratio <= 0.30 and upper_ratio >= 0.24 and lower_ratio >= 0.24:
        return "SPINNING_TOP"
    if body_ratio >= 0.85 and upper_ratio <= 0.08 and lower_ratio <= 0.08:
        return f"{direction}_MARUBOZU"
    if lower_ratio >= 0.52 and lower_ratio >= upper_ratio * 1.8:
        return "LOWER_WICK_REJECTION"
    if upper_ratio >= 0.52 and upper_ratio >= lower_ratio * 1.8:
        return "UPPER_WICK_REJECTION"
    if body_ratio >= 0.65:
        return f"{direction}_IMPULSE"
    return f"{direction}_BALANCED" if direction != "NEUTRAL" else "BALANCED_INDECISION"


def _relation(current: Mapping[str, Any], previous: Mapping[str, Any] | None) -> str:
    if previous is None:
        return "SEQUENCE_START"
    high = float(current["high"])
    low = float(current["low"])
    previous_high = float(previous["high"])
    previous_low = float(previous["low"])
    tolerance = max(1e-12, float(current["range"]) * 1e-9)
    if high <= previous_high + tolerance and low >= previous_low - tolerance:
        return "INSIDE_BAR"
    if high >= previous_high - tolerance and low <= previous_low + tolerance:
        return "OUTSIDE_BAR"
    if high > previous_high and low >= previous_low:
        return "HIGHER_HIGH_HIGHER_LOW"
    if high <= previous_high and low < previous_low:
        return "LOWER_HIGH_LOWER_LOW"
    if high > previous_high:
        return "HIGHER_HIGH_MIXED_LOW"
    if low < previous_low:
        return "LOWER_LOW_MIXED_HIGH"
    return "OVERLAPPING_RANGE"


def _interaction(
    current: Mapping[str, Any],
    previous: Mapping[str, Any] | None,
    *,
    body_ratio: float,
    upper_ratio: float,
    lower_ratio: float,
) -> dict[str, Any]:
    upper_sweep = False
    lower_sweep = False
    accepted_up = False
    accepted_down = False
    if previous is not None:
        tolerance = max(1e-12, float(current["range"]) * 1e-9)
        upper_sweep = bool(
            float(current["high"]) > float(previous["high"]) + tolerance
            and float(current["close"]) <= float(previous["high"]) + tolerance
        )
        lower_sweep = bool(
            float(current["low"]) < float(previous["low"]) - tolerance
            and float(current["close"]) >= float(previous["low"]) - tolerance
        )
        accepted_up = bool(
            float(current["close"]) > float(previous["high"]) + tolerance
            and body_ratio >= 0.40
        )
        accepted_down = bool(
            float(current["close"]) < float(previous["low"]) - tolerance
            and body_ratio >= 0.40
        )

    if upper_sweep and lower_sweep:
        rejection_side = "BOTH"
    elif upper_sweep or upper_ratio >= 0.52:
        rejection_side = "HIGH"
    elif lower_sweep or lower_ratio >= 0.52:
        rejection_side = "LOW"
    else:
        rejection_side = "NONE"
    acceptance_side = "UP" if accepted_up else "DOWN" if accepted_down else "NONE"
    return {
        "rejection": {
            "detected": rejection_side != "NONE",
            "side": rejection_side,
            "strength": round(max(upper_ratio, lower_ratio), 6),
            "upper_wick_swept_previous_high": upper_sweep,
            "lower_wick_swept_previous_low": lower_sweep,
        },
        "acceptance": {
            "detected": acceptance_side != "NONE",
            "side": acceptance_side,
            "body_ratio": round(body_ratio, 6),
            "closed_beyond_previous_high": accepted_up,
            "closed_beyond_previous_low": accepted_down,
        },
    }


def _personality(
    *,
    direction: str,
    candle_type: str,
    range_multiple: float,
    interaction: Mapping[str, Any],
) -> str:
    rejection = _mapping(interaction.get("rejection"))
    acceptance = _mapping(interaction.get("acceptance"))
    if bool(rejection.get("upper_wick_swept_previous_high")):
        return "LIQUIDITY_REJECTION_HIGH"
    if bool(rejection.get("lower_wick_swept_previous_low")):
        return "LIQUIDITY_REJECTION_LOW"
    if acceptance.get("side") == "UP":
        return "BREAKOUT_ACCEPTANCE_UP"
    if acceptance.get("side") == "DOWN":
        return "BREAKOUT_ACCEPTANCE_DOWN"
    if candle_type in {"DOJI", "LONG_LEGGED_DOJI", "SPINNING_TOP", "BALANCED_INDECISION"}:
        return "INDECISION"
    if candle_type == "LOWER_WICK_REJECTION":
        return "LOWER_PRICE_REJECTION"
    if candle_type == "UPPER_WICK_REJECTION":
        return "HIGHER_PRICE_REJECTION"
    if range_multiple >= 1.60:
        return "EXPANSION_UP" if direction == "BULLISH" else "EXPANSION_DOWN"
    if range_multiple <= 0.55:
        return "COMPRESSION"
    if "IMPULSE" in candle_type or "MARUBOZU" in candle_type:
        return "ASSERTIVE_BUYING" if direction == "BULLISH" else "ASSERTIVE_SELLING"
    return "CONTROLLED_BUYING" if direction == "BULLISH" else "CONTROLLED_SELLING"


def _rounded(value: float) -> float:
    return round(float(value), 8)


def _token(payload: Mapping[str, Any]) -> str:
    token_source = {
        "direction": payload.get("direction"),
        "type": payload.get("type"),
        "personality": payload.get("personality"),
        "relation": payload.get("relation_to_previous"),
        "regime": payload.get("regime"),
    }
    encoded = json.dumps(token_source, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def analyze_candle_v3(
    candle: Mapping[str, Any],
    *,
    previous_candle: Mapping[str, Any] | None = None,
    index: int = 0,
    sequence_length: int = 1,
    regime: str = "UNKNOWN",
    baseline_range: float | None = None,
    require_closed: bool = True,
) -> dict[str, Any]:
    """Return exact micro features for one validated candle.

    ``previous_candle`` can be either a raw candle or the canonical private
    geometry mapping used by :func:`analyze_candle_sequence_v3`.
    """

    row = _mapping(candle)
    if not row:
        raise CandleStudyValidationError("candle must be a non-empty mapping")
    if index < 0 or sequence_length <= 0 or index >= sequence_length:
        raise CandleStudyValidationError("candle sequence position is invalid")
    closed = _explicit_closed(row)
    if require_closed and closed is not True:
        reason = "forming candles are excluded" if closed is False else "closed-candle proof is required"
        raise CandleStudyValidationError(reason)

    geometry = _geometry(row)
    previous_geometry: dict[str, Any] | None = None
    if previous_candle is not None:
        candidate = _mapping(previous_candle)
        required = {"open", "high", "low", "close", "range", "body", "upper_wick", "lower_wick"}
        previous_geometry = candidate if required.issubset(candidate) else _geometry(candidate)

    range_size = float(geometry["range"])
    body_size = float(geometry["body"])
    upper_wick = float(geometry["upper_wick"])
    lower_wick = float(geometry["lower_wick"])
    body_ratio = body_size / range_size
    upper_ratio = upper_wick / range_size
    lower_ratio = lower_wick / range_size
    wick_total = upper_wick + lower_wick
    baseline = range_size if baseline_range is None else _finite(baseline_range, field="baseline_range")
    if baseline <= 0.0:
        raise CandleStudyValidationError("baseline_range must be positive")
    range_multiple = range_size / baseline
    direction = _direction(float(geometry["open"]), float(geometry["close"]), range_size)
    candle_type = _candle_type(
        direction=direction,
        body_ratio=body_ratio,
        upper_ratio=upper_ratio,
        lower_ratio=lower_ratio,
    )
    interaction = _interaction(
        geometry,
        previous_geometry,
        body_ratio=body_ratio,
        upper_ratio=upper_ratio,
        lower_ratio=lower_ratio,
    )
    relation = _relation(geometry, previous_geometry)
    personality = _personality(
        direction=direction,
        candle_type=candle_type,
        range_multiple=range_multiple,
        interaction=interaction,
    )
    close_location = (float(geometry["close"]) - float(geometry["low"])) / range_size
    body_to_range = body_ratio
    body_to_wick = None if wick_total <= 1e-12 else body_size / wick_total
    upper_to_body = None if body_size <= 1e-12 else upper_wick / body_size
    lower_to_body = None if body_size <= 1e-12 else lower_wick / body_size

    identity = _candle_identity_evidence(row, index)
    result: dict[str, Any] = {
        "schema_version": CANDLE_INTELLIGENCE_SCHEMA_VERSION,
        "status": "STUDIED",
        "study_only": True,
        "execution_authority": False,
        "candle_id": identity["candle_id"],
        "timestamp": identity["timestamp"],
        "identity_stable": identity["identity_stable"],
        "stable_candle_identity": identity["stable_candle_identity"],
        "identity_source": identity["identity_source"],
        "identity_proof_source": identity["identity_proof_source"],
        "closed_candle_sequence": identity["closed_candle_sequence"],
        "closed": bool(closed) if closed is not None else False,
        "coordinate_space": geometry["coordinate_space"],
        "source_values": dict(cast(Mapping[str, Any], geometry["source_values"])),
        "ohlc": {
            "open": _rounded(float(geometry["open"])),
            "high": _rounded(float(geometry["high"])),
            "low": _rounded(float(geometry["low"])),
            "close": _rounded(float(geometry["close"])),
        },
        "exact_geometry": {
            "range_size": _rounded(range_size),
            "body_size": _rounded(body_size),
            "upper_wick_size": _rounded(upper_wick),
            "lower_wick_size": _rounded(lower_wick),
        },
        "ratios": {
            "body_to_range": round(body_to_range, 6),
            "upper_wick_to_range": round(upper_ratio, 6),
            "lower_wick_to_range": round(lower_ratio, 6),
            "total_wick_to_range": round(wick_total / range_size, 6),
            "body_to_total_wick": None if body_to_wick is None else round(body_to_wick, 6),
            "upper_wick_to_body": None if upper_to_body is None else round(upper_to_body, 6),
            "lower_wick_to_body": None if lower_to_body is None else round(lower_to_body, 6),
            "close_location_in_range": round(close_location, 6),
            "range_vs_sequence_median": round(range_multiple, 6),
        },
        "direction": direction,
        "type": candle_type,
        "personality": personality,
        "regime": _canonical_regime(regime),
        "relation_to_previous": relation,
        "interaction": interaction,
        "sequence_position": {
            "index": index,
            "ordinal": index + 1,
            "sequence_length": sequence_length,
            "from_end": sequence_length - index - 1,
            "fraction": round(index / max(1, sequence_length - 1), 6),
            "is_first": index == 0,
            "is_latest": index == sequence_length - 1,
            "direction_run": 1,
        },
    }
    result["fingerprint_token"] = _token(result)
    return result


def _count_values(rows: Sequence[Mapping[str, Any]], field: str) -> dict[str, int]:
    counter = Counter(str(row.get(field) or "UNKNOWN") for row in rows)
    return dict(sorted(counter.items()))


def analyze_candle_sequence_v3(
    candles: Sequence[Mapping[str, Any]],
    *,
    regime: str = "UNKNOWN",
    require_closed: bool = True,
    max_candles: int = MAX_STUDY_CANDLES,
) -> dict[str, Any]:
    """Study a bounded candle sequence and return per-candle truth records."""

    if isinstance(candles, (str, bytes, bytearray)):
        raise CandleStudyValidationError("candles must be a sequence of mappings")
    limit = int(max_candles)
    if limit <= 0 or limit > 4096:
        raise CandleStudyValidationError("max_candles must be in [1, 4096]")
    raw_rows = [_mapping(row) for row in candles]
    if any(not row for row in raw_rows):
        raise CandleStudyValidationError("every candle must be a non-empty mapping")
    if not raw_rows:
        return {
            "schema_version": CANDLE_INTELLIGENCE_SCHEMA_VERSION,
            "status": "INSUFFICIENT_HISTORY",
            "study_only": True,
            "execution_authority": False,
            "input_count": 0,
            "studied_count": 0,
            "truncated_count": 0,
            "candles": [],
            "summary": {},
        }

    selected = raw_rows[-limit:]
    geometries = [_geometry(row) for row in selected]
    coordinate_spaces = {str(item["coordinate_space"]) for item in geometries}
    if len(coordinate_spaces) != 1:
        raise CandleStudyValidationError("one candle study cannot mix coordinate spaces")
    baseline = median(float(item["range"]) for item in geometries)
    studied: list[dict[str, Any]] = []
    direction_run = 0
    previous_direction = ""
    for index, row in enumerate(selected):
        previous_geometry = geometries[index - 1] if index else None
        result = analyze_candle_v3(
            row,
            previous_candle=previous_geometry,
            index=index,
            sequence_length=len(selected),
            regime=regime,
            baseline_range=baseline,
            require_closed=require_closed,
        )
        current_direction = str(result["direction"])
        direction_run = direction_run + 1 if current_direction == previous_direction else 1
        previous_direction = current_direction
        position = _mapping(result.get("sequence_position"))
        position["direction_run"] = direction_run
        result["sequence_position"] = position
        studied.append(result)

    rejection_count = sum(bool(_mapping(_mapping(row.get("interaction")).get("rejection")).get("detected")) for row in studied)
    acceptance_count = sum(bool(_mapping(_mapping(row.get("interaction")).get("acceptance")).get("detected")) for row in studied)
    sequence_signature_source = [str(row["fingerprint_token"]) for row in studied]
    sequence_signature = hashlib.sha256("|".join(sequence_signature_source).encode("utf-8")).hexdigest()
    return {
        "schema_version": CANDLE_INTELLIGENCE_SCHEMA_VERSION,
        "status": "STUDIED",
        "study_only": True,
        "execution_authority": False,
        "input_count": len(raw_rows),
        "studied_count": len(studied),
        "truncated_count": max(0, len(raw_rows) - len(selected)),
        "sequence_signature": sequence_signature,
        "baseline_range": _rounded(baseline),
        "candles": studied,
        "summary": {
            "direction_counts": _count_values(studied, "direction"),
            "type_counts": _count_values(studied, "type"),
            "personality_counts": _count_values(studied, "personality"),
            "rejection_count": rejection_count,
            "acceptance_count": acceptance_count,
            "rejection_rate": round(rejection_count / len(studied), 6),
            "acceptance_rate": round(acceptance_count / len(studied), 6),
            "latest_direction_run": int(_mapping(studied[-1].get("sequence_position")).get("direction_run", 1)),
        },
    }


__all__ = [
    "CANDLE_INTELLIGENCE_SCHEMA_VERSION",
    "MAX_STUDY_CANDLES",
    "CandleStudyValidationError",
    "adapt_tracker_candle_v3",
    "analyze_candle_sequence_v3",
    "analyze_candle_v3",
]
