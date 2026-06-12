from __future__ import annotations

from typing import Any, Mapping

from PIL import Image, ImageDraw

from phoenixguard.vision.broker_source_lock_v3 import (
    AMBIGUOUS_BROKER_TARGET,
    BROKER_NOT_FOUND,
    BROKER_SOURCE_LOCK_V3_SCHEMA_VERSION,
    CHATGPT,
    INVALID_BROWSER,
    PHOENIXGUARD_DASHBOARD,
    TERMINAL,
    TITLE_MATCH_PIXEL_MISMATCH,
    VALID,
    VIEWPORT_MISMATCH,
    VISUAL_STUDIO_CODE,
    WINDOWS_DESKTOP_TASKBAR,
    WRONG_SURFACE,
    broker_control_fingerprint_v3,
    broker_pixel_fingerprint_v3,
    build_broker_source_lock_v3,
    classify_wrong_surface_v3,
    looks_like_pocket_option_broker_surface_v3,
)


def _synthetic_broker_image(*, width: int = 1280, height: int = 720) -> Image.Image:
    image = Image.new("RGB", (width, height), color=(18, 24, 34))
    draw = ImageDraw.Draw(image)
    for x in range(80, width - 280, 80):
        draw.line((x, 80, x, height - 80), fill=(44, 50, 67), width=1)
    for y in range(80, height - 70, 60):
        draw.line((70, y, width - 280, y), fill=(44, 50, 67), width=1)
    for index in range(14):
        x = 130 + index * 48
        y0 = 380 - index * 10
        y1 = y0 + 46
        color = (86, 220, 98) if index % 3 else (238, 72, 190)
        draw.line((x, y0 - 20, x, y1 + 20), fill=color, width=2)
        draw.rectangle((x - 8, y0, x + 8, y1), fill=color)

    panel_x0 = int(width * 0.78)
    panel_x1 = width - 42
    draw.rectangle((panel_x0, 0, width, height), fill=(31, 37, 57))
    draw.rectangle((panel_x0 + 24, 150, panel_x1, 184), fill=(18, 24, 43), outline=(0, 144, 255), width=2)
    draw.rectangle((panel_x0 + 24, 210, panel_x1, 244), fill=(18, 24, 43), outline=(42, 54, 80), width=1)
    draw.rounded_rectangle((panel_x0 + 24, 305, panel_x1, 366), radius=8, fill=(44, 178, 65))
    draw.rounded_rectangle((panel_x0 + 24, 386, panel_x1, 447), radius=8, fill=(255, 51, 43))
    draw.text((panel_x0 + 70, 324), "BUY", fill=(255, 255, 255))
    draw.text((panel_x0 + 68, 405), "SELL", fill=(255, 255, 255))
    return image


def _non_broker_image(*, width: int = 1280, height: int = 720) -> Image.Image:
    image = Image.new("RGB", (width, height), color=(247, 247, 245))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width, 66), fill=(255, 255, 255))
    draw.rectangle((28, 96, width - 28, height - 36), fill=(238, 238, 235))
    draw.text((62, 126), "Not the broker", fill=(30, 30, 30))
    return image


def _synthetic_chart_source_image(*, width: int = 1280, height: int = 720) -> Image.Image:
    image = Image.new("RGB", (width, height), color=(18, 23, 33))
    draw = ImageDraw.Draw(image)
    for x in range(80, width - 80, 76):
        draw.line((x, 96, x, height - 70), fill=(43, 50, 67), width=1)
    for y in range(104, height - 68, 58):
        draw.line((60, y, width - 60, y), fill=(43, 50, 67), width=1)
    for index in range(24):
        x = 120 + index * 38
        open_y = 430 - index * 7 + ((index % 4) - 1) * 12
        close_y = open_y - 30 if index % 2 == 0 else open_y + 28
        top = min(open_y, close_y)
        bottom = max(open_y, close_y)
        color = (72, 211, 121) if close_y < open_y else (234, 82, 96)
        draw.line((x, top - 22, x, bottom + 22), fill=color, width=3)
        draw.rectangle((x - 8, top, x + 8, bottom), fill=color)
    draw.text((70, 36), "EURUSD TradingView", fill=(216, 224, 235))
    return image


def _desktop_taskbar_image(*, width: int = 1280, height: int = 720) -> Image.Image:
    image = Image.new("RGB", (width, height), color=(42, 74, 105))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, height - 52, width, height), fill=(18, 18, 22))
    return image


