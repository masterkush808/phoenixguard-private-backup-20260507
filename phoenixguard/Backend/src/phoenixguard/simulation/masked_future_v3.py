"""Grouped, leak-free masked-future replay over historical chart images."""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
import re
from statistics import median
import threading
from typing import Any, cast

import numpy as np
from PIL import Image

from phoenixguard.study.behavioral_sequence_v3 import measure_market_behavior_v3
from phoenixguard.study.candle_intelligence_v3 import adapt_tracker_candle_v3, analyze_candle_sequence_v3
from phoenixguard.study.masked_future_behavior_v3 import (
    DEFAULT_HORIZONS,
    MaskedFutureBehaviorModelV3,
    build_masked_future_context_v3,
    candle_ohlc_v3,
    finalize_masked_future_model_v3,
    new_masked_future_model_artifact_v3,
    save_masked_future_model_v3,
    update_masked_future_model_v3,
)
from phoenixguard.vision.candle_palette_v3 import extract_candle_tracks_adaptive_v3


MASKED_FUTURE_REPLAY_SCHEMA_VERSION = "PG_MASKED_FUTURE_REPLAY_V3"
SUPPORTED_IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
DEFAULT_RESERVE_GB = 45.0
STATIC_EXTRACTOR_VERSION = "PG_STATIC_IMAGE_CANDLE_EXTRACTOR_V3_2"
_OCR_THREAD_LOCAL = threading.local()
_CURRENCIES = {
    "AUD", "CAD", "CHF", "CNH", "EUR", "GBP", "HKD", "JPY", "MXN", "NOK", "NZD", "SEK", "SGD", "TRY", "USD", "XAG", "XAU", "ZAR",
}


class DiskReserveError(RuntimeError):
    pass


def available_free_gb(path: str | Path) -> float:
    anchor = Path(path).resolve()
    drive = Path(anchor.anchor or anchor)
    stat = os.statvfs(str(drive)) if os.name != "nt" else None
    if stat is not None:
        return float(stat.f_bavail * stat.f_frsize) / (1024.0**3)
    import ctypes

    free = ctypes.c_ulonglong(0)
    if not ctypes.windll.kernel32.GetDiskFreeSpaceExW(str(drive), None, None, ctypes.pointer(free)):
        raise OSError("GetDiskFreeSpaceExW failed")
    return float(free.value) / (1024.0**3)


def enforce_disk_reserve(path: str | Path, *, minimum_free_gb: float = DEFAULT_RESERVE_GB, required_bytes: int = 0) -> float:
    free = available_free_gb(path)
    required_gb = max(0, int(required_bytes)) / (1024.0**3)
    if free - required_gb < float(minimum_free_gb):
        raise DiskReserveError(
            f"PG_DISK_RESERVE_BLOCKED: free={free:.3f}GB required_after_write={minimum_free_gb:.3f}GB estimated_write={required_gb:.3f}GB"
        )
    return free


def _mapping(value: object) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _number(value: object, default: float = 0.0) -> float:
    try:
        parsed = float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return float(default)
    return parsed if math.isfinite(parsed) else float(default)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _dhash(image: Image.Image) -> str:
    gray = np.asarray(image.convert("L").resize((9, 8), Image.Resampling.BILINEAR), dtype=np.int16)
    bits = gray[:, 1:] > gray[:, :-1]
    value = 0
    for bit in bits.flat:
        value = (value << 1) | int(bool(bit))
    return f"{value:016x}"


def _hamming(left: str, right: str) -> int:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except ValueError:
        return 64


def parse_instrument_text_v3(value: object) -> tuple[str, str]:
    text = str(value or "").upper().replace("/", "").replace("_", " ").replace("-", " ")
    symbol = "UNKNOWN"
    for match in re.finditer(r"(?<![A-Z])([A-Z]{6})(?![A-Z])", text):
        token = match.group(1)
        if token[:3] in _CURRENCIES and token[3:] in _CURRENCIES:
            symbol = token
            break
    timeframe = "UNKNOWN"
    aliases = {"DAILY": "D1", "WEEKLY": "W1", "MONTHLY": "MN1"}
    for alias, canonical in aliases.items():
        if alias in text:
            timeframe = canonical
            break
    if timeframe == "UNKNOWN":
        match = re.search(r"(?<![A-Z0-9])(M1|M5|M15|M30|H1|H4|D1|W1|MN1?)(?![A-Z0-9])", text)
        if match:
            timeframe = match.group(1)
    return symbol, timeframe


def _rapidocr_header_text_v3(image: Image.Image) -> str:
    """Read title/chart headers only; all other OCR text is discarded."""

    try:
        engine = getattr(_OCR_THREAD_LOCAL, "engine", None)
        if engine is None:
            from rapidocr_onnxruntime import RapidOCR

            engine = RapidOCR()
            _OCR_THREAD_LOCAL.engine = engine
        crop_height = min(image.height, max(180, int(round(image.height * 0.20))))
        header = np.asarray(image.crop((0, 0, image.width, crop_height)).convert("RGB"), dtype=np.uint8)
        result, _elapsed = engine(header)
    except Exception:
        return ""
    texts: list[str] = []
    for row in result or []:
        if not isinstance(row, Sequence) or len(row) < 3:
            continue
        confidence = _number(row[2], 0.0)
        text = str(row[1] or "").strip()
        if confidence >= 0.65 and text:
            texts.append(text)
    return " ".join(texts)


