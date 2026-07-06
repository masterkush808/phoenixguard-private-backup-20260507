from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence, cast


SCHEMA_VERSION = "PG_PLAYBOOK_WALK_FORWARD_SCORER_V1"
DEFAULT_HORIZONS_SEC = (60, 300, 600, 900, 1800, 3600)
DEFAULT_TRUTH_WINDOW_SEC = 900.0
DEFAULT_TRUTH_WINDOW_SEQ = 24
DEFAULT_MIN_MOVE_PX = 3.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(cast(Sequence[Any], value))
    return []


def text(value: Any, default: str = "") -> str:
    raw = str(value if value is not None else default).strip()
    return raw or default


def side(value: Any, default: str = "") -> str:
    raw = text(value, default).upper()
    return raw if raw in {"BUY", "SELL"} else default


def number(value: Any, default: float | None = None) -> float | None:
    if value in (None, "", [], {}):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(parsed):
        return default
    return parsed


def bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    raw = text(value).lower()
    return raw in {"1", "true", "yes", "on", "pass", "fresh", "allowed"}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            raw = line.strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            row = mapping(payload)
            if row:
                rows.append(row)
    return rows


def write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), indent=2, ensure_ascii=True, default=str) + "\n", encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(dict(row), ensure_ascii=True, default=str, separators=(",", ":")) + "\n")


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def sample_seq(sample: Mapping[str, Any]) -> int:
    return int(number(sample.get("seq"), 0.0) or 0)


def sample_frame(sample: Mapping[str, Any]) -> int:
    frames = mapping(sample.get("frames"))
    return int(number(frames.get("display_frame_id") or sample.get("frame") or sample.get("frame_id"), 0.0) or 0)


def sample_epoch(sample: Mapping[str, Any]) -> float:
    return float(number(sample.get("captured_epoch") or sample.get("created_epoch") or sample.get("epoch"), 0.0) or 0.0)


def sample_price_y(sample: Mapping[str, Any]) -> float | None:
    price_proxy = mapping(sample.get("price_proxy"))
    return number(price_proxy.get("current_y") or sample.get("current_y") or sample.get("price_y"))


def sample_fresh(sample: Mapping[str, Any]) -> bool:
    freshness = mapping(sample.get("freshness"))
    if not freshness:
        return True
    return bool_value(freshness.get("fresh"))


def candle_context(sample: Mapping[str, Any]) -> dict[str, Any]:
    return mapping(sample.get("candle_movement_context_v3") or sample.get("candle_movement"))


def current_leg(sample: Mapping[str, Any]) -> dict[str, Any]:
    return mapping(candle_context(sample).get("current_leg"))


def candidate_side_from_label(label: str, fallback: str = "") -> str:
    upper = label.upper()
    if " BUY" in f" {upper} " or upper.endswith("BUY") or "SNIPER BUY" in upper:
        return "BUY"
    if " SELL" in f" {upper} " or upper.endswith("SELL") or "SNIPER SELL" in upper:
        return "SELL"
    return side(fallback)


def truth_kind_from_label(label: str) -> str:
    upper = label.upper().replace("_", " ")
    if "WOULD HAVE ENTERED" in upper or "WOULD HAVE ENTER" in upper or "IDEAL ENTRY" in upper:
        return "WOULD_HAVE_ENTERED"
    if "WOULD HAVE EXITED" in upper or "WOULD HAVE EXIT" in upper or "IDEAL EXIT" in upper:
        return "WOULD_HAVE_EXITED"
    if "SNIPER BUY" in upper or "SNIPER SELL" in upper:
        return "SNIPER_ZONE"
    return ""


def bbox_center_y(value: Any) -> float | None:
    values = sequence(value)
    if len(values) < 4:
        return None
    y0 = number(values[1])
    y1 = number(values[3])
    if y0 is None or y1 is None:
        return None
    return (float(y0) + float(y1)) / 2.0


