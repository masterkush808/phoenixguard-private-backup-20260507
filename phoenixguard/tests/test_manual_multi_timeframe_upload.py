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


def test_run_signal_workstation_requires_exactly_four_uploaded_images(monkeypatch) -> None:
    monkeypatch.setattr(main, "_build_render_config", lambda **_: {})

    with pytest.raises(Exception, match="Upload exactly four chart images"):
        main.run_signal_workstation(
            [_Upload("higher_1.png")],
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


def test_run_signal_workstation_combines_higher_and_lower_timeframes(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}
    monkeypatch.setattr(main.RUNTIME, "data_dir", tmp_path / "data")

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
                "summary": "Higher TF pair: Higher TF / Zoomed Out BUY / BUY 0.61, Higher TF / Zoomed In BUY / BUY 0.61 | Lower TF pair: Lower TF / Zoomed Out SELL / SELL 0.57, Lower TF / Zoomed In SELL / SELL 0.57",
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
        [_Upload("higher_tf_1.png"), _Upload("higher_tf_2.png"), _Upload("lower_tf_1.png"), _Upload("lower_tf_2.png")],
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
    assert outputs[-1] == "lower_tf_2.png"
    assert outputs[-2].shape == (16, 24, 3)
    assert outputs[-3]["multi_timeframe"]["entries"][0]["label"] == "Higher TF / Zoomed Out"
    assert outputs[-3]["multi_timeframe"]["entries"][1]["label"] == "Higher TF / Zoomed In"
    assert outputs[-3]["multi_timeframe"]["entries"][2]["label"] == "Lower TF / Zoomed Out"
    assert outputs[-3]["multi_timeframe"]["entries"][3]["label"] == "Lower TF / Zoomed In"
    assert captured["session_entry"] == {
        "result": outputs[-3],
        "file_path": "lower_tf_2.png",
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


def test_build_multi_timeframe_overlay_sheet_composes_both_frames(tmp_path: Path) -> None:
    higher_path = tmp_path / "higher_overlay.png"
    lower_path = tmp_path / "lower_overlay.png"
    Image.new("RGB", (280, 160), color=(20, 180, 60)).save(higher_path)
    Image.new("RGB", (280, 160), color=(180, 40, 20)).save(lower_path)

    sheet = main._build_multi_timeframe_overlay_sheet(
        {
            "multi_timeframe": {
                "gate_state": "confirmed",
                "summary": "Higher TF BUY / BUY | Lower TF SELL / SELL",
                "entries": [
                    {
                        "label": "Higher TF",
                        "action": "BUY",
                        "projection_direction": "BUY",
                        "confidence": 0.81,
                        "overlay_asset_path": str(higher_path),
                    },
                    {
                        "label": "Lower TF",
                        "action": "SELL",
                        "projection_direction": "SELL",
                        "confidence": 0.74,
                        "overlay_asset_path": str(lower_path),
                    },
                ],
            }
        }
    )

    assert sheet is not None
    assert sheet.width > sheet.height
    left_pixel = sheet.getpixel((sheet.width // 4, sheet.height // 2))
    right_pixel = sheet.getpixel(((sheet.width * 3) // 4, sheet.height // 2))
    assert left_pixel[1] > left_pixel[0]
    assert right_pixel[0] > right_pixel[1]


def test_build_multi_timeframe_overlay_fusion_blends_both_frames(tmp_path: Path) -> None:
    higher_path = tmp_path / "higher_overlay.png"
    lower_path = tmp_path / "lower_overlay.png"
    Image.new("RGB", (280, 160), color=(20, 180, 60)).save(higher_path)
    Image.new("RGB", (280, 160), color=(180, 40, 20)).save(lower_path)

    fused = main._build_multi_timeframe_overlay_fusion(
        {
            "multi_timeframe": {
                "gate_state": "confirmed",
                "summary": "Higher TF BUY / BUY | Lower TF SELL / SELL",
                "entries": [
                    {
                        "label": "Higher TF",
                        "action": "BUY",
                        "projection_direction": "BUY",
                        "confidence": 0.81,
                        "overlay_asset_path": str(higher_path),
                    },
                    {
                        "label": "Lower TF",
                        "action": "SELL",
                        "projection_direction": "SELL",
                        "confidence": 0.74,
                        "overlay_asset_path": str(lower_path),
                    },
                ],
            }
        }
    )

    assert fused is not None
    center_pixel = fused.getpixel((fused.width // 2, fused.height // 2))
    assert center_pixel[0] > 40
    assert center_pixel[1] > 40


def test_build_timeframe_overlay_gallery_falls_back_to_current_run_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(main, "_build_overlay_image", lambda *args, **kwargs: Image.new("RGB", (24, 16), color=(12, 18, 24)))
    monkeypatch.setattr(main, "_write_resized_image_asset", lambda _image, path, **kwargs: str(path))
    monkeypatch.setattr(main, "_image_uri_from_file", lambda path, **kwargs: f"uri:{Path(path).name}")

    html = main._build_timeframe_overlay_gallery_html(
        {
            "action": "BUY",
            "confidence": 0.74,
            "projection": {"direction": "BUY"},
            "multi_timeframe": {"entries": []},
        },
        source_image_state=np.zeros((16, 24, 3), dtype=np.uint8),
    )

    assert "Current Run" in html
    assert "Action BUY" in html
    assert "Overlay snapshots are not available for the current run yet." not in html