def _timeframe_seconds(value: str) -> int:
    match = re.fullmatch(r"(M|H|D|W|MN)(\d+)", str(value).upper())
    if not match:
        return 0
    unit, amount = match.groups()
    return int(amount) * {"M": 60, "H": 3600, "D": 86400, "W": 604800, "MN": 2592000}[unit]


def _family_name(path: Path, symbol: str) -> str:
    stem = path.stem.upper()
    stem = re.sub(r"\b(AFTER|BEFORE|BUY|SELL|TRADE|PROFIT|LOSS|ENTRY|ENTRIES|BINARY|SYSTEM|STANDARD)\b", " ", stem)
    stem = re.sub(r"\d+(?:\.\d+)?", " ", stem)
    stem = re.sub(r"[^A-Z]+", " ", stem)
    tokens = [token for token in stem.split() if len(token) >= 3]
    return f"{symbol}|{'_'.join(tokens[:6])}" if tokens else ""


def _compact_candle(row: Mapping[str, Any], *, candle_id: str, timestamp: int) -> dict[str, Any]:
    payload = {
        key: row.get(key)
        for key in (
            "open_y_px", "close_y_px", "wick_top_px", "wick_bottom_px", "body_top_px", "body_bottom_px", "direction", "palette", "parse_confidence", "spacing_confidence", "track_id"
        )
        if row.get(key) is not None
    }
    payload.update({"candle_id": candle_id, "timestamp": timestamp})
    return adapt_tracker_candle_v3(
        payload,
        closure_proof={"proven_closed": True, "event_key": f"masked-future:{candle_id}", "candle_id": candle_id},
    )


def _candle_token(row: Mapping[str, Any], scale: float) -> str:
    ohlc = candle_ohlc_v3(row)
    if ohlc is None:
        return "X"
    open_value, high, low, close = ohlc
    span = max(1e-9, high - low)
    direction = "U" if close > open_value else "D" if close < open_value else "R"
    body = min(4, int(abs(close - open_value) / span * 5.0))
    range_bucket = min(5, int(span / max(scale, 1e-9) * 2.0))
    return f"{direction}{body}{range_bucket}"


def _sequence_shingles(candles: Sequence[Mapping[str, Any]], width: int = 8) -> tuple[str, ...]:
    ohlc = [item for item in (candle_ohlc_v3(row) for row in candles) if item is not None]
    scale = float(median([max(1e-9, row[1] - row[2]) for row in ohlc])) if ohlc else 1.0
    tokens = [_candle_token(row, scale) for row in candles]
    return tuple(
        hashlib.sha1("|".join(tokens[index : index + width]).encode("ascii")).hexdigest()[:12]
        for index in range(max(0, len(tokens) - width + 1))
    )


@dataclass
class ExtractedSequenceV3:
    path: str
    source_bucket: str
    file_size: int
    mtime_ns: int
    image_hash: str
    perceptual_hash: str
    symbol: str
    timeframe: str
    metadata_source: str
    width: int
    height: int
    candles: list[dict[str, Any]] = field(default_factory=list)
    shingles: tuple[str, ...] = ()
    family_name: str = ""
    extraction_status: str = "EXTRACTED"
    extraction_reason: str = ""
    extractor_version: str = STATIC_EXTRACTOR_VERSION

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "ExtractedSequenceV3":
        payload = dict(value)
        payload.setdefault("extractor_version", "LEGACY")
        payload["candles"] = [dict(item) for item in cast(Sequence[Mapping[str, Any]], payload.get("candles", []))]
        payload["shingles"] = tuple(str(item) for item in cast(Sequence[Any], payload.get("shingles", [])))
        return cls(**payload)


def discover_corpus_images(roots: Sequence[str | Path]) -> list[tuple[Path, str]]:
    discovered: list[tuple[Path, str]] = []
    for root_value in roots:
        root = Path(root_value)
        if not root.exists():
            continue
        bucket = "BUY" if "BUY" in root.name.upper() or "BUY" in str(root.parent).upper() else "SELL" if "SELL" in root.name.upper() or "SELL" in str(root.parent).upper() else "UNLABELED"
        discovered.extend((path, bucket) for path in sorted(root.rglob("*")) if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES)
    unique: dict[str, tuple[Path, str]] = {}
    for path, bucket in discovered:
        unique[str(path.resolve()).lower()] = (path.resolve(), bucket)
    return list(unique.values())


