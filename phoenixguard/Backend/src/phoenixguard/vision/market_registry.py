from __future__ import annotations
from collections import deque
from collections.abc import Mapping, Sequence
from pathlib import Path
import hashlib
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from numbers import Real
from typing import Any, cast
from phoenixguard.core.config import RUNTIME
from phoenixguard.vision.v2_overlay_migration import migrate_v2_overlay_object

REGISTRY_DIR = RUNTIME.data_dir / "market_registry"
REGISTRY_DIR.mkdir(parents=True, exist_ok=True)


DEFAULT_LIFECYCLE = "CANDIDATE"
DEFAULT_IOU_THRESHOLD = 0.5
DEFAULT_CONFIRM_TRUTH = 0.75
DEFAULT_STALE_SECONDS = 30
DEFAULT_REGISTRY_MAX_BYTES = 16 * 1024 * 1024
DEFAULT_REGISTRY_RETAIN_LINES = 4000
DEFAULT_REGISTRY_RECORD_MAX_BYTES = 256 * 1024
MIN_REGISTRY_RECORD_MAX_BYTES = 4 * 1024
_REGISTRY_WRITE_LOCK = threading.RLock()
_SAFE_SESSION_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
_WINDOWS_RESERVED_STEMS = {
    "AUX",
    "CLOCK$",
    "CON",
    "NUL",
    "PRN",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    raw = cast(Mapping[object, object], value)
    return {str(key): item for key, item in raw.items()}


def _sequence(value: object) -> list[object]:
    if isinstance(value, (str, bytes, bytearray)) or not isinstance(value, Sequence):
        return []
    return list(cast(Sequence[object], value))


def _float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (str, bytes, bytearray, Real)):
        try:
            return float(value)
        except ValueError:
            return default
    return default


def _int(value: object, default: int = 0) -> int:
    return int(_float(value, float(default)))


def _float_list(value: object) -> list[float]:
    out: list[float] = []
    for item in _sequence(value):
        number = _float(item, float("nan"))
        if number != number:
            return []
        out.append(number)
    return out


def _point_pairs(value: object) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for item in _sequence(value):
        point = _float_list(item)
        if len(point) >= 2:
            points.append((point[0], point[1]))
    return points


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


def _entry_last_seen_epoch(entry: Mapping[str, object]) -> float:
    overlay = _mapping(entry.get("overlay"))
    for key in ("last_seen_at", "updated_at", "timestamp", "created_at"):
        epoch = _epoch_from_value(entry.get(key))
        if epoch > 0.0:
            return epoch
    for key in ("last_seen_at", "updated_at", "timestamp", "created_at"):
        epoch = _epoch_from_value(overlay.get(key))
        if epoch > 0.0:
            return epoch
    return 0.0


def _env_int(name: str, default: int, *, minimum: int) -> int:
    try:
        return max(minimum, int(float(str(os.getenv(name, default) or default).strip())))
    except (TypeError, ValueError):
        return max(minimum, int(default))


def _env_enabled(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _safe_session_token(session_id: object) -> str:
    """Return a stable filename token without changing ordinary session IDs."""

    raw = str(session_id or "").strip()
    if (
        _SAFE_SESSION_TOKEN.fullmatch(raw) is not None
        and raw.upper() not in _WINDOWS_RESERVED_STEMS
    ):
        return raw
    digest = hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:20]
    slug = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-_")[:48] or "session"
    if slug.upper() in _WINDOWS_RESERVED_STEMS:
        slug = "session"
    return f"{slug}-{digest}"


def _assert_resolved_containment(path: Path, root: Path) -> Path:
    """Resolve *path* and reject any child that escapes its declared root."""

    resolved_root = root.resolve(strict=False)
    resolved_path = path.resolve(strict=False)
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError(f"path escapes declared root: {resolved_path}") from exc
    return resolved_path


def _registry_file(session_id: object) -> Path:
    token = _safe_session_token(session_id)
    return _assert_resolved_containment(REGISTRY_DIR / f"{token}.jsonl", REGISTRY_DIR)


