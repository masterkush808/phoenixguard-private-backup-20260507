from __future__ import annotations

import itertools
import math
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, cast

import cv2
import numpy as np
from numpy.typing import NDArray


PALETTE_DEFAULT_SIDE: dict[str, str] = {
    "green": "BUY",
    "blue": "BUY",
    "red": "SELL",
    "orange": "SELL",
    "magenta": "SELL",
}

_PALETTE_ORDER = tuple(PALETTE_DEFAULT_SIDE)


def build_candle_palette_masks(rgb: NDArray[np.uint8]) -> dict[str, NDArray[np.bool_]]:
    """Build mutually exclusive, theme-independent candle-color masks.

    The V3 memory suites mix green/red, blue/red, blue/green, and
    orange/magenta charts on both light and dark backgrounds. Hue and chroma
    are more stable across those themes than fixed RGB channel comparisons.
    The five masks remain separate so candle geometry can infer the bullish
    and bearish meanings for each screenshot.
    """

    image = np.asarray(rgb, dtype=np.uint8)
    if image.ndim != 3 or image.shape[2] < 3:
        empty = np.zeros(image.shape[:2], dtype=np.bool_)
        return {name: empty.copy() for name in _PALETTE_ORDER}

    image = np.ascontiguousarray(image[:, :, :3])
    hsv = cv2.cvtColor(image, cv2.COLOR_RGB2HSV)
    hue = hsv[:, :, 0]
    saturation = hsv[:, :, 1]
    value = hsv[:, :, 2]
    channels = image.astype(np.int16)
    chroma = np.max(channels, axis=2) - np.min(channels, axis=2)

    # The absolute chroma floor rejects gray grid/UI pixels. A modest floor is
    # deliberate: compressed screenshots and pale TradingView candles lose a
    # large amount of saturation while retaining their hue.
    colored = (value >= 42) & (saturation >= 38) & (chroma >= 24)
    masks = {
        "green": colored & (hue >= 34) & (hue <= 94),
        "blue": colored & (hue >= 95) & (hue <= 139),
        "red": colored & ((hue <= 7) | (hue >= 172)),
        "orange": colored & (hue >= 8) & (hue <= 25),
        "magenta": colored & (hue >= 140) & (hue <= 171),
    }
    return {name: np.asarray(masks[name], dtype=np.bool_) for name in _PALETTE_ORDER}


def _body_endpoint(row: Mapping[str, Any], side: str, *, close: bool) -> float:
    top = float(row.get("body_top_px", row.get("body_top", 0.0)) or 0.0)
    bottom = float(row.get("body_bottom_px", row.get("body_bottom", top)) or top)
    if side == "BUY":
        return top if close else bottom
    return bottom if close else top