def extract_image_sequence_v3(
    path: str | Path,
    *,
    source_bucket: str = "UNLABELED",
    maximum_width: int = 1600,
) -> ExtractedSequenceV3:
    image_path = Path(path).resolve()
    stat = image_path.stat()
    image_hash = _sha256_file(image_path)
    with Image.open(image_path) as source:
        image = source.convert("RGB")
        original_width, original_height = image.size
        filename_symbol, filename_timeframe = parse_instrument_text_v3(image_path.stem)
        ocr_text = ""
        if filename_symbol == "UNKNOWN" or filename_timeframe == "UNKNOWN":
            ocr_text = _rapidocr_header_text_v3(image)
        ocr_symbol, ocr_timeframe = parse_instrument_text_v3(ocr_text)
        symbol = filename_symbol if filename_symbol != "UNKNOWN" else ocr_symbol
        timeframe = filename_timeframe if filename_timeframe != "UNKNOWN" else ocr_timeframe
        if ocr_text and (symbol != filename_symbol or timeframe != filename_timeframe):
            metadata_source = "FILENAME_PLUS_RAPIDOCR_HEADER" if filename_symbol != "UNKNOWN" or filename_timeframe != "UNKNOWN" else "RAPIDOCR_HEADER"
        elif filename_symbol != "UNKNOWN" or filename_timeframe != "UNKNOWN":
            metadata_source = "FILENAME"
        else:
            metadata_source = "UNRESOLVED"
        if maximum_width > 0 and image.width > maximum_width:
            ratio = maximum_width / float(image.width)
            image = image.resize((maximum_width, max(64, int(round(image.height * ratio)))), Image.Resampling.LANCZOS)
        rgb = np.asarray(image, dtype=np.uint8)
        perceptual_hash = _dhash(image)
    default_tracks = extract_candle_tracks_adaptive_v3(rgb, minimum_track_length=6)
    latest_default = max((_number(row.get("center_x_px")) for row in default_tracks), default=0.0)
    track_candidates: list[list[dict[str, Any]]] = [default_tracks]
    if len(default_tracks) < 24 or latest_default < rgb.shape[1] * 0.55:
        for x_bounds in ((0.0, 0.98), (0.0, 0.50), (0.20, 0.72), (0.40, 0.92)):
            track_candidates.append(
                extract_candle_tracks_adaptive_v3(
                    rgb,
                    x_bounds=x_bounds,
                    top_ratio=0.04,
                    bottom_candidates=(0.72, 0.82, 0.90, 0.96),
                    minimum_track_length=3,
                )
            )
    # Static historical images need the longest coherent visible path. The
    # live stream's causal-right replacement is intentionally not used here.
    tracks = max(
        track_candidates,
        key=lambda rows: (
            len(rows),
            max((_number(row.get("center_x_px")) for row in rows), default=0.0),
        ),
    )
    cadence = _timeframe_seconds(timeframe) or 1
    candles: list[dict[str, Any]] = []
    for index, row in enumerate(tracks):
        try:
            candles.append(
                _compact_candle(row, candle_id=f"{image_hash[:16]}:{index:04d}", timestamp=index * cadence)
            )
        except ValueError:
            continue
    status = "EXTRACTED" if len(candles) >= 16 else "INSUFFICIENT_CANDLE_TRACK"
    reason = "" if status == "EXTRACTED" else f"Only {len(candles)} valid candles were extracted."
    shingles = _sequence_shingles(candles)
    return ExtractedSequenceV3(
        path=str(image_path),
        source_bucket=str(source_bucket).upper(),
        file_size=int(stat.st_size),
        mtime_ns=int(stat.st_mtime_ns),
        image_hash=image_hash,
        perceptual_hash=perceptual_hash,
        symbol=symbol,
        timeframe=timeframe,
        metadata_source=metadata_source,
        width=original_width,
        height=original_height,
        candles=candles,
        shingles=shingles,
        family_name=_family_name(image_path, symbol),
        extraction_status=status,
        extraction_reason=reason,
        extractor_version=STATIC_EXTRACTOR_VERSION,
    )


def _load_cache(path: Path) -> dict[str, ExtractedSequenceV3]:
    cached: dict[str, ExtractedSequenceV3] = {}
    if not path.exists():
        return cached
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                payload = json.loads(line)
                row = ExtractedSequenceV3.from_mapping(cast(Mapping[str, Any], payload))
                cached[str(Path(row.path).resolve()).lower()] = row
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
    return cached


