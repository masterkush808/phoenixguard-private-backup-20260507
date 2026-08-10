"""Physical future-region masking for screenshot-only V3 replay."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

import numpy as np
from numpy.typing import NDArray
from PIL import Image


MASKED_IMAGE_REGION_SCHEMA_VERSION = "PG_MASKED_IMAGE_REGION_V3"
DEFAULT_MASK_COLOR: tuple[int, int, int] = (7, 10, 12)


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return float(default)


def _pixel_digest(values: NDArray[np.uint8]) -> str:
    contiguous = np.ascontiguousarray(values)
    return hashlib.sha256(contiguous.tobytes()).hexdigest()


@dataclass(frozen=True)
class MaskRectangleV3:
    x1: int
    y1: int
    x2: int
    y2: int

    @classmethod
    def parse(cls, value: str) -> "MaskRectangleV3":
        parts = [int(part.strip()) for part in str(value).split(",")]
        if len(parts) != 4:
            raise ValueError("PG_MASK_RECT_REQUIRES_X1_Y1_X2_Y2")
        return cls(*parts)

    def clamp(self, width: int, height: int) -> "MaskRectangleV3":
        x1 = max(0, min(int(width) - 1, int(self.x1)))
        y1 = max(0, min(int(height) - 1, int(self.y1)))
        x2 = max(x1 + 1, min(int(width), int(self.x2)))
        y2 = max(y1 + 1, min(int(height), int(self.y2)))
        return MaskRectangleV3(x1, y1, x2, y2)

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.x1, self.y1, self.x2, self.y2


def load_analysis_image_v3(
    path: str | Path,
    *,
    maximum_width: int = 1600,
) -> Image.Image:
    with Image.open(Path(path)) as source:
        image = source.convert("RGB")
    if maximum_width > 0 and image.width > int(maximum_width):
        ratio = int(maximum_width) / float(image.width)
        image = image.resize(
            (int(maximum_width), max(64, int(round(image.height * ratio)))),
            Image.Resampling.LANCZOS,
        )
    return image


def automatic_mask_rectangle_v3(
    candles: Sequence[Mapping[str, Any]],
    *,
    cutoff: int,
    width: int,
    height: int,
) -> MaskRectangleV3:
    if cutoff <= 0 or cutoff >= len(candles):
        raise ValueError("PG_AUTO_MASK_CUTOFF_OUT_OF_RANGE")
    left = _number(candles[cutoff - 1].get("center_x_px"), -1.0)
    right = _number(candles[cutoff].get("center_x_px"), -1.0)
    if left < 0.0 or right < 0.0 or right <= left:
        raise ValueError("PG_AUTO_MASK_REQUIRES_ORDERED_CANDLE_X_COORDINATES")
    boundary = int(round((left + right) / 2.0))
    return MaskRectangleV3(boundary, 0, int(width), int(height)).clamp(
        int(width),
        int(height),
    )


def create_masked_image_v3(
    source_path: str | Path,
    destination: str | Path,
    *,
    rectangle: MaskRectangleV3,
    maximum_width: int = 1600,
    mask_color: tuple[int, int, int] = DEFAULT_MASK_COLOR,
) -> dict[str, Any]:
    image = load_analysis_image_v3(source_path, maximum_width=maximum_width)
    original = np.asarray(image, dtype=np.uint8)
    rect = rectangle.clamp(image.width, image.height)
    masked = original.copy()
    masked[rect.y1 : rect.y2, rect.x1 : rect.x2] = np.asarray(
        mask_color,
        dtype=np.uint8,
    )
    destination_path = Path(destination)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(masked, mode="RGB").save(
        destination_path,
        format="PNG",
        optimize=True,
        compress_level=9,
    )
    outside_original = original.copy()
    outside_masked = masked.copy()
    outside_original[rect.y1 : rect.y2, rect.x1 : rect.x2] = 0
    outside_masked[rect.y1 : rect.y2, rect.x1 : rect.x2] = 0
    original_inside = original[rect.y1 : rect.y2, rect.x1 : rect.x2]
    masked_inside = masked[rect.y1 : rect.y2, rect.x1 : rect.x2]
    expected_fill = np.asarray(mask_color, dtype=np.uint8)
    return {
        "schema_version": MASKED_IMAGE_REGION_SCHEMA_VERSION,
        "rectangle": asdict(rect),
        "analysis_width": image.width,
        "analysis_height": image.height,
        "original_pixel_hash": _pixel_digest(original),
        "masked_pixel_hash": _pixel_digest(masked),
        "original_hidden_region_hash": _pixel_digest(original_inside),
        "masked_hidden_region_hash": _pixel_digest(masked_inside),
        "outside_region_original_hash": _pixel_digest(outside_original),
        "outside_region_masked_hash": _pixel_digest(outside_masked),
        "masked_file_hash": hashlib.sha256(destination_path.read_bytes()).hexdigest(),
        "hidden_region_uniform": bool(np.all(masked_inside == expected_fill)),
        "hidden_region_pixel_count": int(masked_inside.shape[0] * masked_inside.shape[1]),
        "mask_color": list(mask_color),
        "future_pixels_visible_to_predictor": False,
    }


def mask_proof_passes_v3(proof: Mapping[str, Any]) -> bool:
    return bool(
        proof.get("hidden_region_uniform") is True
        and str(proof.get("original_pixel_hash"))
        != str(proof.get("masked_pixel_hash"))
        and str(proof.get("original_hidden_region_hash"))
        != str(proof.get("masked_hidden_region_hash"))
        and str(proof.get("outside_region_original_hash"))
        == str(proof.get("outside_region_masked_hash"))
        and int(proof.get("hidden_region_pixel_count", 0) or 0) > 0
        and proof.get("future_pixels_visible_to_predictor") is False
    )


__all__ = [
    "DEFAULT_MASK_COLOR",
    "MASKED_IMAGE_REGION_SCHEMA_VERSION",
    "MaskRectangleV3",
    "automatic_mask_rectangle_v3",
    "create_masked_image_v3",
    "load_analysis_image_v3",
    "mask_proof_passes_v3",
]
