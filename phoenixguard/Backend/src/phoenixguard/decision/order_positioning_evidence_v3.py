from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
from typing import Any, Final, cast

from phoenixguard.decision.order_positioning_v3 import (
    build_order_positioning_candidates_v3,
    order_positioning_stop_confirmation_reason_v3,
)
from phoenixguard.tracking.market_object_tracker_v3 import (
    build_market_object_registry_v3,
)
from phoenixguard.vision.v3_overlay_contract import normalize_v3_overlay_object


ORDER_REFERENCE_MAP_SCHEMA_VERSION: Final = "PG_ORDER_REFERENCE_MAP_V1"
POSITIONING_WINDOW_MAX_STEPS: Final = 32
_ORDER_REACTION_WINDOW_GEOMETRY_ROLE: Final = "FORWARD_REACTION_WINDOW"
_ORDER_REACTION_WINDOW_ANCHOR: Final = "LATEST_COMPLETED_CANDLE"
_DIRECTIONS: Final = frozenset({"BUY", "SELL", "HOLD"})
_OMITTED_KEYS: Final = frozenset(
    {
        "artifact_path",
        "artifact_paths",
        "config_path",
        "dense_history",
        "latent_vector",
        "raw_payload",
        "source_image",
        "source_path",
        "weights_path",
    }
)
_SEMANTIC_PATH_KEYS: Final = frozenset(
    {
        "forecast_path",
        "forecast_paths",
        "prediction_path",
        "progression_path",
        "scenario_path",
        "scenario_paths",
        "trajectory_path",
        "trajectory_paths",
    }
)
_POSITIONING_SOURCE_TYPES: Final = frozenset(
    {
        "DEMAND_ZONE",
        "SUPPLY_ZONE",
        "ORDER_BLOCK",
        "RETEST_BOX",
        "SUPPORT_TRENDLINE",
        "RESISTANCE_TRENDLINE",
    }
)
_POSITIONING_SOURCE_LIMIT: Final = 24
_CANONICAL_ANCHOR_QUALITY_FIELDS: Final = frozenset(
    {
        "has_candle_anchor",
        "has_sequence_anchor",
        "inside_plot_area",
        "matches_symbol_timeframe",
        "matches_selector_fingerprint",
        "chart_transform_valid",
    }
)
_POSITIONING_SOURCE_EXPORT_FIELDS: Final = frozenset(
    {
        "schema_version",
        "overlay_id",
        "id",
        "object_id",
        "track_id",
        "type",
        "side",
        "frame_id",
        "sequence_id",
        "chart_transform_id",
        "broker_source_lock_id",
        "symbol",
        "timeframe",
        "market_selector_visual_fingerprint",
        "instrument_identity_status",
        "coordinate_mode",
        "bounds",
        "bbox",
        "lifecycle_state",
        "anchor_evidence_status",
        "anchor_evidence",
        "anchor_quality",
        "confidence",
        "truth_score",
        "anchor_candle_indices",
        "anchor_candles",
        "projected_entry_band",
        "projected_price_band",
        "confirmation_evidence",
        "confirmation_state",
        "breakout_confirmation_state",
        "stop_entry_confirmation_valid",
        "confirmation_is_closed",
        "confirmation_closed_candle_key",
        "confirmed_candle_key",
        "confirmation_closed_candle_index",
        "confirmation_side",
        "confirmation_direction",
        "breakout_side",
        "confirmation_event",
        "confirmation_type",
        "reaction_type",
        "knowledge_tags",
    }
)
_ORDER_REFERENCE_SOURCE_TYPES: Final = frozenset(
    {"DEMAND_ZONE", "SUPPLY_ZONE", "SUPPORT_TRENDLINE", "RESISTANCE_TRENDLINE"}
)
_ORDER_REFERENCE_LIVE_STATES: Final = frozenset(
    {
        "ACTIVE",
        "CONFIRMED",
        "FRESH",
        "TESTED",
        "MITIGATED",
        "FRESH_ACTIVE",
        "MITIGATED_ACTIVE",
        "ROLE_FLIP_CONFIRMED",
    }
)