def extract_corpus_v3(
    images: Sequence[tuple[Path, str]],
    *,
    cache_path: str | Path,
    minimum_free_gb: float = DEFAULT_RESERVE_GB,
    workers: int = 2,
    maximum_width: int = 1600,
) -> list[ExtractedSequenceV3]:
    cache = Path(cache_path)
    cache.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_cache(cache)
    selected: dict[str, ExtractedSequenceV3] = {}
    pending: list[tuple[Path, str]] = []
    for path, bucket in images:
        key = str(path.resolve()).lower()
        row = existing.get(key)
        stat = path.stat()
        if (
            row
            and row.extractor_version == STATIC_EXTRACTOR_VERSION
            and row.file_size == stat.st_size
            and row.mtime_ns == stat.st_mtime_ns
        ):
            selected[key] = row
        else:
            pending.append((path, bucket))
    if pending:
        enforce_disk_reserve(cache, minimum_free_gb=minimum_free_gb, required_bytes=64 * 1024 * 1024)
        with cache.open("a", encoding="utf-8") as handle, ThreadPoolExecutor(max_workers=max(1, min(4, int(workers)))) as executor:
            futures = {
                executor.submit(extract_image_sequence_v3, path, source_bucket=bucket, maximum_width=maximum_width): path
                for path, bucket in pending
            }
            for index, future in enumerate(as_completed(futures), 1):
                path = futures[future]
                try:
                    row = future.result()
                except Exception as exc:
                    stat = path.stat()
                    row = ExtractedSequenceV3(
                        path=str(path.resolve()), source_bucket="UNLABELED", file_size=stat.st_size, mtime_ns=stat.st_mtime_ns,
                        image_hash="", perceptual_hash="", symbol="UNKNOWN", timeframe="UNKNOWN", metadata_source="UNRESOLVED",
                        width=0, height=0, extraction_status="FAILED", extraction_reason=f"{type(exc).__name__}: {exc}",
                    )
                selected[str(Path(row.path).resolve()).lower()] = row
                handle.write(json.dumps(asdict(row), separators=(",", ":"), ensure_ascii=True, default=list) + "\n")
                handle.flush()
                if index % 20 == 0:
                    enforce_disk_reserve(cache, minimum_free_gb=minimum_free_gb, required_bytes=32 * 1024 * 1024)
    return [selected[str(path.resolve()).lower()] for path, _bucket in images if str(path.resolve()).lower() in selected]


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def group_sequence_families_v3(records: Sequence[ExtractedSequenceV3]) -> list[str]:
    union = _UnionFind(len(records))
    shingle_sets = [set(record.shingles) for record in records]
    for left_index, left in enumerate(records):
        for right_index in range(left_index + 1, len(records)):
            right = records[right_index]
            duplicate = bool(left.image_hash and left.image_hash == right.image_hash)
            visually_near = bool(left.perceptual_hash and right.perceptual_hash and _hamming(left.perceptual_hash, right.perceptual_hash) <= 6)
            named_family = bool(left.family_name and left.family_name == right.family_name and left.symbol != "UNKNOWN")
            overlap = 0.0
            if left.symbol == right.symbol and left.symbol != "UNKNOWN" and shingle_sets[left_index] and shingle_sets[right_index]:
                intersection = len(shingle_sets[left_index] & shingle_sets[right_index])
                overlap = intersection / max(1, min(len(shingle_sets[left_index]), len(shingle_sets[right_index])))
            if duplicate or visually_near or named_family or overlap >= 0.55:
                union.union(left_index, right_index)
    roots: dict[int, str] = {}
    groups: list[str] = []
    for index, record in enumerate(records):
        root = union.find(index)
        roots.setdefault(root, hashlib.sha256(f"{record.image_hash}|{record.path}".encode("utf-8")).hexdigest()[:16])
        groups.append(roots[root])
    return groups


def assign_grouped_folds_v3(group_ids: Sequence[str], *, folds: int = 5) -> list[int]:
    fold_count = max(2, int(folds))
    counts = Counter(group_ids)
    loads = [0] * fold_count
    assignments: dict[str, int] = {}
    for group, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        fold = min(range(fold_count), key=lambda index: (loads[index], index))
        assignments[group] = fold
        loads[fold] += count
    return [assignments[group] for group in group_ids]


def _median_range(candles: Sequence[Mapping[str, Any]]) -> float:
    values = [max(1e-9, row[1] - row[2]) for row in (candle_ohlc_v3(candle) for candle in candles) if row is not None]
    return float(median(values)) if values else 1.0


def _majority_side(candles: Sequence[Mapping[str, Any]]) -> str:
    buy = sell = 0
    for candle in candles:
        ohlc = candle_ohlc_v3(candle)
        if ohlc is None:
            continue
        if ohlc[3] > ohlc[0]:
            buy += 1
        elif ohlc[3] < ohlc[0]:
            sell += 1
    if buy > sell:
        return "BUY"
    if sell > buy:
        return "SELL"
    return "REST"


def build_masked_future_target_v3(
    candles: Sequence[Mapping[str, Any]],
    *,
    cutoff: int,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
) -> dict[str, Any]:
    """Reveal suffix labels after a prediction has been frozen."""

    if cutoff <= 0 or cutoff >= len(candles):
        return {"horizons": {}, "endpoint_horizons": {}, "whole_swing": {}, "pullback": False}
    prefix = list(candles[:cutoff])
    future = list(candles[cutoff:])
    anchor = candle_ohlc_v3(prefix[-1])
    if anchor is None:
        return {"horizons": {}, "endpoint_horizons": {}, "whole_swing": {}, "pullback": False}
    scale = _median_range(prefix[-64:])
    horizon_targets: dict[str, str] = {}
    endpoint_targets: dict[str, str] = {}
    for horizon in sorted({max(1, int(value)) for value in horizons}):
        if len(future) < horizon:
            continue
        window = future[:horizon]
        horizon_targets[str(horizon)] = _majority_side(window)
        endpoint = candle_ohlc_v3(window[-1])
        delta = endpoint[3] - anchor[3] if endpoint is not None else 0.0
        endpoint_targets[str(horizon)] = "BUY" if delta > scale * 0.12 else "SELL" if delta < -scale * 0.12 else "REST"
    maximum = min(max((int(value) for value in horizons), default=34), len(future))
    swing_window = future[:maximum]
    swing_side = _majority_side(swing_window)
    if swing_side == "REST" and swing_window:
        endpoint = candle_ohlc_v3(swing_window[-1])
        delta = endpoint[3] - anchor[3] if endpoint is not None else 0.0
        swing_side = "BUY" if delta > 0.0 else "SELL" if delta < 0.0 else "REST"
    cumulative: list[float] = []
    for candle in swing_window:
        ohlc = candle_ohlc_v3(candle)
        cumulative.append((ohlc[3] - anchor[3]) if ohlc is not None else (cumulative[-1] if cumulative else 0.0))
    if cumulative and swing_side == "BUY":
        swing_length = max(range(len(cumulative)), key=lambda index: cumulative[index]) + 1
    elif cumulative and swing_side == "SELL":
        swing_length = min(range(len(cumulative)), key=lambda index: cumulative[index]) + 1
    else:
        swing_length = maximum
    pullback_candles = 0
    opposite = "SELL" if swing_side == "BUY" else "BUY"
    for candle in swing_window[: min(8, max(0, swing_length))]:
        side = _majority_side([candle])
        if side == opposite:
            pullback_candles += 1
        elif side == swing_side:
            break
    return {
        "horizons": horizon_targets,
        "endpoint_horizons": endpoint_targets,
        "whole_swing": {
            "side": swing_side,
            "candles": max(1, swing_length) if swing_window else 0,
            "maximum_observed_horizon": maximum,
            "rests_included": True,
            "pullback_candles": pullback_candles,
        },
        "pullback": pullback_candles >= 2,
    }