def _bounded_identity(value: object, *, max_chars: int = 160) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    digest = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:20]
    return f"{text[: max_chars - 22]}~{digest}"


def _compact_scalar(value: object) -> object | None:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        return _bounded_identity(value)
    return None


def _compact_geometry_value(value: object, *, depth: int = 0) -> object | None:
    """Keep bounded coordinate data while rejecting arbitrary nested payloads."""

    if depth > 2:
        return None
    scalar = _compact_scalar(value)
    if scalar is not None:
        return scalar
    if isinstance(value, Mapping):
        source = cast(Mapping[object, object], value)
        kept: dict[str, object] = {}
        for raw_key, raw_value in list(source.items())[:24]:
            key = str(raw_key)
            if key not in {
                "x",
                "y",
                "x1",
                "y1",
                "x2",
                "y2",
                "width",
                "height",
                "left",
                "right",
                "top",
                "bottom",
                "start",
                "end",
            }:
                continue
            compacted = _compact_geometry_value(raw_value, depth=depth + 1)
            if compacted is not None:
                kept[key] = compacted
        return kept or None
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        kept_items: list[object] = []
        for item in list(cast(Sequence[object], value))[:32]:
            compacted = _compact_geometry_value(item, depth=depth + 1)
            if compacted is not None:
                kept_items.append(compacted)
        return kept_items or None
    return None


def _compact_registry_entry(entry: Mapping[str, object], *, original_bytes: int) -> dict[str, object]:
    overlay = _mapping(entry.get("overlay"))
    compact_overlay: dict[str, object] = {}
    for key in (
        "id",
        "key",
        "overlay_id",
        "object_id",
        "track_id",
        "type",
        "kind",
        "label",
        "role",
        "side",
        "direction",
        "source_agent",
        "lifecycle_state",
        "truth_score",
        "confidence",
    ):
        if key not in overlay:
            continue
        compacted = _compact_scalar(overlay.get(key))
        if compacted is not None:
            compact_overlay[key] = compacted
    for key in (
        "bbox",
        "box",
        "rect",
        "normalized_bbox",
        "anchors",
        "points",
        "start",
        "end",
        "x",
        "y",
        "x1",
        "y1",
        "x2",
        "y2",
        "width",
        "height",
    ):
        if key not in overlay:
            continue
        compacted = _compact_geometry_value(overlay.get(key))
        if compacted is not None:
            compact_overlay[key] = compacted

    compact: dict[str, object] = {
        "record_compacted": True,
        "record_original_bytes": original_bytes,
        "timestamp": _bounded_identity(entry.get("timestamp")),
        "created_at": _bounded_identity(entry.get("created_at")),
        "updated_at": _bounded_identity(entry.get("updated_at")),
        "last_seen_at": _bounded_identity(entry.get("last_seen_at")),
        "session_id": _bounded_identity(entry.get("session_id")),
        "overlay_id": _bounded_identity(entry.get("overlay_id")),
        "object_id": _bounded_identity(entry.get("object_id")),
        "track_id": _bounded_identity(entry.get("track_id")),
        "lifecycle_state": _bounded_identity(entry.get("lifecycle_state")),
        "truth_score": _float(entry.get("truth_score")),
        "merge_count": _int(entry.get("merge_count")),
        "merged_into": _bounded_identity(entry.get("merged_into")),
        "overlay": compact_overlay,
    }
    chart_transform = _mapping(entry.get("chart_transform"))
    compact_transform: dict[str, object] = {}
    for key in ("chart_transform_id", "frame_id", "width", "height", "chart_bounds"):
        if key not in chart_transform:
            continue
        compacted = _compact_geometry_value(chart_transform.get(key))
        if compacted is not None:
            compact_transform[key] = compacted
    if compact_transform:
        compact["chart_transform"] = compact_transform
    return compact


def _json_line_bytes(value: Mapping[str, object]) -> bytes:
    payload = json.dumps(value, default=str, ensure_ascii=False, separators=(",", ":"))
    return payload.encode("utf-8", errors="replace") + b"\n"


