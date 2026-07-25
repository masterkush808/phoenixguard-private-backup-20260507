from __future__ import annotations

import copy
import hashlib
import math
import re
import statistics
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, cast

from phoenixguard.decision.chronos_scene_forecaster_v3 import (
    build_chronos_scene_forecast_contribution_v3,
)
from phoenixguard.decision.scene_forecast_features_v3 import (
    extract_scene_forecast_features_v3,
)


SCENE_FORECAST_CONTRIBUTION_SCHEMA_V3 = "PG_SCENE_FORECAST_CONTRIBUTION_V3"
FORECAST_HORIZON_STEPS_V3 = 12


def _finite(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _side(value: Any) -> str:
    normalized = str(value or "").strip().upper()
    return normalized if normalized in {"BUY", "HOLD", "SELL"} else "HOLD"


def _explicit_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    normalized = str(value or "").strip().lower()
    if normalized in {"1", "true", "yes", "closed", "complete", "confirmed"}:
        return True
    if normalized in {"0", "false", "no", "open", "forming", "incomplete"}:
        return False
    return None


def candle_is_closed_v3(
    candle: Mapping[str, Any],
    *,
    index: int,
    total: int,
) -> bool:
    for key in (
        "is_closed",
        "closed",
        "candle_closed",
        "closed_candle",
        "is_complete",
        "candle_complete",
    ):
        if key in candle and candle.get(key) is not None:
            parsed = _explicit_bool(candle.get(key))
            if parsed is not None:
                return parsed
    for key in ("is_forming", "forming", "candle_forming", "in_progress"):
        if key in candle and candle.get(key) is not None:
            parsed = _explicit_bool(candle.get(key))
            if parsed is not None:
                return not parsed
    phase = str(candle.get("candle_state", candle.get("bar_state", "")) or "").strip().lower()
    if phase in {"closed", "complete", "confirmed"}:
        return True
    if phase in {"forming", "open", "in_progress", "live"}:
        return False
    return 0 <= index < max(0, total - 1)


def latest_closed_candle_v3(
    candles: Sequence[Mapping[str, Any]],
) -> tuple[int, dict[str, Any]]:
    rows = [dict(row) for row in candles]
    for index in range(len(rows) - 1, -1, -1):
        if candle_is_closed_v3(rows[index], index=index, total=len(rows)):
            return index, rows[index]
    raise ValueError("a verified closed candle is required for the scene forecast anchor")


_IMMUTABLE_CANDLE_ID_FIELDS_V3 = (
    "candle_id",
    "bar_id",
    "source_candle_id",
    "source_bar_id",
    "bar_open_time",
    "open_time",
    "open_timestamp",
    "candle_open_epoch",
    "closed_candle_epoch",
    "close_time",
)
_SOURCE_TIME_ID_FIELDS_V3 = frozenset(
    {
        "bar_open_time",
        "open_time",
        "open_timestamp",
        "candle_open_epoch",
        "closed_candle_epoch",
        "close_time",
    }
)


def _timeframe_seconds_v3(value: object) -> int | None:
    text = str(value or "").strip().upper().replace(" ", "")
    match = re.fullmatch(r"(?:([SMHDW])(\d+)|(\d+)([SMHDW]))", text)
    if match is None:
        return None
    unit = str(match.group(1) or match.group(4) or "")
    raw_count = str(match.group(2) or match.group(3) or "")
    try:
        count = int(raw_count)
    except ValueError:
        return None
    multiplier = {"S": 1, "M": 60, "H": 3_600, "D": 86_400, "W": 604_800}.get(
        unit
    )
    if count <= 0 or multiplier is None:
        return None
    seconds = count * multiplier
    return seconds if seconds <= 31_536_000 else None


def _source_time_seconds_v3(observation: Mapping[str, Any]) -> float | None:
    field = str(observation.get("source_identity_field") or "").strip()
    if field not in _SOURCE_TIME_ID_FIELDS_V3:
        return None
    value = _finite(observation.get("source_identity_value"))
    if value is None or value < 0.0:
        return None
    magnitude = abs(value)
    if magnitude >= 1e17:
        value /= 1e9
    elif magnitude >= 1e14:
        value /= 1e6
    elif magnitude >= 1e11:
        value /= 1e3
    return value


def _source_time_step_count_v3(
    prior: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    timeframe: str,
) -> int | None:
    if str(prior.get("source_identity_field") or "") != str(
        current.get("source_identity_field") or ""
    ):
        return None
    prior_time = _source_time_seconds_v3(prior)
    current_time = _source_time_seconds_v3(current)
    timeframe_seconds = _timeframe_seconds_v3(timeframe)
    if prior_time is None or current_time is None or timeframe_seconds is None:
        return None
    delta = current_time - prior_time
    if delta <= 0.0:
        return None
    steps = round(delta / timeframe_seconds)
    tolerance = max(1e-6, timeframe_seconds * 1e-6)
    if steps <= 0 or abs(delta - steps * timeframe_seconds) > tolerance:
        return None
    return int(steps)


def _immutable_candle_identity_v3(candle: Mapping[str, Any]) -> tuple[str, str] | None:
    """Return broker/source candle identity when the feed supplies one.

    ``track_id`` is deliberately excluded. Vision extraction assigns it from
    the currently visible sequence, so it can be rebased after a chart pan or
    bounded-history refresh. A broker bar id/open time, in contrast, is the
    actual event identity and must win over all rendered geometry.
    """

    for key in _IMMUTABLE_CANDLE_ID_FIELDS_V3:
        value = candle.get(key)
        if value in (None, ""):
            continue
        normalized = str(value).strip()
        if normalized:
            return key, normalized
    return None


def closed_candle_identity_v3(
    candles: Sequence[Mapping[str, Any]],
    *,
    pair: str,
    timeframe: str,
) -> str:
    index, candle = latest_closed_candle_v3(candles)
    pair_key = str(pair or "UNKNOWN").strip().upper()
    timeframe_key = str(timeframe or "UNKNOWN").strip().upper()
    immutable_identity = _immutable_candle_identity_v3(candle)
    if immutable_identity is not None:
        identity_field, identity_value = immutable_identity
        identity_fields = [
            "SOURCE_BAR_V3",
            pair_key,
            timeframe_key,
            identity_field,
            identity_value,
        ]
    else:
        # With a screenshot-only source there is no broker clock or bar id to
        # invent. Use the causal closed-candle sequence identity assigned by
        # the vision tracker, but never encode pixel geometry as market
        # progression. The surrounding tracker advances its monotonic event
        # sequence only when this closed context changes; no wall-clock timeout
        # is involved. A bounded direction tail disambiguates a rebased latest
        # ordinal without making subpixel render noise part of the key.
        closed_rows = [
            (row_index, dict(row))
            for row_index, row in enumerate(candles)
            if candle_is_closed_v3(row, index=row_index, total=len(candles))
        ]
        direction_tail = ",".join(
            _side(
                row.get("direction")
                or row.get("side")
                or row.get("color")
            )
            for _row_index, row in closed_rows[-12:]
        )
        identity_fields = [
            "VISUAL_SEQUENCE_BAR_V3",
            pair_key,
            timeframe_key,
            str(candle.get("track_id", candle.get("id", index))),
            str(index),
            str(len(closed_rows)),
            _side(
                candle.get("direction")
                or candle.get("side")
                or candle.get("color")
            ),
            direction_tail,
        ]
    encoded = "|".join(identity_fields).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:24]