def build_sequence_examples_v3(
    record: ExtractedSequenceV3,
    *,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    minimum_prefix: int = 24,
    stride: int = 2,
) -> list[dict[str, Any]]:
    candles = record.candles
    if len(candles) <= minimum_prefix:
        return []
    examples: list[dict[str, Any]] = []
    cutoffs = list(range(max(4, int(minimum_prefix)), len(candles), max(1, int(stride))))
    if len(candles) - 1 not in cutoffs:
        cutoffs.append(len(candles) - 1)
    for cutoff in cutoffs:
        visible = candles[max(0, cutoff - 128) : cutoff]
        candle_study = analyze_candle_sequence_v3(visible, regime="UNKNOWN", require_closed=True, max_candles=128)
        if candle_study.get("status") != "STUDIED":
            continue
        studied = cast(Sequence[Mapping[str, Any]], candle_study.get("candles", []))
        behavior = measure_market_behavior_v3(candle_study, timeframe_seconds=_timeframe_seconds(record.timeframe) or 1)
        context = build_masked_future_context_v3(studied, behavior, symbol=record.symbol, timeframe=record.timeframe)
        target = build_masked_future_target_v3(candles, cutoff=cutoff, horizons=horizons)
        if not _mapping(target.get("horizons")):
            continue
        target_features = _mapping(context.get("features"))
        target_whole = _mapping(target.get("whole_swing"))
        local_side = str(target_features.get("state_side") or "REST")
        whole_side = str(target_whole.get("side") or "REST")
        target["visible_pullback"] = bool(
            local_side in {"BUY", "SELL"}
            and whole_side in {"BUY", "SELL"}
            and local_side != whole_side
            and str(target_features.get("age_bucket")) in {"2", "3_4"}
        )
        target["visible_local_side"] = local_side
        examples.append(
            {
                "image_hash": record.image_hash,
                "path": record.path,
                "symbol": record.symbol,
                "timeframe": record.timeframe,
                "source_bucket": record.source_bucket,
                "cutoff": cutoff,
                "visible_prefix_hash": hashlib.sha256("|".join(str(row.get("candle_id")) for row in visible).encode("utf-8")).hexdigest(),
                "context": context,
                "target": target,
                "baseline_side": _mapping(context.get("features")).get("inner") and (
                    "BUY" if "BULL" in str(_mapping(context.get("features")).get("inner")) or str(_mapping(context.get("features")).get("inner")) == "UP" else
                    "SELL" if "BEAR" in str(_mapping(context.get("features")).get("inner")) or str(_mapping(context.get("features")).get("inner")) == "DOWN" else
                    str(_mapping(context.get("features")).get("state_side", "REST"))
                ),
            }
        )
    return examples


def _fit_examples(examples: Iterable[Mapping[str, Any]], horizons: Sequence[int]) -> dict[str, Any]:
    artifact = new_masked_future_model_artifact_v3(horizons)
    count = 0
    for example in examples:
        update_masked_future_model_v3(artifact, _mapping(example.get("context")), _mapping(example.get("target")))
        count += 1
    artifact["training"] = {"example_count": count}
    return finalize_masked_future_model_v3(artifact)


def _binary_probabilities(row: Mapping[str, Any]) -> tuple[float, float]:
    probabilities = _mapping(row.get("probabilities"))
    buy = max(0.0, _number(probabilities.get("BUY")))
    sell = max(0.0, _number(probabilities.get("SELL")))
    total = buy + sell
    return ((buy / total, sell / total) if total > 0.0 else (0.5, 0.5))


def _metric_summary(rows: Sequence[Mapping[str, Any]], *, actual_key: str, prediction_key: str = "predicted_side") -> dict[str, Any]:
    scored = [row for row in rows if str(row.get(actual_key)) in {"BUY", "SELL"}]
    if not scored:
        return {"scored": 0, "accuracy": None, "baseline_accuracy": None, "uplift": None, "brier": None, "log_loss": None}
    correct = sum(str(row.get(prediction_key)) == str(row.get(actual_key)) for row in scored)
    baseline_correct = sum(str(row.get("baseline_side")) == str(row.get(actual_key)) for row in scored)
    brier_values: list[float] = []
    log_values: list[float] = []
    for row in scored:
        buy, sell = _binary_probabilities(row)
        actual_buy = 1.0 if row.get(actual_key) == "BUY" else 0.0
        brier_values.append(((buy - actual_buy) ** 2 + (sell - (1.0 - actual_buy)) ** 2) / 2.0)
        probability = buy if actual_buy else sell
        log_values.append(-math.log(max(1e-9, probability)))
    accuracy = correct / len(scored)
    baseline = baseline_correct / len(scored)
    return {
        "scored": len(scored),
        "accuracy": round(accuracy, 6),
        "baseline_accuracy": round(baseline, 6),
        "uplift": round(accuracy - baseline, 6),
        "brier": round(sum(brier_values) / len(brier_values), 6),
        "log_loss": round(sum(log_values) / len(log_values), 6),
    }