def infer_palette_direction_map(candles: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Infer per-image palette semantics from adjacent OHLC continuity.

    A prior candle's close should be near the next candle's open. Both
    orientations are evaluated for every palette present in a coherent track.
    This uses pixels only: folder names, trade outcomes, and future labels are
    never consulted.
    """

    rows = [row for row in candles if str(row.get("palette") or "") in PALETTE_DEFAULT_SIDE]
    counts = Counter(str(row.get("palette")) for row in rows)
    if not counts:
        return dict(PALETTE_DEFAULT_SIDE)

    dominant = [name for name, count in counts.most_common() if count >= 2]
    if len(dominant) < 2:
        return dict(PALETTE_DEFAULT_SIDE)

    heights = [
        max(
            1.0,
            float(row.get("body_bottom_px", 0.0) or 0.0)
            - float(row.get("body_top_px", 0.0) or 0.0),
        )
        for row in rows
    ]
    scale = max(1.0, float(statistics.median(heights)))
    best_score = float("inf")
    best_map: dict[str, str] | None = None

    for assignment in itertools.product(("BUY", "SELL"), repeat=len(dominant)):
        if len(set(assignment)) < 2:
            continue
        candidate_map = dict(PALETTE_DEFAULT_SIDE)
        candidate_map.update(dict(zip(dominant, assignment)))
        errors: list[float] = []
        for left, right in zip(rows, rows[1:]):
            left_side = candidate_map[str(left.get("palette"))]
            right_side = candidate_map[str(right.get("palette"))]
            previous_close = _body_endpoint(left, left_side, close=True)
            next_open = _body_endpoint(right, right_side, close=False)
            errors.append(abs(previous_close - next_open) / scale)
        if not errors:
            continue
        continuity = float(statistics.median(errors)) + 0.25 * float(statistics.mean(errors))
        default_disagreement = sum(
            counts[name]
            for name in dominant
            if candidate_map[name] != PALETTE_DEFAULT_SIDE[name]
        ) / max(1, sum(counts[name] for name in dominant))
        # Only break a nearly equal geometric solution toward conventional
        # colors. Non-standard blue/green and orange/magenta assignments still
        # override the prior whenever their continuity is materially better.
        score = continuity + 0.005 * default_disagreement
        if score < best_score:
            best_score = score
            best_map = candidate_map

    return best_map or dict(PALETTE_DEFAULT_SIDE)


def _vertical_structure_mask(mask: NDArray[np.bool_]) -> NDArray[np.uint8]:
    """Remove one/two-pixel horizontal marks while preserving candle bodies."""

    source = np.asarray(mask, dtype=np.uint8)
    if not source.size:
        return source
    vertical_kernel = np.ones((3, 1), dtype=np.uint8)
    seed = cv2.morphologyEx(source, cv2.MORPH_OPEN, vertical_kernel)
    support = cv2.dilate(seed, np.ones((3, 3), dtype=np.uint8), iterations=1)
    # OpenCV's ``bitwise_and`` is not exposed by every cv2 typing stub even
    # though it is available at runtime. Both operands are same-shaped uint8
    # masks, so NumPy's equivalent operation keeps the exact mask semantics
    # while preserving a concrete ndarray type for strict static analysis.
    restored = np.bitwise_and(source, support)
    return cv2.morphologyEx(restored, cv2.MORPH_CLOSE, vertical_kernel)


def _longest_true_run(values: NDArray[np.bool_]) -> tuple[int, int]:
    best_start = 0
    best_end = -1
    start: int | None = None
    for index, enabled in enumerate(bool(value) for value in values):
        if enabled and start is None:
            start = index
        if start is not None and (not enabled or index == len(values) - 1):
            end = index if enabled and index == len(values) - 1 else index - 1
            if end - start > best_end - best_start:
                best_start, best_end = start, end
            start = None
    return best_start, best_end


def _component_candidates(
    palette: str,
    raw_mask: NDArray[np.bool_],
    *,
    x_offset: int,
    y_offset: int,
    roi_height: int,
    roi_width: int,
) -> list[dict[str, Any]]:
    filtered = _vertical_structure_mask(raw_mask)
    component_count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        filtered,
        connectivity=8,
    )
    minimum_height = max(3, int(round(roi_height * 0.0035)))
    maximum_height = max(28, int(round(roi_height * 0.30)))
    maximum_width = max(24, int(round(roi_width * 0.028)))
    output: list[dict[str, Any]] = []
    for component_id in range(1, int(component_count)):
        start, top, width, height, area = (int(value) for value in stats[component_id])
        if (
            area < max(3, minimum_height)
            or height < minimum_height
            or height > maximum_height
            or width > maximum_width
            or width > max(16, int(round(height * 3.25)))
        ):
            continue

        component = labels[top : top + height, start : start + width] == component_id
        y_values, x_values = np.where(component)
        if y_values.size < max(3, minimum_height):
            continue
        local_top = int(np.min(y_values))
        local_bottom = int(np.max(y_values))
        local_left = int(np.min(x_values))
        local_right = int(np.max(x_values))
        total_height = local_bottom - local_top + 1
        total_width = local_right - local_left + 1
        if total_height < minimum_height:
            continue

        cropped = component[local_top : local_bottom + 1, local_left : local_right + 1]
        row_counts = np.sum(cropped, axis=1)
        peak_width = int(np.max(row_counts)) if row_counts.size else 0
        if peak_width <= 0:
            continue
        dense_threshold = 1 if peak_width == 1 else max(2, int(math.ceil(peak_width * 0.50)))
        dense_start, dense_end = _longest_true_run(row_counts >= dense_threshold)
        if dense_end < dense_start:
            dense_start, dense_end = 0, total_height - 1
        body_top = top + local_top + dense_start
        body_bottom = top + local_top + dense_end
        body_height = max(1, body_bottom - body_top + 1)
        wick_top = top + local_top
        wick_bottom = top + local_bottom
        upper_wick = max(0, body_top - wick_top)
        lower_wick = max(0, wick_bottom - body_bottom)
        fill_ratio = float(y_values.size) / max(1.0, float(total_height * total_width))
        verticality = min(1.0, total_height / max(3.0, total_width * 1.35))
        body_evidence = min(1.0, peak_width / max(2.0, total_width * 0.70))
        height_evidence = min(1.0, total_height / max(7.0, roi_height * 0.045))
        parse_confidence = max(
            0.0,
            min(
                1.0,
                0.20
                + 0.25 * verticality
                + 0.20 * body_evidence
                + 0.20 * min(1.0, fill_ratio / 0.32)
                + 0.15 * height_evidence,
            ),
        )
        global_left = x_offset + start + local_left
        global_right = x_offset + start + local_right
        global_wick_top = y_offset + wick_top
        global_wick_bottom = y_offset + wick_bottom
        global_body_top = y_offset + body_top
        global_body_bottom = y_offset + body_bottom
        output.append(
            {
                "bbox": [
                    float(global_left),
                    float(global_wick_top),
                    float(global_right),
                    float(global_wick_bottom),
                ],
                "palette": palette,
                "color": palette,
                "center_x_px": 0.5 * float(global_left + global_right),
                "center_y_px": 0.5 * float(global_wick_top + global_wick_bottom),
                "wick_top_px": float(global_wick_top),
                "wick_bottom_px": float(global_wick_bottom),
                "body_top_px": float(global_body_top),
                "body_bottom_px": float(global_body_bottom),
                "body_height_pct": body_height / float(total_height),
                "upper_wick_pct": upper_wick / float(total_height),
                "lower_wick_pct": lower_wick / float(total_height),
                "parse_confidence": parse_confidence,
            }
        )
    return output


def _deduplicate_x(candidates: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted((dict(row) for row in candidates), key=lambda row: float(row["center_x_px"]))
    if len(ordered) < 2:
        return ordered
    widths = [
        max(1.0, float(row["bbox"][2]) - float(row["bbox"][0]) + 1.0)
        for row in ordered
    ]
    tolerance = max(0.75, min(2.0, float(statistics.median(widths)) * 0.28))
    groups: list[list[dict[str, Any]]] = []
    for row in ordered:
        if groups and abs(float(row["center_x_px"]) - float(groups[-1][-1]["center_x_px"])) <= tolerance:
            groups[-1].append(row)
        else:
            groups.append([row])

    output: list[dict[str, Any]] = []
    for group in groups:
        output.append(
            max(
                group,
                key=lambda row: (
                    float(row.get("parse_confidence", 0.0)),
                    float(row["bbox"][3]) - float(row["bbox"][1]),
                ),
            )
        )
    return output


def _candidate_gaps(candidates: Sequence[Mapping[str, Any]]) -> list[float]:
    if len(candidates) < 2:
        return []
    widths = [
        max(1.0, float(row["bbox"][2]) - float(row["bbox"][0]) + 1.0)
        for row in candidates
    ]
    median_width = float(statistics.median(widths))
    centers = [float(row["center_x_px"]) for row in candidates]
    minimum = max(1.5, 0.45 * median_width)
    maximum = max(30.0, 7.0 * median_width)
    gaps = [
        right - left
        for left, right in zip(centers, centers[1:])
        if minimum <= right - left <= maximum
    ]
    if not gaps:
        return []
    buckets = Counter(round(gap * 2.0) / 2.0 for gap in gaps)
    ranked = [float(gap) for gap, _count in buckets.most_common(7)]
    ranked.extend((float(statistics.median(gaps)), max(2.0, 1.25 * median_width)))
    unique: list[float] = []
    for gap in ranked:
        if gap >= 1.5 and all(abs(gap - present) > 0.35 for present in unique):
            unique.append(gap)
    return unique


def _regular_path(
    candidates: Sequence[Mapping[str, Any]],
    *,
    expected_gap: float,
    roi_height: int,
) -> tuple[list[dict[str, Any]], float]:
    rows = [dict(row) for row in candidates]
    if not rows:
        return [], float("-inf")
    body_heights = [
        max(1.0, float(row["body_bottom_px"]) - float(row["body_top_px"]) + 1.0)
        for row in rows
    ]
    maximum_y_jump = max(0.28 * roi_height, 12.0 * float(statistics.median(body_heights)))
    scores = [0.55 + float(row.get("parse_confidence", 0.0)) for row in rows]
    lengths = [1] * len(rows)
    missing = [0] * len(rows)
    previous = [-1] * len(rows)
    tolerance = max(1.15, expected_gap * 0.34)

    for index, row in enumerate(rows):
        center_x = float(row["center_x_px"])
        center_y = float(row["center_y_px"])
        for predecessor in range(index - 1, -1, -1):
            left = rows[predecessor]
            delta_x = center_x - float(left["center_x_px"])
            # A broker expiry marker, grid label, or compressed color run can
            # hide several consecutive candle bodies while the wick sequence
            # remains causal. The former four-candle ceiling split one visible
            # chart into a long historical lane and a detached live lane. Keep
            # the bridge bounded to five missing candles and retain the y-jump,
            # palette, spacing-residual, and OHLC-continuity gates below.
            if delta_x > expected_gap * 6.45:
                break
            multiple = int(round(delta_x / expected_gap))
            if multiple < 1 or multiple > 6:
                continue
            residual = abs(delta_x - multiple * expected_gap)
            if residual > tolerance:
                continue
            if abs(center_y - float(left["center_y_px"])) > maximum_y_jump:
                continue
            gain = (
                0.72
                + 0.72 * float(row.get("parse_confidence", 0.0))
                - 0.19 * (multiple - 1)
                - 0.32 * residual / expected_gap
            )
            candidate_score = scores[predecessor] + gain
            candidate_length = lengths[predecessor] + 1
            candidate_missing = missing[predecessor] + multiple - 1
            if (candidate_score, candidate_length, -candidate_missing) > (
                scores[index],
                lengths[index],
                -missing[index],
            ):
                scores[index] = candidate_score
                lengths[index] = candidate_length
                missing[index] = candidate_missing
                previous[index] = predecessor

    best = max(
        range(len(rows)),
        key=lambda index: (scores[index] + 0.12 * lengths[index] - 0.08 * missing[index], lengths[index]),
    )
    indices: list[int] = []
    cursor = best
    while cursor >= 0:
        indices.append(cursor)
        cursor = previous[cursor]
    indices.reverse()
    quality = scores[best] + 0.12 * lengths[best] - 0.08 * missing[best]
    return [rows[index] for index in indices], quality


def _select_regular_track(
    candidates: Sequence[Mapping[str, Any]],
    *,
    roi_height: int,
    minimum_track_length: int,
) -> tuple[list[dict[str, Any]], float | None]:
    rows = _deduplicate_x(candidates)
    if len(rows) < minimum_track_length:
        return rows, None
    best_track: list[dict[str, Any]] = []
    best_gap: float | None = None
    best_quality = float("-inf")
    for gap in _candidate_gaps(rows):
        track, quality = _regular_path(rows, expected_gap=gap, roi_height=roi_height)
        if (quality, len(track)) > (best_quality, len(best_track)):
            best_track = track
            best_gap = gap
            best_quality = quality
    if len(best_track) < minimum_track_length:
        return [], best_gap
    return best_track, best_gap


def _finalize_track(
    selected: list[dict[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    *,
    expected_gap: float | None,
    image_height: int,
    roi_height: int,
    minimum_track_length: int,
) -> list[dict[str, Any]]:
    if not selected:
        return []
    palette_counts = Counter(str(row.get("palette") or "") for row in selected)
    active_palettes = {name for name, _count in palette_counts.most_common(2)}
    if len(active_palettes) == 2 and len(palette_counts) > 2:
        refined, refined_gap = _select_regular_track(
            [row for row in candidates if str(row.get("palette") or "") in active_palettes],
            roi_height=roi_height,
            minimum_track_length=max(2, int(minimum_track_length)),
        )
        if len(refined) >= max(int(minimum_track_length), int(round(0.55 * len(selected)))):
            selected = refined
            expected_gap = refined_gap

    direction_map = infer_palette_direction_map(selected)
    output: list[dict[str, Any]] = []
    for index, row in enumerate(selected):
        palette = str(row.get("palette") or "green")
        side = direction_map.get(palette, PALETTE_DEFAULT_SIDE.get(palette, "BUY"))
        open_y = _body_endpoint(row, side, close=False)
        close_y = _body_endpoint(row, side, close=True)
        spacing_confidence = 1.0
        if expected_gap is not None and index > 0:
            actual_gap = float(row["center_x_px"]) - float(selected[index - 1]["center_x_px"])
            multiple = max(1, int(round(actual_gap / expected_gap)))
            residual = abs(actual_gap - multiple * expected_gap) / max(1.0, expected_gap)
            spacing_confidence = max(0.0, min(1.0, 1.0 - residual))
        parse_confidence = max(
            0.0,
            min(1.0, 0.82 * float(row.get("parse_confidence", 0.0)) + 0.18 * spacing_confidence),
        )
        result = dict(row)
        result.update(
            {
                "track_id": index,
                "direction": side,
                "open_y_px": float(open_y),
                "close_y_px": float(close_y),
                "price_proxy": max(0.0, min(1.0, 1.0 - close_y / max(1.0, float(image_height)))),
                "parse_confidence": parse_confidence,
                "spacing_confidence": spacing_confidence,
            }
        )
        output.append(result)
    return output


def extract_candle_tracks_v3(
    rgb: NDArray[np.uint8],
    *,
    roi_bounds: tuple[int, int, int, int] | None = None,
    minimum_track_length: int = 6,
) -> list[dict[str, Any]]:
    """Extract an ordered, geometry-rich candle track from an RGB array.

    ``roi_bounds`` uses half-open image coordinates ``(x0, y0, x1, y1)``.
    Every returned coordinate remains in the coordinate system of the supplied
    full image, which lets training and live capture share this exact parser.
    Horizontal/dashed annotations are removed with a vertical morphology seed;
    a regular-x dynamic program then rejects remaining text and UI components.
    """

    image = np.asarray(rgb, dtype=np.uint8)
    if image.ndim != 3 or image.shape[2] < 3 or image.shape[0] < 4 or image.shape[1] < 4:
        return []
    image = np.ascontiguousarray(image[:, :, :3])
    image_height, image_width = int(image.shape[0]), int(image.shape[1])
    if roi_bounds is None:
        x0, y0, x1, y1 = 0, 0, image_width, image_height
    else:
        raw_x0, raw_y0, raw_x1, raw_y1 = (int(value) for value in roi_bounds)
        x0 = max(0, min(image_width - 1, raw_x0))
        y0 = max(0, min(image_height - 1, raw_y0))
        x1 = max(x0 + 1, min(image_width, raw_x1))
        y1 = max(y0 + 1, min(image_height, raw_y1))
    roi = image[y0:y1, x0:x1]
    if roi.size == 0:
        return []

    palette_masks = build_candle_palette_masks(roi)
    candidates: list[dict[str, Any]] = []
    for palette, mask in palette_masks.items():
        candidates.extend(
            _component_candidates(
                palette,
                mask,
                x_offset=x0,
                y_offset=y0,
                roi_height=int(roi.shape[0]),
                roi_width=int(roi.shape[1]),
            )
        )
    return _select_coherent_palette_track(
        candidates,
        image_height=image_height,
        image_width=int(roi.shape[1]),
        roi_height=int(roi.shape[0]),
        minimum_track_length=minimum_track_length,
    )


def _track_quality(track: Sequence[Mapping[str, Any]], image_width: int) -> float:
    """Rank competing chart bands by candle continuity, not raw event count."""

    if len(track) < 2:
        return float("-inf")
    body_heights = [
        max(
            1.0,
            float(row.get("body_bottom_px", 0.0))
            - float(row.get("body_top_px", 0.0))
            + 1.0,
        )
        for row in track
    ]
    body_scale = max(1.0, float(statistics.median(body_heights)))
    continuity_errors = [
        abs(float(left.get("close_y_px", 0.0)) - float(right.get("open_y_px", 0.0)))
        for left, right in zip(track, track[1:])
    ]
    continuity = (
        float(statistics.median(continuity_errors))
        + 0.25 * float(statistics.mean(continuity_errors))
    ) / body_scale
    span = (
        float(track[-1].get("center_x_px", 0.0))
        - float(track[0].get("center_x_px", 0.0))
    ) / max(1.0, float(image_width))
    parse_quality = statistics.mean(float(row.get("parse_confidence", 0.0)) for row in track)
    return (
        math.log1p(len(track))
        + 0.55 * max(0.0, min(1.0, span))
        + 0.25 * parse_quality
        - 1.50 * continuity
    )


def _has_price_geometry_variation(track: Sequence[Mapping[str, Any]]) -> bool:
    """Reject repeated UI glyph lattices without imposing a candle width floor."""

    if len(track) < 4:
        return True
    body_heights = [
        max(
            1.0,
            float(row.get("body_bottom_px", 0.0))
            - float(row.get("body_top_px", 0.0))
            + 1.0,
        )
        for row in track
    ]
    geometry_scale = max(1.0, float(statistics.median(body_heights)))
    centers = [float(row.get("center_y_px", 0.0)) for row in track]
    body_tops = [float(row.get("body_top_px", 0.0)) for row in track]
    body_bottoms = [float(row.get("body_bottom_px", 0.0)) for row in track]
    wick_tops = [float(row.get("wick_top_px", 0.0)) for row in track]
    wick_bottoms = [float(row.get("wick_bottom_px", 0.0)) for row in track]
    vertical_spread = max(
        max(centers) - min(centers),
        max(body_tops) - min(body_tops),
        max(body_bottoms) - min(body_bottoms),
        max(wick_tops) - min(wick_tops),
        max(wick_bottoms) - min(wick_bottoms),
    )
    height_spread = max(body_heights) - min(body_heights)
    return (
        vertical_spread >= max(2.0, 0.35 * geometry_scale)
        or height_spread >= max(1.0, 0.20 * geometry_scale)
    )


def _select_coherent_palette_track(
    candidates: Sequence[Mapping[str, Any]],
    *,
    image_height: int,
    image_width: int,
    roi_height: int,
    minimum_track_length: int,
) -> list[dict[str, Any]]:
    """Choose a candle-color hypothesis before accepting a regular x lattice.

    Chart chrome can contain hundreds of narrow, regularly spaced colored
    strokes. If every palette is passed to the x-spacing dynamic program at
    once, raw lattice length can overwhelm a shorter real candle sequence.
    Candle colors, however, form a coherent two-state OHLC stream: after the
    per-image direction assignment, one candle's close stays near the next
    candle's open. Evaluate every observed palette pair as its own hypothesis,
    require both colors to occur in the selected stream, and rank completed
    tracks by that continuity. Width is deliberately *not* a hard gate because
    legitimate compressed blue/red and blue/green suites can be one pixel wide.
    """

    rows = [dict(row) for row in candidates]
    required_length = max(2, int(minimum_track_length))
    if not rows:
        return []

    palette_counts = Counter(
        str(row.get("palette") or "")
        for row in rows
        if str(row.get("palette") or "") in PALETTE_DEFAULT_SIDE
    )
    present_palettes = [
        palette
        for palette in _PALETTE_ORDER
        if palette_counts.get(palette, 0) >= 2
    ]
    pair_tracks: list[tuple[float, list[dict[str, Any]]]] = []
    for left_palette, right_palette in itertools.combinations(present_palettes, 2):
        pair = {left_palette, right_palette}
        pair_candidates = [
            row for row in rows if str(row.get("palette") or "") in pair
        ]
        selected, expected_gap = _select_regular_track(
            pair_candidates,
            roi_height=roi_height,
            minimum_track_length=required_length,
        )
        if len(selected) < required_length:
            continue
        selected_counts = Counter(str(row.get("palette") or "") for row in selected)
        # A stray colored glyph must not turn a one-color UI lattice into a
        # supposed candle pair. The fractional floor scales for long live
        # tracks while retaining short or strongly trending candle sequences.
        minimum_pair_support = max(2, int(math.ceil(0.04 * len(selected))))
        if any(selected_counts.get(palette, 0) < minimum_pair_support for palette in pair):
            continue
        track = _finalize_track(
            selected,
            pair_candidates,
            expected_gap=expected_gap,
            image_height=image_height,
            roi_height=roi_height,
            minimum_track_length=minimum_track_length,
        )
        if len(track) < required_length or not _has_price_geometry_variation(track):
            continue
        pair_tracks.append((_track_quality(track, image_width), track))

    if pair_tracks:
        _score, selected_pair = max(
            pair_tracks,
            key=lambda item: (item[0], len(item[1])),
        )
        return selected_pair

    # Single-color runs and very short minority-color trends remain valid.
    # They use the prior all-palette behavior only when no genuine two-color
    # hypothesis survived the participation invariant above.
    selected, expected_gap = _select_regular_track(
        rows,
        roi_height=roi_height,
        minimum_track_length=required_length,
    )
    fallback = _finalize_track(
        selected,
        rows,
        expected_gap=expected_gap,
        image_height=image_height,
        roi_height=roi_height,
        minimum_track_length=minimum_track_length,
    )
    fallback_palettes = {
        str(row.get("palette") or "") for row in fallback
    }
    if len(fallback_palettes) >= 2 and not _has_price_geometry_variation(fallback):
        return []
    return fallback


def extract_candle_tracks_adaptive_v3(
    rgb: NDArray[np.uint8],
    *,
    x_bounds: tuple[float, float] = (0.0, 0.92),
    top_ratio: float = 0.05,
    bottom_candidates: Sequence[float] = (0.62, 0.68, 0.74, 0.80, 0.86, 0.92, 0.96),
    minimum_track_length: int = 6,
) -> list[dict[str, Any]]:
    """Select the most coherent candle band from chart screenshots with UI.

    MT4/MT5 captures frequently place symbol grids, terminal tabs, or trade
    controls below the chart. Those controls can be regularly spaced and use
    the same colors as candles. We therefore evaluate nested, causal image
    bands and select by OHLC close-to-next-open continuity. Merely detecting
    more colored components can never win over a coherent candle path.
    """

    image = np.asarray(rgb, dtype=np.uint8)
    if image.ndim != 3 or image.shape[2] < 3 or image.shape[0] < 4 or image.shape[1] < 4:
        return []
    height, width = int(image.shape[0]), int(image.shape[1])
    left_ratio, right_ratio = sorted(float(value) for value in x_bounds)
    x0 = max(0, min(width - 1, int(round(width * max(0.0, left_ratio)))))
    x1 = max(x0 + 1, min(width, int(round(width * min(1.0, right_ratio)))))
    y0 = max(0, min(height - 1, int(round(height * max(0.0, min(0.80, top_ratio))))))
    ratios = [
        ratio
        for ratio in sorted(set(float(value) for value in bottom_candidates))
        if ratio > top_ratio
    ]
    if not ratios:
        return []
    maximum_y1 = max(y0 + 1, min(height, int(round(height * min(1.0, max(ratios))))))
    roi = np.ascontiguousarray(image[y0:maximum_y1, x0:x1, :3])
    raw_candidates: list[dict[str, Any]] = []
    for palette, mask in build_candle_palette_masks(roi).items():
        raw_candidates.extend(
            _component_candidates(
                palette,
                mask,
                x_offset=x0,
                y_offset=y0,
                roi_height=int(roi.shape[0]),
                roi_width=int(roi.shape[1]),
            )
        )
    competing_tracks: list[tuple[float, list[dict[str, Any]]]] = []
    for bottom_ratio in ratios:
        if bottom_ratio <= top_ratio:
            continue
        y1 = max(y0 + 1, min(height, int(round(height * min(1.0, bottom_ratio)))))
        band_candidates = [
            row
            for row in raw_candidates
            if float(cast(Sequence[Any], row["bbox"])[1]) >= y0
            and float(cast(Sequence[Any], row["bbox"])[3]) < y1
        ]
        track = _select_coherent_palette_track(
            band_candidates,
            image_height=height,
            image_width=x1 - x0,
            roi_height=y1 - y0,
            minimum_track_length=minimum_track_length,
        )
        if len(track) >= max(2, int(minimum_track_length)):
            competing_tracks.append((_track_quality(track, x1 - x0), track))
    if not competing_tracks:
        return []
    _score, selected = max(competing_tracks, key=lambda item: (item[0], len(item[1])))
    return selected


__all__ = [
    "PALETTE_DEFAULT_SIDE",
    "build_candle_palette_masks",
    "extract_candle_tracks_adaptive_v3",
    "extract_candle_tracks_v3",
    "infer_palette_direction_map",
]
