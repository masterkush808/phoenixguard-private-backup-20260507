from __future__ import annotations

from PIL import Image

from phoenixguard.mobile_api.window_tracker import _external_frame_source_lock_v3


def test_edge_tab_source_lock_is_stable_across_pixel_changes() -> None:
    image = Image.new("RGB", (1280, 720), (12, 18, 28))
    source = {
        "source_id": "edge-background-tab-v3",
        "source_type": "browser_extension_capture",
        "source_url": "https://pocketoption.com/en/cabinet/",
        "sequence_id": "edge-tab-77-stream-a",
        "symbol": "GBP/USD OTC",
        "timeframe": "M5",
        "coordinate_space": "edge_tab_content_v1",
        "metadata": {
            "extension_id": "extension-a",
            "locked_tab_id": 77,
        },
    }

    first = _external_frame_source_lock_v3(source, image, window_signature="pixels-a")
    second = _external_frame_source_lock_v3(source, image, window_signature="pixels-b")

    assert first["selected_target"]["target_id"] == second["selected_target"]["target_id"]
    assert first["broker_pixel_fingerprint"] == "pixels-a"
    assert second["broker_pixel_fingerprint"] == "pixels-b"
    assert first["selected_target"]["browser"] == "edge_extension"
    assert first["evidence"]["coordinate_space"] == "edge_tab_content_v1"
    assert first["evidence"]["title_valid"] is False
    assert first["evidence"]["candidates"] == []


def test_edge_tab_source_lock_changes_only_for_a_new_stream_identity() -> None:
    image = Image.new("RGB", (1280, 720), (12, 18, 28))
    common = {
        "source_id": "edge-background-tab-v3",
        "source_type": "browser_extension_capture",
        "coordinate_space": "edge_tab_content_v1",
        "metadata": {"extension_id": "extension-a", "locked_tab_id": 77},
    }
    first = _external_frame_source_lock_v3(
        {**common, "sequence_id": "stream-a"},
        image,
        window_signature="same-pixels",
    )
    second = _external_frame_source_lock_v3(
        {**common, "sequence_id": "stream-b"},
        image,
        window_signature="same-pixels",
    )

    assert first["selected_target"]["target_id"] != second["selected_target"]["target_id"]


def test_edge_tab_source_lock_preserves_verified_tab_title_and_origin() -> None:
    image = Image.new("RGB", (1280, 720), (12, 18, 28))
    source = {
        "source_id": "edge-background-tab-v3",
        "source_type": "browser_tab_roi_capture",
        "source_url": "https://pocketoption.com/en/cabinet/",
        "sequence_id": "edge-tab-77-stream-a",
        "coordinate_space": "edge_tab_roi_v1",
        "metadata": {
            "extension_id": "extension-a",
            "locked_tab_id": 77,
            "locked_tab_title": "Pocket Option | Trading",
            "locked_origin": "https://pocketoption.com",
        },
    }

    lock = _external_frame_source_lock_v3(source, image, window_signature="pixels-a")

    assert lock["selected_target"]["browser"] == "edge_extension"
    assert lock["selected_target"]["title"] == "Pocket Option | Trading"
    assert lock["evidence"]["source_origin"] == "https://pocketoption.com"
    assert lock["evidence"]["origin_matches"] is True
    assert lock["evidence"]["url_valid"] is True
    assert lock["evidence"]["title_valid"] is True
    assert len(lock["evidence"]["candidates"]) == 1
    assert "BROWSER_TAB_IDENTITY_VERIFIED" in lock["reason_codes"]


def test_edge_tab_source_lock_rejects_mismatched_title_origin_proof() -> None:
    image = Image.new("RGB", (1280, 720), (12, 18, 28))
    source = {
        "source_id": "edge-background-tab-v3",
        "source_type": "browser_tab_roi_capture",
        "source_url": "https://www.tradingview.com/chart/",
        "sequence_id": "edge-tab-77-stream-a",
        "coordinate_space": "edge_tab_roi_v1",
        "metadata": {
            "extension_id": "extension-a",
            "locked_tab_id": 77,
            "locked_tab_title": "TradingView Supercharts",
            "locked_origin": "https://example.test",
        },
    }

    lock = _external_frame_source_lock_v3(source, image, window_signature="pixels-a")

    assert lock["evidence"]["source_origin"] == "https://www.tradingview.com"
    assert lock["evidence"]["origin_matches"] is False
    assert lock["evidence"]["url_valid"] is True
    assert lock["evidence"]["title_valid"] is False
    assert lock["evidence"]["candidates"] == []
    assert "BROWSER_TAB_IDENTITY_VERIFIED" not in lock["reason_codes"]