def row_point_y(row: Mapping[str, Any]) -> float | None:
    for key in ("current_y", "entry_y", "center_y", "line_y", "price_y", "y"):
        value = number(row.get(key))
        if value is not None:
            return value
    return bbox_center_y(row.get("bbox") or row.get("box") or row.get("bounds") or row.get("rect"))


def recursive_mappings(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, Mapping):
        current = dict(cast(Mapping[str, Any], value))
        yield current
        for nested in current.values():
            yield from recursive_mappings(nested)
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for item in cast(Sequence[Any], value):
            yield from recursive_mappings(item)


def explicit_truth_markers(payload: Any, source: str) -> list[dict[str, Any]]:
    root = mapping(payload)
    marker_values: list[Any] = []
    if isinstance(payload, list):
        marker_values = list(cast(list[Any], payload))
    elif isinstance(root.get("markers"), list):
        marker_values = sequence(root.get("markers"))
    elif isinstance(root.get("truth_markers"), list):
        marker_values = sequence(root.get("truth_markers"))
    elif root:
        marker_values = [root]
    markers: list[dict[str, Any]] = []
    for index, value in enumerate(marker_values):
        row = mapping(value)
        if not row:
            continue
        raw_label = text(row.get("label") or row.get("display_label") or row.get("short_label") or row.get("kind") or row.get("type"))
        kind = text(row.get("kind") or truth_kind_from_label(raw_label)).upper()
        if kind not in {"WOULD_HAVE_ENTERED", "WOULD_HAVE_EXITED", "SNIPER_ZONE"}:
            continue
        marker_side = candidate_side_from_label(raw_label, side(row.get("side") or row.get("direction")))
        markers.append(
            {
                "schema_version": "PG_REPLAY_TRUTH_MARKER_V1",
                "source": source,
                "source_index": index,
                "kind": kind,
                "side": marker_side,
                "label": raw_label or kind,
                "seq": int(number(row.get("seq") or row.get("sample_seq"), 0.0) or 0),
                "frame": int(number(row.get("frame") or row.get("frame_id") or row.get("display_frame_id"), 0.0) or 0),
                "epoch": float(number(row.get("epoch") or row.get("captured_epoch") or row.get("created_epoch"), 0.0) or 0.0),
                "y": row_point_y(row),
                "payload": row,
            }
        )
    return markers


def extracted_truth_markers(payload: Any, source: str) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for index, row in enumerate(recursive_mappings(payload)):
        label = text(
            row.get("label")
            or row.get("display_label")
            or row.get("short_label")
            or row.get("text")
            or row.get("name")
            or row.get("kind")
            or row.get("type")
        )
        kind = truth_kind_from_label(label)
        if not kind:
            continue
        marker_side = candidate_side_from_label(label, side(row.get("side") or row.get("direction")))
        markers.append(
            {
                "schema_version": "PG_REPLAY_TRUTH_MARKER_V1",
                "source": source,
                "source_index": index,
                "kind": kind,
                "side": marker_side,
                "label": label,
                "seq": int(number(row.get("seq") or row.get("sample_seq"), 0.0) or 0),
                "frame": int(number(row.get("frame") or row.get("frame_id") or row.get("display_frame_id"), 0.0) or 0),
                "epoch": float(number(row.get("epoch") or row.get("captured_epoch") or row.get("created_epoch"), 0.0) or 0.0),
                "y": row_point_y(row),
                "payload": {
                    "source_path": text(row.get("source_path")),
                    "bbox": row.get("bbox") or row.get("box") or row.get("bounds") or row.get("rect"),
                    "anchor_candle_indices": row.get("anchor_candle_indices"),
                    "contained_candle_indices": row.get("contained_candle_indices"),
                },
            }
        )
    return markers


