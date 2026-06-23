from __future__ import annotations
from pathlib import Path
import json
import time
from datetime import datetime
from typing import Sequence, Mapping, Any
from phoenixguard.core.config import RUNTIME
from phoenixguard.vision.v2_overlay_migration import migrate_v2_overlay_object

REGISTRY_DIR = RUNTIME.data_dir / "market_registry"
REGISTRY_DIR.mkdir(parents=True, exist_ok=True)


DEFAULT_LIFECYCLE = "CANDIDATE"
DEFAULT_IOU_THRESHOLD = 0.5
DEFAULT_CONFIRM_TRUTH = 0.75
DEFAULT_STALE_SECONDS = 30


def _now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"


def _epoch_from_value(value: Any) -> float:
    if value in (None, ""):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        text = str(value).strip()
        if not text:
            return 0.0
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return float(parsed.timestamp())
    except Exception:
        return 0.0


def _entry_last_seen_epoch(entry: Mapping[str, Any]) -> float:
    overlay = entry.get("overlay") if isinstance(entry.get("overlay"), Mapping) else {}
    for key in ("last_seen_at", "updated_at", "timestamp", "created_at"):
        epoch = _epoch_from_value(entry.get(key))
        if epoch > 0.0:
            return epoch
    if isinstance(overlay, Mapping):
        for key in ("last_seen_at", "updated_at", "timestamp", "created_at"):
            epoch = _epoch_from_value(overlay.get(key))
            if epoch > 0.0:
                return epoch
    return 0.0


def _bbox_iou(a: Sequence[float], b: Sequence[float]) -> float:
    """Compute IoU for [x1,y1,x2,y2] boxes. Returns 0.0 on error."""
    try:
        ax1, ay1, ax2, ay2 = map(float, a)
        bx1, by1, bx2, by2 = map(float, b)
        inter_x1 = max(min(ax1, ax2), min(bx1, bx2))
        inter_y1 = max(min(ay1, ay2), min(by1, by2))
        inter_x2 = min(max(ax1, ax2), max(bx1, bx2))
        inter_y2 = min(max(ay1, ay2), max(by1, by2))
        iw = max(0.0, inter_x2 - inter_x1)
        ih = max(0.0, inter_y2 - inter_y1)
        inter = iw * ih
        area_a = abs((ax2 - ax1) * (ay2 - ay1))
        area_b = abs((bx2 - bx1) * (by2 - by1))
        union = area_a + area_b - inter
        if union <= 0:
            return 0.0
        return float(inter / union)
    except Exception:
        return 0.0


def persist_market_objects(session_id: str, objects: Sequence[Mapping[str, Any]], chart_transform: Mapping[str, Any] | None = None) -> Path:
    """Persist market overlay objects with enriched metadata to a per-session JSONL file.

    Best-effort: does not raise on write errors.
    New entries include lifecycle timestamps and merge metadata.
    """
    session_file = REGISTRY_DIR / f"{session_id}.jsonl"
    ts = _now_iso()
    try:
        with session_file.open("a", encoding="utf-8") as fh:
            for obj in objects:
                overlay = dict(obj)
                overlay_id = str(overlay.get("id") or overlay.get("key") or overlay.get("label") or f"overlay_{hash(str(overlay))}")
                entry = {
                    "timestamp": ts,
                    "created_at": ts,
                    "updated_at": ts,
                    "session_id": session_id,
                    "chart_transform": dict(chart_transform) if chart_transform is not None else None,
                    "overlay": overlay,
                    # enrich defaults
                    "overlay_id": overlay_id,
                    "object_id": str(overlay.get("object_id") or overlay.get("id") or ""),
                    "track_id": str(overlay.get("track_id") or ""),
                    "lifecycle_state": str(overlay.get("lifecycle_state") or DEFAULT_LIFECYCLE),
                    "truth_score": float(overlay.get("truth_score") or overlay.get("confidence") or 0.0),
                    "merge_count": int(overlay.get("merge_count") or 0),
                    "merged_into": overlay.get("merged_into") or None,
                    "last_seen_at": ts,
                }
                fh.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass
    # Debug: write a best-effort dump of what was persisted for easier tracing
    try:
        debug_dir = Path(RUNTIME.project_root) / ".codex_runtime" / "overlay_persist_logs"
        debug_dir.mkdir(parents=True, exist_ok=True)
        dbg_path = debug_dir / f"{session_id}_{int(time.time())}.json"
        dump = {"session_id": session_id, "objects": [dict(o) for o in objects], "chart_transform": dict(chart_transform) if chart_transform is not None else None}
        dbg_path.write_text(json.dumps(dump, default=str), encoding="utf-8")
    except Exception:
        # swallow errors to keep persistence best-effort
        pass
    return session_file


