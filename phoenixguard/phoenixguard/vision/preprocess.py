"""
PhoenixGuard SIGE-VLA 3.0 — Preprocessing Pipeline
====================================================
Skills wired:
  - Computer Graphics & Multimedia (CLAHE, bicubic resize, Hough crop)
  - Discrete Mathematics (set complement: keep price candles, discard indicators)
  - Formal Language & Automata Theory (regex FSM rejecting indicator tokens)
  - Predicate Logic (price digit extraction as typed float tensor)
  - Computer Vision (edge detection via gradient, Hough-line price-region crop)
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, cast
import hashlib
import importlib

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageOps, ImageDraw

_torch = None
try:
    import torch as _torch
except Exception:
    pass

torch = _torch
_torch_ok = _torch is not None

# ── Formal Language & Automata Theory — indicator token FSM ──────────────────
# Regex automaton states:
#   ACCEPT  → no indicator token found in text
#   REJECT  → indicator token detected (text is contaminated)
_INDICATOR_PATTERN = re.compile(
    r"\b(ATR|MA\d*|EMA\d*|SMA\d*|WMA|BOLLINGER|BB|RSI|MACD|CCI|ADX|OBV|"
    r"STOCH(?:ASTIC)?|ICHIMOKU|VWAP|PIVOT|FIBONACCI|FIB|PARABOLIC|SAR|"
    r"ENVELOPE|DONCHIAN|KELTNER|WILLIAMS|MFI|DMI|TRIX|PPO|SUPERTREND)\b",
    re.IGNORECASE,
)

# Predicate Logic — price digit extractor (valid 4–6 decimal forex price)
_PRICE_RE = re.compile(r"\b(\d{1,5}\.\d{2,6})\b")


def indicator_regex_filter(text: str) -> tuple[bool, str]:
    """
    Formal Language & Automata Theory FSM.
    Returns (is_clean, cleaned_text).
    REJECT state: any indicator token found → strip and flag.
    ACCEPT state: text is clean raw price action.
    """
    contaminated = bool(_INDICATOR_PATTERN.search(text))
    clean = _INDICATOR_PATTERN.sub("[overlay_removed]", text)
    return not contaminated, clean


def extract_price_floats(text: str) -> list[float]:
    """
    Predicate Logic gate — extract visible price digits from OCR or parsed chart text,
    convert to typed float tensor. Rejects non-price numbers.
    """
    raw_matches = _PRICE_RE.findall(text)
    prices: list[float] = []
    for m in raw_matches:
        try:
            f = float(m)
            # Accept only plausible forex price ranges
            if 0.0001 < f < 99999.0:
                prices.append(f)
        except ValueError:
            pass
    return sorted(set(prices))


def prices_to_tensor(prices: list[float]):
    """Convert price float list to torch tensor (or numpy array if torch absent)."""
    arr = np.array(prices, dtype=np.float32) if prices else np.zeros(1, dtype=np.float32)
    if _torch_ok and torch is not None:
        return cast(Any, torch).from_numpy(arr)
    return arr


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}


def _bytes_to_visual(data: bytes, min_side: int = 512) -> Image.Image:
    if not data:
        data = b"\x00"
    arr = np.frombuffer(data, dtype=np.uint8)
    side = int(np.ceil(np.sqrt(arr.size)))
    padded = np.pad(arr, (0, side * side - arr.size), mode="wrap")
    img_arr = padded.reshape(side, side)
    img = Image.fromarray(img_arr, mode="L").convert("RGB")
    if side < min_side:
        img = img.resize((min_side, min_side), Image.Resampling.BILINEAR)
    return img


def _extract_text_preview(path: Path, max_chars: int = 1000) -> str:
    ext = path.suffix.lower()
    text = ""
    if ext in {".txt", ".md", ".csv", ".json", ".xml", ".log", ".py"}:
        text = path.read_text(encoding="utf-8", errors="ignore")
    elif ext == ".pdf":
        try:
            pypdf = importlib.import_module("pypdf")
            PdfReader = getattr(pypdf, "PdfReader")
            reader = PdfReader(str(path))
            for page in reader.pages[:2]:
                text += page.extract_text() or ""
        except Exception:
            text = ""
    elif ext == ".docx":
        try:
            docx2txt = importlib.import_module("docx2txt")
            text = docx2txt.process(str(path))
        except Exception:
            text = ""
    return " ".join(text.split())[:max_chars]


def _overlay_text(img: Image.Image, text: str) -> Image.Image:
    if not text:
        return img
    overlay = img.copy()
    draw = ImageDraw.Draw(overlay)
    block = text[:800]
    draw.rectangle([(10, 10), (overlay.width - 10, min(180, overlay.height - 10))], fill=(0, 0, 0))
    draw.text((20, 20), block, fill=(255, 255, 255))
    return overlay


def load_any_file_as_image(file_path: str | Path) -> tuple[Image.Image, dict[str, Any]]:
    path = Path(file_path)
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()

    if path.suffix.lower() in IMAGE_EXTS:
        img = Image.open(path).convert("RGB")
        source_type = "image"
    else:
        text_preview = _extract_text_preview(path)
        img = _bytes_to_visual(raw)
        img = _overlay_text(img, text_preview)
        source_type = "converted_non_image"

    meta: dict[str, Any] = {
        "file_name": path.name,
        "file_ext": path.suffix.lower(),
        "sha256": sha,
        "source_type": source_type,
        "size_bytes": len(raw),
        "width": img.width,
        "height": img.height,
    }
    return img, meta


# ── CLAHE (Contrast Limited Adaptive Histogram Equalization) ──────────────────
def apply_clahe(img: Image.Image, clip_limit: int = 3) -> Image.Image:
    """
    Computer Graphics & Multimedia — CLAHE for candle contrast enhancement.
    Improves dark/light candle body distinction on compressed screenshots.
    Works channel-by-channel on RGB.
    """
    try:
        import cv2  # type: ignore
        arr = np.asarray(img, dtype=np.uint8)
        clahe_fn = cv2.createCLAHE(clipLimit=float(clip_limit), tileGridSize=(8, 8))
        lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
        lab[:, :, 0] = clahe_fn.apply(lab[:, :, 0])
        enhanced = cv2.cvtColor(lab, cv2.COLOR_LAB2RGB)
        return Image.fromarray(enhanced)
    except ImportError:
        # Pure-PIL fallback: histogram equalization per channel
        r, g, b = img.split()
        r_eq = ImageOps.equalize(r)
        g_eq = ImageOps.equalize(g)
        b_eq = ImageOps.equalize(b)
        return Image.merge("RGB", (r_eq, g_eq, b_eq))
    except Exception:
        return img


# ── Hough-line price-area crop ────────────────────────────────────────────────
def _full_image_crop_meta(img: Image.Image, *, reason: str, method: str) -> dict[str, Any]:
    return {
        "applied": False,
        "reason": str(reason),
        "method": str(method),
        "pixel_bbox": [0, 0, int(img.width), int(img.height)],
        "normalized_bbox": [0.0, 0.0, 1.0, 1.0],
        "width": int(img.width),
        "height": int(img.height),
        "source_width": int(img.width),
        "source_height": int(img.height),
    }


def _crop_meta_from_bbox(
    img: Image.Image,
    *,
    bbox: tuple[int, int, int, int],
    method: str,
    reason: str,
) -> dict[str, Any]:
    x0, y0, x1, y1 = bbox
    width = max(1, int(x1 - x0))
    height = max(1, int(y1 - y0))
    return {
        "applied": bool(x0 > 0 or y0 > 0 or x1 < img.width or y1 < img.height),
        "reason": str(reason),
        "method": str(method),
        "pixel_bbox": [int(x0), int(y0), int(x1), int(y1)],
        "normalized_bbox": [
            float(max(0.0, min(1.0, x0 / max(img.width, 1)))),
            float(max(0.0, min(1.0, y0 / max(img.height, 1)))),
            float(max(0.0, min(1.0, x1 / max(img.width, 1)))),
            float(max(0.0, min(1.0, y1 / max(img.height, 1)))),
        ],
        "width": width,
        "height": height,
        "source_width": int(img.width),
        "source_height": int(img.height),
    }


def auto_crop_price_area_with_meta(img: Image.Image) -> tuple[Image.Image, dict[str, Any]]:
    """
    Computer Vision — Hough line detection to isolate the price chart area.
    Strategy:
      1. Convert to grayscale, detect horizontal Canny edges.
      2. Use Hough transform to find dominant horizontal lines (axis boundaries).
      3. Crop to the price chart bounding box, excluding indicator sub-panels.
      4. If detection fails, return original image (safe fallback).

    The chart area is identified as the LARGEST contiguous rectangular region
    between the topmost and bottommost price-axis horizontal lines.
    """
    try:
        import cv2  # type: ignore
        gray = np.asarray(img.convert("L"), dtype=np.uint8)
        h, w = gray.shape

        # Canny edge detection
        edges = cv2.Canny(gray, threshold1=30, threshold2=80, apertureSize=3)

        # Probabilistic Hough transform for horizontal lines
        raw_lines = cast(object | None, cv2.HoughLinesP(
            edges, rho=1, theta=np.pi / 180.0,
            threshold=max(w // 5, 50),
            minLineLength=max(w // 4, 80),
            maxLineGap=20
        ))
        if raw_lines is None:
            return img, _full_image_crop_meta(img, reason="no_hough_lines", method="cv2_hough")
        lines = np.asarray(raw_lines)
        if len(lines) == 0:
            return img, _full_image_crop_meta(img, reason="no_hough_lines", method="cv2_hough")

        # Collect Y-coordinates of near-horizontal lines
        y_coords: list[int] = []
        for line in lines:
            x1, y1, x2, y2 = line[0]
            angle = abs(np.degrees(np.arctan2(y2 - y1, x2 - x1 + 1e-6)))
            if angle < 8.0:  # near-horizontal
                y_coords.append((y1 + y2) // 2)

        if len(y_coords) < 2:
            return img, _full_image_crop_meta(img, reason="insufficient_horizontal_lines", method="cv2_hough")

        y_coords_sorted: list[int] = sorted(set(y_coords))

        # Find the largest gap between consecutive horizontal lines
        # — this is typically the main price panel
        if len(y_coords_sorted) < 2:
            return img, _full_image_crop_meta(img, reason="insufficient_line_gaps", method="cv2_hough")

        gaps: list[tuple[int, int]] = [
            (y_coords_sorted[i + 1] - y_coords_sorted[i], i)
            for i in range(len(y_coords_sorted) - 1)
        ]
        gaps.sort(reverse=True)
        best_gap_idx = gaps[0][1]

        # Crop boundaries: add small padding
        y_top = max(0, y_coords_sorted[best_gap_idx] - 10)
        y_bot = min(h, y_coords_sorted[best_gap_idx + 1] + 10)

        # Only crop if the resulting region is at least 30% of original height
        if (y_bot - y_top) < 0.30 * h:
            return img, _full_image_crop_meta(img, reason="gap_too_small", method="cv2_hough")

        bbox = (0, int(y_top), int(w), int(y_bot))
        cropped = img.crop(bbox)
        return cropped, _crop_meta_from_bbox(img, bbox=bbox, method="cv2_hough", reason="largest_horizontal_gap")

    except ImportError:
        # cv2 not available — pure numpy edge-based crop approximation
        return _numpy_crop_price_area_with_meta(img)
    except Exception:
        return img, _full_image_crop_meta(img, reason="crop_exception", method="safe_fallback")


def auto_crop_price_area(img: Image.Image) -> Image.Image:
    cropped, _ = auto_crop_price_area_with_meta(img)
    return cropped


def _numpy_crop_price_area_with_meta(img: Image.Image) -> tuple[Image.Image, dict[str, Any]]:
    """
    Fallback price-area crop using numpy only.
    Detects horizontal regions of high gradient variance (candle activity).
    """
    arr = np.asarray(img.convert("L"), dtype=np.float32)
    h, _ = arr.shape
    # Row-wise variance (rows with high variance = price action area)
    row_var: NDArray[np.float32] = np.asarray(np.var(arr, axis=1), dtype=np.float32)
    # Find rows with above-median variance
    median_var = float(np.median(row_var))
    active_rows = np.where(row_var > median_var * 1.2)[0]
    if len(active_rows) < 10:
        return img, _full_image_crop_meta(img, reason="insufficient_active_rows", method="numpy_variance")
    y_top = max(0, int(active_rows[0]) - 5)
    y_bot = min(h, int(active_rows[-1]) + 5)
    if (y_bot - y_top) < 0.25 * h:
        return img, _full_image_crop_meta(img, reason="variance_band_too_small", method="numpy_variance")
    bbox = (0, int(y_top), int(img.width), int(y_bot))
    return (
        img.crop(bbox),
        _crop_meta_from_bbox(img, bbox=bbox, method="numpy_variance", reason="active_variance_band"),
    )


# ── Main normalization pipeline ───────────────────────────────────────────────
def normalize_for_model(
    img: Image.Image,
    out_size: int = 1024,
    apply_crop: bool = True,
    apply_clahe_flag: bool = True,
) -> Image.Image:
    """
    Full SIGE-VLA 3.0 normalization pipeline:
    1. EXIF correction
    2. Price-area crop (Hough lines)
    3. CLAHE contrast enhancement
    4. 1024×1024 bicubic resize

    Computer Graphics & Multimedia + Discrete Mathematics (keep price area only).
    """
    image = ImageOps.exif_transpose(img)
    image = image.convert("RGB")

    # Step 1: Auto-crop to price action area
    if apply_crop:
        image = auto_crop_price_area(image)

    # Step 2: CLAHE contrast enhancement for candle clarity
    if apply_clahe_flag:
        image = apply_clahe(image, clip_limit=3)

    # Step 3: Bicubic resize to 1024×1024
    image = image.resize((out_size, out_size), Image.Resampling.BICUBIC)

    return image


def image_to_tensor(img: Image.Image):
    """Convert PIL image to normalized float tensor (C, H, W)."""
    arr = np.asarray(img).astype(np.float32) / 255.0
    arr = np.transpose(arr, (2, 0, 1))
    # Bug fix: removed duplicate definition that followed this one;
    # use _TORCH_OK (set at import time) as the authoritative check.
    if not _torch_ok or torch is None:
        return arr
    return cast(Any, torch).from_numpy(arr).unsqueeze(0)