def load_truth_markers(paths: Sequence[Path], raw_dir: Path | None = None) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            continue
        if path.suffix.lower() == ".jsonl":
            for row in load_jsonl(path):
                markers.extend(explicit_truth_markers(row, str(path)))
                markers.extend(extracted_truth_markers(row, str(path)))
        else:
            payload = load_json(path)
            markers.extend(explicit_truth_markers(payload, str(path)))
            markers.extend(extracted_truth_markers(payload, str(path)))
    if raw_dir and raw_dir.exists():
        for path in sorted(raw_dir.rglob("*.json")):
            try:
                payload = load_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            markers.extend(extracted_truth_markers(payload, str(path)))
    unique: dict[str, dict[str, Any]] = {}
    for marker in markers:
        normalized_label = text(marker.get("label")).upper().replace("_", " ")
        key = "|".join(
            [
                text(marker.get("kind")),
                text(marker.get("side")),
                normalized_label,
                str(marker.get("seq") or ""),
                str(marker.get("frame") or ""),
                str(marker.get("epoch") or ""),
                text(marker.get("source")),
            ]
        )
        unique[key] = marker
    return list(unique.values())


@dataclass(frozen=True, slots=True)
class PackageCandidate:
    source: str
    seq: int
    frame: int
    epoch: float
    side: str
    packet_id: str
    lane: str
    allowed: bool
    execution_authorized: bool
    blocked_by: str
    timing_mode: str
    maturity: str
    score: float | None
    expected_move_time: dict[str, Any]
    sample: dict[str, Any]
    event: dict[str, Any]


def find_sample_by_seq(samples: Sequence[dict[str, Any]], seq: int) -> dict[str, Any]:
    for sample in samples:
        if sample_seq(sample) == seq:
            return sample
    return {}


def package_candidates(
    samples: Sequence[dict[str, Any]],
    events: Sequence[dict[str, Any]],
    *,
    include_blocked_trend_study: bool = False,
) -> list[PackageCandidate]:
    candidates: list[PackageCandidate] = []
    for event in events:
        entry = mapping(event.get("entry"))
        event_seq = int(number(event.get("seq"), 0.0) or 0)
        sample = find_sample_by_seq(samples, event_seq)
        included = bool(entry.get("allowed") or entry.get("execution_authorized") or event.get("manual_alert_allowed"))
        if not included and include_blocked_trend_study:
            included = bool(entry.get("blocked_trend_aligned_study"))
        if not included:
            continue
        candidate_side = side(entry.get("side") or mapping(sample.get("entry")).get("side"))
        if candidate_side not in {"BUY", "SELL"}:
            continue
        candidates.append(
            PackageCandidate(
                source="entry_events",
                seq=event_seq,
                frame=int(number(event.get("frame") or sample_frame(sample), 0.0) or 0),
                epoch=float(number(event.get("captured_epoch") or sample_epoch(sample), 0.0) or 0.0),
                side=candidate_side,
                packet_id=text(entry.get("packet_id")),
                lane=text(entry.get("lane_name"), "UNKNOWN").upper(),
                allowed=bool(entry.get("allowed") or event.get("manual_alert_allowed")),
                execution_authorized=bool(entry.get("execution_authorized")),
                blocked_by=text(entry.get("blocked_by")),
                timing_mode=text(entry.get("timing_mode")),
                maturity=text(entry.get("playbook_state") or entry.get("opportunity_maturity_state") or entry.get("maturity")),
                score=number(entry.get("final_score") or entry.get("score")),
                expected_move_time=mapping(entry.get("expected_move_time")),
                sample=sample,
                event=event,
            )
        )
    return candidates


def favorable_move(side_value: str, start_y: float, future_y: float) -> float:
    if side_value == "BUY":
        return start_y - future_y
    if side_value == "SELL":
        return future_y - start_y
    return 0.0


def verdict_for_move(side_value: str, start_y: float, future_y: float, min_move_px: float) -> str:
    favorable = favorable_move(side_value, start_y, future_y)
    if abs(favorable) < min_move_px:
        return "flat"
    return "correct" if favorable > 0 else "wrong"