def _normalize_loaded_market_entry(entry: Mapping[str, Any]) -> Mapping[str, Any]:
    normalized = dict(entry)
    try:
        overlay_raw = normalized.get("overlay") or {}
        overlay = dict(overlay_raw) if isinstance(overlay_raw, Mapping) else {}
        # move nested chart_transform into entry if present in overlay
        if not normalized.get("chart_transform") and overlay.get("chart_transform"):
            normalized["chart_transform"] = overlay.get("chart_transform")

        # ensure bbox exists as [x1, y1, x2, y2]
        bbox = overlay.get("bbox") or overlay.get("box") or overlay.get("rect")
        anchors = overlay.get("anchors")
        if not bbox and anchors and isinstance(anchors, (list, tuple)):
            # anchors may be a list of point pairs [[x,y],...]
            try:
                pts = [
                    tuple(map(float, p))
                    for p in anchors
                    if isinstance(p, (list, tuple)) and len(p) >= 2
                ]
                if pts:
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    bbox = [min(xs), min(ys), max(xs), max(ys)]
            except Exception:
                bbox = None
        if bbox:
            try:
                overlay["bbox"] = [float(v) for v in bbox]
            except Exception:
                # leave as-is if conversion fails
                overlay.setdefault("bbox", bbox)
        # ensure truth_score present at overlay level
        if "truth_score" not in overlay:
            try:
                overlay["truth_score"] = float(overlay.get("confidence") or normalized.get("truth_score") or 0.0)
            except Exception:
                overlay["truth_score"] = 0.0
        # normalize into a V3-shaped object while preserving the legacy payload
        chart_transform = normalized.get("chart_transform") if isinstance(normalized.get("chart_transform"), Mapping) else None
        chart_transform_id = str((chart_transform or {}).get("chart_transform_id") or "") if chart_transform else ""
        frame_id = None
        if isinstance(chart_transform, Mapping) and chart_transform.get("frame_id") is not None:
            try:
                raw_frame_id = chart_transform.get("frame_id")
                frame_id = int(raw_frame_id) if isinstance(raw_frame_id, (int, float, str)) else None
            except Exception:
                frame_id = None
        overlay = migrate_v2_overlay_object(
            overlay,
            frame_id=frame_id,
            chart_transform_id=chart_transform_id or None,
            source_agent=str(overlay.get("source_agent") or normalized.get("source_agent") or "legacy_v2_overlay_migration"),
        )
        normalized["overlay"] = overlay
        if chart_transform_id and not normalized.get("chart_transform_id"):
            normalized["chart_transform_id"] = chart_transform_id
        if frame_id is not None and normalized.get("frame_id") is None:
            normalized["frame_id"] = frame_id
    except Exception:
        pass
    return normalized


def load_market_objects(session_id: str) -> list[Mapping[str, Any]]:
    session_file = REGISTRY_DIR / f"{session_id}.jsonl"
    results: list[Mapping[str, Any]] = []
    if not session_file.exists():
        return results
    try:
        with session_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    results.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return [_normalize_loaded_market_entry(entry) for entry in results]


def _read_recent_jsonl_lines(path: Path, *, max_lines: int = 2000, chunk_size: int = 65536) -> list[str]:
    if max_lines <= 0 or not path.exists():
        return []
    try:
        with path.open("rb") as fh:
            fh.seek(0, 2)
            position = fh.tell()
            buffer = b""
            lines: list[bytes] = []
            while position > 0 and len(lines) <= max_lines:
                read_size = min(int(chunk_size), position)
                position -= read_size
                fh.seek(position)
                buffer = fh.read(read_size) + buffer
                lines = buffer.splitlines()
            return [
                line.decode("utf-8", errors="ignore")
                for line in lines[-max_lines:]
                if line.strip()
            ]
    except Exception:
        return []