def _encode_registry_entry(entry: Mapping[str, object]) -> bytes:
    max_bytes = _env_int(
        "PHOENIXGUARD_MARKET_REGISTRY_RECORD_MAX_BYTES",
        DEFAULT_REGISTRY_RECORD_MAX_BYTES,
        minimum=MIN_REGISTRY_RECORD_MAX_BYTES,
    )
    encoded = _json_line_bytes(entry)
    if len(encoded) <= max_bytes:
        return encoded
    compact = _compact_registry_entry(entry, original_bytes=len(encoded))
    encoded = _json_line_bytes(compact)
    if len(encoded) <= max_bytes:
        return encoded

    # The configured minimum is large enough for this identity/truth/geometry
    # core. This final form makes the bound explicit even if future fields are
    # added to the richer compact representation.
    overlay = _mapping(compact.get("overlay"))
    geometry = {
        key: overlay[key]
        for key in (
            "id",
            "key",
            "type",
            "kind",
            "bbox",
            "box",
            "rect",
            "normalized_bbox",
            "anchors",
            "points",
            "start",
            "end",
            "x",
            "y",
            "x1",
            "y1",
            "x2",
            "y2",
            "width",
            "height",
        )
        if key in overlay
    }
    minimal: dict[str, object] = {
        "record_compacted": True,
        "record_original_bytes": len(encoded),
        "timestamp": _bounded_identity(entry.get("timestamp"), max_chars=80),
        "session_id": _bounded_identity(entry.get("session_id"), max_chars=80),
        "overlay_id": _bounded_identity(entry.get("overlay_id"), max_chars=80),
        "object_id": _bounded_identity(entry.get("object_id"), max_chars=80),
        "track_id": _bounded_identity(entry.get("track_id"), max_chars=80),
        "lifecycle_state": _bounded_identity(entry.get("lifecycle_state"), max_chars=40),
        "truth_score": _float(entry.get("truth_score")),
        "overlay": geometry,
    }
    encoded = _json_line_bytes(minimal)
    if len(encoded) > max_bytes:
        raise ValueError("bounded market-registry record exceeds configured maximum")
    return encoded