def future_sample_at(
    samples: Sequence[dict[str, Any]],
    start_epoch: float,
    horizon_sec: int,
    *,
    include_stale: bool,
) -> dict[str, Any]:
    target = start_epoch + float(horizon_sec)
    for sample in samples:
        if not include_stale and not sample_fresh(sample):
            continue
        if sample_epoch(sample) >= target:
            return sample
    return {}


def samples_after(
    samples: Sequence[dict[str, Any]],
    start_epoch: float,
    horizon_sec: int,
    *,
    include_stale: bool,
) -> list[dict[str, Any]]:
    end = start_epoch + float(horizon_sec)
    # Burn samples are captured on a poll interval, so the first real horizon
    # observation often lands a second or two after the exact target time.
    end_with_poll_grace = end + 5.0
    return [
        sample
        for sample in samples
        if sample_epoch(sample) >= start_epoch
        and sample_epoch(sample) <= end_with_poll_grace
        and (include_stale or sample_fresh(sample))
        and sample_price_y(sample) is not None
    ]


def nearest_truth_marker(
    candidate: PackageCandidate,
    markers: Sequence[dict[str, Any]],
    *,
    truth_window_sec: float,
    truth_window_seq: int,
) -> dict[str, Any]:
    best: dict[str, Any] = {}
    best_distance = float("inf")
    for marker in markers:
        if text(marker.get("kind")).upper() != "WOULD_HAVE_ENTERED":
            continue
        marker_side = side(marker.get("side"))
        if marker_side and marker_side != candidate.side:
            continue
        distance = float("inf")
        marker_epoch = float(number(marker.get("epoch"), 0.0) or 0.0)
        marker_seq = int(number(marker.get("seq"), 0.0) or 0)
        if marker_epoch > 0.0 and candidate.epoch > 0.0:
            distance = abs(candidate.epoch - marker_epoch)
            if distance > truth_window_sec:
                continue
        elif marker_seq > 0 and candidate.seq > 0:
            seq_distance = abs(candidate.seq - marker_seq)
            if seq_distance > truth_window_seq:
                continue
            distance = float(seq_distance)
        else:
            continue
        if distance < best_distance:
            best = marker
            best_distance = distance
    return best


def opposite_truth_near(
    candidate: PackageCandidate,
    markers: Sequence[dict[str, Any]],
    *,
    truth_window_sec: float,
    truth_window_seq: int,
) -> dict[str, Any]:
    opposite = "SELL" if candidate.side == "BUY" else "BUY"
    probe = PackageCandidate(
        source=candidate.source,
        seq=candidate.seq,
        frame=candidate.frame,
        epoch=candidate.epoch,
        side=opposite,
        packet_id=candidate.packet_id,
        lane=candidate.lane,
        allowed=candidate.allowed,
        execution_authorized=candidate.execution_authorized,
        blocked_by=candidate.blocked_by,
        timing_mode=candidate.timing_mode,
        maturity=candidate.maturity,
        score=candidate.score,
        expected_move_time=candidate.expected_move_time,
        sample=candidate.sample,
        event=candidate.event,
    )
    return nearest_truth_marker(probe, markers, truth_window_sec=truth_window_sec, truth_window_seq=truth_window_seq)