def load_recent_market_objects(session_id: str, *, max_lines: int = 2000) -> list[Mapping[str, Any]]:
    entries: list[Mapping[str, Any]] = []
    candidates = _registry_session_candidates(session_id) or [str(session_id or "").strip()]
    for candidate in candidates:
        session_file = REGISTRY_DIR / f"{candidate}.jsonl"
        if not session_file.exists():
            continue
        for line in _read_recent_jsonl_lines(session_file, max_lines=max_lines):
            try:
                parsed = json.loads(line)
            except Exception:
                continue
            if isinstance(parsed, Mapping):
                entries.append(_normalize_loaded_market_entry(parsed))
        if entries:
            break
    return entries


def _active_objects_from_entries(
    entries: Sequence[Mapping[str, Any]],
    *,
    min_truth_score: float,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    now_epoch: float | None = None,
) -> list[Mapping[str, Any]]:
    active: list[Mapping[str, Any]] = []
    now = time.time() if now_epoch is None else float(now_epoch)
    # Take the latest entry for each overlay_id. JSONL append order is the
    # tie-breaker because merge writes the MERGED marker and replacement row
    # with the same timestamp.
    latest_by_overlay: dict[str, Mapping[str, Any]] = {}
    for e in entries:
        oid = str(e.get("overlay_id") or (e.get("overlay") or {}).get("id") or "")
        if not oid:
            continue
        prev = latest_by_overlay.get(oid)
        current_updated = str(e.get("updated_at") or e.get("timestamp") or "")
        previous_updated = str(prev.get("updated_at") or prev.get("timestamp") or "") if prev is not None else ""
        if prev is None or current_updated >= previous_updated:
            latest_by_overlay[oid] = e

    for e in latest_by_overlay.values():
        last_seen_epoch = _entry_last_seen_epoch(e)
        if last_seen_epoch > 0.0 and now - last_seen_epoch > float(stale_seconds):
            continue
        try:
            truth = float(e.get("truth_score") or (e.get("overlay") or {}).get("truth_score") or (e.get("overlay") or {}).get("confidence") or 0.0)
        except Exception:
            truth = 0.0
        lifecycle = str(e.get("lifecycle_state") or (e.get("overlay") or {}).get("lifecycle_state") or "")
        if lifecycle.upper() in {"BROKEN", "STALE", "HIDDEN"}:
            continue
        if truth >= float(min_truth_score) or lifecycle.upper() in {"CONFIRMED", "ACTIVE"}:
            active.append(e)
    return active


def active_objects_from_entries(
    entries: Sequence[Mapping[str, Any]],
    *,
    min_truth_score: float,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    now_epoch: float | None = None,
) -> list[Mapping[str, Any]]:
    return _active_objects_from_entries(
        entries,
        min_truth_score=min_truth_score,
        stale_seconds=stale_seconds,
        now_epoch=now_epoch,
    )


def _registry_session_candidates(session_id: str) -> list[str]:
    normalized = str(session_id or "").strip()
    if not normalized:
        return []
    candidates: list[str] = [normalized]
    parts = normalized.split("-")
    while len(parts) > 1:
        parts = parts[:-1]
        candidate = "-".join(parts).strip()
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    try:
        for path in sorted(REGISTRY_DIR.glob("*.jsonl")):
            stem = path.stem.strip()
            if stem.startswith(normalized) and stem not in candidates:
                candidates.append(stem)
            for candidate in tuple(candidates):
                if candidate and stem.startswith(candidate) and stem not in candidates:
                    candidates.append(stem)
    except Exception:
        pass
    return candidates


def _write_entry(session_id: str, entry: Mapping[str, Any]) -> None:
    try:
        session_file = REGISTRY_DIR / f"{session_id}.jsonl"
        with session_file.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


