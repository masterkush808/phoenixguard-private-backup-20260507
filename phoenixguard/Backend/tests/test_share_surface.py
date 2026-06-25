from __future__ import annotations
import pytest

from pathlib import Path
import sys
from typing import Any, cast

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import share_phoenixguard as share

Payload = dict[str, Any]


def testshare_surface_payload_exposes_alias_and_dialogs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(share, "SHARE_BRAND_ASSET_DIR", Path("missing-share-assets"))
    payload = share.share_surface_payload()

    assert payload["alias"] == share.SHARE_PUBLIC_ALIAS
    assert payload["creator"] == share.SHARE_CREATOR_NAME
    assert "Thabang Johnson Masoabi" in payload["creator_story"]
    assert len(payload["slides"]) == 4
    assert all("gradient" in slide for slide in payload["slides"])
    assert {slide["scene_key"] for slide in payload["slides"]} == {"vision", "security", "creator", "disclosure"}
    assert "share-disclosure" in payload["dialogs_html"]


def test_share_hero_html_contains_alias_and_security_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(share, "SHARE_BRAND_ASSET_DIR", Path("missing-share-assets"))
    hero_html = share.build_share_hero_html()

    assert share.SHARE_PUBLIC_ALIAS in hero_html
    assert "Welcome to" in hero_html
    assert "808FxStandardSystemHybrid" in hero_html
    assert "System Vision" in hero_html
    assert "Server-Side State" in hero_html
    assert "Risk Disclosure" in hero_html
    assert "pg-share-scene-plane" in hero_html
    assert "data-share-scene='vision'" in hero_html
    assert "data-scene-key='security'" in hero_html


def testshare_status_html_emits_notification_marker() -> None:
    html = share.share_status_html(
        "Signal run complete",
        result={"action": "BUY", "confidence": 0.596},
        render_config={"overlay_mode": "history-boxes"},
        notification_event="signal_complete",
    )

    assert "pg-notify-event" in html
    assert "Signal Review Complete" in html


def test_submit_share_contact_brief_requires_consent() -> None:
    share.share_sessions.clear()
    share.share_rate_limit_state.clear()

    status, session_id = share.submit_share_contact_brief(
        "",
        "Test User",
        "test@example.com",
        "",
        "Review the share desk",
        False,
    )

    assert "educational-use" in status
    assert session_id == ""


def test_submit_share_contact_brief_logs_to_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    share.share_sessions.clear()
    share.share_rate_limit_state.clear()
    monkeypatch.setattr(share, "SHARE_CONTACT_LOG_PATH", tmp_path / "contact.log")
    monkeypatch.setattr(share, "SHARE_CONTACT_RATE_LIMIT", 5)
    monkeypatch.setattr(share, "SHARE_CONTACT_RATE_WINDOW_SEC", 60)
    stored_rows: list[dict[str, object]] = []

    class _FakeStore:
        available = True

        def insert_contact_brief(self, row: dict[str, object]) -> None:
            stored_rows.append(dict(row))

    def _get_pref_store() -> _FakeStore:
        return _FakeStore()

    monkeypatch.setattr(share.pg, "_get_pref_store", _get_pref_store)

    status, session_id = share.submit_share_contact_brief(
        "",
        "Thabang Johnson Masoabi",
        "tj@example.com",
        "808 Vision",
        "Review the protected AI structure desk and discuss educational access.",
        True,
    )

    assert "captured on the host machine" in status
    assert session_id
    assert stored_rows
    assert stored_rows[0]["contact_channel"] == "tj@example.com"
    logtext = (tmp_path / "contact.log").read_text(encoding="utf-8")
    assert share.SHARE_PUBLIC_ALIAS in logtext
    assert "tj@example.com" not in logtext


def test_build_share_render_config_pins_multi_timeframe_defaults() -> None:
    render_config = share.build_share_render_config(
        "history-plus-projection",
        0.42,
        0.50,
        8,
        10,
        0.35,
        vision_extras=["grounded-zones"],
        council_scope="auto",
    )

    assert render_config["higher_timeframe"] == "M15"
    assert render_config["lower_timeframe"] == "M5"