def score_candidate(
    candidate: PackageCandidate,
    samples: Sequence[dict[str, Any]],
    markers: Sequence[dict[str, Any]],
    *,
    horizons_sec: Sequence[int],
    include_stale: bool,
    min_move_px: float,
    truth_window_sec: float,
    truth_window_seq: int,
) -> dict[str, Any]:
    start = candidate.sample or find_sample_by_seq(samples, candidate.seq)
    start_y = sample_price_y(start)
    start_epoch = candidate.epoch or sample_epoch(start)
    if start_y is None or start_epoch <= 0.0:
        return {
            "candidate": candidate_summary(candidate),
            "score_status": "INSUFFICIENT_START_SAMPLE",
            "reason": "missing start price proxy or epoch",
        }
    horizon_rows: list[dict[str, Any]] = []
    for horizon in horizons_sec:
        future = future_sample_at(samples, start_epoch, horizon, include_stale=include_stale)
        future_y = sample_price_y(future)
        if not future or future_y is None:
            horizon_rows.append(
                {
                    "horizon_sec": horizon,
                    "verdict": "insufficient_future",
                    "future_seq": None,
                    "future_frame": None,
                    "future_y": None,
                    "favorable_px": None,
                }
            )
            continue
        favorable = favorable_move(candidate.side, float(start_y), float(future_y))
        horizon_rows.append(
            {
                "horizon_sec": horizon,
                "verdict": verdict_for_move(candidate.side, float(start_y), float(future_y), min_move_px),
                "future_seq": sample_seq(future),
                "future_frame": sample_frame(future),
                "future_y": round(float(future_y), 4),
                "favorable_px": round(float(favorable), 4),
            }
        )
    max_horizon = max(horizons_sec) if horizons_sec else 0
    path_samples = samples_after(samples, start_epoch, max_horizon, include_stale=include_stale)
    favorable_values: list[float] = []
    adverse_values: list[float] = []
    first_favorable_sec: float | None = None
    for sample in path_samples:
        y = sample_price_y(sample)
        if y is None:
            continue
        fav = favorable_move(candidate.side, float(start_y), float(y))
        favorable_values.append(max(0.0, fav))
        adverse_values.append(max(0.0, -fav))
        if first_favorable_sec is None and fav >= min_move_px:
            first_favorable_sec = max(0.0, sample_epoch(sample) - start_epoch)
    truth = nearest_truth_marker(
        candidate,
        markers,
        truth_window_sec=truth_window_sec,
        truth_window_seq=truth_window_seq,
    )
    opposite_truth = opposite_truth_near(
        candidate,
        markers,
        truth_window_sec=truth_window_sec,
        truth_window_seq=truth_window_seq,
    )
    leg = current_leg(start)
    mfe = max(favorable_values) if favorable_values else 0.0
    mae = max(adverse_values) if adverse_values else 0.0
    return {
        "schema_version": "PG_PLAYBOOK_WALK_FORWARD_CANDIDATE_SCORE_V1",
        "candidate": candidate_summary(candidate),
        "score_status": "SCORED",
        "start": {
            "seq": sample_seq(start),
            "frame": sample_frame(start),
            "epoch": start_epoch,
            "price_y": round(float(start_y), 4),
            "fresh": sample_fresh(start),
            "current_leg": {
                "side": side(leg.get("side")),
                "candle_count": int(number(leg.get("candle_count"), 0.0) or 0),
                "stage": text(leg.get("move_stage")),
                "duration": mapping(leg.get("duration")),
            },
        },
        "horizons": horizon_rows,
        "path": {
            "sample_count": len(path_samples),
            "mfe_px": round(float(mfe), 4),
            "mae_px": round(float(mae), 4),
            "mfe_mae_ratio": round(float(mfe / max(mae, 1e-9)), 4) if path_samples else 0.0,
            "first_favorable_sec": round(float(first_favorable_sec), 3) if first_favorable_sec is not None else None,
        },
        "replay_truth": {
            "aligned": bool(truth),
            "nearest_same_side": truth_summary(truth),
            "opposite_truth_near": truth_summary(opposite_truth),
            "alignment_status": "ALIGNED_TO_REPLAY_ENTRY"
            if truth
            else ("OPPOSITE_REPLAY_ENTRY_NEAR" if opposite_truth else "NO_REPLAY_ENTRY_NEAR"),
        },
    }


def candidate_summary(candidate: PackageCandidate) -> dict[str, Any]:
    return {
        "source": candidate.source,
        "seq": candidate.seq,
        "frame": candidate.frame,
        "epoch": candidate.epoch,
        "side": candidate.side,
        "packet_id": candidate.packet_id,
        "lane": candidate.lane,
        "allowed": candidate.allowed,
        "execution_authorized": candidate.execution_authorized,
        "blocked_by": candidate.blocked_by,
        "timing_mode": candidate.timing_mode,
        "maturity": candidate.maturity,
        "score": candidate.score,
        "expected_move_time": candidate.expected_move_time,
    }