def cross_validate_masked_future_v3(
    examples_by_record: Sequence[Sequence[Mapping[str, Any]]],
    fold_by_record: Sequence[int],
    *,
    folds: int,
    horizons: Sequence[int],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    predictions: list[dict[str, Any]] = []
    for fold in range(max(2, int(folds))):
        training = [example for index, rows in enumerate(examples_by_record) if fold_by_record[index] != fold for example in rows]
        model = MaskedFutureBehaviorModelV3(_fit_examples(training, horizons))
        for record_index, rows in enumerate(examples_by_record):
            if fold_by_record[record_index] != fold:
                continue
            for example in rows:
                prediction = model.predict_context(_mapping(example.get("context")))
                target = _mapping(example.get("target"))
                target_horizons = _mapping(target.get("horizons"))
                endpoint_horizons = _mapping(target.get("endpoint_horizons"))
                for horizon_row in cast(Sequence[Mapping[str, Any]], prediction.get("horizons", [])):
                    horizon = str(horizon_row.get("candles"))
                    if horizon not in target_horizons:
                        continue
                    predictions.append(
                        {
                            "fold": fold,
                            "image_hash": example.get("image_hash"),
                            "path": example.get("path"),
                            "symbol": example.get("symbol"),
                            "timeframe": example.get("timeframe"),
                            "cutoff": example.get("cutoff"),
                            "feature_digest": _mapping(example.get("context")).get("feature_digest"),
                            "horizon": int(horizon),
                            "predicted_side": horizon_row.get("predicted_side"),
                            "probabilities": horizon_row.get("probabilities"),
                            "support": horizon_row.get("support"),
                            "actual_majority": target_horizons.get(horizon),
                            "actual_endpoint": endpoint_horizons.get(horizon),
                            "baseline_side": example.get("baseline_side"),
                            "source_bucket": example.get("source_bucket"),
                            "folder_label_used_as_target": False,
                        }
                    )
                whole = _mapping(prediction.get("whole_swing"))
                actual_whole = _mapping(target.get("whole_swing"))
                if actual_whole:
                    predictions.append(
                        {
                            "fold": fold,
                            "image_hash": example.get("image_hash"),
                            "path": example.get("path"),
                            "symbol": example.get("symbol"),
                            "timeframe": example.get("timeframe"),
                            "cutoff": example.get("cutoff"),
                            "feature_digest": _mapping(example.get("context")).get("feature_digest"),
                            "horizon": "WHOLE_SWING",
                            "predicted_side": whole.get("predicted_side"),
                            "probabilities": whole.get("probabilities"),
                            "support": whole.get("support"),
                            "actual_whole_swing": actual_whole.get("side"),
                            "actual_swing_candles": actual_whole.get("candles"),
                            "predicted_swing_candles": whole.get("expected_candles"),
                            "pullback": target.get("pullback"),
                            "visible_pullback": target.get("visible_pullback"),
                            "visible_local_side": target.get("visible_local_side"),
                            "baseline_side": example.get("baseline_side"),
                            "source_bucket": example.get("source_bucket"),
                            "folder_label_used_as_target": False,
                        }
                    )
    horizon_metrics: dict[str, Any] = {}
    for horizon in sorted({int(value) for value in horizons}):
        subset = [row for row in predictions if row.get("horizon") == horizon]
        metrics = _metric_summary(subset, actual_key="actual_majority")
        metrics["endpoint_accuracy"] = _metric_summary(subset, actual_key="actual_endpoint").get("accuracy")
        horizon_metrics[str(horizon)] = metrics
    whole_rows = [row for row in predictions if row.get("horizon") == "WHOLE_SWING"]
    whole_metrics = _metric_summary(whole_rows, actual_key="actual_whole_swing")
    length_errors = [
        abs(_number(row.get("predicted_swing_candles")) - _number(row.get("actual_swing_candles")))
        for row in whole_rows
        if _number(row.get("predicted_swing_candles")) > 0.0
    ]
    whole_metrics["horizon_mae_candles"] = round(sum(length_errors) / len(length_errors), 6) if length_errors else None
    future_pullback_rows = [row for row in whole_rows if row.get("pullback") is True]
    future_pullback_metrics = _metric_summary(future_pullback_rows, actual_key="actual_whole_swing")
    whole_metrics["future_counter_move_case_count"] = len(future_pullback_rows)
    whole_metrics["future_counter_move_case_accuracy"] = future_pullback_metrics.get("accuracy")
    visible_pullback_rows = [row for row in whole_rows if row.get("visible_pullback") is True]
    visible_pullback_metrics = _metric_summary(visible_pullback_rows, actual_key="actual_whole_swing")
    whole_metrics["visible_pullback_case_count"] = len(visible_pullback_rows)
    whole_metrics["visible_pullback_case_accuracy"] = visible_pullback_metrics.get("accuracy")
    whole_metrics["visible_pullback_baseline_accuracy"] = visible_pullback_metrics.get("baseline_accuracy")
    whole_metrics["visible_pullback_uplift"] = visible_pullback_metrics.get("uplift")
    primary = [horizon_metrics.get(str(value), {}) for value in (13, 21) if horizon_metrics.get(str(value), {}).get("accuracy") is not None]
    primary_scored = sum(int(row.get("scored", 0)) for row in primary)
    primary_accuracy = sum(_number(row.get("accuracy")) * int(row.get("scored", 0)) for row in primary) / max(1, primary_scored)
    primary_baseline = sum(_number(row.get("baseline_accuracy")) * int(row.get("scored", 0)) for row in primary) / max(1, primary_scored)
    visible_pullback_scored = int(visible_pullback_metrics.get("scored", 0) or 0)
    visible_pullback_accuracy = _number(visible_pullback_metrics.get("accuracy"), 0.0)
    visible_pullback_baseline = _number(visible_pullback_metrics.get("baseline_accuracy"), 0.0)
    visible_pullback_proven = bool(
        visible_pullback_scored >= 100
        and visible_pullback_accuracy >= 0.52
        and visible_pullback_accuracy > visible_pullback_baseline
    )
    eligible = bool(
        primary_scored >= 500
        and primary_accuracy >= 0.52
        and primary_accuracy > primary_baseline
        and visible_pullback_proven
    )
    summary = {
        "schema_version": "PG_MASKED_FUTURE_CROSS_VALIDATION_V3",
        "folds": max(2, int(folds)),
        "prediction_count": len(predictions),
        "horizons": horizon_metrics,
        "whole_swing": whole_metrics,
        "promotion": {
            "eligible": eligible,
            "reason": "OUT_OF_SAMPLE_UPLIFT_AND_VISIBLE_PULLBACK_PROVEN" if eligible else "PROMOTION_CONTRACT_NOT_PROVEN",
            "primary_horizons": [13, 21],
            "primary_scored": primary_scored,
            "primary_accuracy": round(primary_accuracy, 6),
            "primary_baseline_accuracy": round(primary_baseline, 6),
            "minimum_accuracy": 0.52,
            "minimum_scored": 500,
            "visible_pullback_scored": visible_pullback_scored,
            "visible_pullback_accuracy": round(visible_pullback_accuracy, 6),
            "visible_pullback_baseline_accuracy": round(visible_pullback_baseline, 6),
            "visible_pullback_required_accuracy": 0.52,
        },
        "leakage_audit": {
            "grouped_by_image_family_before_split": True,
            "all_cutoffs_from_one_image_in_one_fold": True,
            "folder_buy_sell_label_used_as_target": False,
            "future_candles_in_context": False,
        },
    }
    return summary, predictions


def _write_json(path: Path, payload: Mapping[str, Any], *, reserve_gb: float) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True, default=str).encode("utf-8")
    enforce_disk_reserve(path, minimum_free_gb=reserve_gb, required_bytes=len(encoded) * 2)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(encoded)
    temporary.replace(path)


