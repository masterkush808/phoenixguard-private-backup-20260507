from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from PIL import Image
import pytest


_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import main


class _Upload:
    def __init__(self, path: str) -> None:
        self.name = path


def test_run_signal_workstation_requires_exactly_two_uploaded_images(monkeypatch) -> None:
    monkeypatch.setattr(main, "_build_render_config", lambda **_: {})

    with pytest.raises(Exception, match="Upload exactly two chart images"):
        main.run_signal_workstation(
            _Upload("higher.png"),
            overlay_mode="history-plus-projection",
            min_conf_global=0.42,
            min_conf_latest=0.50,
            history_depth=8,
            label_density=10,
            projection_focus=0.35,
            debug_depth=6,
            audit_tab_loaded=False,
            heatmap_tab_loaded=False,
            compare_tab_loaded=False,
        )


def test_run_signal_workstation_combines_higher_and_lower_timeframes(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        main,
        "_build_render_config",
        lambda **kwargs: {
            "overlay_mode": kwargs["overlay_mode"],
            "min_conf_global": kwargs["min_conf_global"],
            "min_conf_latest": kwargs["min_conf_latest"],
            "history_depth": int(kwargs["history_depth"]),
            "label_density": int(kwargs["label_density"]),
            "projection_focus": kwargs["projection_focus"],
            "debug_depth": int(kwargs["debug_depth"]),
        },
    )

    def _fake_run_inference(file_path: str, **_: object) -> tuple[dict[str, object], Image.Image, object, object]:
        action = "BUY" if "higher" in file_path else "SELL"
        result = {
            "action": action,
            "confidence": 0.61 if action == "BUY" else 0.57,
            "projection": {"direction": action},
            "probabilities": {"BUY": 0.61, "SELL": 0.25, "HOLD": 0.14},
            "memory_similarity": 0.42,
            "timestamp": "2026-03-25T07:01:00+00:00",
        }
        return result, Image.new("RGB", (48, 32), color=(10, 20, 30)), None, None

    monkeypatch.setattr(main.pg_main, "run_inference", _fake_run_inference)
    monkeypatch.setattr(main, "_source_image_to_state", lambda _: np.zeros((16, 24, 3), dtype=np.uint8))
    monkeypatch.setattr(
        main,
        "_build_timeframe_compare_entry",
        lambda result, _source_image_state, file_path, label, **_: {
            "label": label,
            "file_path": file_path,
            "action": result["action"],
            "projection_direction": result["projection"]["direction"],
            "confidence": result["confidence"],
        },
    )
    monkeypatch.setattr(
        main,
        "_build_multi_timeframe_result",
        lambda analyzed: {
            "action": "SELL",
            "confidence": 0.73,
            "projection": {"direction": "SELL"},
            "memory_similarity": 0.42,
            "timestamp": "2026-03-25T07:01:47+00:00",
            "multi_timeframe": {
                "aligned": False,
                "summary": "Higher TF BUY / BUY | Lower TF SELL / SELL",
                "entries": [dict(row["compare_entry"]) for row in analyzed],
            },
        },
    )
    monkeypatch.setattr(main, "_build_session_entry", lambda result, _image_state, file_path, source: {"result": result, "file_path": file_path, "source": source})
    monkeypatch.setattr(main, "_append_session_entry", lambda entry: captured.setdefault("session_entry", entry))
    monkeypatch.setattr(
        main,
        "_render_workspace_from_result",
        lambda result, source_image_state, render_config, **_: tuple(
            [f"slot-{idx}" for idx in range(21)]
        ),
    )

    outputs = main.run_signal_workstation(
        [_Upload("higher_tf.png"), _Upload("lower_tf.png")],
        overlay_mode="history-plus-projection",
        min_conf_global=0.42,
        min_conf_latest=0.50,
        history_depth=8,
        label_density=10,
        projection_focus=0.35,
        debug_depth=6,
        audit_tab_loaded=False,
        heatmap_tab_loaded=False,
        compare_tab_loaded=False,
    )

    assert len(outputs) == 24
    assert outputs[-1] == "lower_tf.png"
    assert outputs[-2].shape == (16, 24, 3)
    assert outputs[-3]["multi_timeframe"]["entries"][0]["label"] == "Higher TF"
    assert outputs[-3]["multi_timeframe"]["entries"][1]["label"] == "Lower TF"
    assert captured["session_entry"] == {
        "result": outputs[-3],
        "file_path": "lower_tf.png",
        "source": "manual-multi-timeframe",
    }


def test_build_session_entry_saves_high_resolution_png_thumbnail(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(main.RUNTIME, "session_thumbnails_dir", tmp_path)
    image_state = np.zeros((720, 1280, 3), dtype=np.uint8)

    entry = main._build_session_entry(
        {
            "action": "BUY",
            "confidence": 0.81,
            "expected_3min_move_pct": 0.12,
            "projection": {"direction": "BUY"},
            "memory_similarity": 0.56,
            "timestamp": "2026-03-25T07:01:47+00:00",
        },
        image_state,
        "lower_tf.png",
        source="manual-multi-timeframe",
    )

    thumbnail_path = Path(str(entry["thumbnail_path"]))
    assert thumbnail_path.exists()
    assert thumbnail_path.suffix == ".png"
    with Image.open(thumbnail_path) as thumb:
        assert thumb.width <= 960
        assert thumb.height <= 540
        assert thumb.width >= 900