def truth_summary(marker: Mapping[str, Any]) -> dict[str, Any]:
    if not marker:
        return {}
    return {
        "kind": text(marker.get("kind")),
        "side": side(marker.get("side")),
        "label": text(marker.get("label")),
        "seq": int(number(marker.get("seq"), 0.0) or 0),
        "frame": int(number(marker.get("frame"), 0.0) or 0),
        "epoch": float(number(marker.get("epoch"), 0.0) or 0.0),
        "source": text(marker.get("source")),
    }


def missed_truth_markers(
    candidates: Sequence[PackageCandidate],
    markers: Sequence[dict[str, Any]],
    *,
    truth_window_sec: float,
    truth_window_seq: int,
) -> list[dict[str, Any]]:
    missed: list[dict[str, Any]] = []
    for marker in markers:
        if text(marker.get("kind")).upper() != "WOULD_HAVE_ENTERED":
            continue
        marker_side = side(marker.get("side"))
        found = False
        for candidate in candidates:
            if marker_side and candidate.side != marker_side:
                continue
            candidate_marker = nearest_truth_marker(
                candidate,
                [marker],
                truth_window_sec=truth_window_sec,
                truth_window_seq=truth_window_seq,
            )
            if candidate_marker:
                found = True
                break
        if not found:
            missed.append(truth_summary(marker))
    return missed