def _compact_registry_file(session_file: Path) -> None:
    """Atomically retain a recent bounded tail; Pair DNA persists separately."""

    session_file = _assert_resolved_containment(session_file, REGISTRY_DIR)

    max_bytes = _env_int(
        "PHOENIXGUARD_MARKET_REGISTRY_MAX_BYTES",
        DEFAULT_REGISTRY_MAX_BYTES,
        minimum=1024,
    )
    try:
        if not session_file.exists() or session_file.stat().st_size <= max_bytes:
            return
    except OSError:
        return
    retain_lines = _env_int(
        "PHOENIXGUARD_MARKET_REGISTRY_RETAIN_LINES",
        DEFAULT_REGISTRY_RETAIN_LINES,
        minimum=1,
    )
    low_water_bytes = max(1024, max_bytes // 2)
    recent_lines = _read_recent_jsonl_lines(
        session_file,
        max_lines=retain_lines,
        max_line_bytes=low_water_bytes,
    )
    retained_reversed: list[bytes] = []
    retained_bytes = 0
    for line in reversed(recent_lines):
        encoded = line.encode("utf-8", errors="ignore") + b"\n"
        if len(encoded) > low_water_bytes:
            continue
        if retained_bytes + len(encoded) > low_water_bytes:
            break
        retained_reversed.append(encoded)
        retained_bytes += len(encoded)
    retained_reversed.reverse()
    tmp_path = _assert_resolved_containment(
        session_file.with_name(
            f".{session_file.name}.{os.getpid():x}.{time.monotonic_ns():x}.compact.tmp"
        ),
        REGISTRY_DIR,
    )
    try:
        tmp_path.write_bytes(b"".join(retained_reversed))
        tmp_path.replace(session_file)
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


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


def persist_market_objects(session_id: str, objects: Sequence[Mapping[str, object]], chart_transform: Mapping[str, object] | None = None) -> Path:
    """Persist market overlay objects with enriched metadata to a per-session JSONL file.

    Best-effort: does not raise on write errors.
    New entries include lifecycle timestamps and merge metadata.
    """
    session_file = _registry_file(session_id)
    ts = _now_iso()
    lines: list[bytes] = []
    try:
        REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        for obj in objects:
            overlay = dict(obj)
            overlay_id = str(overlay.get("id") or overlay.get("key") or overlay.get("label") or f"overlay_{hash(str(overlay))}")
            entry: dict[str, object] = {
                "timestamp": ts,
                "created_at": ts,
                "updated_at": ts,
                "session_id": session_id,
                "chart_transform": dict(chart_transform) if chart_transform is not None else None,
                "overlay": overlay,
                "overlay_id": overlay_id,
                "object_id": str(overlay.get("object_id") or overlay.get("id") or ""),
                "track_id": str(overlay.get("track_id") or ""),
                "lifecycle_state": str(overlay.get("lifecycle_state") or DEFAULT_LIFECYCLE),
                "truth_score": _float(overlay.get("truth_score") or overlay.get("confidence") or 0.0),
                "merge_count": _int(overlay.get("merge_count") or 0),
                "merged_into": overlay.get("merged_into") or None,
                "last_seen_at": ts,
            }
            lines.append(_encode_registry_entry(entry))
        with _REGISTRY_WRITE_LOCK:
            with session_file.open("ab") as fh:
                fh.writelines(lines)
            _compact_registry_file(session_file)
    except Exception:
        pass
    if _env_enabled("PHOENIXGUARD_OVERLAY_PERSIST_DEBUG", False):
        try:
            runtime_root = Path(os.getenv("PHOENIXGUARD_RUNTIME_DIR") or Path(RUNTIME.project_root) / "runtime" / "live")
            debug_dir = _assert_resolved_containment(
                runtime_root / "overlay_persist_logs",
                runtime_root,
            )
            debug_dir.mkdir(parents=True, exist_ok=True)
            dbg_path = _assert_resolved_containment(
                debug_dir / f"{_safe_session_token(session_id)}_{uuid.uuid4().hex}.json",
                debug_dir,
            )
            dump: dict[str, object] = {
                "session_id": session_id,
                "objects": [dict(obj) for obj in objects],
                "chart_transform": dict(chart_transform) if chart_transform is not None else None,
            }
            dbg_path.write_text(json.dumps(dump, default=str), encoding="utf-8")
        except Exception:
            pass
    return session_file


def _normalize_loaded_market_entry(entry: Mapping[str, object]) -> Mapping[str, object]:
    normalized: dict[str, object] = dict(entry)
    try:
        overlay = _mapping(normalized.get("overlay"))
        # move nested chart_transform into entry if present in overlay
        if not normalized.get("chart_transform") and overlay.get("chart_transform"):
            normalized["chart_transform"] = overlay.get("chart_transform")

        # ensure bbox exists as [x1, y1, x2, y2]
        bbox = overlay.get("bbox") or overlay.get("box") or overlay.get("rect")
        anchors = overlay.get("anchors")
        if not bbox and anchors:
            # anchors may be a list of point pairs [[x,y],...]
            pts = _point_pairs(anchors)
            if pts:
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                bbox = [min(xs), min(ys), max(xs), max(ys)]
        bbox_values = _float_list(bbox)
        if bbox_values:
            overlay["bbox"] = bbox_values
        elif bbox:
            overlay.setdefault("bbox", bbox)
        # ensure truth_score present at overlay level
        if "truth_score" not in overlay:
            try:
                overlay["truth_score"] = _float(overlay.get("confidence") or normalized.get("truth_score") or 0.0)
            except Exception:
                overlay["truth_score"] = 0.0
        # normalize into a V3-shaped object while preserving the legacy payload
        chart_transform = _mapping(normalized.get("chart_transform"))
        chart_transform_id = str(chart_transform.get("chart_transform_id") or "") if chart_transform else ""
        frame_id = None
        if chart_transform.get("frame_id") is not None:
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


def load_market_objects(session_id: str) -> list[Mapping[str, object]]:
    session_file = _registry_file(session_id)
    results: list[Mapping[str, object]] = []
    if not session_file.exists():
        return results
    try:
        with session_file.open("r", encoding="utf-8") as fh:
            for line in fh:
                try:
                    parsed: object = json.loads(line)
                    row = _mapping(parsed)
                    if row:
                        results.append(row)
                except Exception:
                    continue
    except Exception:
        return []
    return [_normalize_loaded_market_entry(entry) for entry in results]


def _read_recent_jsonl_lines(
    path: Path,
    *,
    max_lines: int = 2000,
    max_line_bytes: int | None = None,
) -> list[str]:
    path = _assert_resolved_containment(path, REGISTRY_DIR)
    if max_lines <= 0 or not path.exists():
        return []
    line_limit = max_line_bytes or _env_int(
        "PHOENIXGUARD_MARKET_REGISTRY_RECORD_MAX_BYTES",
        DEFAULT_REGISTRY_RECORD_MAX_BYTES,
        minimum=MIN_REGISTRY_RECORD_MAX_BYTES,
    )
    try:
        recent: deque[str] = deque(maxlen=max_lines)
        with path.open("rb") as fh:
            while True:
                raw = fh.readline(line_limit + 1)
                if not raw:
                    break
                oversized = len(raw) > line_limit and not raw.endswith(b"\n")
                if oversized:
                    while raw and not raw.endswith(b"\n"):
                        raw = fh.readline(line_limit + 1)
                    continue
                stripped = raw.rstrip(b"\r\n")
                if not stripped or len(stripped) + 1 > line_limit:
                    continue
                recent.append(stripped.decode("utf-8", errors="ignore"))
        return list(recent)
    except Exception:
        return []


def load_recent_market_objects(session_id: str, *, max_lines: int = 2000) -> list[Mapping[str, object]]:
    entries: list[Mapping[str, object]] = []
    candidates = _registry_session_candidates(session_id) or [str(session_id or "").strip()]
    for candidate in candidates:
        session_file = _registry_file(candidate)
        if not session_file.exists():
            continue
        for line in _read_recent_jsonl_lines(session_file, max_lines=max_lines):
            try:
                parsed: object = json.loads(line)
            except Exception:
                continue
            row = _mapping(parsed)
            if row:
                entries.append(_normalize_loaded_market_entry(row))
        if entries:
            break
    return entries


def _active_objects_from_entries(
    entries: Sequence[Mapping[str, object]],
    *,
    min_truth_score: float,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    now_epoch: float | None = None,
) -> list[Mapping[str, object]]:
    active: list[Mapping[str, object]] = []
    now = time.time() if now_epoch is None else float(now_epoch)
    # Take the latest entry for each overlay_id. JSONL append order is the
    # tie-breaker because merge writes the MERGED marker and replacement row
    # with the same timestamp.
    latest_by_overlay: dict[str, Mapping[str, object]] = {}
    for e in entries:
        overlay = _mapping(e.get("overlay"))
        oid = str(e.get("overlay_id") or overlay.get("id") or "")
        if not oid:
            continue
        prev = latest_by_overlay.get(oid)
        current_updated = str(e.get("updated_at") or e.get("timestamp") or "")
        previous_updated = str(prev.get("updated_at") or prev.get("timestamp") or "") if prev is not None else ""
        if prev is None or current_updated >= previous_updated:
            latest_by_overlay[oid] = e

    for e in latest_by_overlay.values():
        overlay = _mapping(e.get("overlay"))
        last_seen_epoch = _entry_last_seen_epoch(e)
        if last_seen_epoch > 0.0 and now - last_seen_epoch > float(stale_seconds):
            continue
        try:
            truth = _float(e.get("truth_score") or overlay.get("truth_score") or overlay.get("confidence") or 0.0)
        except Exception:
            truth = 0.0
        lifecycle = str(e.get("lifecycle_state") or overlay.get("lifecycle_state") or "")
        if lifecycle.upper() in {"BROKEN", "STALE", "HIDDEN"}:
            continue
        if truth >= float(min_truth_score) or lifecycle.upper() in {"CONFIRMED", "ACTIVE"}:
            active.append(e)
    return active


def active_objects_from_entries(
    entries: Sequence[Mapping[str, object]],
    *,
    min_truth_score: float,
    stale_seconds: int = DEFAULT_STALE_SECONDS,
    now_epoch: float | None = None,
) -> list[Mapping[str, object]]:
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
    exact_token = _safe_session_token(normalized)
    candidates: list[str] = [exact_token]
    if exact_token == normalized:
        parts = normalized.split("-")
        while len(parts) > 1:
            parts = parts[:-1]
            candidate = "-".join(parts).strip()
            if candidate and candidate not in candidates:
                candidates.append(candidate)
    try:
        for path in sorted(REGISTRY_DIR.glob("*.jsonl")):
            path = _assert_resolved_containment(path, REGISTRY_DIR)
            stem = path.stem.strip()
            if stem.startswith(exact_token) and stem not in candidates:
                candidates.append(stem)
            for candidate in tuple(candidates):
                if candidate and stem.startswith(candidate) and stem not in candidates:
                    candidates.append(stem)
    except Exception:
        pass
    return candidates


def _write_entry(session_id: str, entry: Mapping[str, object]) -> None:
    try:
        session_file = _registry_file(session_id)
        REGISTRY_DIR.mkdir(parents=True, exist_ok=True)
        with _REGISTRY_WRITE_LOCK:
            with session_file.open("ab") as fh:
                fh.write(_encode_registry_entry(entry))
            _compact_registry_file(session_file)
    except Exception:
        pass


def merge_market_objects(session_id: str, new_objects: Sequence[Mapping[str, object]], *, iou_threshold: float = DEFAULT_IOU_THRESHOLD) -> int:
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
        truth = _float(overlay.get("truth_score") or overlay.get("confidence") or 0.0)

        matched = None
        for e in reversed(existing):
            e_overlay = _mapping(e.get("overlay"))
            e_overlay_id = str(e.get("overlay_id") or e_overlay.get("id") or "")
            if not e_overlay_id:
                continue
            # prefer id match
            if e_overlay_id == overlay_id:
                matched = e
                break
            # try IoU match
            iou = _bbox_iou(_float_list(e_overlay.get("bbox")) or [0.0, 0.0, 0.0, 0.0], _float_list(overlay.get("bbox")) or [0.0, 0.0, 0.0, 0.0])
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
                matched_entry["merge_count"] = _int(matched_entry.get("merge_count", 0)) + 1
                matched_entry["merged_into"] = merged_into
                _write_entry(session_id, matched_entry)
            except Exception:
                pass
            # append new as CONFIRMED or ACTIVE depending on truth
            new_state = "CONFIRMED" if truth >= DEFAULT_CONFIRM_TRUTH else "ACTIVE"
            new_entry: dict[str, object] = {
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
                "merge_count": _int(overlay.get("merge_count") or 0),
                "merged_into": None,
                "last_seen_at": now_ts,
            }
            _write_entry(session_id, new_entry)
            appended += 1
            existing.append(new_entry)
        else:
            # brand new
            new_state = "CONFIRMED" if truth >= DEFAULT_CONFIRM_TRUTH else DEFAULT_LIFECYCLE
            new_entry: dict[str, object] = {
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
                "merge_count": _int(overlay.get("merge_count") or 0),
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


def query_active_objects(session_id: str, *, min_truth_score: float = 0.55, stale_seconds: int = DEFAULT_STALE_SECONDS) -> list[Mapping[str, object]]:
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
) -> list[Mapping[str, object]]:
    entries = load_recent_market_objects(session_id, max_lines=max_lines)
    return _active_objects_from_entries(entries, min_truth_score=float(min_truth_score), stale_seconds=int(stale_seconds))
