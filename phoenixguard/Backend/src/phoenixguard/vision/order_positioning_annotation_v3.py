from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Final, cast

from phoenixguard.vision.v3_overlay_contract import STOP_ENTRY_CONFIRMATION_EVENTS


ANNOTATION_SCHEMA_VERSION: Final = "PHOENIXGUARD_ORDER_POSITIONING_ANNOTATION_V3"
ANNOTATION_NORMALIZATION_TOLERANCE: Final = 0.005

_LEGACY_PROTECTIVE_STOP_SEMANTICS: Final[dict[str, tuple[str, str, str, str]]] = {
    "BUY_PROTECTIVE_STOP_ZONE": ("SELL", "BUY", "SELL_STOP", "BELOW_CURRENT"),
    "SELL_PROTECTIVE_STOP_ZONE": ("BUY", "SELL", "BUY_STOP", "ABOVE_CURRENT"),
}

_ENTRY_ZONE_SEMANTICS: Final[dict[str, tuple[str, str, str, str, frozenset[str]]]] = {
    "BUY_LIMIT_ZONE": (
        "BUY",
        "BUY",
        "BUY_LIMIT",
        "PASSIVE_ENTRY",
        frozenset({"BELOW_CURRENT", "AT_CURRENT_TOLERANCE"}),
    ),
    "SELL_LIMIT_ZONE": (
        "SELL",
        "SELL",
        "SELL_LIMIT",
        "PASSIVE_ENTRY",
        frozenset({"ABOVE_CURRENT", "AT_CURRENT_TOLERANCE"}),
    ),
    "BUY_STOP_ENTRY_ZONE": (
        "BUY",
        "BUY",
        "BUY_STOP",
        "MOMENTUM_ENTRY",
        frozenset({"ABOVE_CURRENT"}),
    ),
    "SELL_STOP_ENTRY_ZONE": (
        "SELL",
        "SELL",
        "SELL_STOP",
        "MOMENTUM_ENTRY",
        frozenset({"BELOW_CURRENT"}),
    ),
}


@dataclass(frozen=True)
class AnnotationSemanticIssue:
    path: str
    code: str
    message: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "code": self.code, "message": self.message}


@dataclass(frozen=True)
class AnnotationSemanticValidationResult:
    ok: bool
    safe_for_training: bool
    issues: tuple[AnnotationSemanticIssue, ...]
    normalized_annotation: dict[str, object]

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "safe_for_training": self.safe_for_training,
            "issues": [issue.as_dict() for issue in self.issues],
            "normalized_annotation": self.normalized_annotation,
        }


def _mapping(value: object) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    return cast(Mapping[str, object], value)