def _write_predictions(path: Path, rows: Sequence[Mapping[str, Any]], *, reserve_gb: float) -> None:
    payload = "".join(json.dumps(row, separators=(",", ":"), ensure_ascii=True, default=str) + "\n" for row in rows).encode("utf-8")
    compressed = gzip.compress(payload, compresslevel=9, mtime=0)
    enforce_disk_reserve(path, minimum_free_gb=reserve_gb, required_bytes=len(compressed) * 2)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(compressed)


def render_masked_future_report_v3(summary: Mapping[str, Any]) -> str:
    corpus = _mapping(summary.get("corpus"))
    validation = _mapping(summary.get("cross_validation"))
    lines = [
        "# PhoenixGuard V3 Masked-Future Replay",
        "",
        "## Leakage contract",
        "",
        "- Every prediction was built from a candle prefix only.",
        "- All cutoffs from one image family stayed in the same fold.",
        "- BUY/SELL folders were provenance only and were never prediction targets.",
        "- Future annotations and overlays were discarded before V3 feature extraction.",
        "",
        "## Corpus",
        "",
        f"- Discovered images: {corpus.get('discovered_images')}",
        f"- Extracted sequences: {corpus.get('extracted_sequences')}",
        f"- Failed/insufficient sequences: {corpus.get('failed_sequences')}",
        f"- Prefix examples: {corpus.get('prefix_examples')}",
        f"- Near-duplicate families: {corpus.get('family_count')}",
        "",
        "## Out-of-sample majority-direction score",
        "",
    ]
    for horizon, metrics in _mapping(validation.get("horizons")).items():
        row = _mapping(metrics)
        lines.append(
            f"- {horizon} candles: accuracy={row.get('accuracy')} baseline={row.get('baseline_accuracy')} uplift={row.get('uplift')} endpoint_accuracy={row.get('endpoint_accuracy')} scored={row.get('scored')}"
        )
    whole = _mapping(validation.get("whole_swing"))
    lines.extend(
        [
            "",
            "## Whole swing including rests",
            "",
            f"- Accuracy: {whole.get('accuracy')}",
            f"- Baseline accuracy: {whole.get('baseline_accuracy')}",
            f"- Horizon MAE in candles: {whole.get('horizon_mae_candles')}",
            f"- Visible 2-4 candle pullback cases: {whole.get('visible_pullback_case_count')}",
            f"- Visible pullback resolution accuracy: {whole.get('visible_pullback_case_accuracy')}",
            f"- Visible pullback baseline accuracy: {whole.get('visible_pullback_baseline_accuracy')}",
            f"- Future counter-move cases: {whole.get('future_counter_move_case_count')}",
            f"- Future counter-move forecast accuracy: {whole.get('future_counter_move_case_accuracy')}",
            "",
            "## Promotion",
            "",
            f"- {_mapping(validation.get('promotion'))}",
            "",
            "This report measures directional/state prediction. It is not a profitability guarantee and grants no execution permission.",
            "",
        ]
    )
    return "\n".join(lines)