def merge_market_objects(session_id: str, new_objects: Sequence[Mapping[str, Any]], *, iou_threshold: float = DEFAULT_IOU_THRESHOLD) -> int:
    """Merge new overlay objects into the per-session registry.

    Returns the number of objects appended (new or merged entries written).
    Merge strategy: if an existing recent entry overlaps (IoU) with new, mark previous as MERGED and append a new entry with updated merge metadata.
    """
    existing = load_market_objects(session_id)
    appended = 0
    now_ts = _now_iso()
    for obj in new_objects:
        overlay = dict(obj)
        overlay_id = str(overlay.get("id") or overlay.get("key") or overlay.get("overlay_id") or f"overlay_{hash(str(overlay))}")
        truth = float(overlay.get("truth_score") or overlay.get("confidence") or 0.0)

        matched = None
        for e in reversed(existing):
            e_overlay = e.get("overlay") or {}
            e_overlay_id = str(e.get("overlay_id") or e_overlay.get("id") or "")
            if not e_overlay_id:
                continue
            # prefer id match
            if e_overlay_id == overlay_id:
                matched = e
                break
            # try IoU match
            iou = _bbox_iou(e_overlay.get("bbox") or [0, 0, 0, 0], overlay.get("bbox") or [0, 0, 0, 0])
            if iou >= float(iou_threshold):
                matched = e
                break

        if matched is not None:
            # mark previous as MERGED by writing an updated entry signaling merge
            try:
                merged_into = overlay_id
                matched_entry = dict(matched)
                matched_entry["updated_at"] = now_ts
                matched_entry["lifecycle_state"] = "MERGED"
                matched_entry.setdefault("merge_count", 0)
                matched_entry["merge_count"] = int(matched_entry.get("merge_count", 0)) + 1
                matched_entry["merged_into"] = merged_into
                _write_entry(session_id, matched_entry)
            except Exception:
                pass
            # append new as CONFIRMED or ACTIVE depending on truth
            new_state = "CONFIRMED" if truth >= DEFAULT_CONFIRM_TRUTH else "ACTIVE"
            new_entry = {
                "timestamp": now_ts,
                "created_at": now_ts,
                "updated_at": now_ts,
                "session_id": session_id,
                "chart_transform": None,
                "overlay": overlay,
                "overlay_id": overlay_id,
                "object_id": str(overlay.get("object_id") or overlay.get("id") or ""),
                "track_id": str(overlay.get("track_id") or ""),
                "lifecycle_state": new_state,
                "truth_score": truth,
                "merge_count": int(overlay.get("merge_count") or 0),
                "merged_into": None,
                "last_seen_at": now_ts,
            }
            _write_entry(session_id, new_entry)
            appended += 1
            existing.append(new_entry)
        else:
            # brand new
            new_state = "CONFIRMED" if truth >= DEFAULT_CONFIRM_TRUTH else DEFAULT_LIFECYCLE
            new_entry = {
                "timestamp": now_ts,
                "created_at": now_ts,
                "updated_at": now_ts,
                "session_id": session_id,
                "chart_transform": None,
                "overlay": overlay,
                "overlay_id": overlay_id,
                "object_id": str(overlay.get("object_id") or overlay.get("id") or ""),
                "track_id": str(overlay.get("track_id") or ""),
                "lifecycle_state": new_state,
                "truth_score": truth,
                "merge_count": int(overlay.get("merge_count") or 0),
                "merged_into": None,
                "last_seen_at": now_ts,
            }
            _write_entry(session_id, new_entry)
            appended += 1
            existing.append(new_entry)
    return appended


def promote_lifecycle(session_id: str, *, stale_seconds: int = DEFAULT_STALE_SECONDS) -> None:
    """Scan registry and append entries marking stale items.

    This function is best-effort and appends lifecycle-marker entries to the JSONL log.
    """
    entries = load_market_objects(session_id)
    now = time.time()
    for e in entries:
        try:
            last_seen = e.get("last_seen_at") or e.get("updated_at") or e.get("timestamp")
            if not isinstance(last_seen, str) or not last_seen:
                continue
            # parse ISO to epoch
            t = datetime.fromisoformat(last_seen.replace("Z", ""))
            age = now - t.timestamp()
            if age > int(stale_seconds):
                marker = dict(e)
                marker["updated_at"] = _now_iso()
                marker["lifecycle_state"] = "STALE"
                _write_entry(session_id, marker)
        except Exception:
            continue


def query_active_objects(session_id: str, *, min_truth_score: float = 0.55, stale_seconds: int = DEFAULT_STALE_SECONDS) -> list[Mapping[str, Any]]:
    entries = load_market_objects(session_id)
    if not entries:
        for candidate in _registry_session_candidates(session_id)[1:]:
            entries = load_market_objects(candidate)
            if entries:
                break
    return _active_objects_from_entries(entries, min_truth_score=float(min_truth_score), stale_seconds=int(stale_seconds))


def query_recent_active_objects(
    session_id: str,
    *,
    min_truth_score: float = 0.55,
    max_lines: int = 2000,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
) -> list[Mapping[str, Any]]:
    entries = load_recent_market_objects(session_id, max_lines=max_lines)
    return _active_objects_from_entries(entries, min_truth_score=float(min_truth_score), stale_seconds=int(stale_seconds))