def testanalyze_share_bundle_applies_timeframe_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def _fake_run_inference(file_path: str, **kwargs: object) -> tuple[dict[str, object], Image.Image, object, object]:
        calls.append({"file_path": file_path, **kwargs})
        action = "BUY" if "higher" in file_path else "SELL"
        return (
            {
                "action": action,
                "confidence": 0.72,
                "projection": {"direction": action},
                "timestamp": "2026-04-03T00:00:00+00:00",
            },
            Image.new("RGB", (24, 16), color=(12, 18, 24)),
            None,
            None,
        )

    def _source_image_to_state(file_path: str) -> dict[str, str]:
        return {"path": file_path}

    def _build_timeframe_compare_entry(
        result: Payload,
        _source_image_state: object,
        file_path: str,
        label: str,
        **_kwargs: object,
    ) -> Payload:
        return {
            "label": label,
            "file_path": file_path,
            "action": result["action"],
        }

    def build_multi_timeframe_result(analyzed: list[Payload]) -> Payload:
        entries = [dict(cast(Payload, row["compare_entry"])) for row in analyzed]
        return {
            "action": "SELL",
            "confidence": 0.75,
            "multi_timeframe": {"entries": entries},
        }

    monkeypatch.setattr(share.pg.pg_main, "run_inference", _fake_run_inference)
    monkeypatch.setattr(share.pg, "_source_image_to_state", _source_image_to_state)
    monkeypatch.setattr(
        share.pg,
        "_build_timeframe_compare_entry",
        _build_timeframe_compare_entry,
    )
    monkeypatch.setattr(
        share.pg,
        "build_multi_timeframe_result",
        build_multi_timeframe_result,
    )

    result, source_image_state, active_file_path = share.analyze_share_bundle(
        ["higher_tf_1.png", "higher_tf_2.png", "lower_tf_1.png", "lower_tf_2.png"],
        {
            "overlay_mode": "history-plus-projection",
            "min_conf_global": 0.42,
            "min_conf_latest": 0.50,
            "history_depth": 8,
            "label_density": 10,
            "projection_focus": 0.35,
            "vision_extras": ["grounded-zones"],
            "council_scope": "auto",
            "higher_timeframe": "H1",
            "lower_timeframe": "M5",
        },
        side_effect_free=True,
    )

    assert calls[0]["timeframe_override"] == "H1"
    assert calls[1]["timeframe_override"] == "H1"
    assert calls[2]["timeframe_override"] == "M5"
    assert calls[3]["timeframe_override"] == "M5"
    assert result["multi_timeframe"]["entries"][0]["label"] == "Higher TF / Zoomed Out"
    assert result["multi_timeframe"]["entries"][1]["label"] == "Higher TF / Zoomed In"
    assert result["multi_timeframe"]["entries"][2]["label"] == "Lower TF / Zoomed Out"
    assert result["multi_timeframe"]["entries"][3]["label"] == "Lower TF / Zoomed In"
    assert source_image_state == {"path": "lower_tf_2.png"}
    assert active_file_path == "lower_tf_2.png"


def test_load_share_timeframe_overlays_uses_current_source_image(monkeypatch: pytest.MonkeyPatch) -> None:
    share.share_sessions.clear()
    share.share_rate_limit_state.clear()

    def _build_overlay_image(*_args: object, **_kwargs: object) -> Image.Image:
        return Image.new("RGB", (24, 16), color=(12, 18, 24))

    def _write_resized_image_asset(_image: Image.Image, path: str | Path, **_kwargs: object) -> str:
        return str(path)

    def _image_uri_from_file(path: str | Path, **_kwargs: object) -> str:
        return f"uri:{Path(path).name}"

    monkeypatch.setattr(share.pg, "_build_overlay_image", _build_overlay_image)
    monkeypatch.setattr(share.pg, "_write_resized_image_asset", _write_resized_image_asset)
    monkeypatch.setattr(share.pg, "_image_uri_from_file", _image_uri_from_file)

    share.update_share_session(
        "snapshot-session",
        result={
            "action": "SELL",
            "confidence": 0.63,
            "projection": {"direction": "SELL"},
            "timestamp": "2026-04-03T00:00:00+00:00",
            "multi_timeframe": {"entries": []},
        },
        source_image_state=Image.new("RGB", (16, 16), color=(20, 30, 40)),
        active_file_path="current_chart.png",
    )

    html = share.load_share_timeframe_overlays("snapshot-session")

    assert "Current Run" in html
    assert "SELL" in html
    assert "Overlay snapshots are not available for the current run yet." not in html