def run_masked_future_replay_v3(
    *,
    roots: Sequence[str | Path],
    output_dir: str | Path,
    cache_path: str | Path,
    model_path: str | Path,
    horizons: Sequence[int] = DEFAULT_HORIZONS,
    minimum_prefix: int = 24,
    stride: int = 2,
    folds: int = 5,
    workers: int = 2,
    minimum_free_gb: float = DEFAULT_RESERVE_GB,
    maximum_width: int = 1600,
) -> dict[str, Any]:
    output = Path(output_dir)
    enforce_disk_reserve(output, minimum_free_gb=minimum_free_gb, required_bytes=128 * 1024 * 1024)
    images = discover_corpus_images(roots)
    records = extract_corpus_v3(
        images, cache_path=cache_path, minimum_free_gb=minimum_free_gb, workers=workers, maximum_width=maximum_width
    )
    valid = [record for record in records if record.extraction_status == "EXTRACTED"]
    groups = group_sequence_families_v3(valid)
    fold_by_record = assign_grouped_folds_v3(groups, folds=folds)
    examples_by_record = [
        build_sequence_examples_v3(record, horizons=horizons, minimum_prefix=minimum_prefix, stride=stride)
        for record in valid
    ]
    validation, predictions = cross_validate_masked_future_v3(
        examples_by_record, fold_by_record, folds=folds, horizons=horizons
    )
    all_examples = [example for rows in examples_by_record for example in rows]
    artifact = _fit_examples(all_examples, horizons)
    artifact["calibration"] = validation
    artifact["promotion"] = _mapping(validation.get("promotion"))
    training = _mapping(artifact.get("training"))
    training.update(
        {
            "image_count": len(valid),
            "image_family_count": len(set(groups)),
            "source_bucket_counts": dict(Counter(record.source_bucket for record in valid)),
            "symbol_counts": dict(Counter(record.symbol for record in valid)),
            "timeframe_counts": dict(Counter(record.timeframe for record in valid)),
            "folder_labels_used_as_targets": False,
        }
    )
    artifact["training"] = training
    model_destination = Path(model_path)
    model_payload_size = len(json.dumps(artifact, separators=(",", ":"), ensure_ascii=True, default=str))
    enforce_disk_reserve(model_destination, minimum_free_gb=minimum_free_gb, required_bytes=model_payload_size)
    save_masked_future_model_v3(artifact, model_destination)
    summary = {
        "schema_version": MASKED_FUTURE_REPLAY_SCHEMA_VERSION,
        "corpus": {
            "discovered_images": len(images),
            "extracted_sequences": len(valid),
            "failed_sequences": len(records) - len(valid),
            "prefix_examples": len(all_examples),
            "family_count": len(set(groups)),
            "source_bucket_counts": dict(Counter(record.source_bucket for record in records)),
            "symbol_counts": dict(Counter(record.symbol for record in valid)),
            "timeframe_counts": dict(Counter(record.timeframe for record in valid)),
        },
        "cross_validation": validation,
        "model": {
            "path": str(model_destination),
            "compressed_bytes": model_destination.stat().st_size,
            "promotion_eligible": _mapping(validation.get("promotion")).get("eligible") is True,
        },
        "disk_contract": {
            "minimum_free_gb": minimum_free_gb,
            "free_gb_after_run": round(available_free_gb(output), 3),
            "raw_images_duplicated": False,
            "prediction_output_compressed": True,
        },
    }
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "summary.json", summary, reserve_gb=minimum_free_gb)
    _write_predictions(output / "predictions.jsonl.gz", predictions, reserve_gb=minimum_free_gb)
    failures = [asdict(record) | {"candles": [], "shingles": []} for record in records if record.extraction_status != "EXTRACTED"]
    _write_json(output / "extraction_failures.json", {"failures": failures}, reserve_gb=minimum_free_gb)
    report = render_masked_future_report_v3(summary).encode("utf-8")
    enforce_disk_reserve(output, minimum_free_gb=minimum_free_gb, required_bytes=len(report))
    (output / "report.md").write_bytes(report)
    return summary


__all__ = [
    "DEFAULT_RESERVE_GB",
    "DiskReserveError",
    "ExtractedSequenceV3",
    "MASKED_FUTURE_REPLAY_SCHEMA_VERSION",
    "assign_grouped_folds_v3",
    "available_free_gb",
    "build_masked_future_target_v3",
    "build_sequence_examples_v3",
    "cross_validate_masked_future_v3",
    "discover_corpus_images",
    "enforce_disk_reserve",
    "extract_corpus_v3",
    "extract_image_sequence_v3",
    "group_sequence_families_v3",
    "render_masked_future_report_v3",
    "parse_instrument_text_v3",
    "run_masked_future_replay_v3",
]