def _axis_value(candle: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _finite(candle.get(key))
        if value is not None:
            return value
    return None


def _candle_x(candle: Mapping[str, Any]) -> float | None:
    center = _axis_value(candle, "center_x", "center_x_px", "x_center")
    if center is not None:
        return center
    bbox = candle.get("bbox")
    if isinstance(bbox, Sequence) and not isinstance(bbox, (str, bytes, bytearray)):
        values = [_finite(value) for value in list(cast(Sequence[Any], bbox))[:4]]
        if len(values) == 4 and all(value is not None for value in values):
            return (cast(float, values[0]) + cast(float, values[2])) * 0.5
    return None


def _close_y(candle: Mapping[str, Any], *, height: float) -> float | None:
    value = _axis_value(
        candle,
        "close_y_px",
        "close_y",
        "close_price_y_px",
        "close_price_y",
    )
    if value is not None:
        return value
    price_proxy = _finite(candle.get("price_proxy"))
    if price_proxy is not None:
        return (1.0 - max(0.0, min(1.0, price_proxy))) * height
    return None


def _visual_candle_observation_v3(
    candle: Mapping[str, Any],
    *,
    index: int,
) -> dict[str, Any]:
    immutable = _immutable_candle_identity_v3(candle)
    top = _axis_value(candle, "wick_top_px", "wick_top", "high_y_px", "high_y")
    bottom = _axis_value(
        candle,
        "wick_bottom_px",
        "wick_bottom",
        "low_y_px",
        "low_y",
    )
    if top is None or bottom is None:
        bbox = candle.get("bbox")
        if isinstance(bbox, Sequence) and not isinstance(
            bbox,
            (str, bytes, bytearray),
        ):
            values = [_finite(value) for value in list(cast(Sequence[Any], bbox))[:4]]
            if len(values) == 4 and all(value is not None for value in values):
                top = cast(float, values[1]) if top is None else top
                bottom = cast(float, values[3]) if bottom is None else bottom
    return {
        "index": int(index),
        "track_id": str(candle.get("track_id", candle.get("id", index))),
        "side": _side(
            candle.get("direction") or candle.get("side") or candle.get("color")
        ),
        "x": _candle_x(candle),
        "open_y": _axis_value(candle, "open_y_px", "open_y", "open_px"),
        "close_y": _axis_value(candle, "close_y_px", "close_y", "close_px"),
        "top_y": top,
        "bottom_y": bottom,
        "close_y_norm": _axis_value(candle, "close_y_norm", "normalized_close_y"),
        "price_proxy": _finite(candle.get("price_proxy")),
        "close_norm": _finite(candle.get("close_norm")),
        "source_identity_field": immutable[0] if immutable else "",
        "source_identity_value": immutable[1] if immutable else "",
    }


def _closed_candle_observation_state_v3(
    candles: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    rows = [dict(row) for row in candles]
    closed_index, closed = latest_closed_candle_v3(rows)
    closed_rows = [
        (index, row)
        for index, row in enumerate(rows)
        if candle_is_closed_v3(row, index=index, total=len(rows))
    ]
    closed_count = len(closed_rows)
    forming_rows = [
        (index, row)
        for index, row in enumerate(rows)
        if index > closed_index
        and not candle_is_closed_v3(row, index=index, total=len(rows))
    ]
    forming = forming_rows[-1] if forming_rows else None
    x_values = [value for row in rows if (value := _candle_x(row)) is not None]
    x_steps = [
        right - left
        for left, right in zip(x_values, x_values[1:])
        if right - left > 0.5
    ]
    ranges: list[float] = []
    for row in rows[-24:]:
        top = _axis_value(row, "wick_top_px", "wick_top", "high_y_px", "high_y")
        bottom = _axis_value(
            row,
            "wick_bottom_px",
            "wick_bottom",
            "low_y_px",
            "low_y",
        )
        if top is not None and bottom is not None and abs(bottom - top) > 0.0:
            ranges.append(abs(bottom - top))
    return {
        "latest_closed": _visual_candle_observation_v3(closed, index=closed_index),
        "closed_tail": [
            _visual_candle_observation_v3(row, index=index)
            for index, row in closed_rows[-24:]
        ],
        "forming": (
            _visual_candle_observation_v3(forming[1], index=forming[0])
            if forming is not None
            else {}
        ),
        "median_x_step": statistics.median(x_steps[-24:]) if x_steps else 8.0,
        "median_range": statistics.median(ranges) if ranges else 12.0,
        "detected_candle_count": len(rows),
        "detected_closed_count": closed_count,
        "coverage_left_x": min(x_values) if x_values else None,
        "coverage_right_x": max(x_values) if x_values else None,
        "coverage_span_x": (max(x_values) - min(x_values)) if len(x_values) >= 2 else 0.0,
    }


def _observation_count_v3(
    observation: Mapping[str, Any],
    *,
    closed_only: bool,
) -> int:
    key = "detected_closed_count" if closed_only else "detected_candle_count"
    explicit = _finite(observation.get(key))
    if explicit is not None:
        return max(0, int(explicit))

    # Checkpoints written before detector-coverage metadata was introduced
    # still carry causal candle indices. They are sufficient to recognize a
    # large detector rebase on the first frame after an upgrade/restart.
    candidates: list[int] = []
    latest = observation.get("latest_closed")
    if isinstance(latest, Mapping):
        latest_index = _finite(cast(Mapping[str, Any], latest).get("index"))
        if latest_index is not None:
            candidates.append(int(latest_index) + 1)
    if not closed_only:
        forming = observation.get("forming")
        if isinstance(forming, Mapping):
            forming_index = _finite(cast(Mapping[str, Any], forming).get("index"))
            if forming_index is not None:
                candidates.append(int(forming_index) + 1)
    return max(candidates, default=0)


def _detector_coverage_rebase_v3(
    prior: Mapping[str, Any],
    current: Mapping[str, Any],
    *,
    x_step: float,
) -> tuple[bool, dict[str, float | int | bool]]:
    """Detect a multi-candle detector expansion, not a market-time event."""

    prior_count = _observation_count_v3(prior, closed_only=False)
    current_count = _observation_count_v3(current, closed_only=False)
    prior_closed_count = _observation_count_v3(prior, closed_only=True)
    current_closed_count = _observation_count_v3(current, closed_only=True)
    count_growth = current_count - prior_count
    closed_growth = current_closed_count - prior_closed_count
    # Without an explicit detector repair token, require a large coverage jump
    # so three genuinely missed/fast bars cannot be collapsed into a rebase.
    minimum_growth = max(6, int(math.ceil(max(1, prior_count) * 0.25)))

    prior_right = _finite(prior.get("coverage_right_x"))
    current_right = _finite(current.get("coverage_right_x"))
    if prior_right is None:
        prior_forming = prior.get("forming")
        prior_closed = prior.get("latest_closed")
        for candidate in (prior_forming, prior_closed):
            if isinstance(candidate, Mapping):
                prior_right = _finite(cast(Mapping[str, Any], candidate).get("x"))
                if prior_right is not None:
                    break
    if current_right is None:
        current_forming = current.get("forming")
        current_closed = current.get("latest_closed")
        for candidate in (current_forming, current_closed):
            if isinstance(candidate, Mapping):
                current_right = _finite(cast(Mapping[str, Any], candidate).get("x"))
                if current_right is not None:
                    break
    right_growth_steps = (
        (current_right - prior_right) / max(1.0, x_step)
        if current_right is not None and prior_right is not None
        else 0.0
    )

    prior_span = _finite(prior.get("coverage_span_x"))
    current_span = _finite(current.get("coverage_span_x"))
    span_growth_steps = (
        (current_span - prior_span) / max(1.0, x_step)
        if current_span is not None and prior_span is not None
        else 0.0
    )
    structural_expansion = bool(
        prior_count > 0
        and count_growth >= minimum_growth
        and closed_growth >= max(2, minimum_growth - 1)
        and (
            right_growth_steps >= 2.0
            or span_growth_steps >= 2.0
        )
    )
    return structural_expansion, {
        "detector_coverage_rebase": structural_expansion,
        "prior_detected_candle_count": prior_count,
        "current_detected_candle_count": current_count,
        "detected_candle_count_growth": count_growth,
        "prior_detected_closed_count": prior_closed_count,
        "current_detected_closed_count": current_closed_count,
        "detected_closed_count_growth": closed_growth,
        "coverage_right_growth_steps": round(right_growth_steps, 6),
        "coverage_span_growth_steps": round(span_growth_steps, 6),
    }


def _same_source_candle_v3(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
) -> bool | None:
    left_field = str(left.get("source_identity_field") or "")
    left_value = str(left.get("source_identity_value") or "")
    right_field = str(right.get("source_identity_field") or "")
    right_value = str(right.get("source_identity_value") or "")
    if not (left_field and left_value and right_field and right_value):
        return None
    return left_field == right_field and left_value == right_value


def _visual_candle_match_score_v3(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    x_step: float,
    range_scale: float,
) -> float:
    if not left or not right:
        return 0.0
    same_source = _same_source_candle_v3(left, right)
    if same_source is not None:
        return 1.0 if same_source else 0.0

    weighted_score = 0.0
    evidence_weight = 0.0

    def add_numeric(key: str, tolerance: float, weight: float) -> None:
        nonlocal evidence_weight, weighted_score
        left_value = _finite(left.get(key))
        right_value = _finite(right.get(key))
        if left_value is None or right_value is None:
            return
        evidence_weight += weight
        distance = abs(left_value - right_value)
        similarity = max(0.0, 1.0 - distance / max(1e-6, tolerance * 2.0))
        weighted_score += weight * similarity

    add_numeric("x", max(2.0, x_step * 0.45), 0.12)
    add_numeric("open_y", max(3.0, range_scale * 0.15), 0.26)
    add_numeric("top_y", max(4.0, range_scale * 0.22), 0.18)
    add_numeric("bottom_y", max(4.0, range_scale * 0.22), 0.18)
    # A forming candle's close may legitimately travel through most of its
    # range, so close contributes only weak evidence.
    add_numeric("close_y", max(8.0, range_scale * 0.55), 0.07)

    left_track = str(left.get("track_id") or "")
    right_track = str(right.get("track_id") or "")
    if left_track and right_track:
        evidence_weight += 0.12
        if left_track == right_track:
            weighted_score += 0.12
    left_side = _side(left.get("side"))
    right_side = _side(right.get("side"))
    if left_side != "HOLD" and right_side != "HOLD":
        evidence_weight += 0.07
        if left_side == right_side:
            weighted_score += 0.07
    if evidence_weight < 0.45:
        return 0.0
    return max(0.0, min(1.0, weighted_score / evidence_weight))


def _closed_event_key_v3(
    observation: Mapping[str, Any],
    *,
    pair: str,
    timeframe: str,
    event_sequence: int,
) -> str:
    identity_field = str(observation.get("source_identity_field") or "").strip()
    identity_value = str(observation.get("source_identity_value") or "").strip()
    if identity_field and identity_value:
        fields = (
            "SOURCE_BAR_V3",
            pair,
            timeframe,
            identity_field,
            identity_value,
        )
    else:
        fields = (
            "SCREENSHOT_CANDLE_EVENT_V3",
            pair,
            timeframe,
            str(max(0, int(event_sequence))),
        )
    return hashlib.sha256("|".join(fields).encode("utf-8")).hexdigest()[:24]


def _gap_reacquisition_v3(
    prior_closed: Mapping[str, Any],
    prior_forming: Mapping[str, Any],
    observation: Mapping[str, Any],
    *,
    x_step: float,
    range_scale: float,
    coverage_rebase: bool,
) -> dict[str, Any]:
    """Prove missed closes by finding the former live bar in visible history.

    This never uses elapsed time.  A gap is accepted only when the former
    forming candle has one clear visual/source match in the current closed
    tail and every later detected candle is spatially contiguous through the
    new forming candle.  Anything ambiguous remains pending and cannot advance
    an episode.
    """

    evidence: dict[str, Any] = {
        "status": "NOT_CONFIRMED",
        "reason": "NO_PRIOR_FORMING_ANCHOR",
        "confirmed_closed_count": 0,
        "anchor_match_score": 0.0,
        "anchor_match_margin": 0.0,
        "candidate_count": 0,
        "events": [],
    }
    if coverage_rebase:
        evidence["reason"] = "DETECTOR_COVERAGE_REBASE_BLOCKS_GAP_INFERENCE"
        return evidence
    current_forming = cast(Mapping[str, Any], observation.get("forming") or {})
    closed_tail: list[dict[str, Any]] = [
        dict(cast(Mapping[str, Any], row))
        for row in cast(Sequence[Any], observation.get("closed_tail") or [])
        if isinstance(row, Mapping)
    ]
    if not (prior_forming or prior_closed) or not current_forming or not closed_tail:
        return evidence

    def ranked_matches(anchor: Mapping[str, Any]) -> list[tuple[float, int]]:
        matches = [
            (
                _visual_candle_match_score_v3(
                    anchor,
                    row,
                    x_step=x_step,
                    range_scale=range_scale,
                ),
                index,
            )
            for index, row in enumerate(closed_tail)
        ]
        matches.sort(key=lambda item: item[0], reverse=True)
        return matches

    forming_candidates = ranked_matches(prior_forming) if prior_forming else []
    predecessor_candidates = ranked_matches(prior_closed) if prior_closed else []

    def qualified(
        candidates: Sequence[tuple[float, int]],
    ) -> tuple[bool, float, float, int]:
        if not candidates:
            return False, 0.0, 0.0, -1
        score, index = candidates[0]
        second = candidates[1][0] if len(candidates) > 1 else 0.0
        margin = score - second
        return score >= 0.62 and margin >= 0.10, score, margin, index

    forming_ok, forming_anchor_score, forming_margin, forming_index = qualified(
        forming_candidates
    )
    predecessor_ok, predecessor_score, predecessor_margin, predecessor_index = (
        qualified(predecessor_candidates)
    )
    if forming_ok:
        anchor_basis = "FORMER_LIVE_BAR"
        best_score = forming_anchor_score
        margin = forming_margin
        best_index = forming_index
        chain_prefix: list[dict[str, Any]] = []
    elif predecessor_ok and 0 <= predecessor_index < len(closed_tail) - 1:
        # The old completed candle is immutable after close.  Finding it in
        # visible history proves that the immediately following row is the old
        # forming candle even when that candle's final wick/body changed too
        # much for a direct visual match.
        anchor_basis = "PRIOR_CLOSED_PREDECESSOR"
        best_score = predecessor_score
        margin = predecessor_margin
        best_index = predecessor_index + 1
        chain_prefix = [dict(closed_tail[predecessor_index])]
    else:
        evidence.update(
            {
                "reason": "FORMER_LIVE_BAR_MATCH_AMBIGUOUS",
                "anchor_match_score": round(
                    max(forming_anchor_score, predecessor_score),
                    6,
                ),
                "anchor_match_margin": round(
                    max(forming_margin, predecessor_margin),
                    6,
                ),
                "candidate_count": len(closed_tail),
            }
        )
        return evidence
    evidence.update(
        {
            "anchor_match_score": round(best_score, 6),
            "anchor_match_margin": round(margin, 6),
            "candidate_count": len(closed_tail),
            "anchor_basis": anchor_basis,
        }
    )

    confirmed_rows = closed_tail[best_index:]
    # Retain enough proven history to recover an over-hour-old M5 episode. The
    # episode ledger still consumes only its first 12 actual rows.
    if not 1 <= len(confirmed_rows) <= 24:
        evidence["reason"] = "REACQUISITION_GAP_OUT_OF_BOUNDS"
        return evidence

    forming_score = _visual_candle_match_score_v3(
        prior_forming,
        current_forming,
        x_step=x_step,
        range_scale=range_scale,
    )
    evidence["current_forming_match_score"] = round(forming_score, 6)
    if anchor_basis == "FORMER_LIVE_BAR" and best_score < forming_score + 0.10:
        evidence["reason"] = "FORMER_LIVE_BAR_NOT_PROVEN_CLOSED"
        return evidence

    chain = [*chain_prefix, *confirmed_rows, dict(current_forming)]
    x_values = [_finite(row.get("x")) for row in chain]
    if any(value is None for value in x_values):
        evidence["reason"] = "REACQUISITION_CHAIN_MISSING_X"
        return evidence
    steps = [
        cast(float, right) - cast(float, left)
        for left, right in zip(x_values, x_values[1:])
    ]
    minimum_step = max(0.5, x_step * 0.30)
    maximum_step = max(3.0, x_step * 1.80)
    contiguous = bool(
        steps
        and all(minimum_step <= step <= maximum_step for step in steps)
    )
    evidence.update(
        {
            "minimum_observed_x_step": round(min(steps), 6) if steps else 0.0,
            "maximum_observed_x_step": round(max(steps), 6) if steps else 0.0,
            "spatial_chain_contiguous": contiguous,
        }
    )
    if not contiguous:
        evidence["reason"] = "REACQUISITION_CHAIN_NOT_CONTIGUOUS"
        return evidence

    evidence.update(
        {
            "status": "CONFIRMED",
            "reason": "FORMER_LIVE_BAR_FOUND_IN_CONTIGUOUS_CLOSED_HISTORY",
            "confirmed_closed_count": len(confirmed_rows),
            "events": confirmed_rows,
        }
    )
    return evidence


def _prior_close_reobservation_v3(
    prior_closed: Mapping[str, Any],
    observation: Mapping[str, Any],
    *,
    prior_key: str,
    prior_sequence: int,
    transition_observed: bool,
    transition_count: int,
    x_step: float,
    range_scale: float,
) -> dict[str, Any]:
    """Prove the prior event's close on the current screenshot coordinate axis.

    A rolling tracker ``track_id`` is never identity evidence.  The prior close
    must instead be the unique visual/source match at the exact predecessor
    position implied by the resolver's causally confirmed transition count.
    This bounded proof is consumed by the study lane; it cannot authorize a
    trade or manufacture a candle event by itself.
    """

    evidence: dict[str, Any] = {
        "status": "NOT_CONFIRMED",
        "reason": "NO_CONFIRMED_CANDLE_TRANSITION",
        "proof_source": "PG_CLOSED_CANDLE_IDENTITY_STATE_V3",
        "prior_closed_candle_key": str(prior_key or ""),
        "prior_closed_candle_sequence": max(0, int(prior_sequence)),
        "current_row_index": -1,
        "match_score": 0.0,
        "match_margin": 0.0,
    }
    if not transition_observed or transition_count < 1 or not prior_closed:
        return evidence
    closed_tail = [
        dict(cast(Mapping[str, Any], row))
        for row in cast(Sequence[Any], observation.get("closed_tail") or [])
        if isinstance(row, Mapping)
    ]
    expected_index = len(closed_tail) - int(transition_count) - 1
    if expected_index < 0 or expected_index >= len(closed_tail) - 1:
        evidence["reason"] = "PRIOR_CLOSE_NOT_VISIBLE_BEFORE_CONFIRMED_EVENTS"
        return evidence

    scored = [
        _visual_candle_match_score_v3(
            prior_closed,
            row,
            x_step=x_step,
            range_scale=range_scale,
        )
        for row in closed_tail
    ]
    expected_score = scored[expected_index]
    second_score = max(
        (score for index, score in enumerate(scored) if index != expected_index),
        default=0.0,
    )
    margin = expected_score - second_score
    evidence.update(
        {
            "match_score": round(expected_score, 6),
            "match_margin": round(margin, 6),
            "candidate_count": len(closed_tail),
        }
    )
    if expected_score < 0.62 or margin < 0.10:
        evidence["reason"] = "PRIOR_CLOSE_MATCH_AMBIGUOUS"
        return evidence

    matched = closed_tail[expected_index]
    successor = closed_tail[expected_index + 1]
    matched_x = _finite(matched.get("x"))
    successor_x = _finite(successor.get("x"))
    if matched_x is None or successor_x is None:
        evidence["reason"] = "PRIOR_CLOSE_CHAIN_MISSING_X"
        return evidence
    observed_step = successor_x - matched_x
    if not max(0.5, x_step * 0.30) <= observed_step <= max(3.0, x_step * 1.80):
        evidence["reason"] = "PRIOR_CLOSE_CHAIN_NOT_CONTIGUOUS"
        return evidence

    parsed_row_index = _finite(matched.get("index"))
    row_index = int(parsed_row_index) if parsed_row_index is not None else -1
    if row_index < 0:
        evidence["reason"] = "PRIOR_CLOSE_ROW_INDEX_UNPROVEN"
        return evidence
    evidence.update(
        {
            "status": "CONFIRMED",
            "reason": "PRIOR_CLOSE_REOBSERVED_ON_CURRENT_AXIS",
            "current_row_index": row_index,
            "current_track_id": str(matched.get("track_id") or ""),
            "observed_x_step": round(observed_step, 6),
        }
    )
    return evidence


_STABLE_VISIBLE_CANDLE_BINDING_LIMIT_V3 = 32


def _stable_visual_match_score_v3(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    *,
    x_step: float,
    range_scale: float,
) -> float:
    """Match candle shape without allowing a rolling tracker id to decide it."""

    same_source = _same_source_candle_v3(left, right)
    if same_source is not None:
        return 1.0 if same_source else 0.0
    comparable_shape_fields = sum(
        _finite(left.get(key)) is not None and _finite(right.get(key)) is not None
        for key in ("open_y", "close_y", "top_y", "bottom_y")
    )
    if comparable_shape_fields < 3:
        return 0.0
    # ``_visual_candle_match_score_v3`` uses track id only as weak supporting
    # evidence. Remove it entirely here: stable history may survive detector
    # reacquisition only through source identity or candle geometry.
    left_without_track = dict(left)
    right_without_track = dict(right)
    left_without_track.pop("track_id", None)
    right_without_track.pop("track_id", None)
    return _visual_candle_match_score_v3(
        left_without_track,
        right_without_track,
        x_step=x_step,
        range_scale=range_scale,
    )


def _stable_chain_is_contiguous_v3(
    closed_tail: Sequence[Mapping[str, Any]],
    *,
    start_index: int,
    x_step: float,
) -> bool:
    """Require every successor through the current close to remain visible."""

    if start_index < 0 or start_index >= len(closed_tail):
        return False
    chain = closed_tail[start_index:]
    if len(chain) == 1:
        return _finite(chain[0].get("x")) is not None
    x_values = [_finite(row.get("x")) for row in chain]
    if any(value is None for value in x_values):
        return False
    minimum_step = max(0.5, x_step * 0.30)
    maximum_step = max(3.0, x_step * 1.80)
    return all(
        minimum_step <= cast(float, right) - cast(float, left) <= maximum_step
        for left, right in zip(x_values, x_values[1:])
    )


def _stable_binding_row_v3(
    observation: Mapping[str, Any],
    *,
    event_key: str,
    event_sequence: int,
    proof_source: str,
    sequence_distance: int,
    match_score: float,
    match_margin: float,
) -> dict[str, Any] | None:
    parsed_index = _finite(observation.get("index"))
    row_index = int(parsed_index) if parsed_index is not None else -1
    if row_index < 0 or not event_key or event_sequence < 0:
        return None
    return {
        "current_row_index": row_index,
        "closed_candle_key": str(event_key),
        "closed_candle_sequence": int(event_sequence),
        "proof_source": str(proof_source),
        "reobserved_observation": dict(observation),
        "sequence_distance_from_latest": max(0, int(sequence_distance)),
        "match_score": round(max(0.0, min(1.0, match_score)), 6),
        "match_margin": round(max(0.0, min(1.0, match_margin)), 6),
    }


def _initial_stable_visible_candle_binding_v3(
    observation: Mapping[str, Any],
    *,
    event_key: str,
    event_sequence: int,
) -> list[dict[str, Any]]:
    latest_closed = observation.get("latest_closed")
    if not isinstance(latest_closed, Mapping):
        return []
    binding = _stable_binding_row_v3(
        cast(Mapping[str, Any], latest_closed),
        event_key=event_key,
        event_sequence=event_sequence,
        proof_source="INITIAL_CAUSAL_BASELINE_V3",
        sequence_distance=0,
        match_score=1.0,
        match_margin=1.0,
    )
    return [binding] if binding is not None else []


def _stable_visible_candle_bindings_v3(
    prior: Mapping[str, Any],
    observation: Mapping[str, Any],
    *,
    event_sequence: int,
    confirmed_event_batch: Sequence[Mapping[str, Any]],
    x_step: float,
    range_scale: float,
) -> list[dict[str, Any]]:
    """Bind proven event identities to visible rows on the current frame.

    Consumers may use only the returned rows as stable screenshot candle
    identities. A sequence's expected row is derived from its causal distance
    to the newest confirmed close. Source identity, or a unique visual match,
    must confirm that exact row and the X chain through the latest close must
    be contiguous. Missing, ambiguous, duplicate, or off-screen bindings are
    omitted instead of being carried forward or inferred from ``track_id``.
    """

    closed_tail = [
        dict(cast(Mapping[str, Any], row))
        for row in cast(Sequence[Any], observation.get("closed_tail") or [])
        if isinstance(row, Mapping)
    ]
    if not closed_tail:
        return []

    raw_prior_bindings = [
        dict(cast(Mapping[str, Any], row))
        for row in cast(
            Sequence[Any],
            prior.get("stable_visible_candle_bindings") or [],
        )
        if isinstance(row, Mapping)
    ]
    if not raw_prior_bindings:
        prior_observation = prior.get("latest_closed")
        prior_key = str(prior.get("event_key") or "")
        prior_sequence = _finite(prior.get("event_sequence"))
        if (
            isinstance(prior_observation, Mapping)
            and prior_key
            and prior_sequence is not None
        ):
            raw_prior_bindings = [
                {
                    "closed_candle_key": prior_key,
                    "closed_candle_sequence": int(prior_sequence),
                    "reobserved_observation": dict(
                        cast(Mapping[str, Any], prior_observation)
                    ),
                }
            ]

    sequence_counts: dict[int, int] = {}
    for binding in raw_prior_bindings:
        parsed_sequence = _finite(binding.get("closed_candle_sequence"))
        if parsed_sequence is None:
            continue
        sequence = int(parsed_sequence)
        sequence_counts[sequence] = sequence_counts.get(sequence, 0) + 1

    rebound: list[dict[str, Any]] = []
    for binding in raw_prior_bindings:
        parsed_sequence = _finite(binding.get("closed_candle_sequence"))
        prior_observation = binding.get("reobserved_observation")
        key = str(binding.get("closed_candle_key") or "")
        if (
            parsed_sequence is None
            or not isinstance(prior_observation, Mapping)
            or not key
        ):
            continue
        sequence = int(parsed_sequence)
        if sequence_counts.get(sequence) != 1 or not 0 <= sequence <= event_sequence:
            continue
        sequence_distance = event_sequence - sequence
        expected_index = len(closed_tail) - sequence_distance - 1
        if not 0 <= expected_index < len(closed_tail):
            continue
        expected = closed_tail[expected_index]

        source_match_indices = [
            index
            for index, row in enumerate(closed_tail)
            if _same_source_candle_v3(
                cast(Mapping[str, Any], prior_observation),
                row,
            )
            is True
        ]
        if source_match_indices:
            if source_match_indices != [expected_index]:
                continue
            score = 1.0
            margin = 1.0
            proof_source = "UNIQUE_SOURCE_IDENTITY_REOBSERVATION_V3"
        else:
            scores = [
                _stable_visual_match_score_v3(
                    cast(Mapping[str, Any], prior_observation),
                    row,
                    x_step=x_step,
                    range_scale=range_scale,
                )
                for row in closed_tail
            ]
            score = scores[expected_index]
            second_score = max(
                (
                    candidate_score
                    for index, candidate_score in enumerate(scores)
                    if index != expected_index
                ),
                default=0.0,
            )
            margin = score - second_score
            if score < 0.62 or margin < 0.10:
                continue
            proof_source = "UNIQUE_VISUAL_REOBSERVATION_V3"
        if not _stable_chain_is_contiguous_v3(
            closed_tail,
            start_index=expected_index,
            x_step=x_step,
        ):
            continue
        rebound_binding = _stable_binding_row_v3(
            expected,
            event_key=key,
            event_sequence=sequence,
            proof_source=proof_source,
            sequence_distance=sequence_distance,
            match_score=score,
            match_margin=margin,
        )
        if rebound_binding is not None:
            rebound.append(rebound_binding)

    appended: list[dict[str, Any]] = []
    for event in confirmed_event_batch:
        parsed_sequence = _finite(event.get("closed_candle_sequence"))
        event_key = str(event.get("closed_candle_key") or "")
        event_observation = event.get("observation")
        if (
            parsed_sequence is None
            or not event_key
            or not isinstance(event_observation, Mapping)
        ):
            continue
        sequence = int(parsed_sequence)
        if not 0 <= sequence <= event_sequence:
            continue
        sequence_distance = event_sequence - sequence
        expected_index = len(closed_tail) - sequence_distance - 1
        if not 0 <= expected_index < len(closed_tail):
            continue
        expected = closed_tail[expected_index]
        source_match = _same_source_candle_v3(
            cast(Mapping[str, Any], event_observation),
            expected,
        )
        score = _stable_visual_match_score_v3(
            cast(Mapping[str, Any], event_observation),
            expected,
            x_step=x_step,
            range_scale=range_scale,
        )
        if source_match is False or score < 0.62:
            continue
        if not _stable_chain_is_contiguous_v3(
            closed_tail,
            start_index=expected_index,
            x_step=x_step,
        ):
            continue
        appended_binding = _stable_binding_row_v3(
            expected,
            event_key=event_key,
            event_sequence=sequence,
            proof_source=str(event.get("confirmation_reason") or "CONFIRMED_EVENT_V3"),
            sequence_distance=sequence_distance,
            match_score=score,
            match_margin=1.0,
        )
        if appended_binding is not None:
            appended.append(appended_binding)

    by_sequence: dict[int, dict[str, Any]] = {}
    for binding in rebound:
        sequence = int(binding["closed_candle_sequence"])
        by_sequence.setdefault(sequence, binding)
    for binding in appended:
        # A newly confirmed event is the current-frame authority for its
        # sequence; prior bindings never legitimately overlap it.
        by_sequence[int(binding["closed_candle_sequence"])] = binding
    ordered = [by_sequence[sequence] for sequence in sorted(by_sequence)]
    return ordered[-_STABLE_VISIBLE_CANDLE_BINDING_LIMIT_V3:]


def resolve_closed_candle_identity_v3(
    candles: Sequence[Mapping[str, Any]],
    *,
    pair: str,
    timeframe: str,
    previous_state: Mapping[str, Any] | None = None,
    previous_key: str = "",
    previous_sequence: int = -1,
) -> dict[str, Any]:
    """Resolve screenshot candle events without treating detections as a clock.

    Source bar ids/open times are authoritative. With screenshot-only input,
    the event advances only when the prior forming-candle observation matches
    the new latest-closed candle and a distinct forming candle appears. A
    detector dropout, color reclassification, or geometry refinement therefore
    cannot manufacture a new market candle. A multi-candle detector coverage
    repair rebases the observation without incrementing the event sequence and
    explicitly asks the caller to replace same-event forecast geometry.
    """

    pair_key = str(pair or "UNKNOWN").strip().upper()
    timeframe_key = str(timeframe or "UNKNOWN").strip().upper()
    observation = _closed_candle_observation_state_v3(candles)
    current_closed = cast(Mapping[str, Any], observation["latest_closed"])
    current_forming = cast(Mapping[str, Any], observation["forming"])
    prior = dict(previous_state or {})
    prior_matches_context = bool(
        prior
        and str(prior.get("pair") or "").upper() == pair_key
        and str(prior.get("timeframe") or "").upper() == timeframe_key
    )
    if not prior_matches_context:
        context_changed = bool(prior)
        baseline_key = (
            "" if context_changed else str(previous_key or "")
        ) or closed_candle_identity_v3(
            candles,
            pair=pair_key,
            timeframe=timeframe_key,
        )
        baseline_sequence = 0 if context_changed else max(0, int(previous_sequence))
        stable_visible_candle_bindings = _initial_stable_visible_candle_binding_v3(
            observation,
            event_key=baseline_key,
            event_sequence=baseline_sequence,
        )
        state: dict[str, Any] = {
            "schema_version": "PG_CLOSED_CANDLE_IDENTITY_STATE_V3",
            "pair": pair_key,
            "timeframe": timeframe_key,
            "event_key": baseline_key,
            "event_sequence": baseline_sequence,
            "confirmed_event_batch": [],
            "transition_count": 0,
            "reacquisition": {
                "status": "NOT_REQUIRED",
                "reason": "INITIAL_CAUSAL_BASELINE",
                "confirmed_closed_count": 0,
            },
            **observation,
            "stable_visible_candle_bindings": stable_visible_candle_bindings,
        }
        return {
            "closed_candle_key": baseline_key,
            "closed_candle_sequence": baseline_sequence,
            "transition_observed": False,
            "transition_reason": "INITIAL_CAUSAL_BASELINE",
            "same_event_cache_rebuild_required": False,
            "match_scores": {},
            "prior_close_reobservation": {
                "status": "NOT_CONFIRMED",
                "reason": "INITIAL_CAUSAL_BASELINE",
                "proof_source": "PG_CLOSED_CANDLE_IDENTITY_STATE_V3",
            },
            "stable_visible_candle_bindings": stable_visible_candle_bindings,
            "state": state,
        }

    prior_key = str(prior.get("event_key") or previous_key or "")
    prior_sequence = max(
        0,
        int(prior.get("event_sequence", previous_sequence) or 0),
    )
    prior_closed = cast(Mapping[str, Any], prior.get("latest_closed") or {})
    prior_forming = cast(Mapping[str, Any], prior.get("forming") or {})
    x_step = max(
        1.0,
        float(prior.get("median_x_step") or 0.0),
        float(observation.get("median_x_step") or 0.0),
    )
    range_scale = max(
        4.0,
        float(prior.get("median_range") or 0.0),
        float(observation.get("median_range") or 0.0),
    )
    closed_same = _visual_candle_match_score_v3(
        prior_closed,
        current_closed,
        x_step=x_step,
        range_scale=range_scale,
    )
    forming_same = _visual_candle_match_score_v3(
        prior_forming,
        current_forming,
        x_step=x_step,
        range_scale=range_scale,
    )
    rollover = _visual_candle_match_score_v3(
        prior_forming,
        current_closed,
        x_step=x_step,
        range_scale=range_scale,
    )
    scores = {
        "latest_closed_same": round(closed_same, 6),
        "forming_still_active": round(forming_same, 6),
        "forming_became_closed": round(rollover, 6),
    }
    coverage_rebase, coverage_audit = _detector_coverage_rebase_v3(
        prior,
        observation,
        x_step=x_step,
    )
    coverage_degraded = bool(
        _observation_count_v3(observation, closed_only=False)
        < _observation_count_v3(prior, closed_only=False)
        or _observation_count_v3(observation, closed_only=True)
        < _observation_count_v3(prior, closed_only=True)
    )
    coverage_audit["coverage_degradation_observed"] = coverage_degraded
    scores.update(coverage_audit)
    reacquisition = _gap_reacquisition_v3(
        prior_closed,
        prior_forming,
        observation,
        x_step=x_step,
        range_scale=range_scale,
        coverage_rebase=coverage_rebase,
    )

    source_closed_match = _same_source_candle_v3(prior_closed, current_closed)
    source_rollover_match = _same_source_candle_v3(prior_forming, current_closed)
    source_forming_match = _same_source_candle_v3(prior_forming, current_forming)
    source_time_steps = _source_time_step_count_v3(
        prior_closed,
        current_closed,
        timeframe=timeframe_key,
    )
    scores["source_time_step_count"] = source_time_steps or 0
    scores["source_one_step_horizon_proven"] = source_time_steps == 1
    transition = False
    transition_count = 0
    reason = "AMBIGUOUS_SCREENSHOT_REUSES_EVENT"
    if source_rollover_match is True and source_forming_match is False:
        transition = True
        transition_count = 1
        reason = "SOURCE_FORMING_BAR_BECAME_CLOSED"
    elif source_closed_match is False and source_time_steps == 1:
        transition = True
        transition_count = 1
        reason = "SOURCE_BAR_ID_ADVANCED"
    elif (
        reacquisition.get("status") == "CONFIRMED"
        and int(reacquisition.get("confirmed_closed_count", 0) or 0) >= 1
    ):
        transition = True
        transition_count = int(reacquisition["confirmed_closed_count"])
        reason = (
            "VISUAL_CLOSED_CANDLE_GAP_REACQUIRED"
            if transition_count > 1
            else "VISUAL_FORMING_CANDLE_BECAME_CLOSED"
        )
    elif coverage_rebase:
        # Detector history repair can expose many candles that were already on
        # the same captured chart. It changes the model context and anchor but
        # does not manufacture a new market event.
        reason = "DETECTOR_COVERAGE_REBASE"
    elif source_closed_match is False:
        # An arbitrary changed source id does not disclose how many market
        # intervals were missed. Timestamp-like identities must prove exactly
        # one timeframe above, or a complete visible reacquisition chain must
        # enumerate every intervening close. Otherwise preserve the prior
        # event so N -> N+1 outcome maturation cannot absorb a gap.
        reason = "SOURCE_BAR_GAP_UNPROVEN"
    elif source_closed_match is True:
        reason = "SOURCE_BAR_ID_UNCHANGED"
    elif forming_same >= 0.62:
        reason = "FORMING_CANDLE_STILL_ACTIVE"
    elif closed_same >= 0.62:
        reason = "LATEST_CLOSED_CANDLE_UNCHANGED"

    scores["coverage_high_water_preserved"] = bool(
        coverage_degraded and not coverage_rebase and not transition
    )

    if transition:
        event_observations: list[dict[str, Any]] = (
            [
                dict(cast(Mapping[str, Any], row))
                for row in cast(Sequence[Any], reacquisition.get("events") or [])
                if isinstance(row, Mapping)
            ]
            if transition_count > 1
            else [dict(current_closed)]
        )
        transition_count = len(event_observations)
        event_sequence = prior_sequence + transition_count
        confirmed_event_batch: list[dict[str, Any]] = []
        for offset, event_observation in enumerate(event_observations, start=1):
            sequence = prior_sequence + offset
            confirmed_event_batch.append(
                {
                    "closed_candle_key": _closed_event_key_v3(
                        event_observation,
                        pair=pair_key,
                        timeframe=timeframe_key,
                        event_sequence=sequence,
                    ),
                    "closed_candle_sequence": sequence,
                    "observation": event_observation,
                    "confirmation_reason": reason,
                    "reacquired": transition_count > 1,
                }
            )
        event_key = str(confirmed_event_batch[-1]["closed_candle_key"])
        next_observation = observation
    else:
        confirmed_event_batch = []
        event_sequence = prior_sequence
        event_key = prior_key or closed_candle_identity_v3(
            candles,
            pair=pair_key,
            timeframe=timeframe_key,
        )
        # Retain the trusted completed candle across a detector dropout or
        # BUY/SELL reclassification. Refresh the forming observation only when
        # it clearly remains the same physical candle.
        next_observation = dict(observation)
        if not coverage_rebase:
            if closed_same < 0.62:
                next_observation["latest_closed"] = dict(prior_closed)
            if forming_same < 0.62:
                next_observation["forming"] = dict(prior_forming)
            if coverage_degraded:
                # Detector coverage can oscillate while the underlying chart
                # is unchanged. Keep the fuller structural baseline so a
                # subsequent recovery does not repeatedly rebuild history and
                # forecast geometry.
                for key in (
                    "detected_candle_count",
                    "detected_closed_count",
                    "coverage_left_x",
                    "coverage_right_x",
                    "coverage_span_x",
                ):
                    if key in prior:
                        next_observation[key] = prior[key]

    stable_visible_candle_bindings = _stable_visible_candle_bindings_v3(
        prior,
        observation,
        event_sequence=event_sequence,
        confirmed_event_batch=confirmed_event_batch,
        x_step=x_step,
        range_scale=range_scale,
    )
    state: dict[str, Any] = {
        "schema_version": "PG_CLOSED_CANDLE_IDENTITY_STATE_V3",
        "pair": pair_key,
        "timeframe": timeframe_key,
        "event_key": event_key,
        "event_sequence": event_sequence,
        "confirmed_event_batch": confirmed_event_batch,
        "transition_count": transition_count,
        "reacquisition": {
            key: value
            for key, value in reacquisition.items()
            if key != "events"
        },
        **next_observation,
        "stable_visible_candle_bindings": stable_visible_candle_bindings,
    }
    prior_close_reobservation = _prior_close_reobservation_v3(
        prior_closed,
        observation,
        prior_key=prior_key,
        prior_sequence=prior_sequence,
        transition_observed=transition,
        transition_count=transition_count,
        x_step=x_step,
        range_scale=range_scale,
    )
    return {
        "closed_candle_key": event_key,
        "closed_candle_sequence": event_sequence,
        "transition_observed": transition,
        "transition_reason": reason,
        "transition_count": transition_count,
        "reacquisition": {
            key: value
            for key, value in reacquisition.items()
            if key != "events"
        },
        "same_event_cache_rebuild_required": bool(coverage_rebase and not transition),
        "match_scores": scores,
        "prior_close_reobservation": prior_close_reobservation,
        "stable_visible_candle_bindings": stable_visible_candle_bindings,
        "state": state,
    }


def build_scene_forecast_anchor_v3(
    candles: Sequence[Mapping[str, Any]],
    *,
    image_size: tuple[int, int] | Sequence[int],
) -> dict[str, Any]:
    size = list(image_size)[:2]
    if len(size) < 2:
        raise ValueError("image_size must provide width and height")
    width = max(1.0, float(size[0]))
    height = max(1.0, float(size[1]))
    _closed_index, latest = latest_closed_candle_v3(candles)
    latest_x = _candle_x(latest)
    latest_close_y = _close_y(latest, height=height)
    if latest_x is None or latest_close_y is None:
        raise ValueError("the latest closed candle lacks an exact close anchor")

    closed_rows = [
        dict(row)
        for index, row in enumerate(candles)
        if candle_is_closed_v3(row, index=index, total=len(candles))
    ]
    x_values = [value for row in closed_rows if (value := _candle_x(row)) is not None]
    x_steps = [
        right - left
        for left, right in zip(x_values, x_values[1:])
        if right - left > 0.5
    ]
    natural_step = statistics.median(x_steps[-24:]) if x_steps else max(4.0, width * 0.018)
    x_norm = max(0.0, min(1.0, latest_x / width))
    available_step = max(1.0 / width, (0.985 - x_norm) / FORECAST_HORIZON_STEPS_V3)
    event_step_x_norm = min(natural_step / width, available_step)

    range_values: list[float] = []
    for row in closed_rows[-32:]:
        top = _axis_value(row, "wick_top_px", "wick_top", "high_y_px", "high_y")
        bottom = _axis_value(
            row,
            "wick_bottom_px",
            "wick_bottom",
            "low_y_px",
            "low_y",
        )
        if top is None or bottom is None:
            bbox = row.get("bbox")
            if isinstance(bbox, Sequence) and not isinstance(bbox, (str, bytes, bytearray)):
                values = [
                    _finite(value)
                    for value in list(cast(Sequence[Any], bbox))[:4]
                ]
                if len(values) == 4 and all(value is not None for value in values):
                    top, bottom = cast(float, values[1]), cast(float, values[3])
        if top is not None and bottom is not None and abs(bottom - top) > 0.0:
            range_values.append(abs(bottom - top) / height)
    target_scale = statistics.median(range_values) if range_values else 1.0 / height
    y_norm = max(0.0, min(1.0, latest_close_y / height))
    return {
        "x_norm": x_norm,
        "y_norm": y_norm,
        "price_norm": 1.0 - y_norm,
        "target_scale_norm": max(1.0 / height, min(1.0, target_scale)),
        "event_step_x_norm": event_step_x_norm,
        "natural_event_step_x_norm": natural_step / width,
        "horizontal_fit_applied": bool(event_step_x_norm + 1e-12 < natural_step / width),
        "verified_latest_close": True,
        "source": "TRACKER_LATEST_CLOSED_CANDLE",
    }


def _compatibility_forecast_path(
    contribution: Mapping[str, Any],
) -> list[dict[str, Any]]:
    raw_probabilities = contribution.get("raw_side_probabilities")
    probabilities: dict[str, Any] = (
        dict(cast(Mapping[str, Any], raw_probabilities))
        if isinstance(raw_probabilities, Mapping)
        else {}
    )
    buy_probability = max(0.0, float(probabilities.get("BUY") or 0.0))
    sell_probability = max(0.0, float(probabilities.get("SELL") or 0.0))
    rows: list[dict[str, Any]] = []
    for raw in cast(Iterable[Any], contribution.get("forecast_candles", [])):
        if not isinstance(raw, Mapping):
            continue
        row = cast(Mapping[str, Any], raw)
        movement = _side(row.get("movement_side") or row.get("body_bias"))
        open_y = float(row.get("open_y_norm") or 0.0)
        close_y = float(row.get("close_y_norm") or 0.0)
        high_y = float(row.get("high_y_norm") or 0.0)
        low_y = float(row.get("low_y_norm") or 0.0)
        confidence = max(buy_probability, sell_probability, float(probabilities.get("HOLD") or 0.0))
        rows.append(
            {
                "step": int(row.get("step") or len(rows) + 1),
                "label": str(row.get("label") or f"E{len(rows) + 1}"),
                "direction": movement,
                "movement_direction": movement,
                "expected_open_norm": 1.0 - open_y,
                "expected_close_norm": 1.0 - close_y,
                "expected_high_norm": 1.0 - high_y,
                "expected_low_norm": 1.0 - low_y,
                "expected_range_norm": abs(low_y - high_y),
                "buy_probability": buy_probability,
                "sell_probability": sell_probability,
                "confidence": confidence,
                "selective_authorized": False,
            }
        )
    return rows


def synchronize_scene_forecast_geometry_v3(
    contribution: Mapping[str, Any],
    *,
    selected_role: str | None = None,
    selected_side: str | None = None,
) -> dict[str, Any]:
    """Select one scenario and copy its complete geometry as one atomic bundle.

    A side label, close line, and OHLC candle sequence must never be selected
    independently.  ``selected_role`` is preferred because endpoint-derived
    scenario sides may legitimately repeat (for example all sampled paths can
    finish above the anchor).  ``selected_side`` is used by the sticky belief
    tracker only when that side actually exists in the current distribution.
    """

    result = copy.deepcopy(dict(contribution))
    scenarios: list[dict[str, Any]] = [
        dict(cast(Mapping[str, Any], raw))
        for raw in cast(Iterable[Any], result.get("forecast_scenarios", []))
        if isinstance(raw, Mapping)
    ]
    if len(scenarios) != 3:
        raise ValueError("a complete three-scenario forecast bundle is required")

    role_key = str(selected_role or "").strip().lower()
    side_key = _side(selected_side) if selected_side is not None else ""
    candidates = scenarios
    if role_key:
        candidates = [
            row for row in scenarios if str(row.get("role") or "").strip().lower() == role_key
        ]
    elif side_key:
        candidates = [row for row in scenarios if _side(row.get("side")) == side_key]
    else:
        candidates = [row for row in scenarios if bool(row.get("selected", False))]
    if not candidates:
        raise ValueError("the requested forecast scenario is unavailable")
    selected = max(
        candidates,
        key=lambda row: float(_finite(row.get("probability")) or 0.0),
    )
    selected_identity = (
        str(selected.get("role") or "").strip().lower(),
        tuple(
            tuple(cast(Iterable[Any], point))
            for point in cast(Iterable[Any], selected.get("line_points", []))
        ),
    )
    line_points: list[Any] = copy.deepcopy(
        list(cast(Iterable[Any], selected.get("line_points", [])))
    )
    forecast_candles: list[Any] = copy.deepcopy(
        list(cast(Iterable[Any], selected.get("forecast_candles", [])))
    )
    if len(line_points) != FORECAST_HORIZON_STEPS_V3 + 1:
        raise ValueError("the selected scenario must contain an anchor plus twelve events")
    if len(forecast_candles) != FORECAST_HORIZON_STEPS_V3:
        raise ValueError("the selected scenario must contain twelve connected OHLC candles")

    for scenario in scenarios:
        identity = (
            str(scenario.get("role") or "").strip().lower(),
            tuple(
                tuple(cast(Iterable[Any], point))
                for point in cast(Iterable[Any], scenario.get("line_points", []))
            ),
        )
        scenario["selected"] = identity == selected_identity

    selected_side_value = _side(selected.get("side"))
    result.update(
        {
            "forecast_scenarios": scenarios,
            "trajectory_scenarios": copy.deepcopy(scenarios),
            "line_points": line_points,
            "forecast_candles": forecast_candles,
            "path_side": selected_side_value,
            "side": selected_side_value,
            "selected_scenario_role": str(selected.get("role") or "base").lower(),
        }
    )
    path = _compatibility_forecast_path(result)
    result["forecast_path"] = path
    result["next_1_direction"] = path[0]["movement_direction"] if path else "HOLD"
    result["next_2_direction"] = path[1]["movement_direction"] if len(path) > 1 else "HOLD"
    progression = dict(result.get("progression_play", {}))
    progression.update(
        {
            "dominant_direction": selected_side_value,
            "first_direction_change_step": next(
                (
                    int(row["step"])
                    for row in path[1:]
                    if path and row["movement_direction"] != path[0]["movement_direction"]
                ),
                None,
            ),
            "horizon_steps": FORECAST_HORIZON_STEPS_V3,
            "horizon_unit": "CANDLE_EVENTS",
        }
    )
    result["progression_play"] = progression
    return result


def reanchor_scene_forecast_geometry_v3(
    contribution: Mapping[str, Any],
    *,
    anchor: Mapping[str, Any],
) -> dict[str, Any]:
    """Move a complete cached forecast onto one current verified anchor.

    Translation preserves the issued route exactly. If that translation would
    leave the chart plane, one positive affine gain is shared by every value on
    that axis. Individual points are never clipped, so turns, scenario
    separation, OHLC ordering, and interval width remain coherent.
    """

    result = copy.deepcopy(dict(contribution))

    def required_sequence(value: object, *, label: str) -> list[Any]:
        if not isinstance(value, Sequence) or isinstance(
            value,
            (str, bytes, bytearray),
        ):
            raise ValueError(f"cached forecast must contain {label}")
        return list(cast(Sequence[Any], value))

    root_points = required_sequence(
        result.get("line_points"),
        label="an anchor plus twelve events",
    )
    root_candles = required_sequence(
        result.get("forecast_candles"),
        label="twelve connected OHLC candles",
    )
    raw_scenarios = required_sequence(
        result.get("forecast_scenarios"),
        label="exactly three scenarios",
    )
    if len(root_points) != FORECAST_HORIZON_STEPS_V3 + 1:
        raise ValueError("cached forecast must contain an anchor plus twelve events")
    if len(root_candles) != FORECAST_HORIZON_STEPS_V3:
        raise ValueError("cached forecast must contain twelve connected OHLC candles")
    if len(raw_scenarios) != 3:
        raise ValueError("cached forecast must contain exactly three scenarios")

    def point_values(value: object, *, label: str) -> tuple[float, float]:
        if not isinstance(value, Sequence) or isinstance(
            value,
            (str, bytes, bytearray),
        ):
            raise ValueError(f"{label} must be an x/y point")
        values = list(cast(Sequence[Any], value))
        if len(values) < 2:
            raise ValueError(f"{label} must be an x/y point")
        x_value = _finite(values[0])
        y_value = _finite(values[1])
        if x_value is None or y_value is None:
            raise ValueError(f"{label} must be finite")
        return x_value, y_value

    root_origin = point_values(
        root_points[0],
        label="line_points[0]",
    )
    prior_anchor = result.get("forecast_anchor")
    prior_anchor_mapping: Mapping[str, Any]
    if isinstance(prior_anchor, Mapping):
        prior_anchor_mapping = cast(Mapping[str, Any], prior_anchor)
    else:
        prior_anchor_mapping = {}
    old_x = _finite(prior_anchor_mapping.get("x_norm"))
    old_y = _finite(prior_anchor_mapping.get("y_norm"))
    if old_x is None:
        old_x = root_origin[0]
    if old_y is None:
        old_y = root_origin[1]
    new_x = _finite(anchor.get("x_norm"))
    new_y = _finite(anchor.get("y_norm"))
    if new_x is None or new_y is None or not (0.0 <= new_x <= 1.0 and 0.0 <= new_y <= 1.0):
        raise ValueError("current forecast anchor must be normalized")
    if not bool(anchor.get("verified_latest_close", False)):
        raise ValueError("current forecast anchor must be a verified latest close")
    if abs(root_origin[0] - old_x) > 1e-7 or abs(root_origin[1] - old_y) > 1e-7:
        raise ValueError("cached root path does not begin at its forecast anchor")

    x_values: list[float] = []
    y_values: list[float] = []

    def collect_points(value: object, *, label: str) -> None:
        if not isinstance(value, Sequence) or isinstance(
            value,
            (str, bytes, bytearray),
        ):
            raise ValueError(f"{label} must be a point sequence")
        for index, raw_point in enumerate(cast(Sequence[Any], value)):
            x_value, y_value = point_values(raw_point, label=f"{label}[{index}]")
            x_values.append(x_value)
            y_values.append(y_value)

    candle_y_fields = (
        "open_y_norm",
        "high_y_norm",
        "low_y_norm",
        "close_y_norm",
        "interval_top_y_norm",
        "interval_bottom_y_norm",
    )

    def collect_candles(value: object, *, label: str) -> None:
        if not isinstance(value, Sequence) or isinstance(
            value,
            (str, bytes, bytearray),
        ):
            raise ValueError(f"{label} must be a candle sequence")
        rows = cast(Sequence[Any], value)
        if len(rows) != FORECAST_HORIZON_STEPS_V3:
            raise ValueError(f"{label} must contain twelve candles")
        for index, raw_row in enumerate(rows):
            if not isinstance(raw_row, Mapping):
                raise ValueError(f"{label}[{index}] must be a candle mapping")
            row = cast(Mapping[str, Any], raw_row)
            x_value = _finite(row.get("x_norm"))
            if x_value is None:
                raise ValueError(f"{label}[{index}].x_norm must be finite")
            x_values.append(x_value)
            for field in candle_y_fields:
                if field not in row:
                    continue
                y_value = _finite(row.get(field))
                if y_value is None:
                    raise ValueError(f"{label}[{index}].{field} must be finite")
                y_values.append(y_value)

    collect_points(root_points, label="line_points")
    collect_candles(root_candles, label="forecast_candles")
    scenarios = [
        dict(cast(Mapping[str, Any], raw))
        for raw in raw_scenarios
        if isinstance(raw, Mapping)
    ]
    if len(scenarios) != 3:
        raise ValueError("cached forecast scenarios must be mappings")
    for index, scenario in enumerate(scenarios):
        collect_points(scenario.get("line_points"), label=f"forecast_scenarios[{index}].line_points")
        collect_candles(
            scenario.get("forecast_candles"),
            label=f"forecast_scenarios[{index}].forecast_candles",
        )
        scenario_origin = point_values(
            cast(Sequence[Any], scenario["line_points"])[0],
            label=f"forecast_scenarios[{index}].line_points[0]",
        )
        if abs(scenario_origin[0] - old_x) > 1e-7 or abs(scenario_origin[1] - old_y) > 1e-7:
            raise ValueError("all cached scenarios must share the forecast anchor")

    band_points = result.get("forecast_band_points", [])
    if band_points:
        collect_points(band_points, label="forecast_band_points")
    raw_quantiles = result.get("forecast_quantiles", {})
    quantiles = (
        dict(cast(Mapping[str, Any], raw_quantiles))
        if isinstance(raw_quantiles, Mapping)
        else {}
    )
    for key, points in quantiles.items():
        collect_points(points, label=f"forecast_quantiles.{key}")

    def shared_axis_gain(
        values: Sequence[float],
        *,
        old_anchor: float,
        new_anchor: float,
        padding: float,
        label: str,
    ) -> tuple[float, tuple[float, float]]:
        lower = 0.0 if new_anchor < padding else padding
        upper = 1.0 if new_anchor > 1.0 - padding else 1.0 - padding
        offsets = [value - old_anchor for value in values]
        minimum = min(offsets, default=0.0)
        maximum = max(offsets, default=0.0)
        candidates = [1.0]
        if minimum < -1e-12:
            available = new_anchor - lower
            if available <= 1e-12:
                raise ValueError(f"cached forecast cannot move {label} below its edge anchor")
            candidates.append(available / -minimum)
        if maximum > 1e-12:
            available = upper - new_anchor
            if available <= 1e-12:
                raise ValueError(f"cached forecast cannot move {label} above its edge anchor")
            candidates.append(available / maximum)
        gain = min(candidates)
        if not math.isfinite(gain) or gain <= 0.0:
            raise ValueError(f"cached forecast {label} gain must be positive")
        return min(1.0, gain), (lower, upper)

    x_gain, x_bounds = shared_axis_gain(
        x_values,
        old_anchor=old_x,
        new_anchor=new_x,
        padding=0.015,
        label="horizontally",
    )
    y_gain, y_bounds = shared_axis_gain(
        y_values,
        old_anchor=old_y,
        new_anchor=new_y,
        padding=0.035,
        label="vertically",
    )

    def transform_x(value: Any, *, label: str) -> float:
        number = _finite(value)
        if number is None:
            raise ValueError(f"{label} must be finite")
        transformed = (
            number
            if abs(new_x - old_x) <= 1e-12 and abs(x_gain - 1.0) <= 1e-12
            else new_x + (number - old_x) * x_gain
        )
        if transformed < -1e-9 or transformed > 1.0 + 1e-9:
            raise ValueError(f"{label} leaves the chart after shared reanchor")
        return transformed

    def transform_y(value: Any, *, label: str) -> float:
        number = _finite(value)
        if number is None:
            raise ValueError(f"{label} must be finite")
        transformed = (
            number
            if abs(new_y - old_y) <= 1e-12 and abs(y_gain - 1.0) <= 1e-12
            else new_y + (number - old_y) * y_gain
        )
        if transformed < -1e-9 or transformed > 1.0 + 1e-9:
            raise ValueError(f"{label} leaves the chart after shared reanchor")
        return transformed

    def transform_points(value: object, *, label: str) -> list[list[float]]:
        return [
            [
                transform_x(point_values(raw, label=f"{label}[{index}]")[0], label=f"{label}[{index}].x"),
                transform_y(point_values(raw, label=f"{label}[{index}]")[1], label=f"{label}[{index}].y"),
            ]
            for index, raw in enumerate(cast(Sequence[Any], value))
        ]

    def transform_candles(value: object, *, label: str) -> list[dict[str, Any]]:
        transformed: list[dict[str, Any]] = []
        for index, raw_row in enumerate(cast(Sequence[Any], value)):
            row = dict(cast(Mapping[str, Any], raw_row))
            row["x_norm"] = transform_x(row.get("x_norm"), label=f"{label}[{index}].x_norm")
            for field in candle_y_fields:
                if field in row:
                    row[field] = transform_y(row[field], label=f"{label}[{index}].{field}")
            transformed.append(row)
        return transformed

    transformed_scenarios: list[dict[str, Any]] = []
    for index, scenario in enumerate(scenarios):
        scenario["line_points"] = transform_points(
            scenario["line_points"],
            label=f"forecast_scenarios[{index}].line_points",
        )
        scenario["forecast_candles"] = transform_candles(
            scenario["forecast_candles"],
            label=f"forecast_scenarios[{index}].forecast_candles",
        )
        transformed_scenarios.append(scenario)
    result["forecast_scenarios"] = transformed_scenarios
    result["trajectory_scenarios"] = copy.deepcopy(transformed_scenarios)
    result["line_points"] = transform_points(root_points, label="line_points")
    result["forecast_candles"] = transform_candles(
        root_candles,
        label="forecast_candles",
    )
    result["forecast_band_points"] = (
        transform_points(band_points, label="forecast_band_points")
        if band_points
        else []
    )
    result["forecast_quantiles"] = {
        key: transform_points(points, label=f"forecast_quantiles.{key}")
        for key, points in quantiles.items()
    }
    next_anchor = dict(prior_anchor_mapping)
    next_anchor.update(dict(anchor))
    next_anchor.update(
        {
            "x_norm": new_x,
            "y_norm": new_y,
            "verified_latest_close": True,
        }
    )
    result["forecast_anchor"] = next_anchor
    selected_role = str(result.get("selected_scenario_role") or "").strip().lower()
    if not selected_role:
        selected_role = str(
            next(
                (
                    scenario.get("role")
                    for scenario in transformed_scenarios
                    if bool(scenario.get("selected", False))
                ),
                "base",
            )
            or "base"
        ).lower()
    result = synchronize_scene_forecast_geometry_v3(
        result,
        selected_role=selected_role,
    )
    synchronized_points = cast(Sequence[Sequence[float]], result["line_points"])
    actual_event_step_x = float(synchronized_points[1][0]) - float(
        synchronized_points[0][0]
    )
    synchronized_anchor = dict(
        cast(Mapping[str, Any], result["forecast_anchor"])
    )
    synchronized_anchor["event_step_x_norm"] = actual_event_step_x
    natural_event_step_x = _finite(
        synchronized_anchor.get("natural_event_step_x_norm")
    )
    synchronized_anchor["horizontal_fit_applied"] = bool(
        natural_event_step_x is not None
        and actual_event_step_x + 1e-12 < natural_event_step_x
    )
    result["forecast_anchor"] = synchronized_anchor
    prior_gain = _finite(result.get("geometry_gain"))
    effective_y_gain = y_gain
    if prior_gain is not None:
        effective_y_gain = prior_gain * y_gain
        result["geometry_gain"] = effective_y_gain
    result["viewport_fit_applied"] = bool(
        result.get("viewport_fit_applied", False)
        or x_gain < 1.0 - 1e-12
        or y_gain < 1.0 - 1e-12
    )
    geometry_transform = dict(
        cast(Mapping[str, Any], result.get("geometry_transform", {}))
        if isinstance(result.get("geometry_transform"), Mapping)
        else {}
    )
    geometry_transform.update(
        {
            "mode": "SHARED_ANCHOR_AFFINE_FIT",
            "anchor_x_norm": new_x,
            "anchor_y_norm": new_y,
            "gain": effective_y_gain,
            "viewport_left_norm": x_bounds[0],
            "viewport_right_norm": x_bounds[1],
            "viewport_top_norm": y_bounds[0],
            "viewport_bottom_norm": y_bounds[1],
            "reanchor_x_gain": x_gain,
            "reanchor_y_gain": y_gain,
            "pointwise_clipping_applied": False,
        }
    )
    result["geometry_transform"] = geometry_transform
    result["geometry_reanchor"] = {
        "status": "REANCHORED" if (
            abs(new_x - old_x) > 1e-12
            or abs(new_y - old_y) > 1e-12
            or x_gain < 1.0 - 1e-12
            or y_gain < 1.0 - 1e-12
        ) else "ANCHOR_UNCHANGED",
        "method": "SHARED_ANCHOR_AFFINE_FIT",
        "source_anchor": {"x_norm": old_x, "y_norm": old_y},
        "target_anchor": {"x_norm": new_x, "y_norm": new_y},
        "x_gain": x_gain,
        "y_gain": y_gain,
        "x_bounds": [x_bounds[0], x_bounds[1]],
        "y_bounds": [y_bounds[0], y_bounds[1]],
        "pointwise_clipping_applied": False,
    }
    return result


def build_scene_forecast_contribution_v3(
    *,
    candles: Sequence[Mapping[str, Any]],
    image_size: tuple[int, int] | Sequence[int],
    timeframe: str,
    pair: str,
    projection: Mapping[str, Any] | None = None,
    candle_statistics: Mapping[str, Any] | None = None,
    behavior_payload: Mapping[str, Any] | None = None,
    decision_kernel: Mapping[str, Any] | None = None,
    smart_money_context: Mapping[str, Any] | None = None,
    support_resistance_context: Mapping[str, Any] | None = None,
    support_resistance_zones: Sequence[Mapping[str, Any]] | None = None,
    trend_slopes: Mapping[str, Any] | None = None,
    trend_directions: Mapping[str, Any] | None = None,
    allow_foundation_model: bool = False,
    event_key_override: str = "",
) -> dict[str, Any]:
    scene_features = extract_scene_forecast_features_v3(
        candles=candles,
        projection=projection,
        candle_statistics=candle_statistics,
        behavior_payload=behavior_payload,
        decision_kernel=decision_kernel,
        smart_money_context=smart_money_context,
        support_resistance_context=support_resistance_context,
        support_resistance_zones=support_resistance_zones,
        trend_slopes=trend_slopes,
        trend_directions=trend_directions,
        timeframe=timeframe,
        pair=pair,
    )
    anchor = build_scene_forecast_anchor_v3(candles, image_size=image_size)
    event_key = str(event_key_override or "").strip() or closed_candle_identity_v3(
        candles,
        pair=pair,
        timeframe=timeframe,
    )
    seed = int(event_key[:8], 16)
    provider = build_chronos_scene_forecast_contribution_v3(
        scene_features=scene_features,
        anchor=anchor,
        deterministic_seed=seed,
        allow_foundation_model=allow_foundation_model,
    )
    provider_model = cast(Mapping[str, Any], provider.get("model", {}))
    requested_model_version = str(provider_model.get("model_id") or "").strip()
    model_used_for_forecast = bool(provider_model.get("used_for_forecast"))
    result = dict(provider)
    result.update(
        {
            "schema_version": SCENE_FORECAST_CONTRIBUTION_SCHEMA_V3,
            "stack_version": "PHOENIXGUARD_V3",
            "skill": "MULTIMODAL_SCENE_FORECAST",
            "modality": "COMPUTER_VISION_AND_CAUSAL_CANDLE_SERIES",
            "training_source": "RAW_SCREENSHOT_SUITES_AND_CLOSED_CANDLES",
            "model_version": (
                requested_model_version
                if model_used_for_forecast and requested_model_version
                else str(provider.get("provider") or "SCENE_STATISTICAL_FALLBACK_V3")
            ),
            "requested_model_version": requested_model_version,
            "artifact_available": bool(provider_model.get("loaded")),
            "artifact_loaded": bool(provider_model.get("loaded")),
            "artifact_production_gate_passed": bool(
                provider.get("production_authorized", False)
            ),
            "fresh": True,
            "blocker": False,
            "side": _side(provider.get("path_side")),
            "selective_side": "NO_EDGE",
            "selective_authorized": False,
            "selective_status": "NO_EDGE",
            "trade_authorization_status": "NO_EDGE",
            "path_target_semantics": "DIRECT_12_EVENT_COHERENT_TRAJECTORY",
            "trajectory_decoder_status": "AVAILABLE",
            "trajectory_scenarios": list(provider.get("forecast_scenarios", [])),
            "path_confidence": max(
                cast(dict[str, float], provider.get("raw_side_probabilities", {})).values(),
                default=0.0,
            ),
            "path_confidence_status": (
                "CALIBRATED"
                if provider.get("probability_calibrated") is True
                else "UNCALIBRATED"
            ),
            "next_1_direction": _side(
                cast(Sequence[Mapping[str, Any]], provider.get("forecast_candles", []))[0].get(
                    "movement_side"
                )
            )
            if provider.get("forecast_candles")
            else "HOLD",
            "next_1_probability": max(
                cast(dict[str, float], provider.get("raw_side_probabilities", {})).values(),
                default=0.0,
            ),
            "next_2_direction": _side(
                cast(Sequence[Mapping[str, Any]], provider.get("forecast_candles", []))[1].get(
                    "movement_side"
                )
            )
            if len(cast(Sequence[Any], provider.get("forecast_candles", []))) > 1
            else "HOLD",
            "next_2_probability": max(
                cast(dict[str, float], provider.get("raw_side_probabilities", {})).values(),
                default=0.0,
            ),
            "confidence": max(
                cast(dict[str, float], provider.get("raw_side_probabilities", {})).values(),
                default=0.0,
            ),
            "sequence_length": len(
                cast(Mapping[str, Any], scene_features.get("sequence", {})).get(
                    "numeric_rows", []
                )
            ),
            "horizon_steps": FORECAST_HORIZON_STEPS_V3,
            "horizon_unit": "CANDLE_EVENTS",
            "clock_time_assumption": "NONE",
            "forecast_available": True,
            "closed_candle_key": event_key,
            "scene_feature_audit": scene_features.get("audit", {}),
            "scene_feature_schema": {
                "schema_version": scene_features.get("schema_version"),
                "schema_fingerprint": scene_features.get("schema_fingerprint"),
            },
            "source_image_size": [int(float(value)) for value in list(image_size)[:2]],
            "timeframe": str(timeframe or "").upper(),
            "pair": str(pair or "").upper(),
            "usage": {
                "default": "ADVISORY_UNTIL_WALK_FORWARD_PROMOTION",
                "normal_analysis_enabled": True,
                "high_frequency_enabled": True,
            },
        }
    )
    forecast_path = _compatibility_forecast_path(result)
    result["forecast_path"] = forecast_path
    result["progression_play"] = {
        "label": "SCENE_TRAJECTORY",
        "probabilities": dict(result.get("raw_side_probabilities", {})),
        "dominant_direction": result["path_side"],
        "first_direction_change_step": next(
            (
                int(row["step"])
                for row in forecast_path[1:]
                if row["movement_direction"]
                != forecast_path[0]["movement_direction"]
            ),
            None,
        )
        if forecast_path
        else None,
        "horizon_steps": FORECAST_HORIZON_STEPS_V3,
        "horizon_unit": "CANDLE_EVENTS",
    }
    result["interpretation"] = (
        "The V3 scene forecaster consumes closed candle geometry plus the causal suite "
        "and draws one coherent 12-event path. It remains advisory until independent "
        "walk-forward direction and calibration gates pass."
    )
    result["reason"] = result["interpretation"]
    return result


__all__ = [
    "FORECAST_HORIZON_STEPS_V3",
    "SCENE_FORECAST_CONTRIBUTION_SCHEMA_V3",
    "build_scene_forecast_anchor_v3",
    "build_scene_forecast_contribution_v3",
    "candle_is_closed_v3",
    "closed_candle_identity_v3",
    "latest_closed_candle_v3",
    "reanchor_scene_forecast_geometry_v3",
    "resolve_closed_candle_identity_v3",
    "synchronize_scene_forecast_geometry_v3",
]