def _broker_surface_payload() -> dict[str, Any]:
    return {
        "controls_ready": True,
        "capture_plane": {"width": 1280, "height": 720},
        "control_visibility": {
            "image_width": 1280,
            "image_height": 720,
            "buy_visible": True,
            "sell_visible": True,
            "amount_visible": True,
            "time_visible": True,
            "all_required_visible": True,
        },
        "buy_button": {"bbox": [1022, 305, 1238, 366], "visible": True},
        "sell_button": {"bbox": [1022, 386, 1238, 447], "visible": True},
        "amount_field": {"bbox": [1022, 210, 1238, 244], "visible": True},
        "time_field": {"bbox": [1022, 150, 1238, 184], "visible": True},
    }


def _edge_broker_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "browser": "edge",
        "title": "The Most Innovative Trading Platform - Microsoft Edge",
        "url": "https://pocketoption.com/cabinet/",
        "window_handle": "hwnd-1",
        "viewport": {"width": 1280, "height": 720},
        "broker_surface": _broker_surface_payload(),
    }
    payload.update(overrides)
    return payload


def _edge_tradingview_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "browser": "edge",
        "title": "EURUSD Chart - TradingView - Microsoft Edge",
        "url": "https://www.tradingview.com/chart/abc123/",
        "window_handle": "hwnd-tv",
        "viewport": {"width": 1280, "height": 720},
    }
    payload.update(overrides)
    return payload


def _tradingview_study_expected() -> dict[str, Any]:
    return {
        "source_role": "study",
        "source_kind": "tradingview",
        "required_browser": "any",
        "broker_title_tokens": ["tradingview", "trading view"],
        "broker_url_tokens": ["tradingview.com", "tradingview"],
    }


def test_valid_lock_accepts_edge_broker_with_handle_viewport_and_fingerprints() -> None:
    image = _synthetic_broker_image()
    broker_surface = _broker_surface_payload()
    expected = {
        "window_handle": "hwnd-1",
        "viewport": {"width": 1280, "height": 720},
        "broker_pixel_fingerprint": broker_pixel_fingerprint_v3(image),
        "broker_control_fingerprint": broker_control_fingerprint_v3(broker_surface),
    }

    lock = build_broker_source_lock_v3(
        _edge_broker_payload(broker_surface=broker_surface),
        image=image,
        expected=expected,
    )

    assert lock.status == VALID
    assert lock.valid is True
    assert lock.selected_target is not None
    assert lock.selected_target.window_handle == "hwnd-1"
    assert lock.surface_guard.broker_like_pixels is True
    assert lock.broker_pixel_fingerprint == expected["broker_pixel_fingerprint"]
    assert lock.broker_control_fingerprint == expected["broker_control_fingerprint"]
    assert lock.as_dict()["schema_version"] == BROKER_SOURCE_LOCK_V3_SCHEMA_VERSION


def test_devtools_target_id_can_lock_when_window_handle_is_absent() -> None:
    image = _synthetic_broker_image()
    payload = _edge_broker_payload(window_handle="", devtools_target_id="target-abc")

    lock = build_broker_source_lock_v3(
        payload,
        image=image,
        expected={"target_id": "target-abc", "viewport": {"width": 1280, "height": 720}},
    )

    assert lock.status == VALID
    assert lock.selected_target is not None
    assert lock.selected_target.target_id == "target-abc"
    assert lock.selected_target.window_handle == ""


def test_valid_lock_can_be_built_from_control_payload_without_image() -> None:
    broker_surface = _broker_surface_payload()
    lock = build_broker_source_lock_v3(
        _edge_broker_payload(broker_surface=broker_surface),
        expected={
            "window_handle": "hwnd-1",
            "viewport": {"width": 1280, "height": 720},
            "broker_control_fingerprint": broker_control_fingerprint_v3(broker_surface),
        },
    )

    assert lock.status == VALID
    assert lock.surface_guard.wrong_surface is False
    assert lock.surface_guard.reason_codes == ("BROKER_CONTROLS_CONFIRMED",)
    assert lock.broker_pixel_fingerprint == ""


def test_tradingview_study_source_accepts_chart_pixels_without_pocket_controls() -> None:
    image = _synthetic_chart_source_image()

    lock = build_broker_source_lock_v3(
        _edge_tradingview_payload(),
        image=image,
        expected=_tradingview_study_expected(),
    )

    assert lock.status == VALID
    assert lock.valid is True
    assert lock.reason_codes == ("CHART_STUDY_SOURCE_LOCKED",)
    assert lock.surface_guard.wrong_surface is False
    assert lock.surface_guard.reason_codes == ("CHART_SOURCE_PIXELS_CONFIRMED",)
    assert lock.selected_target is not None
    assert lock.selected_target.window_handle == "hwnd-tv"
    assert lock.broker_control_fingerprint == ""