def score_burn(
    *,
    burn_dir: Path,
    samples_path: Path,
    entries_path: Path,
    truth_paths: Sequence[Path],
    raw_dir: Path | None,
    out_dir: Path,
    horizons_sec: Sequence[int],
    include_stale: bool,
    include_blocked_trend_study: bool,
    min_move_px: float,
    truth_window_sec: float,
    truth_window_seq: int,
) -> dict[str, Any]:
    samples_all = load_jsonl(samples_path)
    entries = load_jsonl(entries_path)
    samples = [sample for sample in samples_all if include_stale or sample_fresh(sample)]
    markers = load_truth_markers(truth_paths, raw_dir=raw_dir)
    candidates = package_candidates(samples, entries, include_blocked_trend_study=include_blocked_trend_study)
    scored = [
        score_candidate(
            candidate,
            samples,
            markers,
            horizons_sec=horizons_sec,
            include_stale=include_stale,
            min_move_px=min_move_px,
            truth_window_sec=truth_window_sec,
            truth_window_seq=truth_window_seq,
        )
        for candidate in candidates
    ]
    horizon_counts: dict[str, dict[str, int]] = {}
    for horizon in horizons_sec:
        verdicts: Counter[str] = Counter()
        for row in scored:
            for horizon_row in sequence(mapping(row).get("horizons")):
                payload = mapping(horizon_row)
                if int(number(payload.get("horizon_sec"), -1.0) or -1) == int(horizon):
                    verdicts[text(payload.get("verdict"), "unknown")] += 1
        horizon_counts[str(horizon)] = dict(verdicts)
    alignment_counts = Counter(
        text(mapping(mapping(row).get("replay_truth")).get("alignment_status"), "NOT_SCORED")
        for row in scored
        if mapping(row).get("score_status") == "SCORED"
    )
    lane_counts = Counter(text(mapping(mapping(row).get("candidate")).get("lane"), "UNKNOWN") for row in scored)
    side_counts = Counter(text(mapping(mapping(row).get("candidate")).get("side"), "UNKNOWN") for row in scored)
    missed = missed_truth_markers(
        candidates,
        markers,
        truth_window_sec=truth_window_sec,
        truth_window_seq=truth_window_seq,
    )
    summary = {
        "schema_version": SCHEMA_VERSION,
        "created_at_utc": utc_now(),
        "burn_dir": str(burn_dir),
        "samples_path": str(samples_path),
        "entries_path": str(entries_path),
        "truth_paths": [str(path) for path in truth_paths],
        "raw_dir": str(raw_dir) if raw_dir else "",
        "sample_count": len(samples_all),
        "fresh_scored_sample_count": len(samples),
        "stale_excluded_sample_count": len(samples_all) - len(samples),
        "entry_event_count": len(entries),
        "candidate_count": len(candidates),
        "truth_marker_count": len(markers),
        "missed_truth_entry_count": len(missed),
        "horizon_counts": horizon_counts,
        "alignment_counts": dict(alignment_counts),
        "lane_counts": dict(lane_counts),
        "side_counts": dict(side_counts),
        "settings": {
            "horizons_sec": list(horizons_sec),
            "include_stale": include_stale,
            "include_blocked_trend_study": include_blocked_trend_study,
            "min_move_px": min_move_px,
            "truth_window_sec": truth_window_sec,
            "truth_window_seq": truth_window_seq,
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "walk_forward_summary.json", summary)
    write_jsonl(out_dir / "walk_forward_scores.jsonl", [mapping(row) for row in scored])
    write_jsonl(out_dir / "truth_markers.jsonl", markers)
    write_jsonl(out_dir / "missed_truth_entries.jsonl", missed)
    write_text(out_dir / "walk_forward_report.md", render_report(summary, scored, missed))
    return {
        "summary": summary,
        "scores": scored,
        "truth_markers": markers,
        "missed_truth_entries": missed,
        "out_dir": str(out_dir),
    }


def render_report(summary: Mapping[str, Any], scores: Sequence[Mapping[str, Any]], missed: Sequence[Mapping[str, Any]]) -> str:
    lines = [
        "# PhoenixGuard Playbook Replay / Walk-Forward Score",
        "",
        f"Generated: {summary.get('created_at_utc')}",
        f"Burn: `{summary.get('burn_dir')}`",
        "",
        "## Scope",
        "",
        "- This is a concept-validation and decision-quality scorecard.",
        "- It does not claim profitability; it checks whether package timing, replay truth, and future price-proxy movement align.",
        "- Stale samples are excluded unless the scorer is run with `--include-stale`.",
        "",
        "## Counts",
        "",
        f"- Samples: {summary.get('sample_count')}",
        f"- Fresh scored samples: {summary.get('fresh_scored_sample_count')}",
        f"- Stale excluded samples: {summary.get('stale_excluded_sample_count')}",
        f"- Entry events: {summary.get('entry_event_count')}",
        f"- Scored candidates: {summary.get('candidate_count')}",
        f"- Replay truth markers: {summary.get('truth_marker_count')}",
        f"- Missed replay entry markers: {summary.get('missed_truth_entry_count')}",
        "",
        "## Horizon Verdicts",
        "",
    ]
    for horizon, counts in mapping(summary.get("horizon_counts")).items():
        lines.append(f"- {horizon}s: {counts}")
    lines.extend(["", "## Replay Alignment", ""])
    for key, count in mapping(summary.get("alignment_counts")).items():
        lines.append(f"- {key}: {count}")
    lines.extend(["", "## Candidate Highlights", ""])
    for row in list(scores)[:40]:
        candidate = mapping(row.get("candidate"))
        path = mapping(row.get("path"))
        truth = mapping(row.get("replay_truth"))
        lines.append(
            "- seq {seq} frame {frame} {side} lane={lane} status={status} "
            "MFE={mfe} MAE={mae} truth={truth_status}".format(
                seq=candidate.get("seq"),
                frame=candidate.get("frame"),
                side=candidate.get("side"),
                lane=candidate.get("lane"),
                status=row.get("score_status"),
                mfe=path.get("mfe_px"),
                mae=path.get("mae_px"),
                truth_status=truth.get("alignment_status"),
            )
        )
    if len(scores) > 40:
        lines.append(f"- ... {len(scores) - 40} more candidates in walk_forward_scores.jsonl")
    lines.extend(["", "## Missed Replay Truth Entries", ""])
    if not missed:
        lines.append("- None detected from available truth markers.")
    for marker in missed[:40]:
        lines.append(
            f"- {marker.get('side')} {marker.get('label')} seq={marker.get('seq')} frame={marker.get('frame')} source={marker.get('source')}"
        )
    if len(missed) > 40:
        lines.append(f"- ... {len(missed) - 40} more missed markers in missed_truth_entries.jsonl")
    lines.append("")
    return "\n".join(lines)


def parse_horizons(raw: str) -> tuple[int, ...]:
    values: list[int] = []
    for item in str(raw or "").replace(";", ",").split(","):
        token = item.strip()
        if not token:
            continue
        values.append(int(float(token)))
    return tuple(values or DEFAULT_HORIZONS_SEC)


def default_burn_dir() -> Path:
    runtime = Path("runtime/live/hardening_studies")
    candidates: list[Path] = []
    if runtime.exists():
        candidates.extend(path for path in runtime.iterdir() if path.is_dir())
    orchestrated = Path(".codex_runtime/burn_orchestration")
    if orchestrated.exists():
        candidates.extend(path / "entry_allowance_burn" for path in orchestrated.iterdir() if (path / "entry_allowance_burn").exists())
    if not candidates:
        return Path(".")
    return max(candidates, key=lambda path: path.stat().st_mtime)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score PhoenixGuard playbook replay truth and walk-forward package outcomes.")
    parser.add_argument("--burn-dir", default="", help="Burn directory containing samples.jsonl and entry_events.jsonl.")
    parser.add_argument("--samples", default="", help="Explicit samples.jsonl path.")
    parser.add_argument("--entries", default="", help="Explicit entry_events.jsonl path.")
    parser.add_argument("--truth", action="append", default=[], help="Replay truth JSON/JSONL file. Can be repeated.")
    parser.add_argument("--raw-dir", default="", help="Raw live JSON directory to scan for WOULD HAVE ENTERED/EXITED labels.")
    parser.add_argument("--out-dir", default="", help="Output directory for the scoring report.")
    parser.add_argument("--horizons-sec", default=",".join(str(value) for value in DEFAULT_HORIZONS_SEC))
    parser.add_argument("--include-stale", action="store_true", help="Score stale samples too. Default excludes stale samples.")
    parser.add_argument("--include-blocked-trend-study", action="store_true", help="Also score blocked trend-aligned study events.")
    parser.add_argument("--min-move-px", type=float, default=DEFAULT_MIN_MOVE_PX)
    parser.add_argument("--truth-window-sec", type=float, default=DEFAULT_TRUTH_WINDOW_SEC)
    parser.add_argument("--truth-window-seq", type=int, default=DEFAULT_TRUTH_WINDOW_SEQ)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    burn_dir = Path(args.burn_dir) if args.burn_dir else default_burn_dir()
    samples_path = Path(args.samples) if args.samples else burn_dir / "samples.jsonl"
    entries_path = Path(args.entries) if args.entries else burn_dir / "entry_events.jsonl"
    raw_dir = Path(args.raw_dir) if args.raw_dir else (burn_dir / "raw" if (burn_dir / "raw").exists() else None)
    truth_paths = [Path(item) for item in sequence(args.truth)]
    out_dir = Path(args.out_dir) if args.out_dir else burn_dir / "playbook_walk_forward_score"
    result = score_burn(
        burn_dir=burn_dir,
        samples_path=samples_path,
        entries_path=entries_path,
        truth_paths=truth_paths,
        raw_dir=raw_dir,
        out_dir=out_dir,
        horizons_sec=parse_horizons(args.horizons_sec),
        include_stale=bool(args.include_stale),
        include_blocked_trend_study=bool(args.include_blocked_trend_study),
        min_move_px=float(args.min_move_px),
        truth_window_sec=float(args.truth_window_sec),
        truth_window_seq=int(args.truth_window_seq),
    )
    print(json.dumps({"out_dir": result["out_dir"], "summary": result["summary"]}, indent=2, ensure_ascii=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