def _sequence(value: object) -> list[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    return list(cast(Sequence[object], value))


def _token(value: object) -> str:
    token = str(value or "").strip().upper()
    for needle in ("-", " ", "/", "\\", ".", ":"):
        token = token.replace(needle, "_")
    while "__" in token:
        token = token.replace("__", "_")
    return token.strip("_")


def _text(value: object) -> str:
    return str(value or "").strip()


def _finite_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _integer(value: object) -> int | None:
    number = _finite_number(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _string_items(value: object) -> list[str]:
    return [_text(item) for item in _sequence(value) if _text(item)]


def normalize_order_positioning_zone_v3(zone: Mapping[str, object]) -> dict[str, object]:
    """Normalize only deterministic aliases; do not repair canonical contradictions."""

    normalized = {str(key): deepcopy(value) for key, value in zone.items()}
    raw_label = _token(zone.get("label"))
    legacy_semantics = _LEGACY_PROTECTIVE_STOP_SEMANTICS.get(raw_label)
    if legacy_semantics is None:
        return normalized
    side, thesis_side, order_kind, relation = legacy_semantics
    normalized.update(
        {
            "label": "PROTECTIVE_STOP_ZONE",
            "side": side,
            "thesis_side": thesis_side,
            "order_kind": order_kind,
            "order_role": "PROTECTIVE_INVALIDATION",
            "price_relation_at_anchor": relation,
        }
    )
    return normalized


def normalize_order_positioning_annotation_v3(annotation: Mapping[str, object]) -> dict[str, object]:
    normalized = {str(key): deepcopy(value) for key, value in annotation.items()}
    raw_zones = _sequence(annotation.get("zones"))
    zones: list[object] = []
    for raw_zone in raw_zones:
        zone = _mapping(raw_zone)
        zones.append(normalize_order_positioning_zone_v3(zone) if zone is not None else deepcopy(raw_zone))
    normalized["zones"] = zones
    return normalized


def _add_issue(
    issues: list[AnnotationSemanticIssue],
    path: str,
    code: str,
    message: str,
) -> None:
    issues.append(AnnotationSemanticIssue(path=path, code=code, message=message))


def _mapping_field(
    parent: Mapping[str, object],
    key: str,
    issues: list[AnnotationSemanticIssue],
) -> Mapping[str, object]:
    value = _mapping(parent.get(key))
    if value is None:
        _add_issue(issues, key, "MISSING_OR_INVALID_OBJECT", f"{key} must be an object")
        return {}
    return value


def _bbox(
    value: object,
    *,
    path: str,
    normalized: bool,
    issues: list[AnnotationSemanticIssue],
) -> tuple[float, float, float, float] | None:
    values = _sequence(value)
    if len(values) != 4:
        _add_issue(issues, path, "INVALID_BBOX", "bounding box must contain four finite coordinates")
        return None
    numbers = [_finite_number(item) for item in values]
    if any(number is None for number in numbers):
        _add_issue(issues, path, "INVALID_BBOX", "bounding box must contain four finite coordinates")
        return None
    x1, y1, x2, y2 = cast(tuple[float, float, float, float], tuple(cast(float, item) for item in numbers))
    if x2 <= x1 or y2 <= y1:
        _add_issue(issues, path, "UNORDERED_BBOX", "bounding box requires x2 > x1 and y2 > y1")
    if normalized and any(number < 0.0 or number > 1.0 for number in (x1, y1, x2, y2)):
        _add_issue(issues, path, "NORMALIZED_BBOX_OUT_OF_RANGE", "normalized coordinates must stay within [0, 1]")
    return x1, y1, x2, y2


def _contains(outer: tuple[float, float, float, float], inner: tuple[float, float, float, float]) -> bool:
    return bool(
        inner[0] >= outer[0]
        and inner[1] >= outer[1]
        and inner[2] <= outer[2]
        and inner[3] <= outer[3]
    )


def _positive_area_overlap(
    first: tuple[float, float, float, float],
    second: tuple[float, float, float, float],
) -> bool:
    return bool(
        min(first[2], second[2]) > max(first[0], second[0])
        and min(first[3], second[3]) > max(first[1], second[1])
    )


def _legacy_alias_issues(
    raw_zone: Mapping[str, object],
    *,
    path: str,
    issues: list[AnnotationSemanticIssue],
) -> None:
    semantics = _LEGACY_PROTECTIVE_STOP_SEMANTICS.get(_token(raw_zone.get("label")))
    if semantics is None:
        return
    _, expected_thesis, expected_order_kind, _ = semantics
    explicit_thesis = _token(raw_zone.get("thesis_side"))
    explicit_order_kind = _token(raw_zone.get("order_kind"))
    if explicit_thesis and explicit_thesis != expected_thesis:
        _add_issue(
            issues,
            f"{path}.thesis_side",
            "LEGACY_PROTECTIVE_ALIAS_CONFLICT",
            "legacy protective label contradicts the explicit protected thesis",
        )
    if explicit_order_kind and explicit_order_kind not in {expected_order_kind, "PROTECTIVE_STOP"}:
        _add_issue(
            issues,
            f"{path}.order_kind",
            "LEGACY_PROTECTIVE_ALIAS_CONFLICT",
            "legacy protective label contradicts the explicit actual stop order kind",
        )


def _validate_zone_semantics(
    zone: Mapping[str, object],
    *,
    path: str,
    current_price: float | None,
    current_y: float | None,
    price_axis_direction: str,
    plot_bounds: tuple[float, float, float, float] | None,
    price_tolerance: float,
    pixel_tolerance: float,
    anchor_closed_candle_key: str,
    candidate_scope: str,
    issues: list[AnnotationSemanticIssue],
) -> tuple[str, str, tuple[float, float, float, float] | None]:
    label = _token(zone.get("label"))
    side = _token(zone.get("side"))
    thesis_side = _token(zone.get("thesis_side"))
    order_kind = _token(zone.get("order_kind"))
    order_role = _token(zone.get("order_role"))
    relation = _token(zone.get("price_relation_at_anchor"))

    expected_entry = _ENTRY_ZONE_SEMANTICS.get(label)
    if expected_entry is not None:
        expected_side, expected_thesis, expected_kind, expected_role, expected_relations = expected_entry
        if (side, thesis_side, order_kind, order_role) != (
            expected_side,
            expected_thesis,
            expected_kind,
            expected_role,
        ) or relation not in expected_relations:
            _add_issue(
                issues,
                path,
                "CONTRADICTORY_ORDER_SEMANTICS",
                "entry label, actual side, thesis side, order kind, role, and price relation disagree",
            )
    elif label == "PROTECTIVE_STOP_ZONE":
        valid_protective_semantics = {
            ("SELL", "BUY", "SELL_STOP", "PROTECTIVE_INVALIDATION", "BELOW_CURRENT"),
            ("BUY", "SELL", "BUY_STOP", "PROTECTIVE_INVALIDATION", "ABOVE_CURRENT"),
        }
        if (side, thesis_side, order_kind, order_role, relation) not in valid_protective_semantics:
            _add_issue(
                issues,
                path,
                "CONTRADICTORY_PROTECTIVE_STOP_SEMANTICS",
                "protective side must be the actual stop order side and opposite the protected thesis",
            )
    else:
        _add_issue(issues, f"{path}.label", "UNKNOWN_ZONE_LABEL", "zone label is not canonical")

    if candidate_scope in {"BUY", "SELL"} and thesis_side != candidate_scope:
        _add_issue(
            issues,
            f"{path}.thesis_side",
            "OUTSIDE_CANDIDATE_SCOPE",
            "zone thesis side is outside the annotated candidate scope",
        )

    bbox_px = _bbox(zone.get("bbox_px"), path=f"{path}.bbox_px", normalized=False, issues=issues)
    bbox_normalized = _bbox(
        zone.get("bbox_normalized"),
        path=f"{path}.bbox_normalized",
        normalized=True,
        issues=issues,
    )
    if bbox_px is not None and plot_bounds is not None:
        if not _contains(plot_bounds, bbox_px):
            _add_issue(issues, f"{path}.bbox_px", "ZONE_OUTSIDE_PLOT", "zone must be contained by plot bounds")
        if bbox_normalized is not None:
            plot_width = plot_bounds[2] - plot_bounds[0]
            plot_height = plot_bounds[3] - plot_bounds[1]
            expected_normalized = (
                (bbox_px[0] - plot_bounds[0]) / plot_width,
                (bbox_px[1] - plot_bounds[1]) / plot_height,
                (bbox_px[2] - plot_bounds[0]) / plot_width,
                (bbox_px[3] - plot_bounds[1]) / plot_height,
            )
            if any(
                abs(actual - expected) > ANNOTATION_NORMALIZATION_TOLERANCE
                for actual, expected in zip(bbox_normalized, expected_normalized, strict=True)
            ):
                _add_issue(
                    issues,
                    f"{path}.bbox_normalized",
                    "PIXEL_NORMALIZED_GEOMETRY_MISMATCH",
                    "normalized geometry must be derived from the recorded plot bounds",
                )

    lower_price = _finite_number(zone.get("lower_price_proxy"))
    upper_price = _finite_number(zone.get("upper_price_proxy"))
    anchor_price = _finite_number(zone.get("anchor_price_proxy"))
    if lower_price is None or upper_price is None or anchor_price is None:
        _add_issue(issues, path, "INVALID_PRICE_PROXY", "zone price proxies must be finite numbers")
    else:
        if lower_price > upper_price:
            _add_issue(
                issues,
                path,
                "INVERTED_PRICE_RANGE",
                "lower_price_proxy must not exceed upper_price_proxy",
            )
        if current_price is not None:
            if abs(anchor_price - current_price) > price_tolerance:
                _add_issue(
                    issues,
                    f"{path}.anchor_price_proxy",
                    "ANCHOR_PRICE_MISMATCH",
                    "zone anchor price must equal the frame anchor price within tolerance",
                )
            if relation == "BELOW_CURRENT" and upper_price > current_price + price_tolerance:
                _add_issue(issues, path, "PRICE_RELATION_MISMATCH", "below-current zone crosses above current price")
            elif relation == "ABOVE_CURRENT" and lower_price < current_price - price_tolerance:
                _add_issue(issues, path, "PRICE_RELATION_MISMATCH", "above-current zone crosses below current price")
            elif relation == "AT_CURRENT_TOLERANCE" and not (
                lower_price <= current_price + price_tolerance
                and upper_price >= current_price - price_tolerance
            ):
                _add_issue(issues, path, "PRICE_RELATION_MISMATCH", "at-current zone does not meet current price")

    if bbox_px is not None and current_y is not None:
        if price_axis_direction == "HIGHER_PRICE_AT_SMALLER_Y":
            below_geometry = bbox_px[1] >= current_y - pixel_tolerance
            above_geometry = bbox_px[3] <= current_y + pixel_tolerance
        elif price_axis_direction == "HIGHER_PRICE_AT_LARGER_Y":
            below_geometry = bbox_px[3] <= current_y + pixel_tolerance
            above_geometry = bbox_px[1] >= current_y - pixel_tolerance
        else:
            below_geometry = False
            above_geometry = False
        if relation == "BELOW_CURRENT" and not below_geometry:
            _add_issue(issues, f"{path}.bbox_px", "VERTICAL_GEOMETRY_RELATION_MISMATCH", "zone is not below current price in the recorded transform")
        elif relation == "ABOVE_CURRENT" and not above_geometry:
            _add_issue(issues, f"{path}.bbox_px", "VERTICAL_GEOMETRY_RELATION_MISMATCH", "zone is not above current price in the recorded transform")
        elif relation == "AT_CURRENT_TOLERANCE" and not (
            bbox_px[1] - pixel_tolerance <= current_y <= bbox_px[3] + pixel_tolerance
        ):
            _add_issue(issues, f"{path}.bbox_px", "VERTICAL_GEOMETRY_RELATION_MISMATCH", "zone does not meet current-price geometry")

    evidence = _mapping(zone.get("evidence"))
    if evidence is None:
        _add_issue(issues, f"{path}.evidence", "MISSING_OR_INVALID_OBJECT", "zone evidence must be an object")
    else:
        hard_anchor_count = _integer(evidence.get("hard_anchor_count"))
        anchor_references = {
            _text(item)
            for key in (
                "anchor_candle_indices",
                "anchor_candle_ids",
                "swing_anchor_indices",
                "source_zone_ids",
                "trendline_ids",
            )
            for item in _sequence(evidence.get(key))
            if _text(item)
        }
        if hard_anchor_count is None or hard_anchor_count < 1 or hard_anchor_count > len(anchor_references):
            _add_issue(
                issues,
                f"{path}.evidence.hard_anchor_count",
                "HARD_ANCHOR_COUNT_MISMATCH",
                "declared hard-anchor count must be supported by unique evidence references",
            )
        if label in {"BUY_STOP_ENTRY_ZONE", "SELL_STOP_ENTRY_ZONE"}:
            confirmation_keys = _string_items(evidence.get("confirmation_closed_candle_keys"))
            confirmation_events = {_token(item) for item in _sequence(evidence.get("confirmation_events"))}
            confirmation_side = _token(evidence.get("confirmation_side"))
            if not confirmation_keys or not confirmation_events or not confirmation_events.issubset(
                STOP_ENTRY_CONFIRMATION_EVENTS
            ) or confirmation_side != thesis_side:
                _add_issue(
                    issues,
                    f"{path}.evidence",
                    "MISSING_CLOSED_STOP_ENTRY_CONFIRMATION",
                    "stop-entry zones require a recognized event on a completed candle",
                )

    validity = _mapping(zone.get("validity"))
    if validity is None:
        _add_issue(issues, f"{path}.validity", "MISSING_OR_INVALID_OBJECT", "zone validity must be an object")
    elif anchor_closed_candle_key and _text(validity.get("created_closed_candle_key")) != anchor_closed_candle_key:
        _add_issue(
            issues,
            f"{path}.validity.created_closed_candle_key",
            "ZONE_CREATED_AFTER_ANCHOR",
            "frozen zone must originate on the episode anchor candle",
        )

    return thesis_side, order_role, bbox_px


def validate_order_positioning_annotation_v3(
    annotation: Mapping[str, object],
) -> AnnotationSemanticValidationResult:
    """Validate semantic and training-safety rules that JSON Schema cannot express."""

    issues: list[AnnotationSemanticIssue] = []
    normalized = normalize_order_positioning_annotation_v3(annotation)
    if _text(normalized.get("schema_version")) != ANNOTATION_SCHEMA_VERSION:
        _add_issue(issues, "schema_version", "SCHEMA_VERSION_MISMATCH", "unexpected annotation schema version")

    episode = _mapping_field(normalized, "episode", issues)
    frame = _mapping_field(normalized, "frame", issues)
    market_context = _mapping_field(normalized, "market_context", issues)
    review = _mapping_field(normalized, "review", issues)
    leakage_guard = _mapping_field(normalized, "leakage_guard", issues)
    provenance = _mapping_field(normalized, "provenance", issues)

    frame_width = _integer(frame.get("width"))
    frame_height = _integer(frame.get("height"))
    if frame_width is None or frame_width < 1 or frame_height is None or frame_height < 1:
        _add_issue(issues, "frame", "INVALID_FRAME_SIZE", "frame width and height must be positive integers")
    chart_bounds = _bbox(frame.get("chart_bounds_px"), path="frame.chart_bounds_px", normalized=False, issues=issues)
    plot_bounds = _bbox(frame.get("plot_bounds_px"), path="frame.plot_bounds_px", normalized=False, issues=issues)
    if chart_bounds is not None and plot_bounds is not None and not _contains(chart_bounds, plot_bounds):
        _add_issue(issues, "frame.plot_bounds_px", "PLOT_OUTSIDE_CHART", "plot bounds must stay inside chart bounds")
    if frame_width is not None and frame_height is not None and chart_bounds is not None:
        frame_bounds = (0.0, 0.0, float(frame_width), float(frame_height))
        if not _contains(frame_bounds, chart_bounds):
            _add_issue(issues, "frame.chart_bounds_px", "CHART_OUTSIDE_FRAME", "chart bounds must stay inside the source frame")

    price_axis_direction = _token(frame.get("price_axis_direction"))
    if price_axis_direction not in {"HIGHER_PRICE_AT_SMALLER_Y", "HIGHER_PRICE_AT_LARGER_Y"}:
        _add_issue(
            issues,
            "frame.price_axis_direction",
            "UNKNOWN_PRICE_AXIS_DIRECTION",
            "vertical price semantics require an explicit transform orientation",
        )

    current_price = _finite_number(market_context.get("current_price_proxy"))
    current_y = _finite_number(market_context.get("current_price_y_px"))
    if current_price is None or current_y is None:
        _add_issue(issues, "market_context", "INVALID_CURRENT_PRICE", "current price proxy and y coordinate must be finite")
    elif plot_bounds is not None and not (plot_bounds[1] <= current_y <= plot_bounds[3]):
        _add_issue(issues, "market_context.current_price_y_px", "CURRENT_PRICE_OUTSIDE_PLOT", "current price y must be inside plot bounds")

    price_tolerance = max(abs(current_price or 0.0) * 1e-9, 1e-9)
    pixel_tolerance = 1.5
    tolerance = _mapping(market_context.get("spread_or_uncertainty_tolerance"))
    if tolerance is not None:
        value = _finite_number(tolerance.get("value"))
        unit = _token(tolerance.get("unit"))
        if value is not None and value >= 0.0:
            if unit == "PRICE_PROXY":
                price_tolerance = max(price_tolerance, value)
            elif unit == "PIXEL":
                pixel_tolerance = max(pixel_tolerance, value)

    anchor_closed_candle_key = _text(episode.get("anchor_closed_candle_key"))
    frame_closed_candle_key = _text(frame.get("closed_candle_key"))
    if anchor_closed_candle_key != frame_closed_candle_key:
        _add_issue(
            issues,
            "frame.closed_candle_key",
            "ANCHOR_CANDLE_MISMATCH",
            "frame closed candle must equal the frozen episode anchor candle",
        )
    if _text(episode.get("anchor_frame_id")) != _text(frame.get("frame_id")):
        _add_issue(issues, "frame.frame_id", "ANCHOR_FRAME_MISMATCH", "frame must be the episode anchor frame")

    raw_zones = _sequence(annotation.get("zones"))
    normalized_zones = _sequence(normalized.get("zones"))
    zone_ids: set[str] = set()
    accepted_zone_geometry: list[tuple[str, str, str, tuple[float, float, float, float]]] = []
    candidate_scope = _token(market_context.get("candidate_scope"))
    for index, raw_zone_value in enumerate(raw_zones):
        raw_zone = _mapping(raw_zone_value)
        if raw_zone is not None:
            _legacy_alias_issues(raw_zone, path=f"zones[{index}]", issues=issues)
    for index, zone_value in enumerate(normalized_zones):
        path = f"zones[{index}]"
        zone = _mapping(zone_value)
        if zone is None:
            _add_issue(issues, path, "INVALID_ZONE", "zone must be an object")
            continue
        zone_id = _text(zone.get("zone_id"))
        if not zone_id:
            _add_issue(issues, f"{path}.zone_id", "MISSING_ZONE_ID", "zone id is required")
        elif zone_id in zone_ids:
            _add_issue(issues, f"{path}.zone_id", "DUPLICATE_ZONE_ID", "zone ids must be unique")
        zone_ids.add(zone_id)
        thesis_side, order_role, bbox_px = _validate_zone_semantics(
            zone,
            path=path,
            current_price=current_price,
            current_y=current_y,
            price_axis_direction=price_axis_direction,
            plot_bounds=plot_bounds,
            price_tolerance=price_tolerance,
            pixel_tolerance=pixel_tolerance,
            anchor_closed_candle_key=anchor_closed_candle_key,
            candidate_scope=candidate_scope,
            issues=issues,
        )
        if zone_id and bbox_px is not None:
            accepted_zone_geometry.append((zone_id, thesis_side, order_role, bbox_px))

    for index, first in enumerate(accepted_zone_geometry):
        for second in accepted_zone_geometry[index + 1 :]:
            if first[1] != second[1] or "PROTECTIVE_INVALIDATION" not in {first[2], second[2]}:
                continue
            if first[2] == second[2] or not _positive_area_overlap(first[3], second[3]):
                continue
            _add_issue(
                issues,
                "zones",
                "ENTRY_PROTECTIVE_GEOMETRY_OVERLAP",
                f"entry and protective zones for thesis {first[1]} overlap: {first[0]} and {second[0]}",
            )

    negative_ids: set[str] = set()
    for index, negative_value in enumerate(_sequence(normalized.get("negative_labels"))):
        path = f"negative_labels[{index}]"
        negative = _mapping(negative_value)
        if negative is None:
            _add_issue(issues, path, "INVALID_NEGATIVE_LABEL", "negative label must be an object")
            continue
        negative_id = _text(negative.get("negative_id"))
        if not negative_id or negative_id in negative_ids:
            _add_issue(issues, f"{path}.negative_id", "MISSING_OR_DUPLICATE_NEGATIVE_ID", "negative ids must be present and unique")
        negative_ids.add(negative_id)
        scope = _token(negative.get("side_scope"))
        conflicting_sides = {row[1] for row in accepted_zone_geometry}
        if scope == "BOTH" and conflicting_sides or scope in conflicting_sides:
            _add_issue(
                issues,
                path,
                "NEGATIVE_LABEL_CONTRADICTS_ZONE",
                "NO_VALID_ZONE cannot coexist with an accepted zone for the same thesis side",
            )

    if not normalized_zones and not _sequence(normalized.get("negative_labels")):
        _add_issue(issues, "zones", "EMPTY_ANNOTATION", "at least one zone or explicit negative label is required")

    outcome = _mapping(normalized.get("outcome"))
    if outcome is not None:
        observed_zone_ids: set[str] = set()
        for index, observation_value in enumerate(_sequence(outcome.get("zone_observations"))):
            observation = _mapping(observation_value)
            path = f"outcome.zone_observations[{index}].zone_id"
            observation_id = _text(observation.get("zone_id")) if observation is not None else ""
            if not observation_id or observation_id not in zone_ids:
                _add_issue(issues, path, "UNKNOWN_ZONE_OBSERVATION", "outcome observation must reference a declared zone")
            elif observation_id in observed_zone_ids:
                _add_issue(issues, path, "DUPLICATE_ZONE_OBSERVATION", "each zone may have one outcome observation")
            observed_zone_ids.add(observation_id)

    declared_training_eligibility = _token(review.get("training_eligibility"))
    exclusion_reasons = _string_items(review.get("exclusion_reasons"))
    if declared_training_eligibility == "EXCLUDED" and not exclusion_reasons:
        _add_issue(issues, "review.exclusion_reasons", "EXCLUSION_REASON_REQUIRED", "excluded records require a reason")
    if declared_training_eligibility == "ELIGIBLE":
        eligibility_issue_count = len(issues)
        if _token(normalized.get("annotation_phase")) != "PRE_OUTCOME":
            _add_issue(issues, "annotation_phase", "UNSAFE_TRAINING_ELIGIBILITY", "only causal pre-outcome geometry may train the detector")
        if _token(review.get("state")) not in {"DOUBLE_REVIEWED", "ADJUDICATED"}:
            _add_issue(issues, "review.state", "UNSAFE_TRAINING_ELIGIBILITY", "eligible geometry requires double review or adjudication")
        if review.get("geometry_locked") is not True:
            _add_issue(issues, "review.geometry_locked", "UNSAFE_TRAINING_ELIGIBILITY", "eligible geometry must be locked")
        if review.get("disagreement_present") is not False:
            _add_issue(issues, "review.disagreement_present", "UNSAFE_TRAINING_ELIGIBILITY", "eligible geometry cannot retain disagreement")
        if not _string_items(review.get("reviewer_ids")):
            _add_issue(issues, "review.reviewer_ids", "UNSAFE_TRAINING_ELIGIBILITY", "eligible geometry requires an independent reviewer")
        if exclusion_reasons:
            _add_issue(issues, "review.exclusion_reasons", "UNSAFE_TRAINING_ELIGIBILITY", "eligible geometry cannot retain exclusion reasons")
        if _token(leakage_guard.get("split_assignment")) not in {"TRAIN", "VALIDATION", "TEST"}:
            _add_issue(issues, "leakage_guard.split_assignment", "UNSAFE_TRAINING_ELIGIBILITY", "eligible record must have an assigned group split")
        if leakage_guard.get("no_cross_split_related_frames") is not True:
            _add_issue(issues, "leakage_guard.no_cross_split_related_frames", "UNSAFE_TRAINING_ELIGIBILITY", "related frames must remain in one split")
        if provenance.get("future_frames_visible_to_annotator") is not False:
            _add_issue(issues, "provenance.future_frames_visible_to_annotator", "UNSAFE_TRAINING_ELIGIBILITY", "future-visible annotations cannot train causal geometry")
        if _text(provenance.get("visible_until_closed_candle_key")) != anchor_closed_candle_key:
            _add_issue(issues, "provenance.visible_until_closed_candle_key", "UNSAFE_TRAINING_ELIGIBILITY", "causal visibility must end at the anchor candle")
        if eligibility_issue_count:
            _add_issue(issues, "review.training_eligibility", "UNSAFE_TRAINING_ELIGIBILITY", "semantic errors force training exclusion")

    safe_for_training = declared_training_eligibility == "ELIGIBLE" and not issues
    return AnnotationSemanticValidationResult(
        ok=not issues,
        safe_for_training=safe_for_training,
        issues=tuple(issues),
        normalized_annotation=normalized,
    )


__all__ = [
    "ANNOTATION_NORMALIZATION_TOLERANCE",
    "ANNOTATION_SCHEMA_VERSION",
    "AnnotationSemanticIssue",
    "AnnotationSemanticValidationResult",
    "normalize_order_positioning_annotation_v3",
    "normalize_order_positioning_zone_v3",
    "validate_order_positioning_annotation_v3",
]