def _mapping(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {str(key): item for key, item in cast(Mapping[Any, Any], value).items()}


def _rows(value: Any, *, limit: int = 24) -> list[dict[str, Any]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    output: list[dict[str, Any]] = []
    for item in cast(Sequence[Any], value)[: max(0, int(limit))]:
        row = _mapping(item)
        if row:
            output.append(row)
    return output


def _text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return int(default)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if result != result or result in {float("inf"), float("-inf")}:
        return None
    return result


def _direction(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip().upper()
        aliases = {
            "BULL": "BUY",
            "BULLISH": "BUY",
            "LONG": "BUY",
            "UP": "BUY",
            "BEAR": "SELL",
            "BEARISH": "SELL",
            "DOWN": "SELL",
            "SHORT": "SELL",
            "NEUTRAL": "HOLD",
            "WAIT": "HOLD",
        }
        text = aliases.get(text, text)
        if text in _DIRECTIONS:
            return text
    return "HOLD"


def _json_safe(value: Any, *, depth: int = 0, field_name: str = "") -> Any:
    """Return a bounded, path-free JSON value for public positioning evidence."""

    normalized_field = str(field_name or "").strip().lower()
    if normalized_field in _OMITTED_KEYS or (
        normalized_field not in _SEMANTIC_PATH_KEYS
        and normalized_field.endswith(("_path", "_paths", "_dir", "_directory"))
    ):
        return None
    if depth >= 8:
        return None
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if value == value and value not in {float("inf"), float("-inf")} else None
    if isinstance(value, Mapping):
        output: dict[str, Any] = {}
        for raw_key, item in list(cast(Mapping[Any, Any], value).items())[:96]:
            key = str(raw_key)
            normalized_key = key.strip().lower()
            if normalized_key in _OMITTED_KEYS or (
                normalized_key not in _SEMANTIC_PATH_KEYS
                and normalized_key.endswith(("_path", "_paths", "_dir", "_directory"))
            ):
                continue
            safe = _json_safe(item, depth=depth + 1, field_name=key)
            if safe not in (None, "", [], {}):
                output[key] = safe
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        output_list: list[Any] = []
        for item in cast(Sequence[Any], value)[:48]:
            safe = _json_safe(item, depth=depth + 1, field_name=field_name)
            if safe is not None:
                output_list.append(safe)
        return output_list
    return str(value)


def _safe_mapping(value: Any) -> dict[str, Any]:
    safe = _json_safe(value)
    return cast(dict[str, Any], safe) if isinstance(safe, dict) else {}


def _scene_forecast(session: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = _mapping(session.get("forecast_snapshot_v3"))
    latest = _mapping(session.get("latest_signal"))
    tracking = _mapping(session.get("tracking_summary"))
    return (
        _mapping(snapshot.get("scene_forecast_contribution"))
        or _mapping(latest.get("scene_forecast_contribution"))
        or _mapping(tracking.get("scene_forecast_contribution"))
    )


def _identity(session: Mapping[str, Any]) -> dict[str, Any]:
    snapshot = _mapping(session.get("forecast_snapshot_v3"))
    scene = _scene_forecast(session)
    latest = _mapping(session.get("latest_signal"))
    tracking = _mapping(session.get("tracking_summary"))
    external = _mapping(session.get("external_frame_feed"))
    identity_state = _mapping(scene.get("closed_candle_identity_state"))
    # ``forecast_snapshot_v3`` and its scene can legitimately lag one frame
    # while the selector is binding a new pair.  Live signal/tracking identity
    # therefore owns the current frame; superseded private model identity is only a
    # compatibility fallback when those current surfaces are empty.
    latest_pair = _text(
        latest.get("market"),
        latest.get("symbol"),
        latest.get("pair"),
    ).upper()
    tracking_pair = _text(
        tracking.get("detected_market"),
        tracking.get("market"),
        tracking.get("symbol"),
        tracking.get("pair"),
    ).upper()
    pair = _text(
        latest_pair,
        tracking_pair,
        snapshot.get("pair"),
        scene.get("pair"),
        external.get("symbol"),
        session.get("market"),
    ).upper()
    latest_timeframe = _text(
        latest.get("focus_timeframe"),
        latest.get("timeframe"),
    ).upper()
    tracking_timeframe = _text(
        tracking.get("detected_timeframe"),
        tracking.get("focus_timeframe"),
        tracking.get("timeframe"),
    ).upper()
    timeframe = _text(
        latest_timeframe,
        tracking_timeframe,
        snapshot.get("timeframe"),
        scene.get("timeframe"),
        external.get("timeframe"),
    ).upper()
    closed_key = _text(
        scene.get("closed_candle_key"),
        identity_state.get("event_key"),
        snapshot.get("closed_candle_key"),
    )
    closed_sequence = max(
        0,
        _integer(
            scene.get(
                "closed_candle_sequence",
                identity_state.get("event_sequence", snapshot.get("closed_candle_sequence", 0)),
            )
        ),
    )
    confirmed_event_batch = _rows(
        identity_state.get("confirmed_event_batch"),
        limit=24,
    )
    match_scores = _mapping(scene.get("closed_candle_match_scores"))
    reacquisition = _mapping(identity_state.get("reacquisition"))
    def canonical_identity(value: Any) -> str:
        return "".join(
            character
            for character in _text(value).upper()
            if character.isalnum()
        )

    live_pair_disagreement = bool(
        latest_pair
        and tracking_pair
        and canonical_identity(latest_pair) != canonical_identity(tracking_pair)
    )
    live_timeframe_disagreement = bool(
        latest_timeframe
        and tracking_timeframe
        and canonical_identity(latest_timeframe)
        != canonical_identity(tracking_timeframe)
    )
    latest_fingerprint = _text(latest.get("market_selector_visual_fingerprint"))
    tracking_fingerprint = _text(
        tracking.get("market_selector_visual_fingerprint")
    )
    live_fingerprint_disagreement = bool(
        latest_fingerprint
        and tracking_fingerprint
        and latest_fingerprint != tracking_fingerprint
    )
    identity_transition_pending = any(
        value is True
        or str(value or "").strip().lower() in {"1", "true", "yes", "on"}
        for value in (
            latest.get("market_selector_rebind_required"),
            tracking.get("market_selector_rebind_required"),
            latest.get("market_selector_studying_new_pair"),
            tracking.get("market_selector_studying_new_pair"),
        )
    )
    identity_consistent = not (
        identity_transition_pending
        or live_pair_disagreement
        or live_timeframe_disagreement
        or live_fingerprint_disagreement
    )

    def effective_confirmation(field: str, *, fallback: bool) -> bool:
        live_explicit = [
            value
            for value in (latest.get(field), tracking.get(field))
            if isinstance(value, bool)
        ]
        if live_explicit:
            return False not in live_explicit
        legacy_explicit = [
            value
            for value in (snapshot.get(field), scene.get(field))
            if isinstance(value, bool)
        ]
        if legacy_explicit:
            return False not in legacy_explicit
        return fallback

    market_confirmed = bool(
        identity_consistent
        and effective_confirmation(
            "market_identity_confirmed",
            # Older persisted IDLE sessions predate explicit confirmation
            # fields.  A non-empty, contradiction-free live identity remains
            # the bounded compatibility path for their preview/reference rows.
            fallback=bool(pair),
        )
    )
    timeframe_confirmed = bool(
        identity_consistent
        and effective_confirmation(
            "timeframe_identity_confirmed",
            fallback=bool(timeframe),
        )
    )
    return {
        "pair": pair,
        "timeframe": timeframe,
        "market_selector_visual_fingerprint": _text(
            latest_fingerprint,
            tracking_fingerprint,
            snapshot.get("market_selector_visual_fingerprint"),
            scene.get("market_selector_visual_fingerprint"),
            identity_state.get("market_selector_visual_fingerprint"),
            session.get("market_selector_visual_fingerprint"),
        ),
        "market_identity_confirmed": market_confirmed,
        "timeframe_identity_confirmed": timeframe_confirmed,
        "closed_candle_key": closed_key,
        "closed_candle_sequence": closed_sequence,
        "confirmed_event_batch": confirmed_event_batch,
        "transition_reason": _text(
            scene.get("closed_candle_transition_reason"),
            reacquisition.get("reason"),
        ),
        "transition_count": max(
            0,
            _integer(identity_state.get("transition_count")),
        ),
        "match_scores": match_scores,
        "reacquisition": reacquisition,
        "closed_candle_identity_state": _safe_mapping(identity_state),
    }
def _stable_broker_source_lock_id(session: Mapping[str, Any]) -> str:
    tracking = _mapping(session.get("tracking_summary"))
    latest = _mapping(session.get("latest_signal"))
    return _text(
        _mapping(tracking.get("broker_source")).get("lock_id"),
        _mapping(tracking.get("broker_source_lock")).get("lock_id"),
        _mapping(latest.get("broker_source")).get("lock_id"),
        _mapping(latest.get("broker_source_lock")).get("lock_id"),
        _mapping(session.get("broker_source")).get("lock_id"),
        _mapping(session.get("broker_source_lock")).get("lock_id"),
    )


def _current_positioning_frame_id(session: Mapping[str, Any]) -> int:
    for key in (
        "display_frame_id",
        "chart_frame_id",
        "overlay_frame_id",
        "model_vote_frame_id",
        "frame_index",
        "frame_id",
    ):
        value = session.get(key)
        if value not in (None, ""):
            return _integer(value)
    return 0


def _current_positioning_snapshot(
    session: Mapping[str, Any],
    *,
    frame_id: int,
) -> tuple[bool, list[dict[str, Any]]]:
    tracking = _mapping(session.get("tracking_summary"))
    snapshot = _mapping(tracking.get("order_positioning_sources_v3"))
    if not snapshot or snapshot.get("frame_id") in (None, ""):
        return False, []
    snapshot_frame_id = _integer(snapshot.get("frame_id"), -1)
    current_frame_ids = [
        _integer(session.get(key), -1)
        for key in (
            "display_frame_id",
            "chart_frame_id",
            "overlay_frame_id",
            "model_vote_frame_id",
            "frame_index",
            "frame_id",
        )
        if session.get(key) not in (None, "")
    ]
    if (
        snapshot_frame_id != frame_id
        or any(current != snapshot_frame_id for current in current_frame_ids)
    ):
        return False, []
    return True, _rows(snapshot.get("objects"), limit=_POSITIONING_SOURCE_LIMIT)


def _positioning_source_is_current(
    row: Mapping[str, Any],
    *,
    frame_id: int,
) -> bool:
    coordinate_mode = _text(row.get("coordinate_mode")).upper()
    precision_quality = _mapping(row.get("anchor_quality"))
    return bool(
        _text(row.get("frame_id")) == str(frame_id)
        and _text(row.get("schema_version")) == "PG_V3_OVERLAY_OBJECT_V1"
        and _text(row.get("type")).upper() in _POSITIONING_SOURCE_TYPES
        and "CHART" in coordinate_mode
        and "PLOT" not in coordinate_mode
        and row.get("precision_rejected") is not True
        and _text(precision_quality.get("status")).upper() != "REJECT"
    )


def _canonical_positioning_source(
    row: Mapping[str, Any],
    *,
    stable_source_lock_id: str,
    image_size: Sequence[Any] | None,
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    source = dict(row)
    expected_symbol = _text(identity.get("pair")).upper()
    expected_timeframe = _text(identity.get("timeframe")).upper()
    expected_fingerprint = _text(identity.get("market_selector_visual_fingerprint"))
    source_symbol = _text(
        source.get("symbol"),
        source.get("pair"),
        source.get("market"),
    ).upper()
    source_timeframe = _text(source.get("timeframe"), source.get("tf")).upper()
    source_fingerprint = _text(source.get("market_selector_visual_fingerprint"))
    if (
        not expected_symbol
        or not expected_timeframe
        or not expected_fingerprint.startswith("selector_v2_")
        or identity.get("market_identity_confirmed") is not True
        or identity.get("timeframe_identity_confirmed") is not True
        or (source_symbol and source_symbol != expected_symbol)
        or (source_timeframe and source_timeframe != expected_timeframe)
        or (
            source_fingerprint
            and expected_fingerprint
            and source_fingerprint != expected_fingerprint
        )
    ):
        return {}
    source.update(
        {
            "symbol": expected_symbol,
            "timeframe": expected_timeframe,
            "market_selector_visual_fingerprint": expected_fingerprint,
            "instrument_identity_status": "LOCKED",
            "pair_mismatch": False,
            "timeframe_mismatch": False,
            "selector_fingerprint_mismatch": False,
        }
    )
    quality = _mapping(source.get("anchor_quality"))
    if not _CANONICAL_ANCHOR_QUALITY_FIELDS.issubset(quality):
        # The market-object registry intentionally replaces the public V3
        # contract quality object with its precision-refinement report. Strip
        # that internal shape and rebuild the canonical, fail-closed quality
        # flags consumed by order_positioning_v3.
        source.pop("anchor_quality", None)
        source.pop("anchor_confidence", None)
        try:
            source = {
                str(key): value
                for key, value in normalize_v3_overlay_object(
                    source,
                    strict=False,
                    image_size=image_size,
                ).items()
            }
        except (TypeError, ValueError):
            return {}
    if stable_source_lock_id:
        source["broker_source_lock_id"] = stable_source_lock_id
    return source


def _project_positioning_source(row: Mapping[str, Any]) -> dict[str, Any]:
    return _safe_mapping(
        {
            key: value
            for key, value in row.items()
            if key in _POSITIONING_SOURCE_EXPORT_FIELDS
            and value not in (None, "", [], {})
        }
    )


def _explicit_positioning_overlay_rows(
    session: Mapping[str, Any],
) -> list[dict[str, Any]]:
    containers: list[Any] = [
        session.get("v3_overlay_objects"),
        session.get("overlay_objects"),
    ]
    for parent_key in ("overlays", "live_visual_state", "live_state_v3"):
        parent = _mapping(session.get(parent_key))
        overlay_container = _mapping(parent.get("overlays")) or parent
        containers.extend(
            (
                overlay_container.get("objects"),
                overlay_container.get("all_objects"),
            )
        )
    tracking = _mapping(session.get("tracking_summary"))
    containers.extend(
        (
            tracking.get("v3_overlay_objects"),
            tracking.get("overlay_objects"),
        )
    )
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for container in containers:
        for row in _rows(container, limit=512):
            key = _text(
                row.get("overlay_id"),
                row.get("id"),
                row.get("object_id"),
                row.get("track_id"),
            )
            if not key or key in seen:
                continue
            seen.add(key)
            output.append(row)
    return output


def order_positioning_evidence_rows_v3(
    session: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return bounded canonical positioning sources for the displayed frame.

    Persisted live sessions deliberately omit the full overlay arrays. In
    that compact state the market-object registry is the authoritative fallback
    because it reconstructs the same frame from retained vision artifacts.
    """

    frame_id = _current_positioning_frame_id(session)
    stable_source_lock_id = _stable_broker_source_lock_id(session)
    identity = _identity(session)
    if (
        identity.get("market_identity_confirmed") is not True
        or identity.get("timeframe_identity_confirmed") is not True
        or not _text(
            identity.get("market_selector_visual_fingerprint")
        ).startswith("selector_v2_")
    ):
        return []
    dimensions = _source_image_dimensions(_scene_forecast(session))
    image_size: Sequence[Any] | None = dimensions

    snapshot_is_current, snapshot_rows = _current_positioning_snapshot(
        session,
        frame_id=frame_id,
    )
    if snapshot_is_current:
        raw_sources = snapshot_rows
    else:
        raw_sources = [
            row
            for row in _explicit_positioning_overlay_rows(session)
            if _positioning_source_is_current(row, frame_id=frame_id)
        ]
    if not snapshot_is_current and not raw_sources:
        try:
            registry = build_market_object_registry_v3(session)
        except (AttributeError, KeyError, TypeError, ValueError):
            return []
        raw_sources = [
            row
            for row in _rows(registry.overlays, limit=512)
            if _positioning_source_is_current(row, frame_id=frame_id)
        ]

    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_sources:
        row = _canonical_positioning_source(
            raw,
            stable_source_lock_id=stable_source_lock_id,
            image_size=image_size,
            identity=identity,
        )
        if not row or not _positioning_source_is_current(row, frame_id=frame_id):
            continue
        key = _text(row.get("track_id"), row.get("object_id"), row.get("overlay_id"))
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(_project_positioning_source(row))
        if len(output) >= _POSITIONING_SOURCE_LIMIT:
            break
    output.sort(
        key=lambda row: (
            _text(row.get("sequence_id")),
            _text(row.get("chart_transform_id")),
            _text(row.get("broker_source_lock_id")),
            _text(row.get("track_id"), row.get("object_id")),
        )
    )
    return output
def _order_reference_unavailable(
    session: Mapping[str, Any],
    reason: str,
    *,
    current_y: float | None = None,
    price_basis: str = "",
) -> dict[str, Any]:
    identity = _identity(session)
    return {
        "schema_version": ORDER_REFERENCE_MAP_SCHEMA_VERSION,
        "status": "UNAVAILABLE",
        "availability_reason": reason,
        "frame_id": _current_positioning_frame_id(session),
        "sequence_id": "",
        "chart_transform_id": "",
        "broker_source_lock_id": _stable_broker_source_lock_id(session),
        "market": _text(identity.get("pair")).upper(),
        "timeframe": _text(identity.get("timeframe")).upper(),
        "coordinate_mode": "CHART_NORMALIZED",
        "chart_bounds": [0.0, 0.0, 1.0, 1.0],
        "current_price_y_norm": current_y,
        "current_price_basis": price_basis,
        "reference_count": 0,
        "rows": [],
        "observational_only": True,
        "execution_authority": "NONE",
    }


def _order_reference_geometry(
    session: Mapping[str, Any],
) -> tuple[float, float, float] | None:
    transform = _transform_contract(session)
    width = _number(transform.get("source_width"))
    height = _number(transform.get("source_height"))
    tolerance = _number(transform.get("close_tolerance"))
    if (
        transform.get("status") == "LOCKED"
        and width is not None
        and height is not None
        and tolerance is not None
        and width > 0.0
        and height > 0.0
        and tolerance > 0.0
    ):
        return width, height, round(min(0.012, tolerance), 6)

    tracking = _mapping(session.get("tracking_summary"))
    region = _mapping(tracking.get("chart_region") or tracking.get("display_region"))
    width = _number(region.get("width"))
    height = _number(region.get("height"))
    raw_bbox = region.get("pixel_bbox")
    bbox = (
        cast(Sequence[Any], raw_bbox)
        if isinstance(raw_bbox, Sequence)
        and not isinstance(raw_bbox, (str, bytes, bytearray))
        else ()
    )
    values = [_number(bbox[index]) for index in range(4)] if len(bbox) >= 4 else []
    focus = _mapping(session.get("manual_focus_region"))
    if (
        width is None
        or height is None
        or width <= 0.0
        or height <= 0.0
        or len(values) != 4
        or any(value is None for value in values)
        or focus.get("enabled") is not True
    ):
        return None
    left, top, right, bottom = [cast(float, value) for value in values]
    if (
        abs(left) > 1.0
        or abs(top) > 1.0
        or right <= left
        or bottom <= top
        or abs((right - left) - width) > 1.0
        or abs((bottom - top) - height) > 1.0
    ):
        return None
    return width, height, round(min(0.012, max(3.0 / height, 0.003)), 6)


def _order_candle_x_norm(
    candle: Mapping[str, Any],
    *,
    chart_width: float,
) -> float | None:
    normalized = _number(
        candle.get("x_norm")
        if candle.get("x_norm") is not None
        else candle.get("normalized_x")
        if candle.get("normalized_x") is not None
        else candle.get("center_x_norm")
    )
    if normalized is not None and 0.0 <= normalized <= 1.0:
        return normalized
    center_x = _number(
        candle.get("center_x_px")
        if candle.get("center_x_px") is not None
        else candle.get("center_x")
        if candle.get("center_x") is not None
        else candle.get("x")
    )
    raw_bbox = candle.get("bbox") or candle.get("bounds")
    if (
        center_x is None
        and isinstance(raw_bbox, Sequence)
        and not isinstance(raw_bbox, (str, bytes, bytearray))
    ):
        bbox = cast(Sequence[Any], raw_bbox)
        left = _number(bbox[0]) if len(bbox) >= 4 else None
        right = _number(bbox[2]) if len(bbox) >= 4 else None
        if left is not None and right is not None:
            center_x = 0.5 * (left + right)
    if center_x is None or chart_width <= 0.0:
        return None
    normalized = center_x / chart_width
    return normalized if 0.0 <= normalized <= 1.0 else None


def _order_reaction_window(
    session: Mapping[str, Any],
    *,
    chart_width: float,
) -> dict[str, Any]:
    """Anchor a bounded visible reaction window to one named closed candle."""

    identity = _identity(session)
    actual = _actual_closed_candle(session, identity)
    anchor_id = _text(actual.get("track_id"))
    if not anchor_id:
        return {}
    anchors = order_positioning_reprojection_anchors_v3(session)
    anchor = next(
        (row for row in anchors if _text(row.get("anchor_id")) == anchor_id),
        None,
    )
    anchor_x = _number(anchor.get("x_norm")) if anchor is not None else None
    if anchor_x is None:
        scene = _scene_forecast(session)
        identity_state = _mapping(scene.get("closed_candle_identity_state"))
        latest_closed = _mapping(identity_state.get("latest_closed"))
        if _text(latest_closed.get("track_id")) == anchor_id:
            anchor_x = _order_candle_x_norm(
                latest_closed,
                chart_width=chart_width,
            )
    tracking = _mapping(session.get("tracking_summary"))
    tracked_candles = _rows(tracking.get("tracked_candles"), limit=256)
    if anchor_x is None:
        matching_candle = next(
            (
                candle
                for candle in reversed(tracked_candles)
                if _text(candle.get("track_id")) == anchor_id
                and candle.get("is_closed") is True
            ),
            None,
        )
        if matching_candle is not None:
            anchor_x = _order_candle_x_norm(
                matching_candle,
                chart_width=chart_width,
            )
    if anchor_x is None or not 0.0 <= anchor_x < 1.0:
        return {}

    x_values = sorted(
        {
            round(value, 9)
            for row in anchors
            if (value := _number(row.get("x_norm"))) is not None
            and 0.0 <= value <= 1.0
        }
        | {
            round(value, 9)
            for candle in tracked_candles
            if (value := _order_candle_x_norm(candle, chart_width=chart_width))
            is not None
        }
    )
    local_steps = [
        right - left
        for left, right in zip(x_values, x_values[1:], strict=False)
        if 1e-6 < right - left <= 0.05
    ]
    step_x = _median_positive(local_steps)
    if step_x is None:
        forecast_anchor = _mapping(_scene_forecast(session).get("forecast_anchor"))
        fallback_step = _number(forecast_anchor.get("event_step_x_norm"))
        if fallback_step is not None and 1e-6 < fallback_step <= 0.05:
            step_x = fallback_step
    if step_x is None or anchor_x + step_x <= anchor_x:
        return {}
    visible_steps = min(
        POSITIONING_WINDOW_MAX_STEPS,
        int((1.0 - anchor_x + 1e-9) // step_x),
    )
    if visible_steps < 1:
        return {}
    end_x = min(1.0, anchor_x + step_x * visible_steps)
    return {
        "reaction_window_verified": True,
        "geometry_role": _ORDER_REACTION_WINDOW_GEOMETRY_ROLE,
        "reaction_window_anchor": _ORDER_REACTION_WINDOW_ANCHOR,
        "reaction_window_anchor_id": anchor_id,
        "reaction_window_origin_x_norm": round(anchor_x, 6),
        "reaction_window_step_x_norm": round(step_x, 6),
        "reaction_window_horizon_steps": visible_steps,
        "x_bounds": [
            round(anchor_x, 6),
            round(end_x, 6),
        ],
    }


def _order_reference_current_y(
    session: Mapping[str, Any],
    *,
    chart_height: float,
) -> tuple[float | None, str]:
    scene = _scene_forecast(session)
    identity_state = _mapping(scene.get("closed_candle_identity_state"))
    forming = _mapping(identity_state.get("forming"))
    forming_y = _number(_normalized_observation_geometry(forming, scene).get("chart_close_norm"))
    if forming_y is not None and 0.0 <= forming_y <= 1.0:
        return round(forming_y, 6), "FORMING_LIVE_CANDLE"
    actual_y = _number(_actual_closed_candle(session, _identity(session)).get("chart_close_norm"))
    if actual_y is not None and 0.0 <= actual_y <= 1.0:
        return round(actual_y, 6), "LATEST_COMPLETED_CANDLE"

    tracking = _mapping(session.get("tracking_summary"))
    for candle in reversed(_rows(tracking.get("tracked_candles"), limit=24)):
        close_y = _number(
            candle.get("close_y_px")
            if candle.get("close_y_px") is not None
            else candle.get("close_y")
        )
        if close_y is None:
            raw_bbox = candle.get("body_bbox") or candle.get("bbox") or candle.get("bounds")
            bbox = (
                cast(Sequence[Any], raw_bbox)
                if isinstance(raw_bbox, Sequence)
                and not isinstance(raw_bbox, (str, bytes, bytearray))
                else ()
            )
            if len(bbox) >= 4:
                top = _number(bbox[1])
                bottom = _number(bbox[3])
                side = _direction(candle.get("direction"), candle.get("side"))
                close_y = top if side == "BUY" else bottom if side == "SELL" else None
        if close_y is not None and 0.0 <= close_y <= chart_height:
            return round(close_y / chart_height, 6), "FORMING_LIVE_CANDLE"
    return None, ""


def _order_reference_bounds(
    row: Mapping[str, Any],
    *,
    chart_width: float,
    chart_height: float,
) -> list[float]:
    coordinate_mode = _text(row.get("coordinate_mode")).upper()
    raw_bounds: Any = row.get("bounds") or row.get("bbox")
    if _text(row.get("type")).upper() in {"SUPPORT_TRENDLINE", "RESISTANCE_TRENDLINE"}:
        band = _mapping(row.get("projected_entry_band") or row.get("projected_price_band"))
        if band.get("verified") is not True or _text(band.get("coordinate_mode")).upper() != coordinate_mode:
            return []
        raw_bounds = band.get("bounds") or band.get("bbox")
    if not isinstance(raw_bounds, Sequence) or isinstance(raw_bounds, (str, bytes, bytearray)):
        return []
    raw = cast(Sequence[Any], raw_bounds)
    values = [_number(raw[index]) for index in range(4)] if len(raw) >= 4 else []
    if len(values) != 4 or any(value is None for value in values):
        return []
    x0, y0, x1, y1 = [cast(float, value) for value in values]
    if x0 >= x1 or y0 >= y1:
        return []
    if coordinate_mode == "CHART_IMAGE_SPACE":
        if x0 < 0.0 or y0 < 0.0 or x1 > chart_width or y1 > chart_height:
            return []
        normalized = [x0 / chart_width, y0 / chart_height, x1 / chart_width, y1 / chart_height]
    elif coordinate_mode == "CHART_NORMALIZED":
        normalized = [x0, y0, x1, y1]
    else:
        return []
    return (
        [round(value, 6) for value in normalized]
        if all(0.0 <= value <= 1.0 for value in normalized)
        else []
    )


def _order_reference_source_valid(
    row: Mapping[str, Any],
    *,
    frame_id: int,
    source_lock_id: str,
) -> bool:
    quality = _mapping(row.get("anchor_quality"))
    evidence = _mapping(row.get("anchor_evidence"))
    quality_score = _number(quality.get("score"))
    confidence = _number(row.get("confidence"))
    truth = _number(row.get("truth_score"))
    return bool(
        _text(row.get("schema_version")) == "PG_V3_OVERLAY_OBJECT_V1"
        and _text(row.get("frame_id")) == str(frame_id)
        and _text(row.get("type")).upper() in _ORDER_REFERENCE_SOURCE_TYPES
        and _text(row.get("track_id"), row.get("object_id"), row.get("overlay_id"))
        and _text(row.get("sequence_id"))
        and _text(row.get("chart_transform_id"))
        and _text(row.get("broker_source_lock_id")) == source_lock_id
        and _text(row.get("coordinate_mode")).upper()
        in {"CHART_IMAGE_SPACE", "CHART_NORMALIZED"}
        and _text(row.get("lifecycle_state")).upper()
        in _ORDER_REFERENCE_LIVE_STATES
        and _text(row.get("anchor_evidence_status")).upper() == "VALID"
        and evidence.get("valid") is True
        and quality_score is not None
        and quality_score >= 0.65
        and all(
            quality.get(key) is True
            for key in _CANONICAL_ANCHOR_QUALITY_FIELDS
        )
        and confidence is not None
        and confidence >= 0.70
        and truth is not None
        and truth >= 0.70
    )


def _order_reference_row(
    source: Mapping[str, Any],
    *,
    order_kind: str,
    intent: str,
    side: str,
    role: str,
    bounds: Sequence[float],
    source_bounds: Sequence[float],
    boundary_y: float,
    current_y: float,
) -> dict[str, Any]:
    source_key = _text(
        source.get("track_id"),
        source.get("object_id"),
        source.get("overlay_id"),
    )
    source_digest = sha256(f"order-source|{source_key}".encode()).hexdigest()
    source_reference_id = f"ors_{source_digest[:16]}"
    identity = f"{source_reference_id}|{order_kind}|{intent}"
    quality = _mapping(source.get("anchor_quality"))
    confidence = min(
        cast(float, _number(source.get("confidence"))),
        cast(float, _number(source.get("truth_score"))),
        cast(float, _number(quality.get("score"))),
    )
    payload: dict[str, Any] = {
        "reference_id": f"orr_{sha256(identity.encode()).hexdigest()[:18]}",
        "order_kind": order_kind,
        "intent": intent,
        "side": side,
        "location_role": role,
        "bounds": [round(float(value), 6) for value in bounds[:4]],
        "source_bounds": [
            round(float(value), 6) for value in source_bounds[:4]
        ],
        "geometry_role": _ORDER_REACTION_WINDOW_GEOMETRY_ROLE,
        "reaction_window_anchor": _ORDER_REACTION_WINDOW_ANCHOR,
        "boundary_y_norm": round(boundary_y, 6),
        "distance_from_current_norm": round(abs(boundary_y - current_y), 6),
        "confidence": round(confidence, 4),
        "source_reference_id": source_reference_id,
        "observational_only": True,
        "execution_authority": "NONE",
    }
    return payload
def _order_reference_band(
    bounds: Sequence[float],
    *,
    above: bool,
    thickness: float,
) -> list[float]:
    x0, y0, x1, y1 = [float(value) for value in bounds[:4]]
    if above:
        outer = max(0.0, y0 - thickness)
        return [x0, outer, x1, y0] if outer < y0 else []
    outer = min(1.0, y1 + thickness)
    return [x0, y1, x1, outer] if outer > y1 else []


def build_current_order_reference_map_v3(session: Mapping[str, Any]) -> dict[str, Any]:
    """Return current visual order locations without granting execution authority."""

    geometry = _order_reference_geometry(session)
    if geometry is None:
        return _order_reference_unavailable(session, "TRANSFORM_NOT_LOCKED")
    chart_width, chart_height, band_thickness = geometry
    reaction_window = _order_reaction_window(
        session,
        chart_width=chart_width,
    )
    reaction_x_bounds = reaction_window.get("x_bounds")
    if (
        reaction_window.get("reaction_window_verified") is not True
        or not isinstance(reaction_x_bounds, Sequence)
        or isinstance(reaction_x_bounds, (str, bytes, bytearray))
        or len(cast(Sequence[Any], reaction_x_bounds)) < 2
    ):
        return _order_reference_unavailable(
            session,
            "LATEST_COMPLETED_REACTION_WINDOW_UNAVAILABLE",
        )
    reaction_left = _number(cast(Sequence[Any], reaction_x_bounds)[0])
    reaction_right = _number(cast(Sequence[Any], reaction_x_bounds)[1])
    if (
        reaction_left is None
        or reaction_right is None
        or not 0.0 <= reaction_left < reaction_right <= 1.0
    ):
        return _order_reference_unavailable(
            session,
            "LATEST_COMPLETED_REACTION_WINDOW_UNAVAILABLE",
        )
    current_y, price_basis = _order_reference_current_y(session, chart_height=chart_height)
    if current_y is None:
        return _order_reference_unavailable(session, "CURRENT_PRICE_UNAVAILABLE")
    source_lock_id = _stable_broker_source_lock_id(session)
    if not source_lock_id:
        return _order_reference_unavailable(
            session,
            "CURRENT_SOURCE_LOCK_UNAVAILABLE",
            current_y=current_y,
            price_basis=price_basis,
        )
    frame_id = _current_positioning_frame_id(session)
    sources = [
        row
        for row in order_positioning_evidence_rows_v3(session)
        if _order_reference_source_valid(
            row,
            frame_id=frame_id,
            source_lock_id=source_lock_id,
        )
    ]
    lineages = {
        (
            _text(row.get("sequence_id")),
            _text(row.get("chart_transform_id")),
            _text(row.get("broker_source_lock_id")),
        )
        for row in sources
    }
    if len(lineages) != 1:
        return _order_reference_unavailable(
            session,
            "CURRENT_LINEAGE_UNAVAILABLE",
            current_y=current_y,
            price_basis=price_basis,
        )

    drafts: list[dict[str, Any]] = []
    for source in sources:
        source_type = _text(source.get("type")).upper()
        above = source_type in {"SUPPLY_ZONE", "RESISTANCE_TRENDLINE"}
        expected_side = "SELL" if above else "BUY"
        if _direction(source.get("side")) != expected_side:
            continue
        source_bounds = _order_reference_bounds(
            source,
            chart_width=chart_width,
            chart_height=chart_height,
        )
        if (
            not source_bounds
            or (above and source_bounds[3] >= current_y)
            or (not above and source_bounds[1] <= current_y)
        ):
            continue
        bounds = [
            reaction_left,
            source_bounds[1],
            reaction_right,
            source_bounds[3],
        ]
        if source_type in {"SUPPLY_ZONE", "RESISTANCE_TRENDLINE"}:
            limit_kind = "SELL_LIMIT"
        elif source_type in {"DEMAND_ZONE", "SUPPORT_TRENDLINE"}:
            limit_kind = "BUY_LIMIT"
        else:
            limit_kind = ""
        if limit_kind:
            drafts.append(
                _order_reference_row(
                    source,
                    order_kind=limit_kind,
                    intent="ENTRY_LIMIT",
                    side=expected_side,
                    role="UPPER_ENTRY" if above else "LOWER_ENTRY",
                    bounds=bounds,
                    source_bounds=source_bounds,
                    boundary_y=bounds[3] if above else bounds[1],
                    current_y=current_y,
                )
            )
        stop_kind = "BUY_STOP" if above else "SELL_STOP"
        stop_side = "BUY" if above else "SELL"
        if order_positioning_stop_confirmation_reason_v3(
            source,
            thesis_side=stop_side,
        ):
            continue
        stop_bounds = _order_reference_band(bounds, above=above, thickness=band_thickness)
        if not stop_bounds:
            continue
        boundary_y = 0.5 * (stop_bounds[1] + stop_bounds[3])
        drafts.append(
            _order_reference_row(
                source,
                order_kind=stop_kind,
                intent="ENTRY_STOP",
                side=stop_side,
                role="UPPER_CONFIRMATION" if above else "LOWER_CONFIRMATION",
                bounds=stop_bounds,
                source_bounds=source_bounds,
                boundary_y=boundary_y,
                current_y=current_y,
            )
        )
    route_order = {
        ("ENTRY_LIMIT", "BUY_LIMIT"): 0,
        ("ENTRY_LIMIT", "SELL_LIMIT"): 1,
        ("ENTRY_STOP", "BUY_STOP"): 2,
        ("ENTRY_STOP", "SELL_STOP"): 3,
    }
    selected: list[dict[str, Any]] = []
    seen_routes: set[tuple[str, str]] = set()
    seen_geometries: set[tuple[float, ...]] = set()
    for row in sorted(
        drafts,
        key=lambda item: (
            route_order.get((_text(item.get("intent")), _text(item.get("order_kind"))), 99),
            _number(item.get("distance_from_current_norm")) or 0.0,
            -(_number(item.get("confidence")) or 0.0),
            _text(item.get("reference_id")),
        ),
    ):
        route = (_text(row.get("intent")), _text(row.get("order_kind")))
        geometry_key = tuple(
            float(value)
            for value in cast(Sequence[Any], row.get("bounds", []))[:4]
        )
        if route not in seen_routes and geometry_key not in seen_geometries:
            selected.append(row)
            seen_routes.add(route)
            seen_geometries.add(geometry_key)
    if not selected:
        return _order_reference_unavailable(
            session,
            "NO_CURRENT_STRUCTURAL_REFERENCES",
            current_y=current_y,
            price_basis=price_basis,
        )
    sequence_id, chart_transform_id, lineage_lock_id = next(iter(lineages))
    identity = _identity(session)
    return {
        "schema_version": ORDER_REFERENCE_MAP_SCHEMA_VERSION,
        "status": "READY",
        "availability_reason": "CURRENT_STRUCTURAL_REFERENCES_READY",
        "frame_id": frame_id,
        "sequence_id": sequence_id,
        "chart_transform_id": chart_transform_id,
        "broker_source_lock_id": lineage_lock_id,
        "market": _text(identity.get("pair")).upper(),
        "timeframe": _text(identity.get("timeframe")).upper(),
        "coordinate_mode": "CHART_NORMALIZED",
        "chart_bounds": [0.0, 0.0, 1.0, 1.0],
        "current_price_y_norm": current_y,
        "current_price_basis": price_basis,
        "geometry_role": _ORDER_REACTION_WINDOW_GEOMETRY_ROLE,
        "reaction_window_anchor": _ORDER_REACTION_WINDOW_ANCHOR,
        "reaction_window": reaction_window,
        "reference_count": len(selected),
        "rows": selected,
        "observational_only": True,
        "execution_authority": "NONE",
    }


def _order_positioning_candidate_v3(
    session: Mapping[str, Any],
    *,
    frame_id: int,
    identity: Mapping[str, Any],
) -> dict[str, Any]:
    overlays = order_positioning_evidence_rows_v3(session)
    current_frame = str(frame_id)
    lineage_rows = [
        row
        for row in overlays
        if _text(row.get("frame_id")) == current_frame
        and _text(row.get("schema_version")) == "PG_V3_OVERLAY_OBJECT_V1"
        and _text(row.get("type")).upper() in _POSITIONING_SOURCE_TYPES
        and "CHART" in _text(row.get("coordinate_mode")).upper()
        and "PLOT" not in _text(row.get("coordinate_mode")).upper()
    ]
    lineage_rows.sort(
        key=lambda row: (
            _text(row.get("sequence_id")),
            _text(row.get("chart_transform_id")),
            _text(row.get("broker_source_lock_id")),
            _text(row.get("track_id"), row.get("object_id")),
        )
    )
    lineage_keys = {
        (
            _text(row.get("sequence_id")),
            _text(row.get("chart_transform_id")),
            _text(row.get("broker_source_lock_id")),
            _text(row.get("coordinate_mode")).upper(),
        )
        for row in lineage_rows
    }
    lineage = lineage_rows[0] if len(lineage_keys) == 1 else {}
    coordinate_mode = _text(lineage.get("coordinate_mode")).upper()
    scene = _scene_forecast(session)
    raw_size = scene.get("source_image_size")
    size = (
        cast(Sequence[Any], raw_size)
        if isinstance(raw_size, Sequence)
        and not isinstance(raw_size, (str, bytes, bytearray))
        else ()
    )
    width = _number(size[0]) if len(size) >= 2 else None
    height = _number(size[1]) if len(size) >= 2 else None
    if "NORMALIZED" in coordinate_mode:
        chart_bounds: list[float] = [0.0, 0.0, 1.0, 1.0]
    elif width is not None and height is not None and width > 0.0 and height > 0.0:
        chart_bounds = [0.0, 0.0, width, height]
    else:
        chart_bounds = []
    actual = _actual_closed_candle(session, identity)
    close_level = _number(actual.get("chart_close_norm"))
    current_price_y = (
        close_level
        if close_level is not None and "NORMALIZED" in coordinate_mode
        else close_level * height
        if close_level is not None and height is not None
        else None
    )
    reaction_window = (
        _order_reaction_window(session, chart_width=width)
        if width is not None and width > 0.0
        else {}
    )
    latest = _mapping(session.get("latest_signal"))
    tracking = _mapping(session.get("tracking_summary"))
    thesis = (
        _mapping(session.get("signal_thesis_v3"))
        or _mapping(latest.get("signal_thesis_v3"))
        or _mapping(tracking.get("signal_thesis_v3"))
    )
    side = _direction(thesis.get("side"), thesis.get("effective_side"))
    thesis_state = _text(thesis.get("state"), thesis.get("status")).upper()
    opportunity = (
        _mapping(session.get("execution_opportunity_window_v3"))
        or _mapping(latest.get("execution_opportunity_window_v3"))
    )
    maturity = (
        _mapping(latest.get("opportunity_maturity"))
        or _mapping(session.get("opportunity_maturity"))
    )
    book_strategy = (
        _mapping(latest.get("book_strategy"))
        or _mapping(session.get("book_strategy"))
    )
    allowance = (
        _mapping(latest.get("allowance_package"))
        or _mapping(session.get("allowance_package"))
    )
    promotion = (
        _mapping(latest.get("promotion_trace"))
        or _mapping(session.get("promotion_trace"))
    )
    execution_timing = _mapping(latest.get("execution_timing"))
    timing_decision = _mapping(latest.get("timing_decision"))
    timing_tokens = {
        _text(value).upper()
        for value in (
            thesis.get("entry_state"),
            thesis.get("maturity"),
            latest.get("entry_state"),
            latest.get("opportunity_maturity_state"),
            session.get("opportunity_maturity_state"),
            maturity.get("state"),
            book_strategy.get("state"),
            book_strategy.get("maturity_state"),
            latest.get("book_strategy_state"),
            allowance.get("timing_mode"),
            allowance.get("opportunity_maturity"),
            allowance.get("book_strategy_maturity"),
            promotion.get("timing_mode"),
            latest.get("timing_mode"),
            execution_timing.get("timing_mode"),
            execution_timing.get("state"),
            timing_decision.get("timing_mode"),
            timing_decision.get("state"),
            opportunity.get("status"),
            opportunity.get("state"),
        )
        if _text(value)
    }
    favorable_value = _text(
        opportunity.get("favorable_candles_since_origin"),
        maturity.get("favorable_candles_since_origin"),
        thesis.get("favorable_candles_since_origin"),
        latest.get("favorable_candles_since_origin"),
        session.get("favorable_candles_since_origin"),
    )
    explicit_favorable = max(
        0,
        _integer(favorable_value),
    )
    favorable_candles = (
        5
        if timing_tokens.intersection({"LATE", "LATE_CHASE", "MISSED"})
        else explicit_favorable
    )
    transform = _transform_contract(session)
    return build_order_positioning_candidates_v3(
        {
            "side": side,
            "thesis_verified": bool(
                thesis
                and side in {"BUY", "SELL"}
                and thesis_state not in {"", "INVALID", "INVALIDATED", "EXPIRED", "STALE"}
            ),
            "frame_id": frame_id,
            "sequence_id": lineage.get("sequence_id"),
            "chart_transform_id": lineage.get("chart_transform_id"),
            "broker_source_lock_id": lineage.get("broker_source_lock_id"),
            "market": identity.get("pair"),
            "timeframe": identity.get("timeframe"),
            "coordinate_mode": coordinate_mode,
            "price_axis_orientation": "SCREEN_Y_INCREASES_DOWN",
            "chart_bounds": chart_bounds,
            "current_price_y": current_price_y,
            "current_price_verified": close_level is not None,
            "current_price_basis": _ORDER_REACTION_WINDOW_ANCHOR,
            "timing_verified": bool(
                _text(identity.get("closed_candle_key"))
                and transform.get("status") == "LOCKED"
            ),
            "display_band_norm": transform.get("close_tolerance"),
            "display_band_verified": transform.get("status") == "LOCKED",
            "display_band_basis": "VERIFIED_MEDIAN_CANDLE_RANGE",
            "favorable_candles_since_origin": favorable_candles,
            **reaction_window,
            "overlay_objects": overlays,
            "reprojection_anchors": order_positioning_reprojection_anchors_v3(session),
        }
    )


def build_current_order_positioning_candidate_v3(
    session: Mapping[str, Any],
) -> dict[str, Any]:
    """Build the fail-closed positioning preview for the displayed frame."""

    return _order_positioning_candidate_v3(
        session,
        frame_id=_current_positioning_frame_id(session),
        identity=_identity(session),
    )
def _point_pairs(value: Any, *, limit: int = 33) -> list[list[float]]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    output: list[list[float]] = []
    for item in cast(Sequence[Any], value)[:limit]:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes, bytearray)):
            return []
        pair = cast(Sequence[Any], item)
        if len(pair) < 2:
            return []
        x_value = _number(pair[0])
        y_value = _number(pair[1])
        if (
            x_value is None
            or y_value is None
            or not 0.0 <= x_value <= 1.0
            or not 0.0 <= y_value <= 1.0
        ):
            return []
        output.append([round(x_value, 6), round(y_value, 6)])
    if any(
        output[index][0] <= output[index - 1][0]
        for index in range(1, len(output))
    ):
        return []
    return output


def _chart_level(value: Any, *, price_axis: bool) -> float | None:
    number = _number(value)
    if number is None or not 0.0 <= number <= 1.0:
        return None
    return round(1.0 - number if price_axis else number, 6)


def _forecast_steps(value: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[list[float]]]:
    """Return only real bounded normalized geometry from one shadow artifact."""

    points = _point_pairs(value.get("line_points"), limit=POSITIONING_WINDOW_MAX_STEPS + 1)
    candle_rows = _rows(value.get("forecast_candles"), limit=POSITIONING_WINDOW_MAX_STEPS)
    if not candle_rows:
        candle_rows = _rows(value.get("forecast_path"), limit=POSITIONING_WINDOW_MAX_STEPS)
    steps: list[dict[str, Any]] = []
    event_count = len(candle_rows) if 2 <= len(candle_rows) <= POSITIONING_WINDOW_MAX_STEPS else 0
    if event_count:
        for index, row in enumerate(candle_rows, start=1):
            open_level = _chart_level(row.get("open_y_norm"), price_axis=False)
            close_level = _chart_level(row.get("close_y_norm"), price_axis=False)
            if open_level is None:
                open_level = _chart_level(
                    row.get("expected_open_norm", row.get("open_norm")),
                    price_axis=True,
                )
            if close_level is None:
                close_level = _chart_level(
                    row.get(
                        "expected_close_norm",
                        row.get("close_norm", row.get("relative_close")),
                    ),
                    price_axis=True,
                )
            if open_level is None and len(points) == event_count + 1:
                open_level = points[index - 1][1]
            if close_level is None and len(points) == event_count + 1:
                close_level = points[index][1]
            if open_level is None or close_level is None:
                return [], []
            steps.append(
                {
                    "step": index,
                    "open_level": open_level,
                    "close_level": close_level,
                    "body_size": round(abs(close_level - open_level), 6),
                    "direction": _direction(
                        row.get("movement_side"),
                        row.get("movement_direction"),
                        row.get("body_bias"),
                        row.get("candle_body_direction"),
                        value.get("side"),
                    ),
                }
            )
    elif 3 <= len(points) <= POSITIONING_WINDOW_MAX_STEPS + 1:
        event_count = len(points) - 1
        for index in range(1, event_count + 1):
            open_level = points[index - 1][1]
            close_level = points[index][1]
            steps.append(
                {
                    "step": index,
                    "open_level": open_level,
                    "close_level": close_level,
                    "body_size": round(abs(close_level - open_level), 6),
                    "direction": _direction(
                        value.get("side"),
                        "BUY" if close_level < open_level else "SELL" if close_level > open_level else "HOLD",
                    ),
                }
            )
    if not 2 <= len(steps) <= POSITIONING_WINDOW_MAX_STEPS:
        return [], []
    return steps, points if len(points) == len(steps) + 1 else []


def _selected_scene_geometry(
    scene: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[list[float]]]:
    """Return geometry only when exactly one complete Scene scenario is selected."""

    selected = [
        scenario
        for scenario in _rows(scene.get("forecast_scenarios"), limit=8)
        if scenario.get("selected") is True
    ]
    if len(selected) != 1:
        return [], []
    steps, points = _forecast_steps(selected[0])
    if not 2 <= len(steps) <= POSITIONING_WINDOW_MAX_STEPS or len(points) != len(steps) + 1:
        return [], []
    return steps, points


def _median_positive(values: Sequence[float]) -> float | None:
    usable = sorted(value for value in values if 0.0 < value <= 1.0)
    if not usable:
        return None
    midpoint = len(usable) // 2
    if len(usable) % 2:
        return usable[midpoint]
    return (usable[midpoint - 1] + usable[midpoint]) / 2.0


def _derived_scene_event_step_x(scene: Mapping[str, Any]) -> float | None:
    _, points = _selected_scene_geometry(scene)
    if not 3 <= len(points) <= POSITIONING_WINDOW_MAX_STEPS + 1:
        return None
    deltas = [
        points[index][0] - points[index - 1][0]
        for index in range(1, len(points))
    ]
    event_step = _median_positive(deltas)
    if event_step is None or not 2 <= len(deltas) <= POSITIONING_WINDOW_MAX_STEPS:
        return None
    alignment_tolerance = max(1e-6, event_step * 0.02)
    if any(
        delta <= 0.0 or abs(delta - event_step) > alignment_tolerance
        for delta in deltas
    ):
        return None
    return round(event_step, 6)


def _derived_scene_vertical_scale(scene: Mapping[str, Any]) -> float | None:
    steps, _ = _selected_scene_geometry(scene)
    if not 2 <= len(steps) <= POSITIONING_WINDOW_MAX_STEPS:
        return None
    bodies = [
        abs(close_level - open_level)
        for step in steps
        if (open_level := _number(step.get("open_level"))) is not None
        and (close_level := _number(step.get("close_level"))) is not None
        and abs(close_level - open_level) > 1e-6
    ]
    if len(bodies) < 2:
        return None
    scale = _median_positive(bodies)
    return round(scale, 6) if scale is not None else None


def _source_image_dimensions(
    scene: Mapping[str, Any],
) -> tuple[float, float] | None:
    identity_state = _mapping(scene.get("closed_candle_identity_state"))
    for value in (
        scene.get("source_image_size"),
        identity_state.get("source_image_size"),
    ):
        if isinstance(value, Mapping):
            dimensions = _mapping(value)
            width = _number(dimensions.get("width"))
            height = _number(dimensions.get("height"))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            size = cast(Sequence[Any], value)
            width = _number(size[0]) if len(size) >= 2 else None
            height = _number(size[1]) if len(size) >= 2 else None
        else:
            width = None
            height = None
        if width is not None and height is not None and width > 0.0 and height > 0.0:
            return width, height
    return None


def _source_image_height(scene: Mapping[str, Any]) -> float | None:
    dimensions = _source_image_dimensions(scene)
    return dimensions[1] if dimensions is not None else None


def _normalized_observation_geometry(
    candle: Mapping[str, Any],
    scene: Mapping[str, Any],
) -> dict[str, Any]:
    height = _source_image_height(scene)

    def chart_y(normalized_key: str, pixel_key: str) -> float | None:
        normalized = _chart_level(candle.get(normalized_key), price_axis=False)
        if normalized is not None:
            return normalized
        pixels = _number(candle.get(pixel_key))
        if pixels is None or height is None or not 0.0 <= pixels <= height:
            return None
        return round(pixels / height, 6)

    open_level = chart_y("open_y_norm", "open_y")
    close_level = chart_y("close_y_norm", "close_y")
    top_level = chart_y("top_y_norm", "top_y")
    bottom_level = chart_y("bottom_y_norm", "bottom_y")
    if close_level is None:
        # price_proxy is explicitly defined by the scene contributor as the
        # vertical inverse of normalized chart Y. Arbitrary close_norm values
        # have no such contract and are intentionally not transformed.
        close_level = _chart_level(candle.get("price_proxy"), price_axis=True)
    return {
        "chart_open_norm": open_level,
        "chart_close_norm": close_level,
        "chart_top_norm": top_level,
        "chart_bottom_norm": bottom_level,
        "chart_body_norm": (
            round(abs(close_level - open_level), 6)
            if open_level is not None and close_level is not None
            else None
        ),
    }


def order_positioning_reprojection_anchors_v3(
    session: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Return stable, closed-candle anchors on the chart-normalized plane."""

    tracking = _mapping(session.get("tracking_summary"))
    scene = _scene_forecast(session)
    dimensions = _source_image_dimensions(scene)
    width = dimensions[0] if dimensions is not None else None
    anchors: list[dict[str, Any]] = []
    seen: set[str] = set()
    tracked_closed = [
        candle
        for candle in _rows(tracking.get("tracked_candles"), limit=256)
        if candle.get("is_closed") is True
    ]
    identity_state = _mapping(scene.get("closed_candle_identity_state"))
    closed_tail = _rows(identity_state.get("closed_tail"), limit=24)
    latest_closed = _mapping(identity_state.get("latest_closed"))
    closed_candidates = [*tracked_closed, *closed_tail]
    if latest_closed:
        closed_candidates.append(latest_closed)
    for candle in closed_candidates:
        anchor_id = _text(
            candle.get("track_id"),
            candle.get("object_id"),
            candle.get("candle_id"),
        )
        if not anchor_id or anchor_id in seen:
            continue
        x_norm = _number(
            candle.get("x_norm")
            if candle.get("x_norm") is not None
            else candle.get("normalized_x")
            if candle.get("normalized_x") is not None
            else candle.get("center_x_norm")
        )
        if x_norm is None and width is not None:
            center_x = _number(
                candle.get("center_x_px")
                if candle.get("center_x_px") is not None
                else candle.get("center_x")
                if candle.get("center_x") is not None
                else candle.get("x")
            )
            raw_bbox = candle.get("bbox") or candle.get("bounds")
            if (
                center_x is None
                and isinstance(raw_bbox, Sequence)
                and not isinstance(raw_bbox, (str, bytes, bytearray))
            ):
                bbox = cast(Sequence[Any], raw_bbox)
                left = _number(bbox[0]) if len(bbox) >= 4 else None
                right = _number(bbox[2]) if len(bbox) >= 4 else None
                if left is not None and right is not None:
                    center_x = 0.5 * (left + right)
            if center_x is not None:
                x_norm = center_x / width
        y_norm = _number(_normalized_observation_geometry(candle, scene).get("chart_close_norm"))
        if (
            x_norm is None
            or y_norm is None
            or not 0.0 <= x_norm <= 1.0
            or not 0.0 <= y_norm <= 1.0
        ):
            continue
        seen.add(anchor_id)
        anchors.append(
            {
                "anchor_id": anchor_id,
                "x_norm": round(x_norm, 6),
                "y_norm": round(y_norm, 6),
            }
        )
    anchors.sort(key=lambda row: (float(row["x_norm"]), str(row["anchor_id"])))
    return anchors[-24:]
def _transform_contract(session: Mapping[str, Any]) -> dict[str, Any]:
    scene = _scene_forecast(session)
    identity_state = _mapping(scene.get("closed_candle_identity_state"))
    anchor = _mapping(scene.get("forecast_anchor"))
    image_size = scene.get("source_image_size")
    if not isinstance(image_size, Sequence) or isinstance(
        image_size,
        (str, bytes, bytearray),
    ):
        return {"status": "UNAVAILABLE", "reason": "SOURCE_DIMENSIONS_UNAVAILABLE"}
    values = cast(Sequence[Any], image_size)
    width = _number(values[0]) if len(values) >= 2 else None
    height = _number(values[1]) if len(values) >= 2 else None
    focus = _mapping(session.get("manual_focus_region"))
    raw_bbox = focus.get("normalized_bbox")
    bbox = (
        [
            _number(value)
            for value in cast(Sequence[Any], raw_bbox)[:4]
        ]
        if isinstance(raw_bbox, Sequence)
        and not isinstance(raw_bbox, (str, bytes, bytearray))
        else []
    )
    median_range = _number(identity_state.get("median_range"))
    normalized_range = (
        round(median_range / height, 6)
        if median_range is not None
        and height is not None
        and 0.0 < median_range <= height
        else None
    )
    target_scale = _number(anchor.get("target_scale_norm"))
    target_scale_source = "ANCHOR_METADATA"
    if target_scale is None:
        target_scale = normalized_range or _derived_scene_vertical_scale(scene)
        target_scale_source = (
            "MEDIAN_CANDLE_RANGE"
            if normalized_range is not None
            else "SELECTED_PATH_GEOMETRY"
        )
    event_step_x = _number(anchor.get("event_step_x_norm"))
    event_step_x_source = "ANCHOR_METADATA"
    if event_step_x is None:
        event_step_x = _derived_scene_event_step_x(scene)
        event_step_x_source = "SELECTED_PATH_GEOMETRY"
    comparison_scale = normalized_range or target_scale
    if (
        width is None
        or height is None
        or width <= 0.0
        or height <= 0.0
        or focus.get("enabled") is not True
        or len(bbox) != 4
        or any(value is None or not 0.0 <= value <= 1.0 for value in bbox)
        or comparison_scale is None
        or comparison_scale <= 0.0
        or comparison_scale > 1.0
        or target_scale is None
        or target_scale <= 0.0
        or target_scale > 1.0
        or event_step_x is None
        or event_step_x <= 0.0
        or event_step_x > 1.0
    ):
        return {
            "status": "UNAVAILABLE",
            "reason": "NORMALIZED_CHART_TRANSFORM_NOT_PROVEN",
        }
    normalized_bbox = [round(cast(float, value), 6) for value in bbox]
    identity_seed = "|".join(
        str(value)
        for value in (
            int(width),
            int(height),
            *normalized_bbox,
        )
    )
    transform_id = sha256(identity_seed.encode("utf-8")).hexdigest()[:20]
    close_tolerance = round(
        max(3.0 / height, min(0.02, comparison_scale * 0.35)),
        6,
    )
    return {
        "status": "LOCKED",
        "reason": "NORMALIZED_CHART_TRANSFORM_LOCKED",
        "source_width": int(width),
        "source_height": int(height),
        "surface_transform_identity": transform_id,
        "chart_transform_identity": transform_id,
        "normalized_focus_bounds": normalized_bbox,
        "median_range_norm": round(comparison_scale, 6),
        "target_scale_norm": round(target_scale, 6),
        "target_scale_source": target_scale_source,
        "event_step_x_norm": round(event_step_x, 6),
        "event_step_x_source": event_step_x_source,
        "close_tolerance": close_tolerance,
        "body_tolerance": close_tolerance,
    }


def _actual_closed_candle(
    session: Mapping[str, Any],
    identity: Mapping[str, Any],
    event_observation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    scene = _scene_forecast(session)
    identity_state = _mapping(scene.get("closed_candle_identity_state"))
    latest_closed = _mapping(identity_state.get("latest_closed"))
    tracking = _mapping(session.get("tracking_summary"))
    latest = _mapping(session.get("latest_signal"))
    tracked = _rows(tracking.get("tracked_candles"), limit=128)
    closed_rows = [row for row in tracked if row.get("is_closed") is True]
    latest_track_id = _text(latest_closed.get("track_id"))
    identity_match = next(
        (
            row
            for row in reversed(tracked)
            if latest_track_id and _text(row.get("track_id")) == latest_track_id
        ),
        None,
    )
    explicit_observation = _mapping(event_observation)
    candle = (
        explicit_observation
        or identity_match
        or (closed_rows[-1] if closed_rows else latest_closed)
    )
    side = _direction(
        candle.get("side"),
        candle.get("direction"),
        latest_closed.get("side"),
        latest_closed.get("direction"),
        latest.get("action"),
        tracking.get("local_direction"),
    )
    close_norm = None
    close_space = ""
    for key, space in (
        ("close_y_norm", "CHART_Y_NORM"),
        ("normalized_close_y", "CHART_Y_NORM"),
        ("price_proxy", "PRICE_PROXY_NORM"),
        ("close_norm", "PRICE_NORM"),
    ):
        close_norm = _number(candle.get(key, latest_closed.get(key)))
        if close_norm is not None:
            close_space = space
            break
    chart_geometry = _normalized_observation_geometry(candle, scene)
    return _safe_mapping(
        {
            "closed_candle_key": identity.get("closed_candle_key"),
            "closed_candle_sequence": identity.get("closed_candle_sequence"),
            "side": side,
            "close_norm": close_norm,
            "coordinate_space": close_space,
            "track_id": _text(candle.get("track_id"), latest_closed.get("track_id")),
            **chart_geometry,
        }
    )


__all__ = [
    "ORDER_REFERENCE_MAP_SCHEMA_VERSION",
    "POSITIONING_WINDOW_MAX_STEPS",
    "build_current_order_positioning_candidate_v3",
    "build_current_order_reference_map_v3",
    "order_positioning_evidence_rows_v3",
    "order_positioning_reprojection_anchors_v3",
]