def test_tradingview_study_source_rejects_matching_title_without_chart_pixels() -> None:
    lock = build_broker_source_lock_v3(
        _edge_tradingview_payload(),
        image=_non_broker_image(),
        expected=_tradingview_study_expected(),
    )

    assert lock.status == TITLE_MATCH_PIXEL_MISMATCH
    assert lock.valid is False
    assert "CHART_SOURCE_PIXELS_MISSING" in lock.reason_codes
    assert lock.surface_guard.wrong_surface is True


def test_title_match_pixel_mismatch_blocks_stale_wrong_tab() -> None:
    lock = build_broker_source_lock_v3(_edge_broker_payload(), image=_non_broker_image())

    assert lock.status == TITLE_MATCH_PIXEL_MISMATCH
    assert lock.valid is False
    assert "BROKER_CONTROL_PIXELS_MISSING" in lock.reason_codes
    assert lock.surface_guard.wrong_surface is True


def test_ambiguous_multiple_edge_broker_targets_without_identity() -> None:
    image = _synthetic_broker_image()
    candidates: list[Mapping[str, Any]] = [
        _edge_broker_payload(window_handle="hwnd-1"),
        _edge_broker_payload(window_handle="hwnd-2"),
    ]

    lock = build_broker_source_lock_v3({}, image=image, candidates=candidates)

    assert lock.status == AMBIGUOUS_BROKER_TARGET
    assert lock.matching_candidate_count == 2
    assert lock.selected_target is None


def test_broker_not_found_when_no_candidates_are_available() -> None:
    lock = build_broker_source_lock_v3({}, image=None, candidates=[])

    assert lock.status == BROKER_NOT_FOUND
    assert lock.valid is False
    assert lock.reason_codes == ("NO_CANDIDATES",)


def test_invalid_browser_blocks_broker_title_outside_edge() -> None:
    image = _synthetic_broker_image()
    payload = _edge_broker_payload(browser="chrome", title="Pocket Option - Google Chrome")

    lock = build_broker_source_lock_v3(payload, image=image)

    assert lock.status == INVALID_BROWSER
    assert "EDGE_BROWSER_REQUIRED" in lock.reason_codes


def test_viewport_mismatch_blocks_lock() -> None:
    image = _synthetic_broker_image()
    lock = build_broker_source_lock_v3(
        _edge_broker_payload(),
        image=image,
        expected={"window_handle": "hwnd-1", "viewport": {"width": 1024, "height": 768}},
    )

    assert lock.status == VIEWPORT_MISMATCH
    assert lock.valid is False
    assert lock.viewport_fingerprint == "vp:1280x720"


def test_wrong_surface_classification_known_metadata_surfaces() -> None:
    cases = [
        ({"title": "PhoenixGuard Live Dashboard - Microsoft Edge", "url": "http://127.0.0.1:8788/"}, PHOENIXGUARD_DASHBOARD),
        ({"title": "ChatGPT - Microsoft Edge", "url": "https://chatgpt.com/c/123"}, CHATGPT),
        ({"title": "broker_source_lock_v3.py - Visual Studio Code", "process_name": "Code.exe"}, VISUAL_STUDIO_CODE),
        ({"title": "Windows PowerShell", "process_name": "powershell.exe"}, TERMINAL),
        ({"title": "Program Manager", "class_name": "Progman"}, WINDOWS_DESKTOP_TASKBAR),
    ]

    for payload, expected_class in cases:
        guard = classify_wrong_surface_v3(payload, image=_non_broker_image())
        assert guard.surface_class == expected_class
        assert guard.wrong_surface is True
        assert guard.capture_safe is False


def test_desktop_taskbar_dominant_image_classifies_as_wrong_surface() -> None:
    image = _desktop_taskbar_image()
    guard = classify_wrong_surface_v3({}, image=image)
    lock = build_broker_source_lock_v3({}, image=image)

    assert guard.surface_class == WINDOWS_DESKTOP_TASKBAR
    assert guard.wrong_surface is True
    assert guard.evidence["desktop"]["taskbar_dominant"] is True
    assert lock.status == WRONG_SURFACE
    assert lock.surface_guard.surface_class == WINDOWS_DESKTOP_TASKBAR


def test_chatgpt_current_capture_reports_wrong_surface_status() -> None:
    lock = build_broker_source_lock_v3(
        {
            "browser": "edge",
            "title": "ChatGPT - Microsoft Edge",
            "url": "https://chatgpt.com/",
            "window_handle": "hwnd-chat",
            "viewport": {"width": 1280, "height": 720},
        },
        image=_non_broker_image(),
    )

    assert lock.status == WRONG_SURFACE
    assert lock.surface_guard.surface_class == CHATGPT


def test_broker_pixel_guard_detects_synthetic_broker_controls() -> None:
    assert looks_like_pocket_option_broker_surface_v3(_synthetic_broker_image()) is True
    assert looks_like_pocket_option_broker_surface_v3(_non_broker_image()) is False
