from __future__ import annotations
import pytest

import sys
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import main

Payload = dict[str, Any]


def test_build_zone_editor_value_prefers_processed_overlay_image() -> None:
    source_state = np.zeros((12, 18, 3), dtype=np.uint8)
    overlay = Image.new("RGB", (18, 12), color=(240, 90, 40))

    value = main.build_zone_editor_value(source_state, base_image=overlay)

    assert isinstance(value, Image.Image)
    assert value.mode == "RGBA"
    assert value.size == (18, 12)
    pixel = value.getpixel((0, 0))
    assert isinstance(pixel, tuple)
    assert pixel[:3] == (240, 90, 40)


def test_refresh_zone_canvas_rebuilds_the_current_processed_chart(monkeypatch: pytest.MonkeyPatch) -> None:
    source_image = Image.new("RGB", (20, 14), color=(15, 25, 35))
    expected_overlay = Image.new("RGB", (20, 14), color=(90, 180, 60))

    def _image_from_state(_state: object) -> Image.Image:
        return source_image

    def build_render_config(**kwargs: Any) -> Payload:
        return {
            "overlay_mode": kwargs["overlay_mode"],
            "min_conf_global": kwargs["min_conf_global"],
            "min_conf_latest": kwargs["min_conf_latest"],
            "history_depth": int(kwargs["history_depth"]),
            "label_density": int(kwargs["label_density"]),
            "projection_focus": kwargs["projection_focus"],
            "debug_depth": int(kwargs["debug_depth"]),
        }

    def _build_overlay_image(*_args: object, **_kwargs: object) -> Image.Image:
        return expected_overlay

    monkeypatch.setattr(main, "_image_from_state", _image_from_state)
    monkeypatch.setattr(
        main,
        "build_render_config",
        build_render_config,
    )
    monkeypatch.setattr(main, "_build_overlay_image", _build_overlay_image)

    value = main.refresh_zone_canvas(
        result_state={"action": "BUY"},
        source_image_state=np.zeros((14, 20, 3), dtype=np.uint8),
        overlay_mode="history-plus-projection",
        min_conf_global=0.42,
        min_conf_latest=0.50,
        history_depth=8,
        label_density=10,
        projection_focus=0.35,
        debug_depth=6,
    )

    assert isinstance(value, Image.Image)
    assert value.mode == "RGBA"
    assert value.size == expected_overlay.size
    pixel = value.getpixel((0, 0))
    assert isinstance(pixel, tuple)
    assert pixel[:3] == (90, 180, 60)
