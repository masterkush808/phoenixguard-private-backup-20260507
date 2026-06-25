from __future__ import annotations

from _pg_bootstrap import ensure_project_paths
ensure_project_paths()

from phoenixguard.vision.preprocess import (
    apply_clahe,
    auto_crop_price_area,
    auto_crop_price_area_with_meta,
    extract_price_floats,
    image_to_tensor,
    indicator_regex_filter,
    load_any_file_as_image,
    normalize_for_model,
    prices_to_tensor,
)

__all__ = [
    "apply_clahe",
    "auto_crop_price_area",
    "auto_crop_price_area_with_meta",
    "extract_price_floats",
    "image_to_tensor",
    "indicator_regex_filter",
    "load_any_file_as_image",
    "normalize_for_model",
    "prices_to_tensor",
]
