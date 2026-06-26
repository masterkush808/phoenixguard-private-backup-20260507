from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import time
import ctypes
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Protocol, Sequence, cast

from fastapi.testclient import TestClient
import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw
import pytest

import phoenixguard.mobile_api.window_tracker as window_tracker_module
from phoenixguard.memory.memory_ingest import MemoryEntry
from phoenixguard.mobile_api.app import create_app
from phoenixguard.mobile_api.window_tracker import (
    CaptureSurfaceUnavailableError,
    ContinuousWindowTrackerService,
    PocketOptionBrokerExecutionBackend,
    PhoenixGuardWindowTrackingAdapter,
    TrackingStudy,
    WindowsWindowCaptureBackend,
    model_council_packet_from_payload,
    normalize_broker_execution_state,
    preserve_newer_active_execution_state,
    write_json_atomic,
)


class _BuildSignalPayloads(Protocol):
    def __call__(self, *args: Any, **kwargs: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        ...


def _surface(color: tuple[int, int, int] = (22, 28, 38), *, width: int = 960, height: int = 540) -> Image.Image:
    return Image.new("RGB", (width, height), color=color)


def _synthetic_chart_surface(direction: str = "buy", *, width: int = 960, height: int = 540) -> Image.Image:
    image = Image.new("RGB", (width, height), color=(20, 26, 38))
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, width - 1, height - 1), fill=(21, 26, 38))
    for x in range(80, width - 80, 70):
        draw.line((x, 70, x, height - 54), fill=(50, 56, 74), width=1)
    for y in range(78, height - 48, 52):
        draw.line((70, y, width - 70, y), fill=(50, 56, 74), width=1)

    base_x = 110
    step_x = 34
    points: list[tuple[int, int]] = []
    for index in range(18):
        if direction.lower() == "buy":
            level = 360 - index * 10 + ((index % 3) - 1) * 8
        elif direction.lower() == "sell":
            level = 170 + index * 10 + ((index % 3) - 1) * 8
        else:
            level = 270 + ((index % 4) - 1) * 14
        points.append((base_x + index * step_x, level))

    for index, (x, y) in enumerate(points):
        prev_y = points[index - 1][1] if index else y + 14
        bullish = y <= prev_y
        body_color = (95, 225, 82) if bullish else (255, 72, 214)
        wick_top = min(prev_y, y) - 18
        wick_bottom = max(prev_y, y) + 18
        left = x - 6
        right = x + 6
        top = min(prev_y, y)
        bottom = max(prev_y, y)
        draw.line((x, wick_top, x, wick_bottom), fill=body_color, width=2)
        draw.rectangle((left, top, right, bottom), fill=body_color)

    return image


def _synthetic_broker_window(*, width: int = 960, height: int = 540) -> Image.Image:
    image = _synthetic_chart_surface("buy", width=width, height=height)
    draw = ImageDraw.Draw(image)
    panel_x0 = int(width * 0.72)
    draw.rectangle((panel_x0, 0, width, height), fill=(31, 38, 58))
    amount_x0 = panel_x0 + 34
    amount_x1 = width - 28
    draw.rectangle((amount_x0, int(height * 0.18), amount_x1, int(height * 0.25)), fill=(28, 34, 52), outline=(55, 126, 225), width=2)
    draw.text((amount_x0 + 12, int(height * 0.19)), "5", fill=(255, 255, 255))
    buy_box = (amount_x0, int(height * 0.40), amount_x1, int(height * 0.50))
    sell_box = (amount_x0, int(height * 0.54), amount_x1, int(height * 0.64))
    draw.rounded_rectangle(buy_box, radius=12, fill=(47, 177, 67))
    draw.rounded_rectangle(sell_box, radius=12, fill=(246, 54, 49))
    draw.text((buy_box[0] + 28, buy_box[1] + 12), "BUY", fill=(255, 255, 255))
    draw.text((sell_box[0] + 28, sell_box[1] + 12), "SELL", fill=(255, 255, 255))
    return image


def _synthetic_full_pocket_option_gui(*, width: int = 1920, height: int = 1017) -> Image.Image:
    image = _synthetic_chart_surface("sell", width=width, height=height)
    draw = ImageDraw.Draw(image)
    panel_x0 = width - 240
    panel_x1 = width - 88
    draw.rectangle((panel_x0 - 10, 200, width - 58, height), fill=(31, 37, 57))
    time_box = (panel_x0 + 10, 235, panel_x1, 266)
    amount_box = (panel_x0 + 10, 294, panel_x1, 326)
    buy_box = (panel_x0 + 10, 403, panel_x1, 449)
    sell_box = (panel_x0 + 10, 455, panel_x1, 501)
    draw.rectangle(time_box, fill=(18, 24, 43), outline=(0, 144, 255), width=2)
    draw.text((time_box[0] + 8, time_box[1] + 8), "04:00:00", fill=(255, 255, 255))
    draw.rectangle(amount_box, fill=(18, 24, 43), outline=(42, 54, 80), width=1)
    draw.text((amount_box[0] + 8, amount_box[1] + 8), "5", fill=(255, 255, 255))
    draw.rounded_rectangle(buy_box, radius=8, fill=(44, 178, 65))
    draw.rounded_rectangle(sell_box, radius=8, fill=(255, 51, 43))
    draw.text((buy_box[0] + 40, buy_box[1] + 14), "BUY", fill=(255, 255, 255))
    draw.text((sell_box[0] + 40, sell_box[1] + 14), "SELL", fill=(255, 255, 255))
    return image


def _activate_window_true(hwnd: int) -> bool:
    _ = hwnd
    return True


def _activate_window_false(hwnd: int) -> bool:
    _ = hwnd
    return False


def test_tracker_translates_locked_broker_controls_into_chart_exclusions() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    surface = Image.new("RGB", (1000, 600), color=(18, 24, 34))
    boxes = adapter.chart_space_broker_exclusion_boxes(  # noqa: SLF001
        surface,
        [0, 0, 1000, 600],
        session_payload={
            "_study_focus_region": {"pixel_bbox": [50, 40, 1050, 640]},
            "broker_surface": {
                "capture_plane": {"width": 1200, "height": 800},
                "execution_boxes": {
                    "buy_button": {"bbox": [900, 200, 980, 260]},
                },
            },
        },
    )

    assert boxes
    assert any(box[0] <= 850 <= box[2] and box[1] <= 160 <= box[3] for box in boxes)


def test_model_council_study_packet_synthesizes_fresh_when_storedpacket_is_stale(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(window_tracker_module.time, "time", lambda: 200.0)
    packet = window_tracker_module.model_council_study_packet_from_payload(
        {
            "session_id": "pocket-live-8788",
            "state_version": 200123,
            "last_capture_epoch": 200.0,
            "decision_valid_until_epoch": 208.0,
            "model_council_study_packet": {
                "schema_version": "PG_MODEL_COUNCIL_STUDY_V3",
                "packet_id": "old_study",
                "packet_type": "STUDY_PACKET",
                "created_epoch": 1.0,
                "valid_until_epoch": 2.0,
                "execution": {"enabled": False, "state": "WATCHING", "side": "SELL"},
                "model_council": {"final_state": "WATCHING", "final_side": "SELL"},
                "promotion_trace": {"denied_at": "OLD", "next_required": "fresh state"},
            },
            "model_council_result": {
                "execution": {"enabled": False, "state": "WATCHING", "side": "BUY"},
                "model_council": {
                    "final_state": "WATCHING",
                    "final_side": "BUY",
                    "arbitration_reason": "waiting for retest",
                },
                "promotion_trace": {
                    "denied_at": "TIMING_WAIT",
                    "next_required": "failed retest confirmation",
                },
            },
        }
    )

    assert packet["packet_id"] != "old_study"
    assert packet["packet_type"] == "STUDY_PACKET"
    assert packet["execution"]["side"] == "BUY"
    assert packet["created_epoch"] == 200.0
    assert packet["valid_until_epoch"] == 208.0


def test_read_json_prefers_newer_last_good_snapshot(tmp_path: Path) -> None:
    session_path = tmp_path / "session.json"
    session_path.write_text(
        json.dumps({"session_id": "live", "capture_count": 1, "last_capture_epoch": 100.0}),
        encoding="utf-8",
    )
    session_path.with_suffix(".json.last_good").write_text(
        json.dumps({"session_id": "live", "capture_count": 2, "last_capture_epoch": 200.0}),
        encoding="utf-8",
    )

    payload = window_tracker_module.read_json(session_path, {})

    assert payload["capture_count"] == 2
    assert payload["last_capture_epoch"] == 200.0


def test_model_council_study_packet_uses_signal_freshness_when_synthesized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(window_tracker_module.time, "time", lambda: 200.0)

    packet = window_tracker_module.model_council_study_packet_from_payload(
        {
            "session_id": "pocket-live-8788",
            "state_version": 200123,
            "last_capture_epoch": 200.0,
            "latest_signal": {"freshness_window_sec": 120.0},
            "model_council_result": {
                "execution": {"enabled": False, "state": "WATCHING", "side": "BUY"},
                "model_council": {"final_state": "WATCHING", "final_side": "BUY"},
            },
        }
    )

    assert packet["created_epoch"] == 200.0
    assert packet["valid_until_epoch"] == 320.0
    assert packet["ttl_sec"] == 120.0


def _manual_candle_tracks(
    centers_y: Sequence[float],
    *,
    image_width: int = 620,
    image_height: int = 420,
    direction: str = "BUY",
    half_height: int = 34,
) -> list[dict[str, Any]]:
    tracks: list[dict[str, Any]] = []
    for index, center_y in enumerate(centers_y, start=1):
        center_x = 38.0 + index * 28.0
        y0 = max(0, int(round(center_y - half_height)))
        y1 = min(image_height - 1, int(round(center_y + half_height)))
        x0 = int(round(center_x - 5.0))
        x1 = int(round(center_x + 5.0))
        tracks.append(
            {
                "track_id": index,
                "bbox": [x0, y0, x1, y1],
                "center_x": center_x,
                "center_y": float(center_y),
                "price_proxy": float(1.0 - (float(center_y) / max(1.0, float(image_height - 1)))),
                "direction": direction,
                "color": "green" if direction == "BUY" else "magenta",
                "width": int(x1 - x0),
                "height": int(y1 - y0),
                "body_height_pct": float((y1 - y0) / max(1.0, float(image_height))),
                "normalized_x": float(center_x / max(1.0, float(image_width))),
                "normalized_y": float(float(center_y) / max(1.0, float(image_height))),
            }
        )
    return tracks


def _memory_embed(seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    vec = rng.normal(size=(384,)).astype(np.float32)
    norm = float(np.linalg.norm(vec))
    return (vec / max(norm, 1e-8)).tolist()


class _StubPhoenixBank:
    is_loaded = True

    def __init__(self, rows: Sequence[MemoryEntry]) -> None:
        self.entries = list(rows)

    def embed_description(self, chart_state: Mapping[str, Any], image: Image.Image | None = None) -> NDArray[np.float32]:
        _ = chart_state
        _ = image
        return np.asarray(_memory_embed(216), dtype=np.float32)

    def search(
        self,
        query_embed: NDArray[np.float32],
        top_k: int = 5,
        macro_trend: str | None = None,
        local_phase: str | None = None,
        query_context: Mapping[str, Any] | None = None,
    ) -> list[SimpleNamespace]:
        _ = query_embed
        _ = top_k
        _ = macro_trend
        _ = local_phase
        _ = query_context
        hits = [
            SimpleNamespace(entry_id="sell-a", label="SELL", similarity=0.93),
            SimpleNamespace(entry_id="sell-b", label="SELL", similarity=0.89),
            SimpleNamespace(entry_id="buy-noise", label="BUY", similarity=0.61),
        ]
        return hits[: max(1, int(top_k))]

    def summarize_transition_probabilities(self, results: Sequence[Any]) -> dict[str, float]:
        _ = results
        return {
            "continue": 0.58,
            "pullback": 0.14,
            "reversal_attempt": 0.22,
            "fakeout": 0.06,
        }


def _sample_memory_entries() -> list[MemoryEntry]:
    return [
        MemoryEntry(
            entry_id="sell-a",
            image_path="sell-a.png",
            label="SELL",
            chart_state={
                "direction": "SELL",
                "entry_type": "continuation",
                "continuation_signal": "impulse_pause",
                "momentum_bias": "bearish",
                "entry_candle": {"body_pct": 0.22, "upper_wick_pct": 0.34, "lower_wick_pct": 0.18},
                "memory_teaching": {
                    "lesson_role": "actual_entry",
                    "tags": ["actual_entry", "progression"],
                    "actual_entry_score": 0.91,
                    "win_evidence_score": 0.48,
                    "progression_score": 0.72,
                    "teaching_weight": 0.93,
                },
                "entry_progression": {
                    "progression_stage": "actual_entry",
                    "compression_score": 0.71,
                    "pullback_depth": 0.44,
                    "rejection_score": 0.78,
                    "follow_through_score": 0.69,
                    "aggressive_sniper_score": 0.88,
                    "sniper_window_norm": [0.68, 0.42, 0.92, 0.52],
                    "trigger_window_norm": [0.70, 0.46, 0.95, 0.56],
                    "target_window_norm": [0.74, 0.62, 0.98, 0.72],
                    "invalidation_y_norm": 0.36,
                },
                "sniper_profile": {
                    "style": "aggressive_sniper",
                    "aggressive_entry_score": 0.89,
                    "watch_window_norm": [0.68, 0.42, 0.92, 0.52],
                    "entry_window_norm": [0.68, 0.43, 0.94, 0.55],
                    "trigger_window_norm": [0.70, 0.46, 0.95, 0.56],
                    "target_window_norm": [0.74, 0.62, 0.98, 0.72],
                    "invalidation_y_norm": 0.36,
                },
            },
            text_embed=_memory_embed(210),
            visual_fp=[0.0] * 128,
            combined_embed=_memory_embed(211),
            episode_id="SELL:ep1",
            sequence_index=0,
            macro_trend="BEAR",
            local_phase="with_trend_push",
            phase_risk="breakout_risk",
            intent_next="continue",
        ),
        MemoryEntry(
            entry_id="sell-b",
            image_path="sell-b.png",
            label="SELL",
            chart_state={
                "direction": "SELL",
                "entry_type": "reversal",
                "reversal_signal": "wick_rejection",
                "momentum_bias": "bearish",
                "entry_candle": {"body_pct": 0.18, "upper_wick_pct": 0.36, "lower_wick_pct": 0.16},
                "memory_teaching": {
                    "lesson_role": "win_resolution",
                    "tags": ["win_resolution"],
                    "actual_entry_score": 0.38,
                    "win_evidence_score": 0.92,
                    "progression_score": 0.70,
                    "teaching_weight": 0.94,
                },
                "entry_progression": {
                    "progression_stage": "win_resolution",
                    "compression_score": 0.62,
                    "pullback_depth": 0.35,
                    "rejection_score": 0.76,
                    "follow_through_score": 0.86,
                    "aggressive_sniper_score": 0.74,
                },
                "sniper_profile": {
                    "style": "aggressive_sniper",
                    "aggressive_entry_score": 0.78,
                    "watch_window_norm": [0.66, 0.41, 0.90, 0.52],
                },
            },
            text_embed=_memory_embed(212),
            visual_fp=[0.0] * 128,
            combined_embed=_memory_embed(213),
            episode_id="SELL:ep2",
            sequence_index=1,
            macro_trend="BEAR",
            local_phase="reversal_base",
            phase_risk="exhaustion_risk",
            intent_next="reversal_attempt",
        ),
        MemoryEntry(
            entry_id="buy-noise",
            image_path="buy-noise.png",
            label="BUY",
            chart_state={
                "direction": "BUY",
                "entry_type": "continuation",
                "continuation_signal": "impulse_pause",
                "momentum_bias": "bullish",
                "entry_candle": {"body_pct": 0.20, "upper_wick_pct": 0.18, "lower_wick_pct": 0.34},
            },
            text_embed=_memory_embed(214),
            visual_fp=[0.0] * 128,
            combined_embed=_memory_embed(215),
            episode_id="BUY:ep3",
            sequence_index=0,
            macro_trend="BULL",
            local_phase="with_trend_push",
            phase_risk="breakout_risk",
            intent_next="continue",
        ),
    ]


def _materialize_memory_images(root: Path, entries: Sequence[MemoryEntry]) -> list[MemoryEntry]:
    materialized: list[MemoryEntry] = []
    root.mkdir(parents=True, exist_ok=True)
    for entry in entries:
        image_path = root / Path(str(entry.image_path or entry.entry_id or "memory.png")).name
        direction = "sell" if str(entry.label).upper() == "SELL" else "buy"
        _synthetic_chart_surface(direction).save(image_path)
        payload = entry.to_dict()
        payload["image_path"] = str(image_path)
        materialized.append(MemoryEntry.from_dict(payload))
    return materialized


class _FakeCaptureBackend:
    def __init__(self, images: Sequence[Image.Image]) -> None:
        self.images = [image.convert("RGB") for image in images]
        self.capture_calls = 0

    def list_windows(self, title_query: str | None = None) -> list[dict[str, Any]]:
        _ = title_query
        return [
            {
                "hwnd": 501,
                "title": "The Most Innovative Trading Platform - Microsoft Edge",
                "bbox": [0, 0, 1280, 720],
                "width": 1280,
                "height": 720,
            }
        ]

    def capture_window(self, descriptor: Mapping[str, Any]) -> Image.Image:
        _ = descriptor
        index = min(self.capture_calls, len(self.images) - 1)
        self.capture_calls += 1
        return self.images[index].copy()


class _FastVisibleOnlyCaptureBackend(_FakeCaptureBackend):
    def __init__(self, image: Image.Image) -> None:
        super().__init__([image])
        self.fast_capture_calls = 0

    def _capture_window_imagegrab(self, descriptor: Mapping[str, Any]) -> Image.Image:
        _ = descriptor
        self.fast_capture_calls += 1
        return self.images[0].copy()

    def _looks_blank(self, image: Image.Image) -> bool:
        _ = image
        return False

    def _looks_browser_content_blank(self, image: Image.Image) -> bool:
        _ = image
        return False

    def capture_window(self, descriptor: Mapping[str, Any]) -> Image.Image:
        _ = descriptor
        raise AssertionError("display-only heartbeat should use fast visible capture first")


class _FailingFastVisibleCaptureBackend(_FastVisibleOnlyCaptureBackend):
    def _capture_window_imagegrab(self, descriptor: Mapping[str, Any]) -> Image.Image:
        _ = descriptor
        self.fast_capture_calls += 1
        raise CaptureSurfaceUnavailableError("fast visible unavailable")

    def capture_window(self, descriptor: Mapping[str, Any]) -> Image.Image:
        _ = descriptor
        self.capture_calls += 1
        return self.images[0].copy()


class _ListedWindowCaptureBackend:
    def __init__(self, windows: Sequence[Mapping[str, Any]], image: Image.Image | None = None) -> None:
        self.windows = [dict(item) for item in windows]
        self.image = (image or _surface()).convert("RGB")

    def list_windows(self, title_query: str | None = None) -> list[dict[str, Any]]:
        if not title_query:
            return [dict(item) for item in self.windows]
        lowered_query = str(title_query or "").strip().lower()
        rows = [dict(item) for item in self.windows]
        return [row for row in rows if lowered_query in str(row.get("title", "")).lower()]

    def capture_window(self, descriptor: Mapping[str, Any]) -> Image.Image:
        _ = descriptor
        return self.image.copy()


class _FakeFocusSelectionBackend:
    def __init__(self) -> None:
        self.supported = True
        self.active_session_id = ""
        self._on_selected: Any = None
        self._on_state_change: Any = None

    def is_supported(self) -> bool:
        return self.supported

    def arm_selection(
        self,
        *,
        session_id: str,
        descriptor: Mapping[str, Any],
        on_selected: Any,
        on_state_change: Any,
    ) -> None:
        _ = descriptor
        self.active_session_id = session_id
        self._on_selected = on_selected
        self._on_state_change = on_state_change

    def cancel_selection(self, *, session_id: str | None = None) -> None:
        if session_id and self.active_session_id and session_id != self.active_session_id:
            return
        self.active_session_id = ""
        self._on_selected = None
        self._on_state_change = None

    def complete_selection(
        self,
        normalized_bbox: Sequence[float],
        *,
        source: str = "native_ctrl_v_window",
    ) -> None:
        if not self.active_session_id or self._on_selected is None:
            raise AssertionError("Selection backend is not armed.")
        session_id = self.active_session_id
        callback = self._on_selected
        self.active_session_id = ""
        self._on_selected = None
        self._on_state_change = None
        callback(session_id, [float(value) for value in normalized_bbox[:4]], source)


class _FakeExecutionBackend:
    def __init__(self) -> None:
        self.clicks: list[dict[str, Any]] = []
        self.reader = PocketOptionBrokerExecutionBackend()

    def is_supported(self) -> bool:
        return True

    def read_surface(self, image: Image.Image) -> dict[str, Any]:
        return self.reader.read_surface(image)

    def prepare_and_click(
        self,
        *,
        descriptor: Mapping[str, Any],
        window_image: Image.Image,
        side: str,
        amount: str,
        expiry_seconds: int,
        broker_surface: Mapping[str, Any],
        skip_expiry_adjustment: bool = False,
    ) -> dict[str, Any]:
        _ = skip_expiry_adjustment
        payload: dict[str, Any] = {
            "descriptor": dict(descriptor),
            "side": side,
            "amount": amount,
            "expiry_seconds": expiry_seconds,
            "broker_surface": dict(broker_surface),
            "window_size": [window_image.width, window_image.height],
        }
        self.clicks.append(payload)
        return {
            "status": "clicked",
            "message": f"Clicked {side} in fake backend.",
            "side": side,
            "amount": amount,
            "expiry_seconds": expiry_seconds,
        }


class _StubIdentityAdapter:
    def __init__(
        self,
        *,
        market: str = "GBP/AUD OTC",
        timeframe: str = "M5",
        market_confidence: float = 0.91,
        timeframe_confidence: float = 0.88,
    ) -> None:
        self.market = market
        self.timeframe = timeframe
        self.market_confidence = market_confidence
        self.timeframe_confidence = timeframe_confidence

    def _detect_timeframe_selector(self, image: Image.Image) -> dict[str, Any]:
        _ = image
        return {
            "value": self.timeframe,
            "confidence": self.timeframe_confidence,
            "bbox": [140, 42, 176, 72],
            "source": "stub_timeframe",
        }

    def _detect_market_selector(
        self,
        image: Image.Image,
        timeframe_selector: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        _ = image
        _ = timeframe_selector
        return {
            "value": self.market,
            "confidence": self.market_confidence,
            "bbox": [12, 42, 132, 72],
            "source": "stub_market",
            "rawtext": self.market,
        }


class _IdentityExecutionBackend(_FakeExecutionBackend):
    def __init__(
        self,
        *,
        market: str = "GBP/AUD OTC",
        timeframe: str = "M5",
        market_confidence: float = 0.91,
        timeframe_confidence: float = 0.88,
    ) -> None:
        super().__init__()
        self.reader = PocketOptionBrokerExecutionBackend(
            identity_adapter=_StubIdentityAdapter(
                market=market,
                timeframe=timeframe,
                market_confidence=market_confidence,
                timeframe_confidence=timeframe_confidence,
            )
        )


class _CountingIdentityExecutionBackend(_IdentityExecutionBackend):
    def __init__(self) -> None:
        super().__init__()
        self.read_count = 0

    def read_surface(self, image: Image.Image) -> dict[str, Any]:
        self.read_count += 1
        return super().read_surface(image)


def _test_popup_visual_payload(
    backend: PocketOptionBrokerExecutionBackend,
    image: Image.Image,
    time_field: Mapping[str, Any],
) -> dict[str, Any]:
    popup_controls = backend.expiry_popup_control_points(time_field)
    popup_locks = {
        name: backend.control_lock(
            key=name,
            label=name,
            row={
                "bbox": [point[0] - 12, point[1] - 10, point[0] + 12, point[1] + 10],
                "confidence": 0.9,
                "source": "test_popup",
            },
            read_at="2026-04-30T00:00:00+00:00",
            image_width=image.width,
            image_height=image.height,
            role="expiry_popup",
        )
        for name, point in popup_controls.items()
    }
    return {
        "controls": popup_controls,
        "execution_boxes": popup_locks,
        "geometry": {"source": "test_visual_popup_grid"},
    }


class _BlockingExecutionBackend(_FakeExecutionBackend):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    def prepare_and_click(
        self,
        *,
        descriptor: Mapping[str, Any],
        window_image: Image.Image,
        side: str,
        amount: str,
        expiry_seconds: int,
        broker_surface: Mapping[str, Any],
        skip_expiry_adjustment: bool = False,
    ) -> dict[str, Any]:
        _ = descriptor
        _ = window_image
        _ = broker_surface
        _ = skip_expiry_adjustment
        self.attempts += 1
        return {
            "status": "blocked",
            "message": "Expiry verification blocked the broker click because the visible timer did not match 00:03:00.",
            "side": side,
            "amount": amount,
            "expiry_seconds": expiry_seconds,
            "expiry_text": "00:03:00",
            "visible_expiry_before": "00:00:30",
            "expiry_popup_clicks": [{"name": "quick_m3"}],
            "expiry_popup_geometry": {"source": "visual_popup_shortcut_grid"},
            "expiry_verification": {
                "status": "mismatch",
                "matches": False,
                "target_seconds": 180,
                "visible_seconds": 30,
                "visible_text": "00:00:30",
            },
        }


class _FakeTrackingAdapter:
    def __init__(
        self,
        action: str = "BUY",
        *,
        timeframe: str = "M5",
        timeframe_confidence: float = 0.88,
    ) -> None:
        self.action = action
        self.timeframe = timeframe
        self.timeframe_confidence = timeframe_confidence
        self.calls = 0

    def study(self, image: Image.Image, *, session_payload: Mapping[str, Any] | None = None) -> TrackingStudy:
        _ = session_payload
        self.calls += 1
        overlay = image.copy()
        draw = ImageDraw.Draw(overlay)
        draw.rectangle((20, 20, overlay.width - 20, overlay.height - 20), outline=(220, 194, 123), width=3)
        signal_action = str(self.action or "BUY").upper()
        study_timeframe = str(self.timeframe or "M5").upper()
        timeframe_source = "synthetic" if self.timeframe else "default_m5_policy"
        p_next_buy = 0.70 if signal_action == "BUY" else 0.12
        p_next_sell = 0.70 if signal_action == "SELL" else 0.12
        decision_kernel: dict[str, Any] = {
            "state": "TRIGGERED",
            "decision": "EXECUTE",
            "trade_mode": "TREND_FOLLOW",
            "dominant_side": signal_action.lower(),
            "major_trend_side": signal_action.lower(),
            "target_horizon_candles": 10,
            "hold_for_candles": 10,
            "trend_follow_window_candles": 10,
            "eta_trigger_candles": 0,
            "eta_target_after_trigger_candles": 10,
            "eta_invalidation_candles": 14,
            "conflict_score": 0.12,
            "p_target_before_invalidation": 0.70,
            "hazard_invalidation": 0.12,
            "hazard_trigger": 0.48,
            "p_expire_before_trigger": 0.10,
            "next_most_likely_event": "trigger",
            "next_candle_bias": signal_action.lower(),
            "p_next_buy": p_next_buy,
            "p_next_sell": p_next_sell,
            "expected_value_R": 1.4,
        }
        tracking_summary: dict[str, Any] = {
            "chart_valid": True,
            "surface_kind": "manual_focus_surface",
            "visible_candle_count": 12,
            "active_track_count": 12,
            "chart_region": {
                "pixel_bbox": [0, 0, image.width, image.height],
                "normalized_bbox": [0.0, 0.0, 1.0, 1.0],
                "width": image.width,
                "height": image.height,
            },
            "display_region": {
                "pixel_bbox": [0, 0, image.width, image.height],
                "normalized_bbox": [0.0, 0.0, 1.0, 1.0],
                "width": image.width,
                "height": image.height,
            },
            "detected_timeframe": study_timeframe,
            "timeframe_source": timeframe_source,
            "timeframe_confidence": self.timeframe_confidence,
            "detected_market": "",
            "market_source": "unconfirmed",
            "market_confidence": 0.0,
            "global_direction": signal_action,
            "local_direction": signal_action,
            "impulse_direction": signal_action,
            "latest_candle_color": "green" if signal_action == "BUY" else "magenta",
            "overlay_kind": f"CONTINUATION {signal_action}",
            "tracked_candles": [],
            "structure_boxes": [
                {"key": "global", "label": "GLOBAL", "bbox": [24, 24, image.width - 24, image.height - 24]},
                {"key": "local", "label": "LOCAL", "bbox": [64, 54, image.width - 64, image.height - 54]},
                {"key": "current", "label": "CURRENT", "bbox": [image.width - 160, 110, image.width - 32, image.height - 72]},
            ],
            "current_box": {"bbox": [image.width - 160, 110, image.width - 32, image.height - 72]},
            "candle_statistics": {"opposing_ratio": 0.12},
            "box_context": {"failure_risk": 0.18},
            "decision_kernel": decision_kernel,
        }
        latest_signal: dict[str, Any] = {
            "action": signal_action,
            "headline_action": signal_action,
            "candidate_action": signal_action,
            "model_action": signal_action,
            "execution_action": signal_action,
            "execution_confidence": 0.84,
            "confidence": 0.84,
            "effective_confidence": 0.84,
            "candidate_confidence": 0.84,
            "raw_confidence": 0.84,
            "status": "tracking",
            "summary": f"CONTINUATION {signal_action}. Synthetic test tracker read.",
            "setup": f"CONTINUATION {signal_action}",
            "focus_timeframe": study_timeframe,
            "focus_timeframe_source": timeframe_source,
            "market": "",
            "execution_permission": "EXECUTE",
            "actionable": True,
            "decision_kernel": decision_kernel,
            "reasons": [f"synthetic {signal_action}"],
            "timestamp": "2026-04-21T00:00:00+00:00",
        }
        return TrackingStudy(
            chart_image=image.copy(),
            overlay_image=overlay,
            chart_region=cast(dict[str, Any], tracking_summary["chart_region"]),
            tracking_summary=tracking_summary,
            latest_signal=latest_signal,
        )


class _MismatchedPlaneTrackingAdapter:
    def study(self, image: Image.Image, *, session_payload: Mapping[str, Any] | None = None) -> TrackingStudy:
        _ = session_payload
        overlay_image = Image.new("RGB", (max(1, image.width - 17), image.height), color=(10, 20, 30))
        return TrackingStudy(
            chart_image=image.copy(),
            overlay_image=overlay_image,
            chart_region={
                "pixel_bbox": [0, 0, image.width, image.height],
                "normalized_bbox": [0.0, 0.0, 1.0, 1.0],
                "width": image.width,
                "height": image.height,
            },
            tracking_summary={
                "chart_valid": True,
                "chart_region": {"width": image.width, "height": image.height},
                "display_region": {"width": image.width, "height": image.height},
                "visible_candle_count": 4,
                "global_direction": "BUY",
                "local_direction": "BUY",
                "impulse_direction": "BUY",
            },
            latest_signal={
                "action": "BUY",
                "effective_confidence": 0.99,
                "summary": "This result must not be trusted because overlay dimensions drifted.",
                "status": "tracking",
            },
        )


def _wait_for_capture_count(tracker: ContinuousWindowTrackerService, session_id: str, target: int) -> dict[str, Any]:
    deadline = time.time() + 3.0
    while time.time() < deadline:
        payload = tracker.get_session(session_id)
        if int(payload["capture_count"]) >= target:
            return payload
        time.sleep(0.05)
    raise AssertionError(f"Timed out waiting for capture_count >= {target}")


def _focus_session_without_preview(tracker: ContinuousWindowTrackerService, session_id: str) -> None:
    payload = tracker.load_session_payload(session_id)
    payload["manual_focus_region"] = {
        "enabled": True,
        "normalized_bbox": [0.0, 0.0, 1.0, 1.0],
        "source": "test",
        "updated_at": "2026-05-06T00:00:00+00:00",
    }
    payload["status"] = "ready"
    payload["updated_at"] = "2026-05-06T00:00:00+00:00"
    write_json_atomic(tracker.session_dir(session_id) / "session.json", payload)


def _allow_next_capture(tracker: ContinuousWindowTrackerService, session_id: str) -> None:
    last_capture_time = getattr(tracker, "_last_capture_time", None)
    if isinstance(last_capture_time, dict):
        cast(dict[str, float], last_capture_time).pop(session_id, None)


def _artifact_frame_from_name(path: Any) -> int:
    return int(Path(str(path)).name.split("_", 1)[0])


def test_windows_capture_backend_prefers_printwindow_for_pocket_option_browser_windows(monkeypatch: Any) -> None:
    backend = WindowsWindowCaptureBackend()
    calls: list[str] = []
    broker_image = _synthetic_full_pocket_option_gui(width=1200, height=760)

    def capture_with_imagegrab(descriptor: Mapping[str, Any]) -> Image.Image:
        calls.append(f"grab:{descriptor.get('title', '')}")
        return broker_image.copy()

    def capture_with_printwindow(hwnd: int, descriptor: Mapping[str, Any]) -> Image.Image:
        _ = descriptor
        calls.append(f"print:{hwnd}")
        return broker_image.copy()

    monkeypatch.setattr(backend, "_is_windows", lambda: True)
    monkeypatch.setattr(backend, "_capture_window_imagegrab", capture_with_imagegrab)
    monkeypatch.setattr(backend, "_capture_window_printwindow", capture_with_printwindow)
    monkeypatch.setattr(backend, "_activate_window_for_visible_capture", _activate_window_true)

    captured = backend.capture_window(
        {
            "hwnd": 101,
            "title": "The Most Innovative Trading Platform - Microsoft Edge",
            "bbox": [0, 0, 1200, 760],
        }
    )

    assert captured.size == (1200, 760)
    assert calls == ["print:101"]


def test_windows_capture_backend_falls_back_when_pocket_option_canvas_is_blank(monkeypatch: Any) -> None:
    backend = WindowsWindowCaptureBackend()
    calls: list[str] = []
    live_image = _synthetic_full_pocket_option_gui(width=1200, height=760)

    def capture_with_imagegrab(descriptor: Mapping[str, Any]) -> Image.Image:
        calls.append(f"grab:{descriptor.get('title', '')}")
        return live_image.copy()

    def capture_with_printwindow(hwnd: int, descriptor: Mapping[str, Any]) -> Image.Image:
        _ = descriptor
        calls.append(f"print:{hwnd}")
        image = Image.new("RGB", (1200, 760), color=(36, 36, 36))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 1199, 90), fill=(38, 58, 54))
        draw.rectangle((0, 0, 170, 759), fill=(38, 58, 54))
        return image

    monkeypatch.setattr(backend, "_is_windows", lambda: True)
    monkeypatch.setattr(backend, "_capture_window_imagegrab", capture_with_imagegrab)
    monkeypatch.setattr(backend, "_capture_window_printwindow", capture_with_printwindow)
    monkeypatch.setattr(backend, "_activate_window_for_visible_capture", _activate_window_true)

    captured = backend.capture_window(
        {
            "hwnd": 101,
            "title": "The Most Innovative Trading Platform - Microsoft Edge",
            "bbox": [0, 0, 1200, 760],
        }
    )

    assert captured.size == (1200, 760)
    assert calls == ["print:101", "grab:The Most Innovative Trading Platform - Microsoft Edge"]
    assert np.asarray(captured, dtype=np.uint8).std() > 3.0


def test_windows_capture_backend_accepts_pocket_chart_study_pixels(monkeypatch: Any) -> None:
    backend = WindowsWindowCaptureBackend()
    calls: list[str] = []
    chart_image = _synthetic_chart_surface("buy", width=1200, height=760)

    def capture_with_imagegrab(descriptor: Mapping[str, Any]) -> Image.Image:
        calls.append(f"grab:{descriptor.get('title', '')}")
        return chart_image.copy()

    def capture_with_printwindow(hwnd: int, descriptor: Mapping[str, Any]) -> Image.Image:
        _ = descriptor
        calls.append(f"print:{hwnd}")
        return chart_image.copy()

    monkeypatch.setattr(backend, "_is_windows", lambda: True)
    monkeypatch.setattr(backend, "_capture_window_imagegrab", capture_with_imagegrab)
    monkeypatch.setattr(backend, "_capture_window_printwindow", capture_with_printwindow)
    monkeypatch.setattr(backend, "_activate_window_for_visible_capture", _activate_window_false)

    captured = backend.capture_window(
        {
            "hwnd": 101,
            "title": "The Most Innovative Trading Platform - Microsoft Edge",
            "bbox": [0, 0, 1200, 760],
        }
    )

    assert captured.size == (1200, 760)
    assert calls == ["print:101"]


def test_windows_capture_backend_does_not_grab_wrong_foreground_for_pocket_option(monkeypatch: Any) -> None:
    backend = WindowsWindowCaptureBackend()
    calls: list[str] = []

    def capture_with_imagegrab(descriptor: Mapping[str, Any]) -> Image.Image:
        calls.append(f"grab:{descriptor.get('title', '')}")
        return _synthetic_full_pocket_option_gui(width=1200, height=760)

    def capture_with_printwindow(hwnd: int, descriptor: Mapping[str, Any]) -> Image.Image:
        _ = descriptor
        calls.append(f"print:{hwnd}")
        return Image.new("RGB", (1200, 760), color=(36, 36, 36))

    monkeypatch.setattr(backend, "_is_windows", lambda: True)
    monkeypatch.setattr(backend, "_capture_window_imagegrab", capture_with_imagegrab)
    monkeypatch.setattr(backend, "_capture_window_printwindow", capture_with_printwindow)
    monkeypatch.setattr(backend, "_activate_window_for_visible_capture", _activate_window_false)

    with pytest.raises(RuntimeError, match="broker/chart surface"):
        backend.capture_window(
            {
                "hwnd": 101,
                "title": "The Most Innovative Trading Platform - Microsoft Edge",
                "bbox": [0, 0, 1200, 760],
            }
        )

    assert calls == ["print:101"]


def test_tracker_capture_surface_unavailable_preserves_overlay_authority(tmp_path: Path) -> None:
    class _UnavailableCaptureBackend(_FakeCaptureBackend):
        def capture_window(self, descriptor: Mapping[str, Any]) -> Image.Image:
            _ = descriptor
            raise window_tracker_module.CaptureSurfaceUnavailableError(
                "Pocket Option capture did not include the broker/chart surface."
            )

    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_UnavailableCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    session_id = str(tracker.create_session(session_id="pocket-live")["session_id"])
    _focus_session_without_preview(tracker, session_id)
    payload = tracker.load_session_payload(session_id)
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    payload["frame_index"] = 11
    payload["overlay_frame_id"] = 11
    existing_overlay = tmp_path / "existing_overlay.png"
    _surface().save(existing_overlay)
    payload["last_overlay_path"] = str(existing_overlay)
    write_json_atomic(tracker.session_dir(session_id) / "session.json", payload)

    captured = tracker.capture_and_analyze(session_id, force=True)
    refreshed = tracker.get_session(session_id)

    assert captured is False
    assert refreshed["tracking_enabled"] is True
    assert refreshed["status"] != "error"
    assert refreshed["frame_index"] == 11
    assert refreshed["overlay_frame_id"] == 11
    assert refreshed["last_overlay_path"] == str(existing_overlay)
    assert refreshed["latest_signal"]["status"] == "waiting_for_broker_surface"
    assert refreshed["tracking_summary"]["source_capture_status"] == "WAITING_FOR_SOURCE_PIXELS"


def test_tradingview_window_query_matches_compact_visible_tab_title() -> None:
    assert window_tracker_module.title_matches_window_query(
        "EURUSD Chart - TradingView - Microsoft Edge",
        "Trading View",
    )
    assert not window_tracker_module.title_matches_window_query(
        "EURUSD Chart - TradingView - Microsoft Edge",
        "Pocket Option",
    )


def test_windows_capture_dimensions_preserve_window_rect_coordinate_space(monkeypatch: Any) -> None:
    backend = WindowsWindowCaptureBackend()
    calls: list[str] = []

    class _FakeUser32:
        def GetWindowRect(self, hwnd: int, rect_ref: Any) -> bool:
            calls.append(f"window:{hwnd}")
            rect = rect_ref._obj
            rect.left = 100
            rect.top = 40
            rect.right = 1380
            rect.bottom = 760
            return True

        def GetClientRect(self, hwnd: int, rect_ref: Any) -> bool:
            calls.append(f"client:{hwnd}")
            rect = rect_ref._obj
            rect.left = 0
            rect.top = 0
            rect.right = 1240
            rect.bottom = 680
            return True

    class _FakeWindll:
        user32 = _FakeUser32()

    monkeypatch.setattr(ctypes, "windll", _FakeWindll(), raising=False)

    capture_dimensions = cast(
        Callable[[int, Mapping[str, Any]], tuple[int, int]],
        getattr(backend, "_window_capture_dimensions"),
    )

    assert capture_dimensions(77, {"width": 1280, "height": 720}) == (1280, 720)
    assert calls == ["window:77"]


def test_window_tracker_adds_sniper_watch_before_confirmation_execute() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    chart = Image.new("RGB", (620, 420), color=(20, 26, 38))
    candles = _manual_candle_tracks([380, 356, 332, 308, 284, 260, 236, 212, 188, 164, 140, 116])
    build_payloads = cast(_BuildSignalPayloads, getattr(adapter, "_build_signal_payloads"))

    tracking, signal = build_payloads(
        chart,
        {"confidence": 1.0},
        candles,
        {"value": "M5", "source": "test", "confidence": 1.0},
    )

    zones = cast(Sequence[Mapping[str, Any]], tracking["projection"]["zones"])
    assert signal["candidate_action"] == "BUY"
    assert float(signal["confidence"]) >= 0.58
    assert signal["execution_action"] == "HOLD"
    assert signal["execution_permission"] == "WAIT"
    assert signal["entry_state"] in {"WAIT_FOR_SNIPER", "WAIT_FOR_TRIGGER"}
    assert any(str(zone.get("kind", "")) == "sniper" for zone in zones)
    assert "not an entry" in str(signal["summary"])


def test_window_tracker_projection_adds_continuous_probability_payload() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    chart = Image.new("RGB", (620, 420), color=(20, 26, 38))
    candles = _manual_candle_tracks(
        [380, 356, 332, 308, 284, 260, 236, 212, 188, 164, 140, 116, 104, 94, 84, 74],
        image_width=620,
        image_height=420,
        direction="BUY",
    )
    build_payloads = cast(_BuildSignalPayloads, getattr(adapter, "_build_signal_payloads"))

    tracking, signal = build_payloads(
        chart,
        {"confidence": 1.0},
        candles,
        {"value": "M5", "source": "test", "confidence": 1.0},
    )
    projection = cast(Mapping[str, Any], tracking["projection"])
    target_probability = float(projection["target_first_probability"])
    invalidation_probability = float(projection["invalidation_first_probability"])
    sideways_probability = float(projection["sideways_probability"])
    probability_sum = target_probability + invalidation_probability + sideways_probability

    assert 0.99 <= probability_sum <= 1.01
    assert target_probability > invalidation_probability
    assert projection["probability_state"] in {"TARGET_FAVORED", "MIXED_EDGE"}
    assert cast(Mapping[str, Any], projection["candle_statistics"])["sample_size"] == len(candles)
    assert cast(Mapping[str, Any], signal["probability"])["target_first_probability"] == projection["target_first_probability"]
    kernel = cast(Mapping[str, Any], tracking["decision_kernel"])
    assert kernel["dominant_side"] == "buy"
    assert kernel["state"] in {"WATCH", "ARMED", "TRIGGERED", "STALE"}
    assert signal["decision_kernel"] == kernel
    assert signal["decision"] == kernel["decision"]


def test_window_tracker_keeps_overlay_when_execution_timing_is_blocked() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    chart = Image.new("RGB", (620, 420), color=(20, 26, 38))
    candles = _manual_candle_tracks(
        [380, 356, 332, 308, 284, 260, 236, 212, 188, 164, 140, 116, 104, 94, 84, 74],
        image_width=620,
        image_height=420,
        direction="BUY",
    )
    build_payloads = cast(
        Callable[..., tuple[dict[str, Any], dict[str, Any]]],
        getattr(adapter, "_build_signal_payloads"),
    )

    tracking, signal = build_payloads(
        chart,
        {"confidence": 1.0},
        candles,
        {"value": "H21", "source": "test", "confidence": 1.0},
    )

    assert tracking["chart_valid"] is True
    assert tracking["structure_boxes"]
    assert tracking["support_resistance_zones"]
    assert signal["execution_action"] == "HOLD"
    assert signal["actionable"] is False
    assert signal["execution_lane"] == "TIMING_BLOCKED"
    assert signal["expiry_seconds"] == 0
    assert "timeframe" in str(signal["execution_block_reason"]).lower()


def test_window_tracker_blocks_new_trigger_when_target_zone_is_already_reached() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    candles = _manual_candle_tracks(
        [220, 198, 176, 154, 132, 112],
        image_width=620,
        image_height=420,
        direction="BUY",
        half_height=18,
    )
    projection: dict[str, Any] = {
        "direction": "BUY",
        "zones": [
            {"kind": "sniper", "direction": "BUY", "bbox": [180, 168, 260, 204], "invalidation_y": 260},
            {
                "kind": "primary",
                "direction": "BUY",
                "bbox": [180, 146, 260, 182],
                "target_bbox": [180, 96, 260, 132],
                "invalidation_y": 260,
            },
        ],
    }

    entry_plan = cast(
        Callable[..., dict[str, Any]],
        getattr(adapter, "_derive_entry_plan"),
    )(
        candles,
        projection,
        candidate_action="BUY",
        global_direction="BUY",
        local_direction="BUY",
        impulse_direction="BUY",
        confidence=0.82,
        latest_body_height_pct=0.08,
    )

    assert entry_plan["entry_state"] == "COMPLETE"
    assert entry_plan["execution_action"] == "HOLD"
    assert entry_plan["target_reached"] is True
    assert cast(Mapping[str, Any], entry_plan["map_timing"])["target_reached"] is True


def test_window_tracker_expiry_uses_mapped_timing_instead_of_fixed_short_expiry(tmp_path: Path) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    expiry_for_swing = cast(Callable[..., int], getattr(adapter, "_execution_expiry_seconds"))(
        {"focus_timeframe": "M5"},
        {
            "detected_timeframe": "M5",
            "decision_kernel": {
                "hold_for_candles": 2,
                "eta_target_after_trigger_candles": 15,
                "target_horizon_candles": 15,
                "eta_invalidation_candles": 20,
                "p_target_before_invalidation": 0.72,
            },
        },
        lane="PRIMARY",
    )
    expiry_for_scalp = cast(Callable[..., int], getattr(adapter, "_execution_expiry_seconds"))(
        {"focus_timeframe": "M5"},
        {
            "detected_timeframe": "M5",
            "decision_kernel": {
                "hold_for_candles": 1,
                "eta_target_after_trigger_candles": 2,
                "target_horizon_candles": 10,
                "countertrend_window_candles": 3,
                "eta_invalidation_candles": 5,
                "p_target_before_invalidation": 0.55,
            },
        },
        lane="COUNTERTREND_SCALP",
    )

    assert expiry_for_swing == 60 * 60
    assert expiry_for_scalp == 15 * 60

    service = ContinuousWindowTrackerService(root_dir=tmp_path / "expiry")
    service_expiry = cast(Callable[..., int], getattr(service, "_execution_expiry_seconds"))(
        {"focus_timeframe": "M5"},
        {
            "detected_timeframe": "M5",
            "decision_kernel": {
                "hold_for_candles": 2,
                "eta_target_after_trigger_candles": 6,
                "target_horizon_candles": 24,
                "eta_invalidation_candles": 30,
                "p_target_before_invalidation": 0.70,
            },
        },
        lane="PRIMARY",
    )
    assert service_expiry == 30 * 60


def test_window_tracker_live_flow_trigger_uses_target_eta_not_m15_floor() -> None:
    build_profile = cast(Callable[..., dict[str, Any]], getattr(window_tracker_module, "_build_execution_timing_profile"))
    profile = build_profile(
        {
            "execution_action": "BUY",
            "action": "BUY",
            "entry_state": "TRIGGER_READY",
            "focus_timeframe": "M5",
        },
        {
            "detected_timeframe": "M5",
            "decision_kernel": {
                "state": "ARMED",
                "trade_mode": "TREND_FOLLOW",
                "dominant_side": "buy",
                "major_trend_side": "buy",
                "target_horizon_candles": 10,
                "eta_trigger_candles": 1,
                "eta_target_after_trigger_candles": 1,
                "eta_invalidation_candles": 4,
                "stale_after_candles": 3,
                "hold_for_candles": 10,
                "p_target_before_invalidation": 0.74,
                "p_next_buy": 0.68,
                "p_trigger_next_1": 0.90,
                "p_trigger_next_3": 0.96,
                "p_expire_before_trigger": 0.08,
            },
        },
        lane="LIVE_MARKET_FLOW",
    )

    assert profile["timing_class"] == "current_flow_trigger"
    assert profile["recommended_expiry_seconds"] == 5 * 60
    assert profile["recommended_candles"] == 1.0


def test_window_tracker_timing_blocks_buy_into_nested_resistance_history() -> None:
    build_profile = cast(Callable[..., dict[str, Any]], getattr(window_tracker_module, "_build_execution_timing_profile"))
    tracked = [{"close_proxy": value} for value in [0.20, 0.32, 0.41, 0.55, 0.64, 0.78, 0.91, 0.98]]
    profile = build_profile(
        {
            "execution_action": "BUY",
            "action": "BUY",
            "focus_timeframe": "M5",
            "latest_price_proxy": 0.98,
        },
        {
            "detected_timeframe": "M5",
            "tracked_candles": tracked,
            "latest_price_proxy": 0.98,
            "impulse_direction": "SELL",
            "smart_money_context": {
                "support_resistance": {
                    "significant_zones": [
                        {
                            "key": "resistance_1",
                            "role": "resistance",
                            "label": "GLOBAL RESISTANCE 12T",
                            "direction": "SELL",
                            "price_relation": "at_price",
                            "significance_score": 0.86,
                            "confidence": 0.86,
                            "distance_to_latest_norm": 0.01,
                            "still_significant": True,
                        }
                    ]
                }
            },
            "decision_kernel": {
                "state": "ARMED",
                "dominant_side": "buy",
                "major_trend_side": "buy",
                "target_horizon_candles": 10,
                "eta_target_after_trigger_candles": 5,
                "hold_for_candles": 10,
                "p_target_before_invalidation": 0.64,
                "p_next_buy": 0.52,
                "p_trigger_next_1": 0.44,
                "p_trigger_next_3": 0.50,
            },
        },
        lane="PRIMARY",
    )

    assert profile["entry_allowed"] is False
    assert profile["timing_class"] in {"opposing_force_wait", "extreme_trap_wait", "history_area_wait"}
    assert profile["opposing_force_count"] == 1
    assert profile["opposing_force_zone"]["label"] == "GLOBAL RESISTANCE 12T"


def test_window_tracker_timing_blocks_sell_into_lower_history_area() -> None:
    build_profile = cast(Callable[..., dict[str, Any]], getattr(window_tracker_module, "_build_execution_timing_profile"))
    tracked = [{"close_proxy": value} for value in [0.88, 0.72, 0.56, 0.44, 0.32, 0.24, 0.27, 0.26]]
    profile = build_profile(
        {
            "execution_action": "SELL",
            "action": "SELL",
            "focus_timeframe": "M5",
            "latest_price_proxy": 0.26,
        },
        {
            "detected_timeframe": "M5",
            "tracked_candles": tracked,
            "latest_price_proxy": 0.26,
            "impulse_direction": "SELL",
            "continuation_score": 0.62,
            "decision_kernel": {
                "state": "ARMED",
                "dominant_side": "sell",
                "major_trend_side": "sell",
                "target_horizon_candles": 10,
                "eta_target_after_trigger_candles": 5,
                "hold_for_candles": 10,
                "p_target_before_invalidation": 0.84,
                "p_next_sell": 0.70,
                "p_trigger_next_1": 0.66,
                "p_trigger_next_3": 0.66,
            },
        },
        lane="PRIMARY",
    )

    assert profile["entry_allowed"] is False
    assert profile["timing_class"] == "history_area_wait"
    assert profile["history_area_label"] == "studied_low_extreme"
    assert str(profile["block_reason"]).startswith("SELL is already in the lower studied history area")


def test_window_tracker_timing_allows_live_sell_continuation_through_lower_history() -> None:
    build_profile = cast(Callable[..., dict[str, Any]], getattr(window_tracker_module, "_build_execution_timing_profile"))
    tracked = [{"close_proxy": value} for value in [0.88, 0.72, 0.56, 0.44, 0.32, 0.22, 0.27, 0.32]]
    profile = build_profile(
        {
            "execution_action": "SELL",
            "action": "SELL",
            "candidate_action": "SELL",
            "focus_timeframe": "M5",
            "latest_price_proxy": 0.32,
        },
        {
            "detected_timeframe": "M5",
            "tracked_candles": tracked,
            "latest_price_proxy": 0.32,
            "global_direction": "SELL",
            "local_direction": "SELL",
            "impulse_direction": "SELL",
            "continuation_score": 0.76,
            "decision_kernel": {
                "trade_mode": "TREND_FOLLOW",
                "state": "ARMED",
                "decision": "WATCH_FOR_TRIGGER",
                "dominant_side": "sell",
                "major_trend_side": "sell",
                "next_most_likely_event": "trigger",
                "next_candle_bias": "sell",
                "candle_execution_side": "sell",
                "target_horizon_candles": 10,
                "eta_target_after_trigger_candles": 4,
                "hold_for_candles": 10,
                "p_target_before_invalidation": 0.80,
                "p_next_sell": 0.70,
                "p_trigger_next_1": 0.86,
                "p_trigger_next_3": 0.99,
                "p_expire_before_trigger": 0.08,
                "hazard_trigger": 0.86,
                "hazard_invalidation": 0.18,
            },
        },
        lane="PRIMARY",
    )

    assert profile["entry_allowed"] is True
    assert profile["current_flow_continuation_ready"] is True
    assert profile["current_flow_direction_confirmed"] is True
    assert profile["timing_class"] == "breakout_extension"
    assert profile["history_area_label"].endswith("_live_flow_break")


def test_window_tracker_timing_blocks_weak_sell_continuation_at_lower_history() -> None:
    build_profile = cast(Callable[..., dict[str, Any]], getattr(window_tracker_module, "_build_execution_timing_profile"))
    tracked = [{"close_proxy": value} for value in [0.88, 0.72, 0.56, 0.44, 0.32, 0.22, 0.27, 0.32]]
    profile = build_profile(
        {
            "execution_action": "SELL",
            "action": "SELL",
            "candidate_action": "SELL",
            "focus_timeframe": "M5",
            "latest_price_proxy": 0.32,
        },
        {
            "detected_timeframe": "M5",
            "tracked_candles": tracked,
            "latest_price_proxy": 0.32,
            "global_direction": "SELL",
            "local_direction": "SELL",
            "impulse_direction": "BUY",
            "continuation_score": 0.76,
            "decision_kernel": {
                "trade_mode": "TREND_FOLLOW",
                "state": "ARMED",
                "decision": "WATCH_FOR_TRIGGER",
                "dominant_side": "sell",
                "major_trend_side": "sell",
                "next_most_likely_event": "trigger",
                "next_candle_bias": "sell",
                "candle_execution_side": "sell",
                "target_horizon_candles": 10,
                "eta_target_after_trigger_candles": 4,
                "hold_for_candles": 10,
                "p_target_before_invalidation": 0.80,
                "p_next_sell": 0.50,
                "p_trigger_next_1": 0.82,
                "p_trigger_next_3": 0.99,
                "p_expire_before_trigger": 0.08,
                "hazard_trigger": 0.82,
                "hazard_invalidation": 0.18,
            },
        },
        lane="PRIMARY",
    )

    assert profile["entry_allowed"] is False
    assert profile["current_flow_continuation_ready"] is False
    assert "lower studied history" in profile["block_reason"]


def test_window_tracker_timing_allows_live_buy_continuation_through_upper_history() -> None:
    build_profile = cast(Callable[..., dict[str, Any]], getattr(window_tracker_module, "_build_execution_timing_profile"))
    tracked = [{"close_proxy": value} for value in [0.12, 0.24, 0.36, 0.48, 0.62, 0.82, 0.91, 0.88]]
    profile = build_profile(
        {
            "execution_action": "BUY",
            "action": "BUY",
            "candidate_action": "BUY",
            "focus_timeframe": "M5",
            "latest_price_proxy": 0.88,
        },
        {
            "detected_timeframe": "M5",
            "tracked_candles": tracked,
            "latest_price_proxy": 0.88,
            "global_direction": "BUY",
            "local_direction": "BUY",
            "impulse_direction": "BUY",
            "continuation_score": 0.78,
            "decision_kernel": {
                "trade_mode": "TREND_FOLLOW",
                "state": "ARMED",
                "decision": "WATCH_FOR_TRIGGER",
                "dominant_side": "buy",
                "major_trend_side": "buy",
                "next_most_likely_event": "trigger",
                "next_candle_bias": "buy",
                "candle_execution_side": "buy",
                "target_horizon_candles": 10,
                "eta_target_after_trigger_candles": 4,
                "hold_for_candles": 10,
                "p_target_before_invalidation": 0.80,
                "p_next_buy": 0.70,
                "p_trigger_next_1": 0.86,
                "p_trigger_next_3": 0.99,
                "p_expire_before_trigger": 0.08,
                "hazard_trigger": 0.86,
                "hazard_invalidation": 0.18,
            },
        },
        lane="PRIMARY",
    )

    assert profile["entry_allowed"] is True
    assert profile["current_flow_continuation_ready"] is True
    assert profile["current_flow_direction_confirmed"] is True
    assert profile["timing_class"] == "breakout_extension"
    assert profile["history_area_label"].endswith("_live_flow_break")


def test_window_tracker_timing_blocks_weak_buy_continuation_at_upper_history() -> None:
    build_profile = cast(Callable[..., dict[str, Any]], getattr(window_tracker_module, "_build_execution_timing_profile"))
    tracked = [{"close_proxy": value} for value in [0.12, 0.24, 0.36, 0.48, 0.62, 0.82, 0.91, 0.88]]
    profile = build_profile(
        {
            "execution_action": "BUY",
            "action": "BUY",
            "candidate_action": "BUY",
            "focus_timeframe": "M5",
            "latest_price_proxy": 0.88,
        },
        {
            "detected_timeframe": "M5",
            "tracked_candles": tracked,
            "latest_price_proxy": 0.88,
            "global_direction": "BUY",
            "local_direction": "BUY",
            "impulse_direction": "HOLD",
            "continuation_score": 0.78,
            "decision_kernel": {
                "trade_mode": "TREND_FOLLOW",
                "state": "ARMED",
                "decision": "WATCH_FOR_TRIGGER",
                "dominant_side": "buy",
                "major_trend_side": "buy",
                "next_most_likely_event": "trigger",
                "next_candle_bias": "buy",
                "candle_execution_side": "buy",
                "target_horizon_candles": 10,
                "eta_target_after_trigger_candles": 4,
                "hold_for_candles": 10,
                "p_target_before_invalidation": 0.76,
                "p_next_buy": 0.58,
                "p_trigger_next_1": 0.86,
                "p_trigger_next_3": 0.99,
                "p_expire_before_trigger": 0.08,
                "hazard_trigger": 0.86,
                "hazard_invalidation": 0.18,
            },
        },
        lane="PRIMARY",
    )

    assert profile["entry_allowed"] is False
    assert profile["current_flow_continuation_ready"] is False
    assert "upper studied history" in profile["block_reason"]


def test_window_tracker_timing_surfaces_history_wait_for_hold_candidate_side() -> None:
    build_profile = cast(Callable[..., dict[str, Any]], getattr(window_tracker_module, "_build_execution_timing_profile"))
    tracked = [{"close_proxy": value} for value in [0.20, 0.32, 0.41, 0.55, 0.64, 0.78, 0.91, 0.98]]
    profile = build_profile(
        {
            "execution_action": "HOLD",
            "action": "BUY",
            "focus_timeframe": "M5",
            "latest_price_proxy": 0.98,
        },
        {
            "detected_timeframe": "M5",
            "tracked_candles": tracked,
            "latest_price_proxy": 0.98,
            "impulse_direction": "BUY",
            "continuation_score": 0.62,
            "decision_kernel": {
                "state": "ARMED",
                "dominant_side": "buy",
                "major_trend_side": "buy",
                "target_horizon_candles": 10,
                "eta_target_after_trigger_candles": 5,
                "hold_for_candles": 10,
                "p_target_before_invalidation": 0.76,
                "p_next_buy": 0.66,
                "p_trigger_next_1": 0.62,
                "p_trigger_next_3": 0.64,
            },
        },
        lane="PRIMARY",
    )

    assert profile["side"] == "HOLD"
    assert profile["candidate_side"] == "BUY"
    assert profile["entry_allowed"] is False
    assert profile["timing_class"] == "history_area_wait"
    assert profile["history_area_label"] == "studied_high_extreme"
    assert str(profile["block_reason"]).startswith("BUY is already in the upper studied history area")


def test_window_tracker_timing_allows_buy_reclaim_from_full_history_low() -> None:
    build_profile = cast(Callable[..., dict[str, Any]], getattr(window_tracker_module, "_build_execution_timing_profile"))
    promote = cast(Callable[..., dict[str, Any]], getattr(window_tracker_module, "_kernel_trigger_promotion_decision"))
    tracked = [
        {"close_proxy": value}
        for value in [1.00, 0.42, 0.33, 0.25, 0.18, 0.14, 0.16, 0.18, 0.20, 0.22, 0.25, 0.29, 0.33, 0.37]
    ]
    signal: dict[str, Any] = {
        "execution_action": "HOLD",
        "action": "BUY",
        "candidate_action": "BUY",
        "entry_state": "SNIPER_WATCH",
        "focus_timeframe": "M5",
        "entry_distance": {"trigger": 0.04},
    }
    tracking: dict[str, Any] = {
        "detected_timeframe": "M5",
        "tracked_candles": tracked,
        "latest_price_proxy": 0.37,
        "impulse_direction": "BUY",
        "continuation_score": 0.46,
        "decision_kernel": {
            "state": "ARMED",
            "decision": "WATCH_FOR_TRIGGER",
            "trade_mode": "TREND_FOLLOW",
            "dominant_side": "buy",
            "major_trend_side": "buy",
            "candle_execution_side": "buy",
            "next_candle_bias": "buy",
            "next_most_likely_event": "trigger",
            "distance_to_trigger": 0.04,
            "eta_trigger_candles": 1,
            "target_horizon_candles": 10,
            "eta_target_after_trigger_candles": 5,
            "hold_for_candles": 10,
            "p_target_before_invalidation": 0.56,
            "p_next_buy": 0.62,
            "p_trigger_next_1": 0.88,
            "p_trigger_next_3": 0.99,
            "p_expire_before_trigger": 0.08,
        },
    }

    profile = build_profile(signal, tracking, lane="PRIMARY")
    decision = promote(signal, tracking, profile)

    assert profile["entry_allowed"] is True
    assert profile["history_area_label"] == "lower_studied_reclaim"
    assert profile["favorable_history_reclaim"] is True
    assert decision["accepted"] is True
    assert decision["side"] == "BUY"


def test_window_tracker_trigger_promotion_rejects_upper_history_buy() -> None:
    build_profile = cast(Callable[..., dict[str, Any]], getattr(window_tracker_module, "_build_execution_timing_profile"))
    promote = cast(Callable[..., dict[str, Any]], getattr(window_tracker_module, "_kernel_trigger_promotion_decision"))
    tracked = [{"close_proxy": value} for value in [0.20, 0.32, 0.41, 0.55, 0.64, 0.78, 0.91, 0.98]]
    signal: dict[str, Any] = {
        "execution_action": "HOLD",
        "action": "BUY",
        "candidate_action": "BUY",
        "entry_state": "SNIPER_WATCH",
        "focus_timeframe": "M5",
        "entry_distance": {"trigger": 0.03},
    }
    tracking: dict[str, Any] = {
        "detected_timeframe": "M5",
        "tracked_candles": tracked,
        "latest_price_proxy": 0.98,
        "impulse_direction": "BUY",
        "decision_kernel": {
            "state": "ARMED",
            "decision": "WATCH_FOR_TRIGGER",
            "trade_mode": "TREND_FOLLOW",
            "dominant_side": "buy",
            "major_trend_side": "buy",
            "candle_execution_side": "buy",
            "next_candle_bias": "buy",
            "next_most_likely_event": "trigger",
            "distance_to_trigger": 0.03,
            "eta_trigger_candles": 1,
            "target_horizon_candles": 10,
            "eta_target_after_trigger_candles": 5,
            "hold_for_candles": 10,
            "p_target_before_invalidation": 0.76,
            "p_next_buy": 0.66,
            "p_trigger_next_1": 0.88,
            "p_trigger_next_3": 0.99,
            "p_expire_before_trigger": 0.08,
        },
    }

    profile = build_profile(signal, tracking, lane="PRIMARY")
    decision = promote(signal, tracking, profile)

    assert profile["entry_allowed"] is False
    assert decision["accepted"] is False
    assert "upper studied history" in str(decision["reason"])


def test_window_tracker_timing_uses_wick_range_to_block_buy_at_historical_high() -> None:
    build_profile = cast(Callable[..., dict[str, Any]], getattr(window_tracker_module, "_build_execution_timing_profile"))
    tracked = [
        {"low_proxy": 0.18, "high_proxy": 0.28, "close_proxy": 0.24},
        {"low_proxy": 0.24, "high_proxy": 0.38, "close_proxy": 0.34},
        {"low_proxy": 0.34, "high_proxy": 0.48, "close_proxy": 0.42},
        {"low_proxy": 0.42, "high_proxy": 0.58, "close_proxy": 0.54},
        {"low_proxy": 0.54, "high_proxy": 0.72, "close_proxy": 0.66},
        {"low_proxy": 0.66, "high_proxy": 0.86, "close_proxy": 0.78},
        {"low_proxy": 0.76, "high_proxy": 0.98, "close_proxy": 0.72},
    ]
    profile = build_profile(
        {
            "execution_action": "BUY",
            "action": "BUY",
            "focus_timeframe": "M5",
            "latest_price_proxy": 0.97,
        },
        {
            "detected_timeframe": "M5",
            "tracked_candles": tracked,
            "latest_price_proxy": 0.97,
            "impulse_direction": "BUY",
            "decision_kernel": {
                "state": "ARMED",
                "dominant_side": "buy",
                "major_trend_side": "buy",
                "target_horizon_candles": 10,
                "eta_target_after_trigger_candles": 5,
                "hold_for_candles": 10,
                "p_target_before_invalidation": 0.82,
                "p_next_buy": 0.72,
                "p_trigger_next_1": 0.78,
                "p_trigger_next_3": 0.92,
            },
        },
        lane="PRIMARY",
    )

    assert profile["entry_allowed"] is False
    assert profile["history_area_label"] in {"upper_studied_history", "studied_high_extreme"}
    assert profile["price_position"]["range_sample_size"] >= len(tracked) * 3
    assert str(profile["block_reason"]).startswith("BUY is already in the upper studied history area")


def test_window_tracker_timing_requires_significant_entry_area_in_middle_history() -> None:
    build_profile = cast(Callable[..., dict[str, Any]], getattr(window_tracker_module, "_build_execution_timing_profile"))
    tracked = [{"close_proxy": value} for value in [0.20, 0.32, 0.44, 0.70, 0.82, 0.54, 0.56, 0.55]]
    profile = build_profile(
        {
            "execution_action": "BUY",
            "action": "BUY",
            "focus_timeframe": "M5",
            "latest_price_proxy": 0.55,
        },
        {
            "detected_timeframe": "M5",
            "tracked_candles": tracked,
            "latest_price_proxy": 0.55,
            "impulse_direction": "BUY",
            "continuation_score": 0.70,
            "decision_kernel": {
                "state": "ARMED",
                "dominant_side": "buy",
                "major_trend_side": "buy",
                "target_horizon_candles": 10,
                "eta_target_after_trigger_candles": 5,
                "hold_for_candles": 10,
                "p_target_before_invalidation": 0.86,
                "p_next_buy": 0.76,
                "p_trigger_next_1": 0.82,
                "p_trigger_next_3": 0.94,
            },
        },
        lane="PRIMARY",
    )

    assert profile["entry_allowed"] is False
    assert profile["significant_entry_context"] is False
    assert profile["entry_area_count"] == 0
    assert "requires a significant historical support" in str(profile["block_reason"])


def test_window_tracker_timing_allows_sell_from_significant_resistance_area() -> None:
    build_profile = cast(Callable[..., dict[str, Any]], getattr(window_tracker_module, "_build_execution_timing_profile"))
    tracked = [{"close_proxy": value} for value in [0.20, 0.35, 0.48, 0.62, 0.78, 0.92, 0.88, 0.80]]
    profile = build_profile(
        {
            "execution_action": "SELL",
            "action": "SELL",
            "focus_timeframe": "M5",
            "latest_price_proxy": 0.80,
        },
        {
            "detected_timeframe": "M5",
            "tracked_candles": tracked,
            "latest_price_proxy": 0.80,
            "impulse_direction": "SELL",
            "support_resistance_context": {
                "significant_zones": [
                    {
                        "key": "resistance_1",
                        "role": "resistance",
                        "label": "NEAREST RESISTANCE 4T",
                        "direction": "SELL",
                        "price_relation": "at_price",
                        "entry_relevance": "entry_resistance",
                        "significance_score": 0.84,
                        "historical_significance": 0.78,
                        "confidence": 0.84,
                        "distance_to_latest_norm": 0.01,
                        "still_significant": True,
                    }
                ]
            },
            "decision_kernel": {
                "state": "ARMED",
                "dominant_side": "sell",
                "major_trend_side": "sell",
                "target_horizon_candles": 10,
                "eta_target_after_trigger_candles": 5,
                "hold_for_candles": 10,
                "p_target_before_invalidation": 0.76,
                "p_next_sell": 0.68,
                "p_trigger_next_1": 0.78,
                "p_trigger_next_3": 0.92,
            },
        },
        lane="PRIMARY",
    )

    assert profile["entry_allowed"] is True
    assert profile["significant_entry_context"] is True
    assert profile["entry_area_zone"]["role"] == "resistance"
    assert profile["entry_area_score"] >= 0.38


def test_window_tracker_blocks_primary_lane_when_kernel_is_pullback_wait(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path / "lane")
    selected = tracker.select_execution_lane(
        {
            "execution_action": "BUY",
            "actionable": True,
            "decision_kernel": {
                "trade_mode": "PULLBACK_WAIT",
                "dominant_side": "buy",
                "major_trend_side": "buy",
                "target_horizon_candles": 10,
            },
        },
        {
            "decision_kernel": {
                "trade_mode": "PULLBACK_WAIT",
                "dominant_side": "buy",
                "major_trend_side": "buy",
                "target_horizon_candles": 10,
            }
        },
        {"allow_countertrend_scalp": False, "min_primary_target_candles": 10},
    )

    assert selected["actionable"] is False
    assert selected["lane"] == "MAJOR_TREND_WAIT"


def test_window_tracker_accepts_location_sniper_at_significant_resistance(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path / "location-sniper")
    tracked = [{"close_proxy": value} for value in [0.20, 0.35, 0.48, 0.62, 0.78, 0.92, 0.88, 0.80]]
    selected = tracker.select_execution_lane(
        {
            "execution_action": "SELL",
            "action": "SELL",
            "actionable": True,
            "focus_timeframe": "M5",
            "latest_price_proxy": 0.80,
        },
        {
            "detected_timeframe": "M5",
            "latest_price_proxy": 0.80,
            "tracked_candles": tracked,
            "impulse_direction": "SELL",
            "support_resistance_context": {
                "significant_zones": [
                    {
                        "key": "resistance_1",
                        "role": "resistance",
                        "direction": "SELL",
                        "entry_relevance": "entry_resistance",
                        "significance_score": 0.84,
                        "historical_significance": 0.78,
                        "confidence": 0.84,
                        "distance_to_latest_norm": 0.01,
                        "still_significant": True,
                    }
                ]
            },
            "decision_kernel": {
                "state": "ARMED",
                "trade_mode": "STAND_ASIDE",
                "dominant_side": "sell",
                "major_trend_side": "sell",
                "target_horizon_candles": 4,
                "eta_target_after_trigger_candles": 4,
                "hold_for_candles": 4,
                "conflict_score": 0.16,
                "p_target_before_invalidation": 0.68,
                "hazard_invalidation": 0.10,
                "hazard_trigger": 0.42,
                "p_expire_before_trigger": 0.12,
                "next_most_likely_event": "trigger",
                "next_candle_bias": "sell",
                "p_next_buy": 0.18,
                "p_next_sell": 0.70,
                "p_trigger_next_1": 0.78,
                "p_trigger_next_3": 0.92,
                "expected_value_R": 1.2,
            },
            "candle_statistics": {"opposing_ratio": 0.14},
            "box_context": {"failure_risk": 0.18},
        },
        {
            "allow_countertrend_scalp": False,
            "allow_location_sniper_entries": True,
            "min_primary_target_candles": 10,
            "min_location_sniper_target_candles": 3,
        },
    )

    assert selected["actionable"] is True
    assert selected["lane"] == "LOCATION_SNIPER"
    assert selected["side"] == "SELL"


def test_window_tracker_accepts_live_market_flow_from_current_movement(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path / "live-flow")
    tracked = [{"close_proxy": value} for value in [0.30, 0.42, 0.55, 0.68, 0.82, 0.78, 0.74, 0.70]]
    selected = tracker.select_execution_lane(
        {
            "execution_action": "HOLD",
            "action": "HOLD",
            "candidate_action": "HOLD",
            "actionable": False,
            "focus_timeframe": "M5",
            "latest_price_proxy": 0.70,
        },
        {
            "detected_timeframe": "M5",
            "visible_candle_count": 12,
            "latest_price_proxy": 0.70,
            "tracked_candles": tracked,
            "local_direction": "SELL",
            "impulse_direction": "SELL",
            "local_slope": -0.052,
            "current_slope": -0.044,
            "impulse_delta": -0.055,
            "continuation_score": 0.48,
            "impulse_score": 0.62,
            "latest_body_height_pct": 0.16,
            "global_local_control": {"owner": "local", "direction": "SELL", "control_strength": 0.72},
            "smart_money_context": {"dominant_side": "SELL", "confidence": 0.86},
            "behavior": {
                "state_confidence": 0.74,
                "candle_tokens": [{"direction": "SELL", "body_pct": 0.64}],
            },
            "support_resistance_context": {
                "significant_zones": [
                    {
                        "key": "resistance_1",
                        "role": "resistance",
                        "direction": "SELL",
                        "entry_relevance": "entry_resistance",
                        "price_relation": "at_price",
                        "significance_score": 0.84,
                        "historical_significance": 0.78,
                        "confidence": 0.84,
                        "distance_to_latest_norm": 0.01,
                        "still_significant": True,
                    }
                ]
            },
            "decision_kernel": {
                "state": "ARMED",
                "trade_mode": "STAND_ASIDE",
                "dominant_side": "sell",
                "major_trend_side": "buy",
                "target_horizon_candles": 10,
                "eta_target_after_trigger_candles": 4,
                "hold_for_candles": 10,
                "p_target_before_invalidation": 0.66,
                "p_next_buy": 0.18,
                "p_next_sell": 0.68,
                "p_trigger_next_1": 0.72,
                "p_trigger_next_3": 0.86,
                "p_expire_before_trigger": 0.16,
                "hazard_trigger": 0.48,
                "hazard_invalidation": 0.16,
                "next_most_likely_event": "trigger",
                "next_candle_bias": "sell",
                "expected_value_R": 0.44,
            },
            "candle_statistics": {"direction_run": 3, "momentum_consistency": 0.62, "opposing_ratio": 0.12},
            "box_context": {"failure_risk": 0.18},
        },
        {"allow_live_momentum_entries": True, "min_live_momentum_score": 0.54},
    )

    assert selected["actionable"] is True
    assert selected["lane"] == "LIVE_MARKET_FLOW"
    assert selected["side"] == "SELL"


def test_window_tracker_blocks_live_market_flow_into_opposing_support(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path / "live-flow-opposing")
    tracked = [{"close_proxy": value} for value in [0.30, 0.42, 0.55, 0.68, 0.82, 0.78, 0.74, 0.70]]
    selected = tracker.select_execution_lane(
        {
            "execution_action": "HOLD",
            "action": "HOLD",
            "candidate_action": "HOLD",
            "actionable": False,
            "focus_timeframe": "M5",
            "latest_price_proxy": 0.70,
        },
        {
            "detected_timeframe": "M5",
            "visible_candle_count": 12,
            "latest_price_proxy": 0.70,
            "tracked_candles": tracked,
            "local_direction": "SELL",
            "impulse_direction": "SELL",
            "local_slope": -0.052,
            "current_slope": -0.044,
            "impulse_delta": -0.055,
            "continuation_score": 0.48,
            "impulse_score": 0.62,
            "latest_body_height_pct": 0.16,
            "global_local_control": {"owner": "local", "direction": "SELL", "control_strength": 0.72},
            "smart_money_context": {"dominant_side": "SELL", "confidence": 0.86},
            "behavior": {
                "state_confidence": 0.74,
                "candle_tokens": [{"direction": "SELL", "body_pct": 0.64}],
            },
            "support_resistance_context": {
                "significant_zones": [
                    {
                        "key": "resistance_1",
                        "role": "resistance",
                        "direction": "SELL",
                        "entry_relevance": "entry_resistance",
                        "price_relation": "at_price",
                        "significance_score": 0.84,
                        "historical_significance": 0.78,
                        "confidence": 0.84,
                        "distance_to_latest_norm": 0.01,
                        "still_significant": True,
                    },
                    {
                        "key": "support_1",
                        "role": "support",
                        "direction": "BUY",
                        "price_relation": "at_price",
                        "significance_score": 0.86,
                        "historical_significance": 0.82,
                        "confidence": 0.86,
                        "distance_to_latest_norm": 0.01,
                        "still_significant": True,
                    },
                ]
            },
            "decision_kernel": {
                "state": "ARMED",
                "trade_mode": "STAND_ASIDE",
                "dominant_side": "sell",
                "major_trend_side": "buy",
                "target_horizon_candles": 10,
                "eta_target_after_trigger_candles": 4,
                "hold_for_candles": 10,
                "p_target_before_invalidation": 0.62,
                "p_next_buy": 0.18,
                "p_next_sell": 0.68,
                "p_trigger_next_1": 0.72,
                "p_trigger_next_3": 0.86,
                "p_expire_before_trigger": 0.16,
                "hazard_trigger": 0.48,
                "hazard_invalidation": 0.16,
                "next_most_likely_event": "trigger",
                "next_candle_bias": "sell",
                "expected_value_R": 0.44,
            },
            "candle_statistics": {"direction_run": 3, "momentum_consistency": 0.62, "opposing_ratio": 0.12},
            "box_context": {"failure_risk": 0.18},
        },
        {"allow_live_momentum_entries": True, "min_live_momentum_score": 0.54},
    )

    assert selected["actionable"] is False
    assert selected["lane"] == "LIVE_MARKET_FLOW_WAIT"
    assert "opposing" in str(selected["reason"]).lower()


def test_window_tracker_accepts_opposing_force_reaction_from_active_support(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path / "opposing-force-reaction")
    tracked = [{"close_proxy": value} for value in [0.88, 0.75, 0.60, 0.47, 0.35, 0.24, 0.27, 0.31]]
    selected = tracker.select_execution_lane(
        {
            "execution_action": "HOLD",
            "action": "SELL",
            "candidate_action": "SELL",
            "actionable": False,
            "focus_timeframe": "M5",
            "latest_price_proxy": 0.31,
        },
        {
            "detected_timeframe": "M5",
            "visible_candle_count": 16,
            "latest_price_proxy": 0.31,
            "tracked_candles": tracked,
            "global_direction": "SELL",
            "local_direction": "BUY",
            "impulse_direction": "BUY",
            "local_slope": 0.052,
            "current_slope": 0.044,
            "impulse_delta": 0.051,
            "continuation_score": 0.46,
            "reversal_score": 0.24,
            "impulse_score": 0.58,
            "latest_body_height_pct": 0.18,
            "global_local_control": {"owner": "local", "direction": "BUY", "control_strength": 0.76},
            "smart_money_context": {"dominant_side": "SELL", "confidence": 0.72},
            "behavior": {
                "state_confidence": 0.70,
                "candle_tokens": [{"direction": "BUY", "body_pct": 0.58}],
            },
            "support_resistance_context": {
                "significant_zones": [
                    {
                        "key": "support_1",
                        "role": "support",
                        "label": "NEAREST SUPPORT 19T",
                        "direction": "BUY",
                        "entry_relevance": "entry_support",
                        "price_relation": "at_price",
                        "significance_score": 0.90,
                        "historical_significance": 0.86,
                        "confidence": 0.90,
                        "distance_to_latest_norm": 0.01,
                        "still_significant": True,
                    }
                ]
            },
            "decision_kernel": {
                "state": "ARMED",
                "trade_mode": "PULLBACK_WAIT",
                "dominant_side": "sell",
                "major_trend_side": "sell",
                "target_horizon_candles": 10,
                "eta_target_after_trigger_candles": 5,
                "hold_for_candles": 10,
                "conflict_score": 0.34,
                "p_target_before_invalidation": 0.48,
                "hazard_invalidation": 0.24,
                "hazard_trigger": 0.42,
                "p_expire_before_trigger": 0.22,
                "next_most_likely_event": "invalidation",
                "next_candle_bias": "buy",
                "candle_execution_side": "buy",
                "p_next_buy": 0.62,
                "p_next_sell": 0.24,
                "p_trigger_next_1": 0.64,
                "p_trigger_next_3": 0.82,
                "expected_value_R": 0.58,
            },
            "candle_statistics": {"direction_run": 2, "momentum_consistency": 0.58, "opposing_ratio": 0.20},
            "box_context": {"failure_risk": 0.24},
        },
        {"allow_opposing_force_reactions": True, "allow_live_momentum_entries": True},
    )

    timing = cast(Mapping[str, Any], selected["execution_timing"])
    assert selected["actionable"] is True
    assert selected["lane"] == "OPPOSING_FORCE_REACTION"
    assert selected["side"] == "BUY"
    assert timing["entry_allowed"] is True
    assert timing["opposing_force_reaction_ready"] is True
    assert timing["primary_blocked_side"] == "SELL"
    assert cast(Mapping[str, Any], timing["primary_opposing_force_zone"])["role"] == "support"


def test_window_tracker_waits_when_opposing_force_reaction_has_only_impulse_and_kernel(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path / "opposing-force-impulse-only")
    tracked = [{"close_proxy": value} for value in [0.20, 0.34, 0.48, 0.62, 0.76, 0.88, 0.93, 0.98]]
    selected = tracker.select_execution_lane(
        {
            "execution_action": "HOLD",
            "action": "BUY",
            "candidate_action": "BUY",
            "actionable": False,
            "focus_timeframe": "M5",
            "latest_price_proxy": 0.98,
        },
        {
            "detected_timeframe": "M5",
            "visible_candle_count": 16,
            "latest_price_proxy": 0.98,
            "tracked_candles": tracked,
            "global_direction": "BUY",
            "local_direction": "BUY",
            "impulse_direction": "SELL",
            "local_slope": 0.052,
            "current_slope": 0.044,
            "impulse_delta": -0.051,
            "continuation_score": 0.62,
            "reversal_score": 0.18,
            "impulse_score": 0.58,
            "latest_body_height_pct": 0.18,
            "global_local_control": {"owner": "local", "direction": "BUY", "control_strength": 0.76},
            "smart_money_context": {"dominant_side": "BUY", "confidence": 0.72},
            "behavior": {
                "state_confidence": 0.70,
                "candle_tokens": [{"direction": "BUY", "body_pct": 0.42}],
            },
            "support_resistance_context": {
                "significant_zones": [
                    {
                        "key": "resistance_1",
                        "role": "resistance",
                        "label": "NEAREST RESISTANCE 22T",
                        "direction": "SELL",
                        "entry_relevance": "entry_resistance",
                        "price_relation": "at_price",
                        "significance_score": 0.92,
                        "historical_significance": 0.88,
                        "confidence": 0.92,
                        "distance_to_latest_norm": 0.01,
                        "still_significant": True,
                    }
                ]
            },
            "decision_kernel": {
                "state": "ARMED",
                "trade_mode": "PULLBACK_WAIT",
                "dominant_side": "buy",
                "major_trend_side": "buy",
                "target_horizon_candles": 10,
                "eta_target_after_trigger_candles": 5,
                "hold_for_candles": 10,
                "conflict_score": 0.34,
                "p_target_before_invalidation": 0.58,
                "hazard_invalidation": 0.20,
                "hazard_trigger": 0.48,
                "p_expire_before_trigger": 0.18,
                "next_most_likely_event": "trigger",
                "next_candle_bias": "sell",
                "candle_execution_side": "sell",
                "p_next_buy": 0.28,
                "p_next_sell": 0.52,
                "p_trigger_next_1": 0.64,
                "p_trigger_next_3": 0.82,
                "expected_value_R": 0.42,
            },
            "candle_statistics": {"direction_run": 3, "momentum_consistency": 0.58, "opposing_ratio": 0.20},
            "box_context": {"failure_risk": 0.24},
        },
        {"allow_opposing_force_reactions": True, "allow_live_momentum_entries": True},
    )

    timing = cast(Mapping[str, Any], selected["execution_timing"])
    assert selected["actionable"] is False
    assert selected["lane"] == "OPPOSING_FORCE_REACTION_WAIT"
    assert selected["candidate_side"] == "SELL"
    assert "not decisive enough" in str(selected["reason"])
    assert timing["opposing_force_reaction_decisive_votes"] == ["impulse"]


def test_window_tracker_waits_when_opposing_force_reaction_is_only_latest_candle(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path / "opposing-force-weak-current")
    tracked = [{"close_proxy": value} for value in [0.88, 0.75, 0.60, 0.47, 0.35, 0.24, 0.25, 0.29]]
    selected = tracker.select_execution_lane(
        {
            "execution_action": "HOLD",
            "action": "SELL",
            "candidate_action": "SELL",
            "actionable": False,
            "focus_timeframe": "M5",
            "latest_price_proxy": 0.29,
        },
        {
            "detected_timeframe": "M5",
            "visible_candle_count": 16,
            "latest_price_proxy": 0.29,
            "tracked_candles": tracked,
            "global_direction": "SELL",
            "local_direction": "SELL",
            "impulse_direction": "SELL",
            "local_slope": -0.018,
            "current_slope": -0.016,
            "impulse_score": 0.42,
            "latest_body_height_pct": 0.22,
            "global_local_control": {"owner": "local", "direction": "SELL", "control_strength": 0.62},
            "smart_money_context": {"dominant_side": "SELL", "confidence": 0.72},
            "behavior": {"candle_tokens": [{"direction": "BUY", "body_pct": 0.58}]},
            "support_resistance_context": {
                "significant_zones": [
                    {
                        "key": "support_1",
                        "role": "support",
                        "label": "NEAREST SUPPORT 19T",
                        "direction": "BUY",
                        "entry_relevance": "entry_support",
                        "price_relation": "at_price",
                        "significance_score": 0.92,
                        "historical_significance": 0.88,
                        "confidence": 0.92,
                        "distance_to_latest_norm": 0.01,
                        "still_significant": True,
                    }
                ]
            },
            "decision_kernel": {
                "state": "ARMED",
                "trade_mode": "PULLBACK_WAIT",
                "dominant_side": "sell",
                "major_trend_side": "sell",
                "target_horizon_candles": 10,
                "eta_target_after_trigger_candles": 5,
                "hold_for_candles": 10,
                "conflict_score": 0.34,
                "p_target_before_invalidation": 0.48,
                "hazard_invalidation": 0.24,
                "hazard_trigger": 0.42,
                "p_expire_before_trigger": 0.22,
                "next_most_likely_event": "invalidation",
                "next_candle_bias": "buy",
                "candle_execution_side": "buy",
                "p_next_buy": 0.62,
                "p_next_sell": 0.24,
                "p_trigger_next_1": 0.64,
                "p_trigger_next_3": 0.82,
                "expected_value_R": 0.58,
            },
            "candle_statistics": {"direction_run": 1, "momentum_consistency": 0.52, "opposing_ratio": 0.20},
            "box_context": {"failure_risk": 0.24},
        },
        {"allow_opposing_force_reactions": True, "allow_live_momentum_entries": True},
    )

    timing = cast(Mapping[str, Any], selected["execution_timing"])
    assert selected["actionable"] is False
    assert selected["lane"] == "OPPOSING_FORCE_REACTION_WAIT"
    assert "local/control/impulse" in str(selected["reason"])
    assert timing["opposing_force_reaction_decisive_votes"] == []


def test_window_tracker_waits_when_opposing_force_has_no_reaction_confirmation(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path / "opposing-force-no-confirm")
    tracked = [{"close_proxy": value} for value in [0.88, 0.75, 0.60, 0.47, 0.35, 0.24, 0.27, 0.31]]
    selected = tracker.select_execution_lane(
        {
            "execution_action": "HOLD",
            "action": "SELL",
            "candidate_action": "SELL",
            "actionable": False,
            "focus_timeframe": "M5",
            "latest_price_proxy": 0.31,
        },
        {
            "detected_timeframe": "M5",
            "visible_candle_count": 16,
            "latest_price_proxy": 0.31,
            "tracked_candles": tracked,
            "global_direction": "SELL",
            "local_direction": "SELL",
            "impulse_direction": "SELL",
            "local_slope": -0.052,
            "current_slope": -0.044,
            "impulse_delta": -0.051,
            "continuation_score": 0.46,
            "reversal_score": 0.24,
            "impulse_score": 0.58,
            "latest_body_height_pct": 0.18,
            "global_local_control": {"owner": "local", "direction": "SELL", "control_strength": 0.76},
            "smart_money_context": {"dominant_side": "SELL", "confidence": 0.72},
            "behavior": {
                "state_confidence": 0.70,
                "candle_tokens": [{"direction": "SELL", "body_pct": 0.58}],
            },
            "support_resistance_context": {
                "significant_zones": [
                    {
                        "key": "support_1",
                        "role": "support",
                        "label": "NEAREST SUPPORT 19T",
                        "direction": "BUY",
                        "entry_relevance": "entry_support",
                        "price_relation": "at_price",
                        "significance_score": 0.90,
                        "historical_significance": 0.86,
                        "confidence": 0.90,
                        "distance_to_latest_norm": 0.01,
                        "still_significant": True,
                    }
                ]
            },
            "decision_kernel": {
                "state": "ARMED",
                "trade_mode": "PULLBACK_WAIT",
                "dominant_side": "sell",
                "major_trend_side": "sell",
                "target_horizon_candles": 10,
                "eta_target_after_trigger_candles": 5,
                "hold_for_candles": 10,
                "conflict_score": 0.34,
                "p_target_before_invalidation": 0.48,
                "hazard_invalidation": 0.24,
                "hazard_trigger": 0.42,
                "p_expire_before_trigger": 0.22,
                "next_most_likely_event": "trigger",
                "next_candle_bias": "sell",
                "candle_execution_side": "sell",
                "p_next_buy": 0.18,
                "p_next_sell": 0.62,
                "p_trigger_next_1": 0.64,
                "p_trigger_next_3": 0.82,
                "expected_value_R": 0.58,
            },
            "candle_statistics": {"direction_run": 2, "momentum_consistency": 0.58, "opposing_ratio": 0.20},
            "box_context": {"failure_risk": 0.24},
        },
        {"allow_opposing_force_reactions": True, "allow_live_momentum_entries": True},
    )

    assert selected["actionable"] is False
    assert selected["side"] == "HOLD"
    assert selected["lane"] == "LIVE_MARKET_FLOW_WAIT"


def test_window_tracker_blocks_primary_lane_when_target_horizon_is_too_short(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path / "short-target")
    selected = tracker.select_execution_lane(
        {
            "execution_action": "SELL",
            "actionable": True,
            "decision_kernel": {
                "trade_mode": "TREND_FOLLOW",
                "dominant_side": "sell",
                "major_trend_side": "sell",
                "target_horizon_candles": 4,
            },
        },
        {
            "decision_kernel": {
                "trade_mode": "TREND_FOLLOW",
                "dominant_side": "sell",
                "major_trend_side": "sell",
                "target_horizon_candles": 4,
            }
        },
        {"allow_countertrend_scalp": False, "min_primary_target_candles": 10},
    )

    assert selected["actionable"] is False
    assert selected["lane"] == "TARGET_TOO_SHORT"


def test_window_tracker_blocks_primary_lane_when_risk_metrics_are_missing(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path / "missing-risk")
    selected = tracker.select_execution_lane(
        {"execution_action": "BUY", "actionable": True},
        {
            "decision_kernel": {
                "trade_mode": "TREND_FOLLOW",
                "dominant_side": "buy",
                "major_trend_side": "buy",
                "target_horizon_candles": 10,
            }
        },
        {"allow_countertrend_scalp": False, "min_primary_target_candles": 10},
    )

    assert selected["actionable"] is False
    assert selected["lane"] == "RISK_GATE"


def test_window_tracker_accepts_primary_lane_only_with_complete_risk_metrics(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path / "complete-risk")
    selected = tracker.select_execution_lane(
        {"execution_action": "BUY", "actionable": True},
        {
            "decision_kernel": {
                "trade_mode": "TREND_FOLLOW",
                "dominant_side": "buy",
                "major_trend_side": "buy",
                "target_horizon_candles": 10,
                "conflict_score": 0.18,
                "p_target_before_invalidation": 0.68,
                "hazard_invalidation": 0.14,
                "hazard_trigger": 0.42,
                "p_expire_before_trigger": 0.12,
                "next_most_likely_event": "trigger",
                "next_candle_bias": "buy",
                "p_next_buy": 0.68,
                "p_next_sell": 0.18,
                "expected_value_R": 1.4,
            },
            "candle_statistics": {"opposing_ratio": 0.16},
            "box_context": {"failure_risk": 0.20},
        },
        {"allow_countertrend_scalp": False, "min_primary_target_candles": 10},
    )

    assert selected["actionable"] is True
    assert selected["lane"] == "TREND_FOLLOW"


def test_window_tracker_rejects_garbled_broker_markettext() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    normalize = cast(Callable[[str], str], getattr(adapter, "_normalize_market_candidate"))

    assert normalize("AUD/CHF OTC") == "AUD/CHF OTC"
    assert normalize("GBPJPY OTC") == "GBP/JPY OTC"
    assert normalize("W D0CR01ILJI . /JFW1 P IY W P 1") == ""


def test_window_tracker_reuses_cached_market_only_when_selector_fingerprint_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    fingerprint = cast(
        Callable[[Image.Image], str],
        getattr(window_tracker_module, "_market_selector_visual_fingerprint"),
    )
    surface = _synthetic_chart_surface("buy", width=900, height=520)
    draw = ImageDraw.Draw(surface)
    draw.rectangle((4, 4, 176, 34), fill=(29, 38, 58))
    draw.text((12, 12), "AUD/NZD OTC", fill=(235, 240, 248))
    selector_fingerprint = fingerprint(surface)

    def fail_market_detector(
        image: Image.Image,
        timeframe_selector: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        _ = image
        _ = timeframe_selector
        raise AssertionError("cached market selector should be reused while the header fingerprint is unchanged")

    monkeypatch.setattr(adapter, "_detect_market_selector", fail_market_detector)
    study = adapter.study(
        surface,
        session_payload={
            "session_id": "pocket-live",
            "manual_focus_region": {"enabled": True},
            "tracking_summary": {
                "detected_market": "AUD/NZD",
                "market_confidence": 0.91,
                "detected_timeframe": "M5",
                "timeframe_confidence": 1.0,
                "market_selector_visual_fingerprint": selector_fingerprint,
            },
            "latest_signal": {
                "market": "AUD/NZD",
                "market_confidence": 0.91,
                "focus_timeframe": "M5",
                "focus_timeframe_confidence": 1.0,
                "market_selector_visual_fingerprint": selector_fingerprint,
            },
        },
    )

    assert study.latest_signal["market"] == "AUD/NZD"
    assert study.latest_signal["market_source"] == "live_cached_selector"
    assert study.latest_signal["market_selector_visual_changed"] is False


def test_window_tracker_rebinds_market_when_selector_fingerprint_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    fingerprint = cast(
        Callable[[Image.Image], str],
        getattr(window_tracker_module, "_market_selector_visual_fingerprint"),
    )
    old_surface = _synthetic_chart_surface("buy", width=900, height=520)
    new_surface = _synthetic_chart_surface("sell", width=900, height=520)
    old_draw = ImageDraw.Draw(old_surface)
    new_draw = ImageDraw.Draw(new_surface)
    old_draw.rectangle((4, 4, 176, 34), fill=(29, 38, 58))
    old_draw.text((12, 12), "AUD/NZD OTC", fill=(235, 240, 248))
    new_draw.rectangle((4, 4, 176, 34), fill=(29, 38, 58))
    new_draw.text((12, 12), "EUR/USD OTC", fill=(235, 240, 248))
    previous_fingerprint = fingerprint(old_surface)
    assert previous_fingerprint != fingerprint(new_surface)

    detector_calls = 0

    def detect_market_selector(
        image: Image.Image,
        timeframe_selector: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        nonlocal detector_calls
        _ = image
        _ = timeframe_selector
        detector_calls += 1
        return {"value": "EUR/USD OTC", "source": "header_text", "confidence": 0.93}

    monkeypatch.setattr(adapter, "_detect_market_selector", detect_market_selector)
    study = adapter.study(
        new_surface,
        session_payload={
            "session_id": "pocket-live",
            "manual_focus_region": {"enabled": True},
            "tracking_summary": {
                "detected_market": "AUD/NZD",
                "market_confidence": 0.91,
                "detected_timeframe": "M5",
                "timeframe_confidence": 1.0,
                "market_selector_visual_fingerprint": previous_fingerprint,
            },
            "latest_signal": {
                "market": "AUD/NZD",
                "market_confidence": 0.91,
                "focus_timeframe": "M5",
                "focus_timeframe_confidence": 1.0,
                "market_selector_visual_fingerprint": previous_fingerprint,
            },
        },
    )

    assert detector_calls == 1
    assert study.latest_signal["market"] == "EUR/USD OTC"
    assert study.latest_signal["market_source"] == "header_text"
    assert study.latest_signal["market_selector_visual_changed"] is True
    assert study.tracking_summary["detected_market"] == "EUR/USD OTC"


def test_window_tracker_candle_count_increases_probability_sample_weight() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    build_stats = cast(Callable[..., dict[str, Any]], getattr(adapter, "_build_candle_statistics"))
    short_stats = build_stats(_manual_candle_tracks([300, 280, 260, 240, 220], direction="BUY"), candidate_action="BUY")
    long_stats = build_stats(
        _manual_candle_tracks(
            [380, 360, 340, 320, 300, 280, 260, 240, 220, 200, 180, 160, 140, 120, 100],
            direction="BUY",
        ),
        candidate_action="BUY",
    )

    assert float(long_stats["sample_weight"]) > float(short_stats["sample_weight"])
    assert float(long_stats["candidate_ratio"]) == 1.0
    assert int(long_stats["direction_run"]) == 15


def test_window_tracker_builds_behavior_sequence_payload() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    chart = Image.new("RGB", (620, 420), color=(20, 26, 38))
    candles = _manual_candle_tracks(
        [380, 356, 332, 308, 284, 260, 236, 212, 188, 164, 140, 116],
        image_width=620,
        image_height=420,
        direction="BUY",
    )
    build_payloads = cast(
        Callable[..., tuple[dict[str, Any], dict[str, Any]]],
        getattr(adapter, "_build_signal_payloads"),
    )

    tracking, signal = build_payloads(
        chart,
        {"confidence": 1.0},
        candles,
        {"value": "M5", "source": "test", "confidence": 1.0},
    )
    behavior = cast(Mapping[str, Any], tracking["behavior"])
    box_context = cast(Mapping[str, Any], tracking["box_context"])
    trend_context = cast(Mapping[str, Any], tracking["trend_context"])
    transition_probs = cast(Mapping[str, float], behavior["next_state_probs"])
    tokens = cast(Sequence[Mapping[str, Any]], behavior["candle_tokens"])

    assert behavior["current_state"] != "noise"
    assert behavior["next_most_likely_state"]
    assert 0.99 <= sum(float(value) for value in transition_probs.values()) <= 1.01
    assert tokens
    assert "micro_structure_event" in tokens[-1]
    assert "failure_risk" in box_context
    assert "slope_current" in trend_context
    assert cast(Mapping[str, Any], signal["behavior"])["current_state"] == behavior["current_state"]


def test_window_tracker_builds_phoenixguard_live_report(monkeypatch: Any) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    chart = Image.new("RGB", (620, 420), color=(20, 26, 38))
    candles = _manual_candle_tracks(
        [132, 154, 176, 198, 220, 242, 264, 286, 308, 330, 352, 374],
        image_width=620,
        image_height=420,
        direction="SELL",
    )
    monkeypatch.setattr(adapter, "_get_phoenixguard_memory_bank", lambda: _StubPhoenixBank(_sample_memory_entries()))

    build_payloads = cast(_BuildSignalPayloads, getattr(adapter, "_build_signal_payloads"))

    tracking, signal = build_payloads(
        chart,
        {"confidence": 1.0},
        candles,
        {"value": "M5", "source": "test", "confidence": 1.0},
    )

    report = cast(Mapping[str, Any], tracking["phoenixguard_report"])
    memory_findings = cast(Mapping[str, Any], report["memory_findings"])
    memory_match = cast(Mapping[str, Any], report["memory_to_current_match"])
    forward_projection = cast(Mapping[str, Any], report["forward_projection"])

    assert report["status"] == "ready"
    assert report["decision_state"] in {
        "forming",
        "building",
        "armed",
        "triggering",
        "active",
        "late",
        "exhausted",
        "invalidated",
        "transition",
        "uncertain but maturing",
    }
    assert int(memory_findings["total_entries"]) == 3
    assert cast(Sequence[Mapping[str, Any]], memory_match["top_matches"])
    assert forward_projection["dominant_side"] == "SELL"
    assert signal["phoenixguard_decision_state"] == report["decision_state"]
    assert str(signal["phoenixguard_report_summary"])


def test_window_tracker_reuses_fresh_phoenixguard_live_report(monkeypatch: Any) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    chart = Image.new("RGB", (620, 420), color=(20, 26, 38))
    candles = _manual_candle_tracks(
        [132, 154, 176, 198, 220, 242, 264, 286, 308, 330, 352, 374],
        image_width=620,
        image_height=420,
        direction="SELL",
    )
    previous_report: dict[str, Any] = {
        "status": "ready",
        "headline": "cached precision report",
        "decision_state": "armed",
        "generated_epoch": time.time(),
    }
    monkeypatch.setattr(
        adapter,
        "_get_phoenixguard_memory_bank",
        lambda: pytest.fail("fresh PhoenixGuard report cache should avoid memory-bank inference"),
    )

    build_payloads = cast(_BuildSignalPayloads, getattr(adapter, "_build_signal_payloads"))

    tracking, signal = build_payloads(
        chart,
        {"confidence": 1.0},
        candles,
        {"value": "M5", "source": "test", "confidence": 1.0},
        session_payload={
            "execution_controls": {"phoenix_report_interval_sec": 30.0},
            "tracking_summary": {"phoenixguard_report": previous_report},
        },
    )

    report = cast(Mapping[str, Any], tracking["phoenixguard_report"])
    assert report["headline"] == "cached precision report"
    assert report["cached"] is True
    assert float(report["cache_age_sec"]) >= 0.0
    assert cast(Mapping[str, Any], report["cache_key"])
    assert signal["phoenixguard_report_status"] == "ready"


def test_window_tracker_builds_memory_projection_payload(monkeypatch: Any) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    chart = Image.new("RGB", (620, 420), color=(20, 26, 38))
    candles = _manual_candle_tracks(
        [132, 154, 176, 198, 220, 242, 264, 286, 308, 330, 352, 374],
        image_width=620,
        image_height=420,
        direction="SELL",
    )
    monkeypatch.setattr(adapter, "_get_phoenixguard_memory_bank", lambda: _StubPhoenixBank(_sample_memory_entries()))

    build_payloads = cast(_BuildSignalPayloads, getattr(adapter, "_build_signal_payloads"))

    tracking, signal = build_payloads(
        chart,
        {"confidence": 1.0, "pixel_bbox": [0, 0, chart.width, chart.height], "normalized_bbox": [0.0, 0.0, 1.0, 1.0], "width": chart.width, "height": chart.height},
        candles,
        {"value": "M5", "source": "test", "confidence": 1.0},
        market_selector={"value": "GBP/JPY OTC", "source": "headertext", "confidence": 0.91},
    )

    payload = adapter.build_memory_projection(chart, tracking, signal, mode="future")

    assert payload["status"] == "ready"
    assert payload["mode"] == "future"
    assert payload["dominant_side"] == "SELL"
    assert payload["counter_side"] == "BUY"
    assert float(payload["memory_similarity"]) > 0.0
    assert float(payload["memory_precision_score"]) >= 0.70
    assert float(payload["memory_edge"]) >= 0.06
    assert cast(Mapping[str, Any], payload["memory_precision"])["accepted"] is True
    primary_matches = cast(Sequence[Mapping[str, Any]], cast(Mapping[str, Any], payload["primary_fit"])["top_matches"])
    assert primary_matches
    assert float(primary_matches[0]["retrieval_similarity"]) >= 0.89
    assert float(primary_matches[0]["precision_score"]) >= 0.70
    assert primary_matches[0]["lesson_role"] == "actual_entry"
    assert float(primary_matches[0]["aggressive_entry_score"]) >= 0.80
    prediction_stack = cast(Sequence[Mapping[str, Any]], payload["prediction_stack"])
    assert len(prediction_stack) == 2
    assert prediction_stack[0]["rank"] == 1
    assert str(prediction_stack[0]["lesson_role"]) == "actual_entry"
    assert cast(Mapping[str, Any], payload["counter_fit"])["top_matches"]
    assert cast(Mapping[str, Any], payload["forward_projection"])["projected_candles"]
    hotspots = cast(Sequence[Mapping[str, Any]], payload["hotspots"])
    assert hotspots
    assert any(row.get("role") == "aggressive_entry" for row in hotspots)
    assert any(str(row.get("role", "")).startswith("forecast_") for row in hotspots)
    assert payload["market"] == "GBP/JPY OTC"


def test_tracker_memory_projection_actions_persist_and_go_stale(tmp_path: Path, monkeypatch: Any) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    entries = _materialize_memory_images(tmp_path / "memory-images", _sample_memory_entries())
    monkeypatch.setattr(adapter, "_get_phoenixguard_memory_bank", lambda: _StubPhoenixBank(entries))

    def detect_market_selector(
        image: Image.Image,
        timeframe_selector: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        _ = image
        _ = timeframe_selector
        return {"value": "GBP/JPY OTC", "source": "headertext", "confidence": 0.92}

    monkeypatch.setattr(
        adapter,
        "_detect_market_selector",
        detect_market_selector,
    )
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_chart_surface("sell"), _synthetic_chart_surface("buy")]),
        tracking_adapter=adapter,
    )

    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.02, 0.02, 0.98, 0.98], source="test")

    predicted = tracker.run_memory_projection(str(session["session_id"]), mode="predict")
    assert predicted["memory_projection_active_mode"] == "predict"
    assert predicted["memory_projection_current"]["status"] == "ready"
    assert predicted["memory_projection_current"]["mode"] == "predict"
    assert Path(str(predicted["memory_projection_current"]["reference_image_path"])).is_file()
    assert Path(str(predicted["memory_projection_current"]["projection_image_path"])).is_file()
    assert predicted["latest_signal"]["market"] == "GBP/JPY OTC"

    future = tracker.run_memory_projection(str(session["session_id"]), mode="future")
    assert future["memory_projection_active_mode"] == "future"
    assert future["memory_projection_current"]["status"] == "ready"
    assert future["memory_projection_current"]["mode"] == "future"
    assert Path(str(future["memory_projection_current"]["reference_image_path"])).is_file()
    assert Path(str(future["memory_projection_current"]["projection_image_path"])).is_file()

    refreshed = tracker.capture_once(str(session["session_id"]))
    assert refreshed["memory_projection_future"]["status"] == "stale"
    assert refreshed["memory_projection_future"]["is_current"] is False
    assert "Run Show Future again" in str(refreshed["memory_projection_future"]["summary"])
    assert refreshed["latest_signal"]["market"] == "GBP/JPY OTC"


def test_window_tracker_behavior_detects_box_reaction_context() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    build_stats = cast(Callable[..., dict[str, Any]], getattr(adapter, "_build_candle_statistics"))
    build_behavior = cast(Callable[..., dict[str, Any]], getattr(adapter, "_build_behavior_payload"))
    candles = _manual_candle_tracks(
        [310, 288, 266, 244, 264, 286, 278, 248],
        image_width=420,
        image_height=360,
        direction="BUY",
        half_height=18,
    )
    for index in (4, 5):
        candles[index]["direction"] = "SELL"
        candles[index]["color"] = "magenta"
    projection: dict[str, Any] = {
        "direction": "BUY",
        "confidence": 0.78,
        "fit_bounds": [4, 160, 400, 330],
        "zones": [
            {"kind": "sniper", "direction": "BUY", "bbox": [250, 266, 330, 302], "invalidation_y": 322},
            {"kind": "primary", "direction": "BUY", "bbox": [270, 220, 350, 250], "target_bbox": [270, 178, 350, 208], "invalidation_y": 322},
        ],
    }
    stats = build_stats(candles, candidate_action="BUY")

    behavior = build_behavior(
        candles,
        projection,
        stats,
        candidate_action="BUY",
        global_direction="BUY",
        local_direction="SELL",
        impulse_direction="BUY",
        global_slope=0.06,
        local_slope=-0.03,
        current_slope=0.04,
        recent_range=0.24,
        consolidation_score=0.18,
        impulse_score=0.74,
        reversal_score=0.20,
    )

    box_context = cast(Mapping[str, Any], behavior["box_context"])
    transition_probs = cast(Mapping[str, float], behavior["next_state_probs"])
    assert int(box_context["candles_seen_in_box"]) >= 1
    assert str(box_context["box_type"]) == "sniper_buy"
    assert behavior["current_state"] in {"bullish_rejection_building", "bullish_pullback", "bullish_continuation"}
    assert any(key.startswith("bullish_") for key in transition_probs)


def test_window_tracker_invalidation_cancels_instead_of_entering() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    derive_entry_plan = cast(Callable[..., dict[str, Any]], getattr(adapter, "_derive_entry_plan"))
    candles = _manual_candle_tracks([260, 238, 216, 194, 172, 150], half_height=24)
    latest = candles[-1]
    latest["bbox"] = [190, 132, 202, 248]
    latest["center_y"] = 190.0
    projection: dict[str, Any] = {
        "direction": "BUY",
        "zones": [
            {"kind": "sniper", "direction": "BUY", "bbox": [210, 188, 290, 220], "invalidation_y": 240},
            {"kind": "primary", "direction": "BUY", "bbox": [230, 126, 320, 158], "invalidation_y": 240},
        ],
    }

    plan = derive_entry_plan(
        candles,
        projection,
        candidate_action="BUY",
        global_direction="BUY",
        local_direction="BUY",
        impulse_direction="BUY",
        confidence=0.82,
        latest_body_height_pct=0.18,
    )

    assert plan["entry_state"] == "INVALIDATED"
    assert plan["execution_action"] == "HOLD"
    assert plan["execution_permission"] == "WAIT"
    assert "do not enter" in str(plan["instruction"])


def test_window_tracker_sniper_reclaim_can_execute_before_trigger() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    derive_entry_plan = cast(Callable[..., dict[str, Any]], getattr(adapter, "_derive_entry_plan"))
    candles = _manual_candle_tracks([260, 238, 216, 194, 172, 150], half_height=24)
    latest = candles[-1]
    latest["bbox"] = [190, 190, 202, 236]
    latest["center_y"] = 214.0
    latest["direction"] = "BUY"
    projection: dict[str, Any] = {
        "direction": "BUY",
        "zones": [
            {"kind": "sniper", "direction": "BUY", "bbox": [210, 198, 290, 230], "invalidation_y": 252},
            {"kind": "primary", "direction": "BUY", "bbox": [230, 126, 320, 158], "invalidation_y": 252},
        ],
    }

    plan = derive_entry_plan(
        candles,
        projection,
        candidate_action="BUY",
        global_direction="BUY",
        local_direction="BUY",
        impulse_direction="HOLD",
        confidence=0.72,
        latest_body_height_pct=0.11,
    )

    assert plan["entry_state"] == "SNIPER_READY"
    assert plan["entry_quality"] == "SNIPER"
    assert plan["execution_action"] == "BUY"
    assert plan["execution_permission"] == "EXECUTE"


def test_window_tracker_filters_top_strip_noise_from_candle_tracks() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    filter_tracks = cast(
        Callable[[Sequence[Mapping[str, Any]], tuple[int, int]], list[dict[str, Any]]],
        getattr(adapter, "_filter_main_candle_tracks"),
    )
    real_tracks = _manual_candle_tracks([310, 292, 274, 256, 238, 220, 202, 184], image_width=640, image_height=420)
    top_noise: list[dict[str, Any]] = [
        {
            "track_id": 900 + index,
            "bbox": [500 + index * 12, 4, 506 + index * 12, 24],
            "center_x": 503.0 + index * 12.0,
            "center_y": 14.0,
            "direction": "BUY",
            "color": "green",
            "width": 6,
            "height": 20,
        }
        for index in range(4)
    ]

    filtered = filter_tracks([*real_tracks, *top_noise], (640, 420))

    assert len(filtered) == len(real_tracks)
    assert max(float(track["center_y"]) for track in filtered) > 180.0
    assert all(int(track["track_id"]) < 900 for track in filtered)


def test_window_tracker_projection_zones_fit_inside_candle_bounds_near_right_edge() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    build_projection = cast(Callable[..., dict[str, Any]], getattr(adapter, "_build_projection_payload"))
    candles = _manual_candle_tracks(
        [318, 300, 282, 264, 246, 228, 210, 192, 174, 156],
        image_width=640,
        image_height=420,
        direction="BUY",
    )
    for index, candle in enumerate(candles):
        center_x = 408.0 + index * 22.0
        candle["center_x"] = center_x
        candle["bbox"] = [int(center_x - 5), int(candle["bbox"][1]), int(center_x + 5), int(candle["bbox"][3])]

    projection = build_projection(
        candles,
        (640, 420),
        candidate_action="BUY",
        execution_action="HOLD",
        global_direction="BUY",
        local_direction="BUY",
        impulse_direction="BUY",
        confidence=0.86,
        local_slope=0.06,
        impulse_delta=0.07,
        recent_range=0.22,
        latest_body_height_pct=0.16,
    )
    fit_left, fit_top, fit_right, fit_bottom = [float(value) for value in cast(Sequence[Any], projection["fit_bounds"])]

    for zone in cast(Sequence[Mapping[str, Any]], projection["zones"]):
        bbox = cast(Sequence[Any], zone["bbox"])
        assert fit_left <= float(bbox[0]) < float(bbox[2]) <= fit_right
        assert fit_top <= float(bbox[1]) < float(bbox[3]) <= fit_bottom
        target = cast(Sequence[Any], zone.get("target_bbox", []))
        if target:
            assert fit_left <= float(target[0]) < float(target[2]) <= fit_right
            assert fit_top <= float(target[1]) < float(target[3]) <= fit_bottom


def test_window_tracker_chart_bbox_ignores_broker_top_strip_noise() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    detect_chart_bbox = cast(Callable[[Image.Image], tuple[list[int], float]], getattr(adapter, "_detect_chart_bbox"))
    image = Image.new("RGB", (900, 520), color=(20, 26, 38))
    draw = ImageDraw.Draw(image)
    for x in range(24, 860, 140):
        draw.text((x, 4), "$20", fill=(95, 225, 82))
    for index in range(16):
        x = 120 + index * 32
        y = 330 - index * 9
        previous_y = y + 18 if index == 0 else 330 - (index - 1) * 9
        color = (95, 225, 82) if y <= previous_y else (255, 72, 214)
        draw.line((x, min(previous_y, y) - 20, x, max(previous_y, y) + 20), fill=color, width=2)
        draw.rectangle((x - 6, min(previous_y, y), x + 6, max(previous_y, y)), fill=color)

    bbox, confidence = detect_chart_bbox(image)

    assert bbox[1] > 40
    assert confidence > 0.32


def test_window_tracker_support_resistance_zones_fit_touch_candles() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()

    def derive_zones(candles: Sequence[Mapping[str, Any]], size: tuple[int, int]) -> list[dict[str, Any]]:
        return adapter.derive_support_resistance_zones(  # noqa: SLF001
            candles,
            size,
            candidate_action="BUY",
        )

    candles = _manual_candle_tracks(
        [230, 224, 232, 226, 231, 225, 233, 227],
        image_width=900,
        image_height=520,
        direction="BUY",
        half_height=22,
    )

    zones = derive_zones(candles, (900, 520))

    assert zones
    for zone in zones:
        bbox = cast(Sequence[Any], zone["bbox"])
        assert (float(bbox[2]) - float(bbox[0])) < 900 * 0.45
        assert int(zone["line_x0"]) >= int(bbox[0])
        assert int(zone["line_x1"]) <= int(bbox[2])
        assert "distance_to_latest_norm" in zone
        assert zone["price_relation"] in {"above_price", "at_price", "below_price"}
        assert zone["entry_relevance"] in {"entry_support", "entry_resistance", "target_support", "target_resistance", "context"}
        assert "historical_significance" in zone
        assert "significance_score" in zone
        assert "still_significant" in zone
        assert zone["supply_demand_model_version"] == "base_departure_v1"
        assert zone["supply_demand_origin"] in {"base_departure_imbalance", "reaction_cluster"}
        assert zone["freshness_state"] in {"FRESH", "TESTED_ONCE", "TESTED_TWICE", "CONSUMED", "BROKEN", "REFERENCE"}
        assert zone["quality_grade"] in {"A", "B", "C", "REFERENCE"}
        assert zone["zone_authority_state"] in {
            "FRESH_ACTIVE",
            "MITIGATED_ACTIVE",
            "HISTORICAL_ACTIVE",
            "CONTEXT_REFERENCE",
            "CONSUMED_REFERENCE",
            "BROKEN_REFERENCE",
            "ROLE_FLIP_CONFIRMED",
        }
        assert isinstance(zone["entry_authority_allowed"], bool)
        assert "zone_not_exact_price" in zone["book_rule_flags"]
        assert "body_close_break_checked" in zone["book_rule_flags"]
        assert "historical_context_preserved" in zone["book_rule_flags"]
        assert "authority_score" in zone
        assert "institutional_zone_score" in zone
        assert "proximal_y" in zone
        assert "distal_y" in zone
    assert any(bool(zone.get("nearest")) for zone in zones)


def test_window_tracker_supply_demand_origin_prefers_fresh_base_departure() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    candles = _manual_candle_tracks(
        [300, 332, 354, 352, 351, 315, 286, 260, 235, 220],
        image_width=900,
        image_height=520,
        direction="BUY",
        half_height=10,
    )

    zones = adapter.derive_support_resistance_zones(
        candles,
        (900, 520),
        candidate_action="BUY",
    )

    fresh_demand_zones = [
        zone
        for zone in zones
        if str(zone.get("role", "")) == "support"
        and str(zone.get("supply_demand_origin", "")) == "base_departure_imbalance"
    ]

    assert fresh_demand_zones
    best_demand = max(fresh_demand_zones, key=lambda zone: float(zone.get("institutional_zone_score", 0.0)))
    assert best_demand["zone_pattern"] in {"DROP_BASE_RALLY", "RALLY_BASE_RALLY"}
    assert best_demand["freshness_state"] in {"FRESH", "TESTED_ONCE", "TESTED_TWICE"}
    assert float(best_demand["institutional_zone_score"]) >= 0.52
    assert best_demand["quality_grade"] in {"A", "B", "C"}
    assert float(best_demand["proximal_y"]) < float(best_demand["distal_y"])
    assert float(best_demand["distal_buffer_y"]) > float(best_demand["distal_y"])
    assert best_demand["entry_authority_allowed"] is True
    assert str(best_demand["zone_authority_state"]) in {"FRESH_ACTIVE", "MITIGATED_ACTIVE"}
    assert "base_departure_imbalance_validated" in best_demand["book_rule_flags"]


def test_window_tracker_demotes_body_close_broken_support_to_reference_only() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    candles = _manual_candle_tracks(
        [220, 222, 221, 223, 222, 260, 232, 300, 314, 330, 326, 318],
        image_width=900,
        image_height=520,
        direction="BUY",
        half_height=12,
    )

    zones = adapter.derive_support_resistance_zones(
        candles,
        (900, 520),
        candidate_action="BUY",
        max_zones_per_role=8,
        max_total_zones=16,
    )

    broken_supports = [
        zone
        for zone in zones
        if str(zone.get("role", "")) == "support" and bool(zone.get("broken_after_touch", False))
    ]

    assert broken_supports
    for zone in broken_supports:
        assert zone["entry_authority_allowed"] is False
        assert zone["zone_authority_state"] == "BROKEN_REFERENCE"
        assert zone["validation_reason"] == "body_close_broken_reference_only"
        assert int(zone["body_close_break_count"]) >= 1
        assert "broken_body_close_demoted" in zone["book_rule_flags"]
        assert "REFERENCE_ONLY_ZONE" in zone["knowledge_tags"]


def test_window_tracker_adds_smart_money_and_significant_sr_context_to_boxes() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    chart = Image.new("RGB", (620, 420), color=(20, 26, 38))
    candles = _manual_candle_tracks(
        [360, 334, 368, 330, 352, 318, 338, 292, 314, 268, 288, 240, 260, 212, 232, 184],
        image_width=620,
        image_height=420,
        direction="BUY",
        half_height=24,
    )
    build_payloads = cast(
        Callable[
            [Image.Image, Mapping[str, Any], Sequence[Mapping[str, Any]], Mapping[str, Any]],
            tuple[dict[str, Any], dict[str, Any]],
        ],
        getattr(adapter, "_build_signal_payloads"),
    )

    tracking, signal = build_payloads(
        chart,
        {"confidence": 1.0},
        candles,
        {"value": "M5", "source": "test", "confidence": 1.0},
    )

    smart_money = cast(Mapping[str, Any], tracking["smart_money_context"])
    support_resistance = cast(Mapping[str, Any], tracking["support_resistance_context"])
    projection = cast(Mapping[str, Any], tracking["projection"])
    projection_zones = cast(Sequence[Mapping[str, Any]], projection["zones"])
    structure_boxes = cast(Sequence[Mapping[str, Any]], tracking["structure_boxes"])
    kernel_stream = cast(Sequence[Mapping[str, Any]], cast(Mapping[str, Any], tracking["decision_kernel"])["evidence_stream"])

    assert smart_money["dominant_side"] in {"BUY", "SELL", "HOLD"}
    assert "summary" in smart_money
    assert "significant_count" in support_resistance
    assert "institutional_zone_count" in support_resistance
    assert "fresh_zone_count" in support_resistance
    assert "reference_zone_count" in support_resistance
    assert "active_authority_count" in support_resistance
    assert signal["smart_money_context"] == smart_money
    assert all("smart_money" in box and "smc_score" in box for box in structure_boxes)
    assert all("smart_money" in zone and "smc_score" in zone for zone in projection_zones)
    assert any(str(item.get("zone_type", "")) == "smart_money" for item in kernel_stream)
    assert any(str(item.get("zone_type", "")) in {"support", "resistance"} for item in kernel_stream)


def test_tracker_session_defaults_to_awaiting_focus(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface()]),
        tracking_adapter=_FakeTrackingAdapter(),
    )

    session = tracker.create_session(session_id="pocket-live")

    assert session["status"] == "awaiting_focus"
    assert session["tracking_enabled"] is False
    assert session["manual_focus_region"]["enabled"] is False
    assert session["latest_signal"]["status"] == "awaiting_focus"
    assert str(session["latest_signal"]["signal_id"]).startswith("default_")
    assert "Strict execution blocked" not in str(session["latest_signal"]["summary"])


def test_start_session_clears_stalepackets_before_first_fresh_capture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface()]),
        tracking_adapter=_FakeTrackingAdapter(),
    )
    session = tracker.create_session(session_id="pocket-live")
    payload = tracker.load_session(str(session["session_id"]))
    assert payload is not None
    payload["manual_focus_region"] = {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]}
    payload["status"] = "ready"
    payload["tracking_enabled"] = False
    payload["last_capture_epoch"] = 100.0
    payload["last_capture_started_epoch"] = 99.0
    payload["decision_valid_until_epoch"] = 130.0
    payload["model_council_result"] = {"state": "WATCHING"}
    payload["model_council_study_packet"] = {"packet_id": "stalepacket", "packet_type": "STUDY_PACKET"}
    payload["model_council_packet"] = {"packet_id": "stale_exec", "packet_type": "PG_EXECUTION_PACKET_V3"}
    payload["execution_packet"] = {"packet_id": "stale_exec", "packet_type": "PG_EXECUTION_PACKET_V3"}
    tracker.save_session(payload)

    def ensure_worker_stub(*_args: object, **_kwargs: object) -> None:
        return None

    monkeypatch.setattr(tracker, "_ensure_worker", ensure_worker_stub)

    started = tracker.start_session(str(session["session_id"]))

    assert started["status"] == "running"
    assert started["tracking_enabled"] is True
    assert started["last_capture_epoch"] == 0.0
    assert started["latest_signal"]["status"] == "warming"
    stored = tracker.load_session(str(session["session_id"]))
    assert stored is not None
    assert stored["decision_valid_until_epoch"] == 0.0
    assert stored["model_council_study_packet"] == {}
    with pytest.raises(KeyError):
        tracker.latest_model_council_study_packet(str(session["session_id"]))


def test_model_council_packet_lookup_ignores_expired_execution_packet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(window_tracker_module, "_now_epoch", lambda: 150.0)
    expiredpacket: dict[str, Any] = {
        "schema_version": "PG_EXECUTION_PACKET_V3",
        "packet_type": "PG_EXECUTION_PACKET_V3",
        "packet_id": "expired-exec",
        "created_epoch": 100.0,
        "valid_until_epoch": 120.0,
        "valid_until_epoch_sec": 120.0,
    }

    assert model_council_packet_from_payload(
        {
            "model_council_packet": expiredpacket,
            "execution_packet": expiredpacket,
        }
    ) == {}


def test_model_council_packet_lookup_rejects_demoted_execution_root_without_side(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(window_tracker_module, "_now_epoch", lambda: 150.0)
    demoted_root: dict[str, Any] = {
        "schema_version": "PG_EXECUTION_PACKET_V3",
        "packet_type": "PG_EXECUTION_PACKET_V3",
        "packet_id": "pgpkt-demoted-study",
        "created_epoch": 149.0,
        "created_epoch_sec": 149.0,
        "valid_until_epoch": 180.0,
        "valid_until_epoch_sec": 180.0,
        "execution": {
            "enabled": False,
            "state": "WATCHING",
            "side": None,
            "expiry_seconds": 600,
        },
        "model_council": {
            "final_state": "WATCHING",
            "final_side": None,
        },
        "promotion_trace": {
            "packet_result": "STUDY_PACKET_PUBLISHED",
            "denied_at": "SIGNAL_THESIS_V3_COUNTERTREND_BLOCK",
        },
    }

    assert model_council_packet_from_payload({"model_council_result": demoted_root}) == {}


def testpublic_session_payload_does_not_block_non_executable_missing_signal_id(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path)

    payload: dict[str, Any] = {
        "session_id": "pocket-live",
        "status": "running",
        "tracking_enabled": True,
        "capture_count": 1,
        "manual_focus_region": {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]},
        "latest_signal": {
            "status": "blocked",
            "action": "HOLD",
            "execution_action": "HOLD",
            "actionable": False,
            "summary": "Strict execution blocked this signal because signal_id is missing.",
            "phoenixguard_report_summary": "Phoenix forming",
        },
        "tracking_summary": {"chart_valid": True},
    }

    public = tracker.public_session_payload(payload)

    assert public["latest_signal"]["status"] == "tracking"
    assert public["latest_signal"]["summary"] == "Phoenix forming"
    assert public["latest_signal"]["execution_action"] == "HOLD"
    assert public["latest_signal"]["actionable"] is False


def testpublic_session_payload_blocks_executable_missing_signal_id(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path)

    payload: dict[str, Any] = {
        "session_id": "pocket-live",
        "status": "running",
        "tracking_enabled": True,
        "capture_count": 1,
        "manual_focus_region": {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]},
        "latest_signal": {
            "status": "tracking",
            "action": "BUY",
            "execution_action": "BUY",
            "actionable": True,
            "summary": "Ready",
        },
        "tracking_summary": {"chart_valid": True},
    }

    public = tracker.public_session_payload(payload)

    assert public["latest_signal"]["status"] == "blocked"
    assert public["latest_signal"]["execution_action"] == "HOLD"
    assert public["latest_signal"]["actionable"] is False
    assert public["latest_signal"]["summary"] == "Strict execution blocked this signal because signal_id is missing."


def testpublic_session_payload_publishes_strict_executable_signal_contract(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path)
    published_epoch = time.time()

    payload: dict[str, Any] = {
        "session_id": "pocket-live",
        "status": "running",
        "tracking_enabled": True,
        "capture_count": 7,
        "frame_index": 42,
        "last_capture_epoch": published_epoch,
        "manual_focus_region": {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]},
        "latest_signal": {
            "signal_id": "sig-1",
            "status": "tracking",
            "action": "BUY",
            "execution_action": "BUY",
            "actionable": True,
            "setup": "Continuation",
            "entry_state": "TRIGGER_READY",
            "expiry_seconds": 180,
            "expiry_source": "opposing_force_timing",
            "pipeline_latency_sec": 0.25,
            "summary": "Ready",
            "active_hypothesis": {"side": "BUY"},
            "buy_hypothesis": {"side": "BUY", "score": 0.71},
            "sell_hypothesis": {"side": "SELL", "score": 0.22},
            "invalidation_condition": "Break below trigger low.",
        },
        "tracking_summary": {
            "chart_valid": True,
            "detected_market": "GBP/AUD OTC",
            "detected_timeframe": "M5",
            "market_confidence": 0.91,
            "timeframe_confidence": 0.88,
            "decision_kernel": {"state": "TRIGGERED", "dominant_side": "BUY"},
        },
    }

    public = tracker.public_session_payload(payload)
    signal = public["latest_signal"]

    assert signal["signal_id"] == "sig-1"
    assert signal["session_id"] == "pocket-live"
    assert signal["symbol"] == "GBP/AUD OTC"
    assert signal["timeframe"] == "M5"
    assert signal["side"] == "BUY"
    assert signal["execution_action"] == "BUY"
    assert signal["setup_type"] == "Continuation"
    assert signal["entry_state"] == "TRIGGER_READY"
    assert signal["expiry_seconds"] == 180
    assert signal["expiry_source"] == "opposing_force_timing"
    assert signal["signal_created_epoch"] == published_epoch
    assert float(signal["signal_valid_until_epoch"]) > published_epoch
    assert signal["tracker_frame_id"] == 42
    assert signal["capture_count"] == 7
    assert signal["latency_seconds"] == 0.25
    assert signal["decision_kernel"]["dominant_side"] == "BUY"
    assert signal["market_context"]["symbol"] == "GBP/AUD OTC"
    assert signal["buy_hypothesis"]["side"] == "BUY"
    assert signal["sell_hypothesis"]["side"] == "SELL"
    assert signal["active_hypothesis"]["side"] == "BUY"
    assert signal["invalidation_condition"] == "Break below trigger low."
    assert signal["entry_reason"] == "Ready"
    assert signal["no_trade_reason"] == ""


@pytest.mark.parametrize(
    ("signal_overrides", "expected_reason"),
    [
        (
            {
                "active_hypothesis": {},
                "buy_hypothesis": {"side": "BUY", "score": 0.54},
                "sell_hypothesis": {"side": "SELL", "score": 0.53},
            },
            "Dual BUY/SELL hypotheses are unresolved",
        ),
        ({"current_candle_late": True, "active_hypothesis": {"side": "BUY"}}, "Current candle is late"),
        ({"expiry_source": "timeframe_fallback", "active_hypothesis": {"side": "BUY"}}, "Expiry is fallback-derived"),
        ({"active_hypothesis": {"side": "SELL"}}, "Active hypothesis is SELL"),
    ],
)
def testpublic_session_payload_blocks_unsafe_published_execution_contract(
    tmp_path: Path,
    signal_overrides: Mapping[str, Any],
    expected_reason: str,
) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path)
    latest_signal: dict[str, Any] = {
        "signal_id": "sig-unsafe",
        "status": "tracking",
        "action": "BUY",
        "execution_action": "BUY",
        "actionable": True,
        "entry_state": "TRIGGER_READY",
        "expiry_seconds": 180,
        "expiry_source": "opposing_force_timing",
        "summary": "Ready",
    }
    latest_signal.update(dict(signal_overrides))

    public = tracker.public_session_payload(
        {
            "session_id": "pocket-live",
            "status": "running",
            "tracking_enabled": True,
            "capture_count": 2,
            "frame_index": 3,
            "manual_focus_region": {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]},
            "latest_signal": latest_signal,
            "tracking_summary": {"chart_valid": True, "detected_market": "GBP/AUD OTC", "detected_timeframe": "M5"},
        }
    )
    signal = public["latest_signal"]

    assert signal["execution_action"] == "HOLD"
    assert signal["side"] == "HOLD"
    assert signal["actionable"] is False
    assert signal["execution_permission"] == "WAIT"
    assert expected_reason in signal["no_trade_reason"]
    assert signal["execution_block_reason"] == signal["no_trade_reason"]


def test_tracker_capture_once_writes_window_chart_overlay_and_decision_artifacts(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface(width=1280, height=720)]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )

    session = tracker.create_session(session_id="pocket-live")
    focused = tracker.set_focus_region(str(session["session_id"]), [0.10, 0.10, 0.88, 0.86], source="test")
    assert focused["capture_count"] == 1
    assert Path(str(focused["last_overlay_path"])).exists()
    _allow_next_capture(tracker, str(session["session_id"]))
    payload = tracker.capture_once(str(session["session_id"]))

    assert payload["capture_count"] == 2
    assert payload["latest_signal"]["action"] == "BUY"
    assert str(payload["latest_signal"]["signal_id"]).startswith("tracker_")
    assert payload["latest_signal"]["major_bias"] == "BUY"
    assert payload["latest_signal"]["timestamp"] == payload["latest_signal"]["published_at"]
    assert float(payload["latest_signal"]["published_epoch"]) > 0.0
    assert float(payload["latest_signal"]["capture_started_epoch"]) > 0.0
    assert float(payload["latest_signal"]["pipeline_latency_sec"]) >= 0.0
    assert float(payload["latest_signal"]["signal_age_sec"]) >= 0.0
    assert 0.0 <= float(payload["latest_signal"]["freshness_score"]) <= 1.0
    assert float(payload["latest_signal"]["freshness_window_sec"]) >= 8.0
    assert payload["latest_signal"]["stale"] is False
    assert 0.0 <= float(payload["freshness_score"]) <= 1.0
    assert payload["last_capture_at"] == payload["latest_signal"]["published_at"]
    assert float(payload["last_capture_epoch"]) == float(payload["latest_signal"]["published_epoch"])
    assert float(payload["latest_signal"]["countdown_seconds"]) >= 0.0
    assert payload["tracking_summary"]["visible_candle_count"] == 12
    assert payload["tracking_summary"]["pipeline_timing"]["published_at"] == payload["latest_signal"]["published_at"]
    assert cast(Sequence[Mapping[str, Any]], payload["tracking_summary"]["pipeline_timing"]["stages"])
    assert Path(str(payload["last_window_path"])).exists()
    assert Path(str(payload["last_chart_path"])).exists()
    assert Path(str(payload["last_overlay_path"])).exists()
    assert Path(str(payload["last_full_overlay_path"])).exists()
    assert Path(str(payload["last_decision_path"])).exists()
    decision_payload = json.loads(Path(str(payload["last_decision_path"])).read_text(encoding="utf-8"))
    assert decision_payload["published_at"] == payload["latest_signal"]["published_at"]
    assert float(decision_payload["pipeline_latency_sec"]) >= 0.0
    integrity = cast(dict[str, Any], payload["tracking_summary"]["artifact_integrity"])
    selected_plane = cast(dict[str, Any], integrity["selected_plane"])
    chart_plane = cast(dict[str, Any], integrity["chart"])
    overlay_plane = cast(dict[str, Any], integrity["overlay"])
    full_overlay_plane = cast(dict[str, Any], integrity["full_overlay"])
    assert integrity["matches_selected_plane"] is True
    assert chart_plane == selected_plane
    assert overlay_plane == selected_plane
    assert full_overlay_plane == {"width": 1280, "height": 720}
    with Image.open(str(payload["last_chart_path"])) as chart_image:
        assert chart_image.size == (selected_plane["width"], selected_plane["height"])
    with Image.open(str(payload["last_overlay_path"])) as overlay_image:
        assert overlay_image.size == (selected_plane["width"], selected_plane["height"])
    with Image.open(str(payload["last_full_overlay_path"])) as full_overlay_image:
        assert full_overlay_image.size == (1280, 720)


def test_tracker_live_mode_writes_fresh_hot_overlays_by_default(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_LIVE_MINIMAL_HOT_ARTIFACTS", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_LIVE_FULL_OVERLAY_EVERY_N", raising=False)
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend(
            [
                _synthetic_chart_surface("buy", width=1280, height=720),
                _synthetic_chart_surface("buy", width=1280, height=720),
            ]
        ),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    session = tracker.create_session(session_id="pocket-live")
    focused = tracker.set_focus_region(str(session["session_id"]), [0.10, 0.10, 0.88, 0.86], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    controls = dict(payload["execution_controls"])
    controls.update({"live_execution_enabled": True, "execution_mode": "live"})
    payload["execution_controls"] = controls
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    tracker.capture_and_analyze(str(session["session_id"]))
    result = tracker.get_session(str(session["session_id"]))

    assert result["last_overlay_path"] != focused["last_overlay_path"]
    assert result["last_full_overlay_path"] != focused["last_full_overlay_path"]
    assert _artifact_frame_from_name(result["last_overlay_path"]) == int(result["frame_index"])
    assert _artifact_frame_from_name(result["last_full_overlay_path"]) == int(result["frame_index"])
    assert int(result["overlay_frame_id"]) == int(result["frame_index"])
    assert int(result["full_overlay_frame_id"]) == int(result["frame_index"])
    assert result["tracking_summary"]["artifact_integrity"]["hot_artifacts_reused"] is False


def test_tracker_forced_minimal_hot_artifacts_overwrites_fresh_overlay(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_LIVE_MINIMAL_HOT_ARTIFACTS", "1")
    monkeypatch.setenv("PHOENIXGUARD_LIVE_FULL_OVERLAY_EVERY_N", "300")
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend(
            [
                _synthetic_chart_surface("buy", width=1280, height=720),
                _synthetic_chart_surface("buy", width=1280, height=720),
            ]
        ),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    session = tracker.create_session(session_id="pocket-live")
    focused = tracker.set_focus_region(str(session["session_id"]), [0.10, 0.10, 0.88, 0.86], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    controls = dict(payload["execution_controls"])
    controls.update({"live_execution_enabled": True, "execution_mode": "live"})
    payload["execution_controls"] = controls
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    tracker.capture_and_analyze(str(session["session_id"]))
    result = tracker.get_session(str(session["session_id"]))

    assert result["last_overlay_path"] != focused["last_overlay_path"]
    assert result["last_full_overlay_path"] != focused["last_full_overlay_path"]
    assert Path(str(result["last_overlay_path"])).name.startswith("hot_latest_overlay")
    assert Path(str(result["last_full_overlay_path"])).name.startswith("hot_latest_full_overlay")
    assert int(result["overlay_frame_id"]) == int(result["frame_index"])
    assert int(result["full_overlay_frame_id"]) == int(result["frame_index"])
    assert int(result["model_vote_frame_id"]) == int(result["frame_index"])
    assert result["tracking_summary"]["artifact_integrity"]["hot_artifacts_reused"] is False
    assert result["tracking_summary"]["artifact_integrity"]["hot_artifacts_overwritten"] is True
    assert result["tracking_summary"]["artifact_integrity"]["hot_artifact_policy"] == "OVERWRITE_LATEST_FRESH"


def test_tracker_capture_once_live_fast_path_returns_fresh_display_when_worker_busy(tmp_path: Path) -> None:
    adapter = _FakeTrackingAdapter("BUY")
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend(
            [
                _surface(width=1280, height=720),
                _surface(color=(35, 42, 58), width=1280, height=720),
            ]
        ),
        tracking_adapter=adapter,
    )

    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.10, 0.10, 0.88, 0.86], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    controls = dict(payload["execution_controls"])
    controls.update({"live_execution_enabled": True, "execution_mode": "live"})
    payload["execution_controls"] = controls
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    adapter_calls = adapter.calls
    before_display = int(payload.get("display_frame_id", 0) or 0)
    before_published = float(payload.get("display_published_epoch", 0.0) or 0.0)
    with tracker.lock:
        tracker.active_studies.add(str(session["session_id"]))
        tracker.active_study_started_epoch[str(session["session_id"])] = time.time()
    try:
        result = tracker.capture_once(str(session["session_id"]))
    finally:
        with tracker.lock:
            tracker.active_studies.discard(str(session["session_id"]))
            tracker.active_study_started_epoch.pop(str(session["session_id"]), None)

    capture_result = cast(Mapping[str, Any], result["capture_once_result"])
    assert capture_result["ok"] is True
    assert capture_result["busy"] is True
    assert capture_result["fast_display_path"] is True
    assert adapter.calls == adapter_calls
    assert int(result["display_frame_id"]) > before_display
    assert float(result["display_published_epoch"]) > before_published
    assert Path(str(result["last_display_window_path"])).exists()


def test_tracker_capture_once_display_only_does_not_schedule_study_worker(tmp_path: Path) -> None:
    adapter = _FakeTrackingAdapter("BUY")
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend(
            [
                _surface(width=1280, height=720),
                _surface(color=(35, 42, 58), width=1280, height=720),
            ]
        ),
        tracking_adapter=adapter,
    )

    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.10, 0.10, 0.88, 0.86], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    controls = dict(payload["execution_controls"])
    controls.update({"live_execution_enabled": True, "execution_mode": "live"})
    payload["execution_controls"] = controls
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    adapter_calls = adapter.calls
    result = tracker.capture_once(str(session["session_id"]), display_only=True)

    capture_result = cast(Mapping[str, Any], result["capture_once_result"])
    assert capture_result["ok"] is True
    assert capture_result["fast_display_path"] is True
    assert capture_result["busy"] is False
    assert adapter.calls == adapter_calls
    with tracker.lock:
        assert str(session["session_id"]) not in tracker.active_studies


def test_tracker_capture_once_display_only_returns_busy_when_snapshot_in_flight(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _FakeTrackingAdapter("BUY")
    backend = _FakeCaptureBackend([_surface(width=1280, height=720)])
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=adapter,
    )

    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.10, 0.10, 0.88, 0.86], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)
    monkeypatch.setenv("PHOENIXGUARD_DISPLAY_REUSE_ONLY_HEARTBEAT", "0")
    backend.capture_calls = 0
    adapter.calls = 0
    with tracker.lock:
        tracker.display_snapshot_started_epoch[str(session["session_id"])] = time.time()

    result = tracker.capture_once(str(session["session_id"]), display_only=True)

    capture_result = cast(Mapping[str, Any], result["capture_once_result"])
    assert capture_result["ok"] is True
    assert capture_result["status"] == "busy"
    assert capture_result["display_busy"] is True
    assert backend.capture_calls == 0
    assert adapter.calls == 0


def test_tracker_display_only_busy_is_single_flight_until_stale_reset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = _FakeTrackingAdapter("BUY")
    backend = _FakeCaptureBackend([_surface(width=1280, height=720)])
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=adapter,
    )

    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.10, 0.10, 0.88, 0.86], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)
    monkeypatch.setenv("PHOENIXGUARD_DISPLAY_SNAPSHOT_STALE_RESET_SEC", "30")
    monkeypatch.setenv("PHOENIXGUARD_DISPLAY_REUSE_ONLY_HEARTBEAT", "0")
    backend.capture_calls = 0
    adapter.calls = 0
    with tracker.lock:
        tracker.display_snapshot_started_epoch[str(session["session_id"])] = time.time() - 5.0

    result = tracker.capture_once(str(session["session_id"]), display_only=True)

    capture_result = cast(Mapping[str, Any], result["capture_once_result"])
    assert capture_result["status"] == "busy"
    assert capture_result["display_busy"] is True
    assert result["display_snapshot_busy_v3"] is True
    assert float(result["display_snapshot_busy_age_sec"]) >= 5.0
    assert backend.capture_calls == 0
    assert adapter.calls == 0


def test_tracker_display_only_stale_inflight_resets_as_emergency_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _FakeTrackingAdapter("BUY")
    backend = _FakeCaptureBackend([_surface(width=1280, height=720)])
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=adapter,
    )

    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.10, 0.10, 0.88, 0.86], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)
    monkeypatch.setenv("PHOENIXGUARD_DISPLAY_SNAPSHOT_STALE_RESET_SEC", "5")
    monkeypatch.setenv("PHOENIXGUARD_DISPLAY_REUSE_ONLY_HEARTBEAT", "0")
    backend.capture_calls = 0
    adapter.calls = 0
    with tracker.lock:
        tracker.display_snapshot_started_epoch[str(session["session_id"])] = time.time() - 10.0

    result = tracker.capture_once(str(session["session_id"]), display_only=True)

    capture_result = cast(Mapping[str, Any], result["capture_once_result"])
    assert capture_result["ok"] is True
    assert capture_result["status"] == "captured"
    assert capture_result["display_busy"] is False
    assert backend.capture_calls == 1
    event_log = tracker.session_dir(str(session["session_id"])) / "events.jsonl"
    assert "display_snapshot_stale_reset" in event_log.read_text(encoding="utf-8")


def test_tracker_display_only_refresh_does_not_replace_authority_frame(tmp_path: Path) -> None:
    adapter = _FakeTrackingAdapter("BUY")
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend(
            [
                _surface(width=1280, height=720),
                _surface(color=(35, 42, 58), width=1280, height=720),
                _surface(color=(41, 49, 67), width=1280, height=720),
            ]
        ),
        tracking_adapter=adapter,
    )

    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.10, 0.10, 0.88, 0.86], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    controls = dict(payload["execution_controls"])
    controls.update({"live_execution_enabled": True, "execution_mode": "live"})
    payload["execution_controls"] = controls
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    studied = tracker.capture_once(str(session["session_id"]))
    authority_capture_count = int(studied["capture_count"])
    authority_frame_index = int(studied["frame_index"])
    authority_window_path = str(studied["last_window_path"])
    authority_chart_path = str(studied["last_chart_path"])
    authority_signal_id = str(cast(Mapping[str, Any], studied["latest_signal"]).get("signal_id") or "")

    refreshed = tracker.capture_once(str(session["session_id"]), display_only=True)

    assert int(refreshed["display_frame_id"]) > int(studied["display_frame_id"])
    assert int(refreshed["capture_count"]) == authority_capture_count
    assert int(refreshed["frame_index"]) == authority_frame_index
    assert str(refreshed["last_window_path"]) == authority_window_path
    assert str(refreshed["last_chart_path"]) == authority_chart_path
    assert Path(str(refreshed["last_display_window_path"])).exists()
    assert str(refreshed["last_display_surface_signature"])
    assert str(cast(Mapping[str, Any], refreshed["latest_signal"]).get("signal_id") or "") == authority_signal_id

    display_state = json.loads((tracker.session_dir(str(session["session_id"])) / "display_state.json").read_text(encoding="utf-8"))
    assert display_state["capture_count"] == authority_capture_count
    assert display_state["frame_index"] == authority_frame_index
    assert display_state["last_chart_path"] == authority_chart_path
    assert display_state["last_overlay_path"] == studied["last_overlay_path"]
    assert display_state["last_full_overlay_path"] == studied["last_full_overlay_path"]
    assert display_state["overlay_frame_id"] == studied["overlay_frame_id"]
    assert display_state["model_vote_frame_id"] == studied["model_vote_frame_id"]
    assert "last_window_path" not in display_state
    assert "last_frame_path" not in display_state


def test_tracker_display_only_records_signature_without_moving_overlay_authority(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend(
            [
                _synthetic_chart_surface("buy", width=1280, height=720),
                _synthetic_chart_surface("buy", width=1280, height=720),
            ]
        ),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )

    session = tracker.create_session(session_id="pocket-shadow")
    tracker.set_focus_region(str(session["session_id"]), [0.10, 0.10, 0.88, 0.86], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    controls = dict(payload["execution_controls"])
    controls.update({"live_execution_enabled": False, "execution_mode": "shadow"})
    payload["execution_controls"] = controls
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    studied = tracker.capture_once(str(session["session_id"]))
    refreshed = tracker.capture_once(str(session["session_id"]), display_only=True)

    assert str(studied["overlay_source_window_signature"])
    assert str(refreshed["last_display_surface_signature"])
    assert refreshed["overlay_source_window_signature"] == studied["overlay_source_window_signature"]
    assert int(refreshed["overlay_frame_id"]) == int(studied["overlay_frame_id"])
    assert int(refreshed["display_frame_id"]) > int(studied["display_frame_id"])
    display_state = json.loads((tracker.session_dir(str(session["session_id"])) / "display_state.json").read_text(encoding="utf-8"))
    assert display_state["overlay_source_window_signature"] == studied["overlay_source_window_signature"]
    assert display_state["last_study_surface_signature"] == studied["last_study_surface_signature"]
    display_state["display_frame_id"] = int(refreshed["display_frame_id"]) + 10
    display_state["overlay_source_window_signature"] = "stale-surface"
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "display_state.json", display_state)
    merged = tracker.get_session_snapshot(str(session["session_id"]))
    assert merged["overlay_source_window_signature"] == studied["overlay_source_window_signature"]
    assert merged["last_display_surface_signature"] == display_state["last_display_surface_signature"]


def test_tracker_display_only_reuses_identical_surface_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = _surface(width=1280, height=720)
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([surface, surface.copy()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )

    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.10, 0.10, 0.88, 0.86], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)
    monkeypatch.setenv("PHOENIXGUARD_DISPLAY_REUSE_IDENTICAL_SURFACE", "1")

    first = tracker.capture_once(str(session["session_id"]), display_only=True)
    second = tracker.capture_once(str(session["session_id"]), display_only=True)

    assert int(second["display_frame_id"]) > int(first["display_frame_id"])
    assert second["last_display_window_path"] == first["last_display_window_path"]
    assert Path(str(second["last_display_window_path"])).exists()
    assert second["display_fast_path_v3"]["reused_window_path"] is True


def test_tracker_display_only_prefers_validated_fast_visible_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FastVisibleOnlyCaptureBackend(_synthetic_full_pocket_option_gui(width=1280, height=720))
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )

    session = tracker.create_session(session_id="pocket-live")
    session_id = str(session["session_id"])
    _focus_session_without_preview(tracker, session_id)
    payload = tracker.load_session_payload(session_id)
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    write_json_atomic(tracker.session_dir(session_id) / "session.json", payload)
    monkeypatch.setenv("PHOENIXGUARD_DISPLAY_FAST_VISIBLE_CAPTURE", "1")

    result = tracker.capture_once(session_id, display_only=True)

    assert result["capture_once_result"]["ok"] is True
    assert backend.fast_capture_calls == 1
    assert backend.capture_calls == 0
    assert Path(str(result["last_display_window_path"])).exists()


def test_tracker_display_only_blocks_native_capture_fallback_when_fast_visible_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FailingFastVisibleCaptureBackend(_synthetic_full_pocket_option_gui(width=1280, height=720))
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )

    session = tracker.create_session(session_id="pocket-live")
    session_id = str(session["session_id"])
    _focus_session_without_preview(tracker, session_id)
    payload = tracker.load_session_payload(session_id)
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    write_json_atomic(tracker.session_dir(session_id) / "session.json", payload)
    monkeypatch.setenv("PHOENIXGUARD_DISPLAY_FAST_VISIBLE_CAPTURE", "1")
    monkeypatch.delenv("PHOENIXGUARD_DISPLAY_ALLOW_NATIVE_CAPTURE_FALLBACK", raising=False)

    result = tracker.capture_once(session_id, display_only=True)

    assert result["capture_once_result"]["ok"] is False
    assert backend.fast_capture_calls == 1
    assert backend.capture_calls == 0


def test_tracker_display_only_reuse_only_heartbeat_skips_capture_after_locked_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FakeCaptureBackend([_surface(width=1280, height=720)])
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )

    session = tracker.create_session(session_id="pocket-live")
    session_id = str(session["session_id"])
    tracker.set_focus_region(session_id, [0.10, 0.10, 0.88, 0.86], source="test")
    payload = tracker.load_session_payload(session_id)
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    write_json_atomic(tracker.session_dir(session_id) / "session.json", payload)
    monkeypatch.setenv("PHOENIXGUARD_DISPLAY_REUSE_ONLY_HEARTBEAT", "1")

    first = tracker.capture_once(session_id, display_only=True)
    backend.capture_calls = 0
    second = tracker.capture_once(session_id, display_only=True)

    assert backend.capture_calls == 0
    assert int(second["display_frame_id"]) > int(first["display_frame_id"])
    assert second["last_display_window_path"] == first["last_display_window_path"]
    assert second["display_fast_path_v3"]["reuse_only_heartbeat"] is True
    assert second["display_reuse_only_heartbeat_v3"]["window_path"] == first["last_display_window_path"]


def test_tracker_display_only_busy_reuses_last_display_heartbeat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _FakeCaptureBackend([_surface(width=1280, height=720)])
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )

    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.10, 0.10, 0.88, 0.86], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)
    monkeypatch.setenv("PHOENIXGUARD_DISPLAY_BUSY_REUSE_HEARTBEAT", "1")
    monkeypatch.setenv("PHOENIXGUARD_DISPLAY_REUSE_ONLY_HEARTBEAT", "0")

    first = tracker.capture_once(str(session["session_id"]), display_only=True)
    first_path = str(first["last_display_window_path"])
    first_display = int(first["display_frame_id"])
    backend.capture_calls = 0
    with tracker.lock:
        tracker.display_snapshot_started_epoch[str(session["session_id"])] = 1000.0

    now_values = iter([1002.0, 1009.0])

    def queued_request_then_write_epoch() -> float:
        return next(now_values, 1009.0)

    monkeypatch.setattr(window_tracker_module, "_now_epoch", queued_request_then_write_epoch)

    second = tracker.capture_once(str(session["session_id"]), display_only=True)

    assert backend.capture_calls == 0
    assert int(second["display_frame_id"]) > first_display
    assert second["last_display_window_path"] == first_path
    assert second["display_snapshot_busy_v3"] is True
    assert second["display_published_epoch"] == 1009.0
    assert second["display_busy_reuse_heartbeat_v3"]["published_epoch"] == 1009.0
    assert second["display_busy_reuse_heartbeat_v3"]["window_path"] == first_path


def test_tracker_prunes_stale_artifact_groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(window_tracker_module, "_TRACKER_ARTIFACT_RETENTION_FRAMES", 2)
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface(width=1280, height=720)]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    artifact_dir = tmp_path / "sessions" / "prune-test" / "artifacts"
    artifact_dir.mkdir(parents=True)
    for index in range(1, 31):
        stem = f"{index:06d}_abcdef12"
        for suffix in ("window.png", "chart.png", "overlay.png", "full_overlay.png", "decision.json"):
            (artifact_dir / f"{stem}_{suffix}").write_text("x", encoding="utf-8")

    tracker.prune_session_artifacts(artifact_dir)

    remaining_groups = {
        "_".join(path.name.split("_", 2)[:2])
        for path in artifact_dir.iterdir()
        if path.is_file()
    }
    assert len(remaining_groups) == 24
    assert "000001_abcdef12" not in remaining_groups
    assert "000030_abcdef12" in remaining_groups


def test_tracker_prune_preserves_session_referenced_artifact_groups(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(window_tracker_module, "_TRACKER_ARTIFACT_RETENTION_FRAMES", 2)
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface(width=1280, height=720)]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    artifact_dir = tmp_path / "sessions" / "prune-test" / "artifacts"
    artifact_dir.mkdir(parents=True)
    protected_window = artifact_dir / "000001_abcdef12_window.png"
    protected_overlay = artifact_dir / "000001_abcdef12_overlay.png"
    for index in range(1, 31):
        stem = f"{index:06d}_abcdef12"
        for suffix in ("window.png", "chart.png", "overlay.png", "full_overlay.png", "decision.json"):
            (artifact_dir / f"{stem}_{suffix}").write_text("x", encoding="utf-8")
    write_json_atomic(
        artifact_dir.parent / "session.json",
        {
            "last_window_path": str(protected_window),
            "last_overlay_path": str(protected_overlay),
        },
    )

    tracker.prune_session_artifacts(artifact_dir)

    remaining_groups = {
        "_".join(path.name.split("_", 2)[:2])
        for path in artifact_dir.iterdir()
        if path.is_file()
    }
    assert "000001_abcdef12" in remaining_groups
    assert "000030_abcdef12" in remaining_groups


def test_tracker_prune_preserves_display_state_referenced_artifact_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(window_tracker_module, "_TRACKER_ARTIFACT_RETENTION_FRAMES", 2)
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface(width=1280, height=720)]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    artifact_dir = tmp_path / "sessions" / "display-prune-test" / "artifacts"
    artifact_dir.mkdir(parents=True)
    protected_display = artifact_dir / "000001_display_window.png"
    for index in range(1, 31):
        stem = f"{index:06d}_abcdef12"
        for suffix in ("window.png", "chart.png", "overlay.png", "full_overlay.png", "decision.json"):
            (artifact_dir / f"{stem}_{suffix}").write_text("x", encoding="utf-8")
    protected_display.write_text("display", encoding="utf-8")
    write_json_atomic(
        artifact_dir.parent / "display_state.json",
        {
            "last_display_window_path": str(protected_display),
        },
    )

    tracker.prune_session_artifacts(artifact_dir)

    assert protected_display.exists()


def test_tracker_event_log_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIXGUARD_TRACKER_EVENT_LOG_MAX_MB", "0.001")
    monkeypatch.setenv("PHOENIXGUARD_TRACKER_EVENT_LOG_TAIL_LINES", "3")
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface(width=1280, height=720)]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    session_id = "event-log-prune-test"
    for index in range(20):
        tracker.write_session_event_log(session_id, "event", index=index, payload="x" * 200)

    rows = (tracker.session_dir(session_id) / "events.jsonl").read_text(encoding="utf-8").splitlines()

    assert len(rows) <= 3
    assert any('"index": 19' in row for row in rows)


def test_tracker_capture_rate_limiter_skips_immediate_duplicate_capture(tmp_path: Path, monkeypatch: Any) -> None:
    backend = _FakeCaptureBackend([
        _surface(width=1280, height=720),
        _surface(color=(28, 34, 44), width=1280, height=720),
    ])
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    session_id = str(tracker.create_session(session_id="pocket-live")["session_id"])
    _focus_session_without_preview(tracker, session_id)
    clock = {"now": 1000.0}
    monkeypatch.setattr(window_tracker_module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(window_tracker_module.time, "time", lambda: clock["now"])

    tracker.capture_and_analyze(session_id)
    first = tracker.get_session(session_id)
    tracker.capture_and_analyze(session_id)
    duplicate = tracker.get_session(session_id)
    clock["now"] += 0.201
    tracker.capture_and_analyze(session_id)
    after_floor = tracker.get_session(session_id)

    assert int(first["capture_count"]) == 1
    assert int(duplicate["capture_count"]) == 1
    assert int(after_floor["capture_count"]) == 2
    assert backend.capture_calls == 2


def test_tracker_capture_preserves_newer_tracking_enabled_state(tmp_path: Path, monkeypatch: Any) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface(width=1280, height=720)]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    session_id = str(tracker.create_session(session_id="pocket-live")["session_id"])
    _focus_session_without_preview(tracker, session_id)
    persisted = tracker.load_session_payload(session_id)
    persisted["tracking_enabled"] = True
    persisted["status"] = "running"
    write_json_atomic(tracker.session_dir(session_id) / "session.json", persisted)

    original_require = tracker.require_session
    returned_stale_once = {"value": False}

    def _require_with_stale_local_state(requested_session_id: str) -> dict[str, Any]:
        payload = original_require(requested_session_id)
        if not returned_stale_once["value"]:
            returned_stale_once["value"] = True
            stale_payload = dict(payload)
            stale_payload["tracking_enabled"] = False
            stale_payload["status"] = "ready"
            return stale_payload
        return payload

    monkeypatch.setattr(tracker, "require_session", _require_with_stale_local_state)

    tracker.capture_and_analyze(session_id, force=True)
    refreshed = original_require(session_id)

    assert refreshed["tracking_enabled"] is True
    assert refreshed["status"] == "running"


def test_tracker_capture_does_not_overwrite_newer_published_capture(tmp_path: Path, monkeypatch: Any) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface(width=1280, height=720)]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    session_id = str(tracker.create_session(session_id="pocket-live")["session_id"])
    _focus_session_without_preview(tracker, session_id)
    persisted = tracker.load_session_payload(session_id)
    persisted["tracking_enabled"] = True
    persisted["status"] = "running"
    persisted["capture_count"] = 9
    persisted["frame_index"] = 9
    persisted["last_capture_epoch"] = 2000.0
    persisted["last_capture_at"] = "1970-01-01T00:33:20+00:00"
    write_json_atomic(tracker.session_dir(session_id) / "session.json", persisted)

    monkeypatch.setattr(window_tracker_module, "_now_epoch", lambda: 1500.0)

    tracker.capture_and_analyze(session_id, force=True)
    refreshed = tracker.load_session_payload(session_id)

    assert refreshed["last_capture_epoch"] == 2000.0
    assert refreshed["capture_count"] == 9


def test_tracker_study_gate_blocks_overlapping_capture(tmp_path: Path) -> None:
    backend = _FakeCaptureBackend([
        _surface(width=1280, height=720),
        _surface(color=(28, 34, 44), width=1280, height=720),
    ])
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    session_id = str(tracker.create_session(session_id="pocket-live")["session_id"])
    _focus_session_without_preview(tracker, session_id)

    started = threading.Event()
    release = threading.Event()

    class _BlockingTrackingAdapter(_FakeTrackingAdapter):
        def study(self, image: Image.Image, *, session_payload: Mapping[str, Any] | None = None) -> TrackingStudy:
            started.set()
            assert release.wait(5.0)
            return super().study(image, session_payload=session_payload)

    setattr(tracker, "tracking_adapter", _BlockingTrackingAdapter("BUY"))
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(tracker.capture_and_analyze, session_id, force=True)
        assert started.wait(5.0)
        tracker.capture_and_analyze(session_id, force=True)
        release.set()
        future.result(timeout=10.0)

    payload = tracker.get_session(session_id)
    assert int(payload["capture_count"]) == 1
    assert payload["study_in_progress"] is False
    assert backend.capture_calls == 1


def test_tracker_full_window_focus_derives_chart_study_plane_for_overlays(tmp_path: Path) -> None:
    full_gui = _synthetic_full_pocket_option_gui(width=1280, height=720)
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([full_gui]),
        tracking_adapter=PhoenixGuardWindowTrackingAdapter(),
    )

    session = tracker.create_session(session_id="pocket-live")
    payload = tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")

    integrity = cast(dict[str, Any], payload["tracking_summary"]["artifact_integrity"])
    selected_plane = cast(dict[str, Any], integrity["selected_plane"])
    study_plane = cast(dict[str, Any], integrity["study_plane"])
    focus_region = cast(dict[str, Any], payload["tracking_summary"]["focus_region"])
    focus_bbox = cast(Sequence[Any], focus_region["pixel_bbox"])
    assert selected_plane == {"width": 1280, "height": 720}
    assert study_plane["width"] < selected_plane["width"]
    assert study_plane["height"] < selected_plane["height"]
    assert focus_region["source"] == "auto_full_window_chart_plane"
    assert int(focus_bbox[2]) <= int(full_gui.width * 0.88)
    assert payload["broker_surface"]["scan_skipped"] is True
    assert payload["broker_surface"]["scan_skip_reason"] == "live_execution_disabled"
    with Image.open(str(payload["last_chart_path"])) as chart_image:
        assert chart_image.size == (study_plane["width"], study_plane["height"])
    with Image.open(str(payload["last_full_overlay_path"])) as full_overlay_image:
        assert full_overlay_image.size == full_gui.size


def test_tracker_accepts_tradingview_visible_chart_as_study_source(tmp_path: Path) -> None:
    chart = _synthetic_chart_surface("buy", width=1280, height=720)
    backend = _ListedWindowCaptureBackend(
        [
            {
                "hwnd": 606,
                "title": "EURUSD Chart - TradingView - Microsoft Edge",
                "bbox": [0, 0, 1280, 720],
                "width": 1280,
                "height": 720,
            }
        ],
        image=chart,
    )
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )

    session = tracker.create_session(session_id="tradingview-study", window_query="TradingView")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    payload = tracker.capture_once(str(session["session_id"]))

    tracking_summary = cast(dict[str, Any], payload["tracking_summary"])
    broker_source_lock = cast(dict[str, Any], tracking_summary["broker_source_lock"])
    broker_source = cast(dict[str, Any], tracking_summary["broker_source"])
    broker_surface = cast(dict[str, Any], payload["broker_surface"])
    broker_execution_state = cast(dict[str, Any], payload["broker_execution_state"])
    assert payload["status"] != "waiting_for_broker_surface"
    assert broker_source_lock["valid"] is True
    assert broker_source_lock["reason_codes"] == ["CHART_STUDY_SOURCE_LOCKED"]
    assert broker_source["valid"] is True
    assert broker_source["wrong_surface"] is False
    assert broker_surface["state"] == "study_source_only"
    assert broker_surface["controls_ready"] is False
    assert broker_surface["study_source_only"] is True
    assert broker_surface["buy_button"] == {}
    assert broker_surface["sell_button"] == {}
    assert broker_execution_state["status"] not in {"armed", "ready_to_click"}
    assert payload["latest_signal"]["status"] != "waiting_for_broker_surface"


def test_tracker_accepts_pocket_chart_as_shadow_study_source(tmp_path: Path) -> None:
    chart = _synthetic_chart_surface("buy", width=1280, height=720)
    backend = _ListedWindowCaptureBackend(
        [
            {
                "hwnd": 707,
                "title": "The Most Innovative Trading Platform - Microsoft Edge",
                "bbox": [0, 0, 1280, 720],
                "width": 1280,
                "height": 720,
            }
        ],
        image=chart,
    )
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )

    session = tracker.create_session(session_id="pocket-shadow-study", window_query="The Most Innovative Trading Platform")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    tracker.update_session_controls(str(session["session_id"]), live_execution_enabled=False, execution_mode="shadow")

    payload = tracker.capture_once(str(session["session_id"]))

    tracking_summary = cast(dict[str, Any], payload["tracking_summary"])
    broker_source_lock = cast(dict[str, Any], tracking_summary["broker_source_lock"])
    broker_source = cast(dict[str, Any], tracking_summary["broker_source"])
    broker_surface = cast(dict[str, Any], payload["broker_surface"])
    broker_execution_state = cast(dict[str, Any], payload["broker_execution_state"])
    assert payload["status"] != "waiting_for_broker_surface"
    assert broker_source_lock["valid"] is True
    assert broker_source_lock["reason_codes"] == ["CHART_STUDY_SOURCE_LOCKED"]
    assert broker_source_lock["surface_guard"]["reason_codes"] == ["CHART_SOURCE_PIXELS_CONFIRMED"]
    assert broker_source["valid"] is True
    assert broker_source["wrong_surface"] is False
    assert broker_source["study_source_only"] is True
    assert broker_source["broker_click_safe"] is False
    assert broker_surface["study_source_only"] is True
    assert broker_surface["broker_click_safe"] is False
    assert broker_execution_state["status"] not in {"armed", "ready_to_click"}


def test_tracker_scenario_generation_runs_when_enabled(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([
            _synthetic_chart_surface("buy", width=960, height=540),
            _synthetic_chart_surface("buy", width=960, height=540),
        ]),
        tracking_adapter=PhoenixGuardWindowTrackingAdapter(),
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    tracker.update_session_controls(str(session["session_id"]), scenario_generation_enabled=True)

    payload = tracker.capture_once(str(session["session_id"]))

    assert payload["execution_controls"]["scenario_generation_enabled"] is True
    assert payload["scenario_analysis"]["enabled"] is True
    assert payload["scenario_analysis"]["status"] != "disabled"


def test_tracker_scenario_generation_stays_disabled_in_live_hot_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHOENIXGUARD_ENABLE_LIVE_SCENARIO_GENERATION", raising=False)
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([
            _synthetic_chart_surface("buy", width=960, height=540),
            _synthetic_chart_surface("buy", width=960, height=540),
        ]),
        tracking_adapter=PhoenixGuardWindowTrackingAdapter(),
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    tracker.update_session_controls(
        str(session["session_id"]),
        live_execution_enabled=True,
        execution_mode="live",
        scenario_generation_enabled=True,
    )

    payload = tracker.capture_once(str(session["session_id"]))

    assert payload["execution_controls"]["scenario_generation_enabled"] is True
    assert payload["scenario_analysis"]["enabled"] is False
    assert payload["scenario_analysis"]["status"] == "disabled"


def test_tracker_rejects_mismatched_overlay_plane_before_publishing_signal(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface(width=1280, height=720)]),
        tracking_adapter=_MismatchedPlaneTrackingAdapter(),
    )

    session = tracker.create_session(session_id="pocket-live")
    payload = tracker.set_focus_region(str(session["session_id"]), [0.10, 0.10, 0.88, 0.86], source="test")

    assert payload["latest_signal"]["status"] == "error"
    assert payload["latest_signal"]["action"] == "HOLD"
    assert "Tracker plane integrity failed" in str(payload["last_error"])
    integrity = cast(dict[str, Any], payload["tracking_summary"]["artifact_integrity"])
    selected_plane = cast(dict[str, Any], integrity["selected_plane"])
    assert integrity["matches_selected_plane"] is True
    assert selected_plane == {"width": 998, "height": 547}
    with Image.open(str(payload["last_chart_path"])) as chart_image:
        assert chart_image.size == (998, 547)
    with Image.open(str(payload["last_overlay_path"])) as overlay_image:
        assert overlay_image.size == (998, 547)


def test_tracker_worker_loop_uses_adaptive_interval_without_default_floor(tmp_path: Path, monkeypatch: Any) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface()]),
        tracking_adapter=_FakeTrackingAdapter("SELL"),
    )

    session_id = str(tracker.create_session(session_id="pocket-live", capture_interval_sec=3.0)["session_id"])
    _focus_session_without_preview(tracker, session_id)
    payload = tracker.load_session_payload(session_id)
    payload["tracking_enabled"] = True
    payload["latest_signal"] = {"status": "tracking", "action": "SELL"}
    payload["tracking_summary"] = {"chart_valid": True}
    write_json_atomic(tracker.session_dir(session_id) / "session.json", payload)
    clock = {"now": 1000.0}
    capture_times: list[float] = []

    class _LoopEvent:
        def __init__(self, *, set_initially: bool = False) -> None:
            self._is_set = set_initially

        def is_set(self) -> bool:
            return self._is_set

        def set(self) -> None:
            self._is_set = True

        def clear(self) -> None:
            self._is_set = False

        def wait(self, timeout: float | None = None) -> bool:
            clock["now"] += float(timeout or 0.0)
            return self._is_set

    stop_evt = _LoopEvent()
    capture_now_evt = _LoopEvent(set_initially=True)

    def capture_stub(captured_session_id: str, *, force: bool = False) -> None:
        _ = force
        capture_times.append(clock["now"])
        payload = tracker.load_session_payload(captured_session_id)
        next_count = int(payload.get("capture_count", 0) or 0) + 1
        payload["capture_count"] = next_count
        payload["frame_index"] = next_count
        payload["last_capture_at"] = f"2026-05-06T00:00:0{next_count}+00:00"
        payload["updated_at"] = payload["last_capture_at"]
        payload["latest_signal"] = {"status": "tracking", "action": "SELL"}
        payload["tracking_summary"] = {"chart_valid": True}
        write_json_atomic(tracker.session_dir(captured_session_id) / "session.json", payload)
        if next_count >= 2:
            stop_evt.set()

    monkeypatch.setattr(tracker, "capture_and_analyze", capture_stub)
    def adaptive_capture_interval_plan(_payload: Mapping[str, Any]) -> dict[str, Any]:
        return {"interval_sec": 0.5, "reason": "entry_ready"}

    monkeypatch.setattr(
        tracker,
        "adaptive_capture_interval_plan",
        adaptive_capture_interval_plan,
    )
    monkeypatch.setattr(window_tracker_module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(window_tracker_module.time, "time", lambda: clock["now"])

    tracker.worker_loop(session_id, cast(threading.Event, stop_evt), cast(threading.Event, capture_now_evt))
    payload = tracker.load_session_payload(session_id)

    assert int(payload["capture_count"]) >= 2
    assert capture_times == [1000.0, 1000.5]


def test_tracker_arm_focus_selector_applies_selected_region(tmp_path: Path) -> None:
    focus_backend = _FakeFocusSelectionBackend()
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface()]),
        tracking_adapter=_FakeTrackingAdapter(),
        focus_selector_backend=focus_backend,
    )

    session = tracker.create_session(session_id="pocket-live")
    armed = tracker.arm_focus_selector(str(session["session_id"]))
    assert armed["focus_selector"]["status"] == "armed"

    focus_backend.complete_selection([0.18, 0.12, 0.84, 0.88])
    payload = tracker.get_session(str(session["session_id"]))

    assert payload["manual_focus_region"]["enabled"] is True
    assert payload["manual_focus_region"]["normalized_bbox"] == [0.18, 0.12, 0.84, 0.88]
    assert payload["focus_selector"]["status"] == "selected"


def test_tracker_clear_focus_region_stops_tracker_and_clears_live_artifacts(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface(), _surface()]),
        tracking_adapter=_FakeTrackingAdapter(),
    )

    session = tracker.create_session(session_id="pocket-live", capture_interval_sec=0.5)
    tracker.set_focus_region(str(session["session_id"]), [0.10, 0.10, 0.90, 0.90], source="test")
    tracker.start_session(str(session["session_id"]))
    _wait_for_capture_count(tracker, str(session["session_id"]), 1)

    cleared = tracker.clear_focus_region(str(session["session_id"]))

    assert cleared["tracking_enabled"] is False
    assert cleared["status"] == "awaiting_focus"
    assert cleared["manual_focus_region"]["enabled"] is False
    assert cleared["last_overlay_path"] == ""
    assert cleared["last_chart_path"] == ""


def test_tracker_reacquires_same_browser_family_when_pocket_option_title_drifts(tmp_path: Path) -> None:
    backend = _ListedWindowCaptureBackend(
        [
            {
                "hwnd": 8801,
                "title": "Netflix and 27 more pages - Personal - Microsoft Edge",
                "bbox": [0, 0, 1280, 720],
                "width": 1280,
                "height": 720,
            },
            {
                "hwnd": 8802,
                "title": "127.0.0.1 - Google Chrome",
                "bbox": [0, 0, 1280, 720],
                "width": 1280,
                "height": 720,
            },
        ],
        image=_surface(width=1280, height=720),
    )
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )

    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.10, 0.10, 0.90, 0.90], source="test")

    session_path = tmp_path / "sessions" / "pocket-live" / "session.json"
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    payload["locked_window"] = {
        "hwnd": 7001,
        "title": "The Most Innovative Trading Platform - Personal - Microsoft Edge",
        "bbox": [0, 0, 1280, 720],
        "width": 1280,
        "height": 720,
    }
    payload["locked_title"] = payload["locked_window"]["title"]
    payload["last_chart_path"] = ""
    payload["last_overlay_path"] = ""
    payload["last_display_chart_path"] = ""
    session_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    _allow_next_capture(tracker, str(session["session_id"]))
    refreshed = tracker.capture_once(str(session["session_id"]))

    assert refreshed["last_error"] == ""
    assert refreshed["locked_window"]["hwnd"] == 8801
    assert refreshed["latest_signal"]["action"] == "BUY"
    assert Path(str(refreshed["last_overlay_path"])).exists()


def test_tracker_ignores_wrong_saved_pocket_option_hwnd_and_reacquires_broker(tmp_path: Path) -> None:
    backend = _ListedWindowCaptureBackend(
        [
            {
                "hwnd": 7001,
                "title": "Meet - wyv-yqxq-zjf and 27 more pages - Personal - Microsoft Edge",
                "bbox": [0, 0, 1280, 720],
                "width": 1280,
                "height": 720,
            },
            {
                "hwnd": 8801,
                "title": "The Most Innovative Trading Platform and 28 more pages - Personal - Microsoft Edge",
                "bbox": [80, 60, 2018, 1098],
                "width": 1938,
                "height": 1038,
            },
        ],
        image=_surface(width=1938, height=1038),
    )
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )

    session = tracker.create_session(session_id="pocket-live", window_query="Pocket Option")
    tracker.set_focus_region(str(session["session_id"]), [0.03, 0.09, 0.99, 0.99], source="test")
    session_path = tmp_path / "sessions" / "pocket-live" / "session.json"
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    payload["locked_window"] = dict(backend.windows[0])
    payload["locked_title"] = str(backend.windows[0]["title"])
    payload["last_chart_path"] = ""
    payload["last_overlay_path"] = ""
    payload["last_display_chart_path"] = ""
    session_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    _allow_next_capture(tracker, str(session["session_id"]))
    refreshed = tracker.capture_once(str(session["session_id"]))

    assert refreshed["last_error"] == ""
    assert refreshed["locked_window"]["hwnd"] == 8801
    assert "The Most Innovative Trading Platform" in refreshed["locked_window"]["title"]
    assert refreshed["latest_signal"]["action"] == "BUY"
    assert Path(str(refreshed["last_overlay_path"])).exists()


def test_tracker_create_session_persists_requested_broker_hwnd(tmp_path: Path) -> None:
    backend = _ListedWindowCaptureBackend(
        [
            {
                "hwnd": 592668,
                "title": "The Most Innovative Trading Platform - Personal - Microsoft Edge",
                "bbox": [80, 60, 2018, 1098],
                "width": 1938,
                "height": 1038,
            }
        ],
        image=_surface(width=1938, height=1038),
    )
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )

    session = tracker.create_session(
        session_id="pocket-live",
        window_query="The Most Innovative Trading Platform",
        locked_hwnd=592668,
    )

    locked = cast(dict[str, Any], session["locked_window"])
    assert locked["hwnd"] == 592668
    assert locked["width"] == 1938
    assert session["locked_title"] == "The Most Innovative Trading Platform - Personal - Microsoft Edge"


def test_tracker_http_surface_updates_reused_session_locked_hwnd(tmp_path: Path) -> None:
    backend = _ListedWindowCaptureBackend(
        [
            {
                "hwnd": 525544,
                "title": "The Most Innovative Trading Platform and 29 more pages - Personal - Microsoft Edge",
                "bbox": [0, 0, 1280, 720],
                "width": 1280,
                "height": 720,
            },
            {
                "hwnd": 592668,
                "title": "The Most Innovative Trading Platform - Personal - Microsoft Edge",
                "bbox": [80, 60, 2018, 1098],
                "width": 1938,
                "height": 1038,
            },
        ],
        image=_surface(width=1938, height=1038),
    )
    tracker_service = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    app = create_app(window_tracker_service=tracker_service)
    client = TestClient(app)

    create_response = client.post(
        "/v1/mobile/window-tracker/sessions",
        json={
            "session_id": "pocket-live",
            "window_query": "The Most Innovative Trading Platform",
            "locked_hwnd": 525544,
        },
    )
    assert create_response.status_code == 201
    assert create_response.json()["locked_window"]["hwnd"] == 525544

    update_response = client.patch(
        "/v1/mobile/window-tracker/sessions/pocket-live/locked-window",
        json={
            "locked_hwnd": 592668,
            "locked_title": "The Most Innovative Trading Platform",
        },
    )
    assert update_response.status_code == 200
    payload = update_response.json()
    assert payload["locked_window"]["hwnd"] == 592668
    assert payload["locked_window"]["width"] == 1938
    assert payload["locked_title"] == "The Most Innovative Trading Platform - Personal - Microsoft Edge"


def test_tracker_reacquires_visible_window_when_locked_handle_is_minimized(tmp_path: Path) -> None:
    backend = _ListedWindowCaptureBackend(
        [
            {
                "hwnd": 7001,
                "title": "The Most Innovative Trading Platform and 29 more pages - Personal - Microsoft Edge",
                "bbox": [-32000, -32000, -31801, -31966],
                "width": 199,
                "height": 34,
                "is_minimized": True,
            },
            {
                "hwnd": 8801,
                "title": "The Most Innovative Trading Platform and 29 more pages - Personal - Microsoft Edge",
                "bbox": [80, 60, 2018, 1098],
                "width": 1938,
                "height": 1038,
                "is_minimized": False,
            },
        ],
        image=_surface(width=1938, height=1038),
    )
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )

    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.03, 0.09, 0.99, 0.99], source="test")
    session_path = tmp_path / "sessions" / "pocket-live" / "session.json"
    payload = json.loads(session_path.read_text(encoding="utf-8"))
    payload["locked_window"] = dict(backend.windows[0])
    payload["locked_title"] = str(backend.windows[0]["title"])
    payload["last_chart_path"] = ""
    payload["last_overlay_path"] = ""
    payload["last_display_chart_path"] = ""
    session_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

    refreshed = tracker.capture_once(str(session["session_id"]))

    assert refreshed["last_error"] == ""
    assert refreshed["locked_window"]["hwnd"] == 8801
    assert refreshed["locked_window"]["width"] == 1938
    assert refreshed["latest_signal"]["action"] == "BUY"


def test_tracker_http_surface_serves_session_artifacts_and_dashboard(tmp_path: Path) -> None:
    tracker_service = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface(width=1280, height=720)]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    app = create_app(window_tracker_service=tracker_service)
    client = TestClient(app)

    create_response = client.post(
        "/v1/mobile/window-tracker/sessions",
        json={
            "session_id": "pocket-live",
            "window_query": "Pocket Option",
            "capture_interval_sec": 0.5,
        },
    )
    assert create_response.status_code == 201
    session_id = create_response.json()["session_id"]

    focus_response = client.put(
        f"/v1/mobile/window-tracker/sessions/{session_id}/focus-region",
        json={"normalized_bbox": [0.08, 0.10, 0.92, 0.88], "source": "dashboard"},
    )
    assert focus_response.status_code == 200

    capture_response = client.post(f"/v1/mobile/window-tracker/sessions/{session_id}/capture-once")
    assert capture_response.status_code == 200
    payload = capture_response.json()
    assert payload["latest_signal"]["action"] == "BUY"

    session_response = client.get(f"/v1/mobile/window-tracker/sessions/{session_id}")
    assert session_response.status_code == 200

    chart_response = client.get(f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-chart")
    assert chart_response.status_code == 200
    assert chart_response.headers["content-type"].startswith("image/png")
    assert "no-store" in chart_response.headers["cache-control"]

    overlay_response = client.get(f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-overlay")
    assert overlay_response.status_code == 200
    assert overlay_response.headers["content-type"].startswith("image/png")
    assert "no-store" in overlay_response.headers["cache-control"]

    full_overlay_response = client.get(f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-full-overlay")
    assert full_overlay_response.status_code == 200
    assert full_overlay_response.headers["content-type"].startswith("image/png")
    assert "no-store" in full_overlay_response.headers["cache-control"]

    dashboard_response = client.get(f"/v1/mobile/window-tracker/dashboard/{session_id}")
    assert dashboard_response.status_code == 200
    assert "Locked Broker Surface Tracker" in dashboard_response.text


def test_tracker_dashboard_prediction_images_use_uncropped_full_width_layout() -> None:
    dashboard_html = (
        Path(__file__).resolve().parents[2]
        / "Frontend"
        / "dashboard"
        / "static"
        / "window_tracker_dashboard.html"
    ).read_text(encoding="utf-8")

    assert "grid-column: 1 / -1;" in dashboard_html
    assert "grid-template-columns: repeat(3, minmax(0, 1fr));" in dashboard_html
    assert "height: 220px;" not in dashboard_html
    assert "object-fit: contain;" in dashboard_html
    assert "aspect-ratio: auto;" in dashboard_html
    assert "Precision" in dashboard_html
    assert "Edge" in dashboard_html
    assert "Top 3 Forecasts" in dashboard_html
    assert "accepted_by" in dashboard_html
    assert "failure_or_probe" in dashboard_html
    assert "memory retrieval running" in dashboard_html
    assert "Retrieved Memory | scanning bank" in dashboard_html
    assert "Future Runway | building overlay boxes" in dashboard_html
    assert "aggressive sniper" in dashboard_html
    assert "addMicroPlanBoxes" in dashboard_html
    assert "SNIPER" in dashboard_html
    assert "Control Map" in dashboard_html
    assert "Map Clock" in dashboard_html
    assert "Wick + S/R Read" in dashboard_html
    assert "global_local_control" in dashboard_html
    assert "candles_remaining_in_sniper_zone" in dashboard_html
    assert "voice-toggle" not in dashboard_html
    assert "/v1/voice/status" not in dashboard_html
    assert "Promise.allSettled" not in dashboard_html


def test_tracker_dashboard_fits_selected_surface_without_width_only_crop() -> None:
    dashboard_html = (
        Path(__file__).resolve().parents[2]
        / "Frontend"
        / "dashboard"
        / "static"
        / "window_tracker_dashboard.html"
    ).read_text(encoding="utf-8")

    assert "function surfaceFitViewportHeight()" in dashboard_html
    assert "stageHeight / size.height" in dashboard_html
    assert "stageWidth / size.width" in dashboard_html
    assert "full selected plane fitted" in dashboard_html
    assert "full broker window fitted" in dashboard_html
    assert 'const kind = "full-overlay";' in dashboard_html
    assert 'state.surfaceUsesFullOverlay = image.dataset.surfaceUsesFullOverlay === "1";' in dashboard_html
    assert "mode-overlay" in dashboard_html
    assert "Signal Overlay" in dashboard_html
    assert "mode-raw" in dashboard_html
    assert "Locked Surface" in dashboard_html
    assert "function surfaceIdentityKey(session = {})" in dashboard_html
    assert "function overlaySurfaceMatchesDisplay(session = {})" in dashboard_html
    assert "function displayOnlyOverlayAuthorityLocked(session = {})" in dashboard_html
    assert "function overlayAuthorityFrame(session = {})" in dashboard_html
    assert "const displayArtifact = clean(session.last_display_window_path" in dashboard_html
    assert "overlayFrame > 0 && displayArtifact && overlayArtifact" in dashboard_html
    assert "explicitArtifactAligned === true" in dashboard_html
    assert "const overlayFrameId = overlayAuthorityFrame(session);" in dashboard_html
    assert "display_frame_id: displayFrameId" in dashboard_html
    assert "overlay_render_frame_id: overlayFrameId" in dashboard_html
    assert "overlay_source_window_signature" in dashboard_html
    assert "overlayLocks: new Map()" in dashboard_html
    assert "const overlayLockUsable = wantsOverlay && hasLockedOverlayForSession(session);" in dashboard_html
    assert "const useLockedWindowOverlayPlane = wantsOverlay" in dashboard_html
    assert 'useSurfaceImage(els.rawImg, "window", "window-locked-overlay", true);' in dashboard_html
    assert "if (wantsOverlay && hasFullOverlay && !overlayStale)" in dashboard_html
    assert "function backendOverlayFrameAligned(session = {})" in dashboard_html
    assert 'clean_live: "CLEAN_LIVE"' in dashboard_html
    assert 'full_history_read: "FULL_HISTORY_READ"' in dashboard_html
    assert "displayOnlyOverlayAuthorityLocked(session)" in dashboard_html
    assert "rawFallbackVisible || state.surface.overlayStale || !overlayFrameReady" in dashboard_html
    assert "else if (hasChart)" in dashboard_html
    assert "DASHBOARD_REFRESH_FAST_INTERVAL_MS = 15000" in dashboard_html
    assert "DASHBOARD_HEARTBEAT_INTERVAL_MS = 15000" in dashboard_html


def test_tracker_dashboard_replay_overlays_use_professional_label_budget() -> None:
    dashboard_html = (
        Path(__file__).resolve().parents[2]
        / "Frontend"
        / "dashboard"
        / "static"
        / "window_tracker_dashboard.html"
    ).read_text(encoding="utf-8")

    assert "function frontendOverlayBudget" in dashboard_html
    assert "REPLAY: {objects: null, labels: 28}" in dashboard_html
    assert "FULL_HISTORY_READ: {objects: null, labels: 28}" in dashboard_html
    assert "function frontendOverlayPriority" in dashboard_html
    assert "function frontendOverlayLabelCandidate" in dashboard_html
    assert "function resolveLabelCollisions" in dashboard_html
    assert "window.resolveLabelCollisions = resolveLabelCollisions;" in dashboard_html
    assert "label-collision-hidden" in dashboard_html
    assert "font-size: calc(7px * var(--overlay-label-scale, 1));" in dashboard_html
    assert "font-size: calc(4.65px * var(--overlay-label-scale, 1));" not in dashboard_html
    assert 'if (mode !== "CLEAN_LIVE")' not in dashboard_html


def test_memory_precision_allows_aggressive_stacked_primary_when_counter_is_probe() -> None:
    primary_fit: dict[str, Any] = {
        "top_matches": [
            {"similarity": 0.83, "precision_score": 0.70},
            {"similarity": 0.80, "precision_score": 0.69},
            {"similarity": 0.79, "precision_score": 0.68},
        ],
        "high_precision_count": 1,
    }
    counter_fit: dict[str, Any] = {
        "top_matches": [{"similarity": 0.87, "precision_score": 0.62}],
        "transition_bias": {
            "continue": 0.52,
            "pullback": 0.18,
            "reversal_attempt": 0.28,
            "fakeout": 0.02,
        },
    }

    precision = PhoenixGuardWindowTrackingAdapter.memory_precision_payload(primary_fit, counter_fit)

    assert precision["accepted"] is True
    assert precision["accepted_by"] == "stacked_favor"
    assert precision["quality"] == "aggressive_stacked"
    assert precision["counter_behavior"]["hard_counter_risk"] is False
    assert float(precision["precision_edge"]) >= 0.08


def test_live_dashboard_launcher_delegates_to_final_live_profile() -> None:
    launcher = (Path(__file__).resolve().parents[2] / "Backend" / "launch" / "start_live_dashboard.ps1").read_text(encoding="utf-8")

    assert "launch_phoenixguard_live_ready.ps1" in launcher
    assert "FINAL_LIVE" in launcher


def test_full_local_launcher_has_one_final_live_profile_and_keeps_broker_auto_open_off() -> None:
    launcher = (Path(__file__).resolve().parents[2] / "Backend" / "launch" / "start_phoenixguard_full_local.ps1").read_text(encoding="utf-8")

    assert "FINAL_LIVE" in launcher
    assert "[string]$ShooterMode" in launcher
    assert "else { 'PACKAGE_REPORTER' }" in launcher
    assert "PAPER_EXECUTION" not in launcher
    legacy_calibration_profile = "CALIBRATION" + "_TEST_ONLY"
    assert legacy_calibration_profile not in launcher
    assert "PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS" in launcher
    assert "BrokerWindowHwnd" in launcher
    assert "PHOENIXGUARD_BROKER_WINDOW_HWND" in launcher
    assert "--window-hwnd" not in launcher
    assert "$liveClickArm = if ($ShooterMode -eq 'LIVE_READY'" not in launcher
    assert "--shooter-mode" not in launcher
    assert "--no-auto-open" not in launcher


def test_tracker_http_surface_runs_memory_projection_actions(tmp_path: Path, monkeypatch: Any) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    entries = _materialize_memory_images(tmp_path / "memory-images", _sample_memory_entries())
    monkeypatch.setattr(adapter, "_get_phoenixguard_memory_bank", lambda: _StubPhoenixBank(entries))

    def detect_market_selector(
        image: Image.Image,
        timeframe_selector: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        _ = image
        _ = timeframe_selector
        return {"value": "GBP/JPY OTC", "source": "headertext", "confidence": 0.90}

    monkeypatch.setattr(
        adapter,
        "_detect_market_selector",
        detect_market_selector,
    )
    tracker_service = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_chart_surface("sell")]),
        tracking_adapter=adapter,
    )
    app = create_app(window_tracker_service=tracker_service)
    client = TestClient(app)

    create_response = client.post(
        "/v1/mobile/window-tracker/sessions",
        json={
            "session_id": "pocket-live",
            "window_query": "Pocket Option",
            "capture_interval_sec": 0.5,
        },
    )
    assert create_response.status_code == 201
    session_id = create_response.json()["session_id"]

    focus_response = client.put(
        f"/v1/mobile/window-tracker/sessions/{session_id}/focus-region",
        json={"normalized_bbox": [0.02, 0.02, 0.98, 0.98], "source": "dashboard"},
    )
    assert focus_response.status_code == 200

    predict_response = client.post(f"/v1/mobile/window-tracker/sessions/{session_id}/predict")
    assert predict_response.status_code == 200
    predict_payload = predict_response.json()["memory_projection_current"]
    assert predict_payload["mode"] == "predict"
    assert predict_payload["status"] == "ready"
    assert predict_payload["memory_retrieval"]["state"] == "ready"
    assert predict_payload["memory_retrieval"]["entries"] == 3
    assert predict_payload["memory_precision"]["accepted"] is True
    assert predict_payload["primary_fit"]["top_matches"][0]["candle_regression"]["direction"] in {"BUY", "SELL", "HOLD"}
    reference_response = client.get(f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-memory-reference")
    assert reference_response.status_code == 200
    assert len(reference_response.content) > 0
    projection_response = client.get(f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-projection")
    assert projection_response.status_code == 200
    assert len(projection_response.content) > 0

    future_response = client.post(f"/v1/mobile/window-tracker/sessions/{session_id}/show-future")
    assert future_response.status_code == 200
    future_payload = future_response.json()["memory_projection_current"]
    assert future_payload["mode"] == "future"
    assert future_payload["status"] == "ready"
    assert future_payload["memory_retrieval"]["state"] == "ready"
    assert future_payload["memory_precision"]["accepted"] is True
    future_reference_response = client.get(f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-memory-reference")
    assert future_reference_response.status_code == 200
    assert len(future_reference_response.content) > 0
    future_projection_response = client.get(f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-projection")
    assert future_projection_response.status_code == 200
    assert len(future_projection_response.content) > 0


def test_tracker_service_updates_capture_interval_control(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )

    session = tracker.create_session(session_id="pocket-live", capture_interval_sec=3.0)
    updated = tracker.update_session_controls(
        str(session["session_id"]),
        capture_interval_sec=3.0,
        min_capture_interval_sec=0.5,
        max_capture_interval_sec=10.0,
    )

    assert float(updated["capture_interval_sec"]) == 3.0
    assert float(updated["execution_controls"]["min_capture_interval_sec"]) == 0.5
    assert float(updated["execution_controls"]["max_capture_interval_sec"]) == 10.0


def test_tracker_accepts_hardened_subsecond_dashboard_timing(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )

    session = tracker.create_session(session_id="pocket-live", capture_interval_sec=0.5)
    updated = tracker.update_session_controls(
        str(session["session_id"]),
        capture_interval_sec=0.5,
        min_capture_interval_sec=0.5,
        max_capture_interval_sec=2.0,
    )

    assert float(updated["capture_interval_sec"]) == 0.5
    assert float(updated["execution_controls"]["min_capture_interval_sec"]) == 0.5
    assert float(updated["execution_controls"]["max_capture_interval_sec"]) == 2.0

    payload = tracker.load_session_payload(str(session["session_id"]))
    payload["manual_focus_region"] = {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]}
    payload["latest_signal"] = {"action": "HOLD", "execution_action": "HOLD", "entry_state": "WAIT"}
    payload["tracking_summary"] = {"decision_kernel": {"state": "IDLE"}}
    base = tracker.adaptive_capture_interval_plan(payload)

    assert float(base["interval_sec"]) == 0.5
    assert base["reason"] == "base_timer"


def test_tracker_http_surface_updates_capture_interval_control(tmp_path: Path) -> None:
    tracker_service = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface(width=1280, height=720)]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    app = create_app(window_tracker_service=tracker_service)
    client = TestClient(app)

    create_response = client.post(
        "/v1/mobile/window-tracker/sessions",
        json={
            "session_id": "pocket-live",
            "window_query": "Pocket Option",
            "capture_interval_sec": 3.0,
        },
    )
    assert create_response.status_code == 201
    session_id = create_response.json()["session_id"]

    update_response = client.patch(
        f"/v1/mobile/window-tracker/sessions/{session_id}/controls",
        json={
            "capture_interval_sec": 3.0,
            "require_memory_projection": False,
            "require_market_identity": False,
            "require_timeframe_identity": True,
            "auto_memory_projection": False,
            "adaptive_timer_enabled": False,
            "min_capture_interval_sec": 0.5,
            "max_capture_interval_sec": 10.0,
            "max_executions_per_window": 3,
            "execution_window_sec": 180.0,
            "cooldown_sec": 12.0,
            "phoenix_report_interval_sec": 24.0,
        },
    )

    assert update_response.status_code == 200
    payload = update_response.json()
    assert float(payload["capture_interval_sec"]) == 3.0
    assert payload["execution_controls"]["require_memory_projection"] is False
    assert payload["execution_controls"]["require_market_identity"] is False
    assert payload["execution_controls"]["require_timeframe_identity"] is True
    assert payload["execution_controls"]["auto_memory_projection"] is False
    assert payload["execution_controls"]["adaptive_timer_enabled"] is False
    assert float(payload["execution_controls"]["min_capture_interval_sec"]) == 0.5
    assert float(payload["execution_controls"]["max_capture_interval_sec"]) == 10.0
    assert int(payload["execution_controls"]["max_executions_per_window"]) == 3
    assert float(payload["execution_controls"]["execution_window_sec"]) == 180.0
    assert float(payload["execution_controls"]["cooldown_sec"]) == 600.0
    assert float(payload["execution_controls"]["phoenix_report_interval_sec"]) == 24.0


def test_tracker_capture_preserves_concurrent_control_updates(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface(width=1280, height=720), _surface(width=1280, height=720)]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.10, 0.10, 0.88, 0.86], source="test")

    started = threading.Event()
    release = threading.Event()

    class _BlockingTrackingAdapter(_FakeTrackingAdapter):
        def study(self, image: Image.Image, *, session_payload: Mapping[str, Any] | None = None) -> TrackingStudy:
            started.set()
            assert release.wait(5.0)
            return super().study(image, session_payload=session_payload)

    setattr(tracker, "tracking_adapter", _BlockingTrackingAdapter("BUY"))
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(tracker.capture_once, str(session["session_id"]))
        assert started.wait(5.0)
        tracker.update_session_controls(
            str(session["session_id"]),
            live_execution_enabled=True,
            execution_mode="shadow",
            scenario_generation_enabled=True,
            auto_memory_projection=True,
            min_capture_interval_sec=0.5,
            max_capture_interval_sec=10.0,
        )
        release.set()
        payload = future.result(timeout=10.0)

    controls = cast(dict[str, Any], payload["execution_controls"])
    assert controls["live_execution_enabled"] is True
    assert controls["scenario_generation_enabled"] is True
    persisted = tracker.get_session(str(session["session_id"]))
    assert persisted["execution_controls"]["live_execution_enabled"] is True
    assert persisted["execution_controls"]["scenario_generation_enabled"] is True


def test_tracker_http_emergency_stop_disables_live_execution(tmp_path: Path) -> None:
    tracker_service = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface(width=1280, height=720)]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    app = create_app(window_tracker_service=tracker_service)
    client = TestClient(app)

    create_response = client.post(
        "/v1/mobile/window-tracker/sessions",
        json={"session_id": "pocket-live", "window_query": "Pocket Option"},
    )
    assert create_response.status_code == 201
    session_id = create_response.json()["session_id"]
    focus_response = client.put(
        f"/v1/mobile/window-tracker/sessions/{session_id}/focus-region",
        json={"normalized_bbox": [0.0, 0.0, 1.0, 1.0], "source": "test"},
    )
    assert focus_response.status_code == 200
    controls_response = client.patch(
        f"/v1/mobile/window-tracker/sessions/{session_id}/controls",
        json={"live_execution_enabled": True, "execution_mode": "live"},
    )
    assert controls_response.status_code == 200

    stop_response = client.post(f"/v1/mobile/window-tracker/sessions/{session_id}/emergency-stop")

    assert stop_response.status_code == 200
    payload = stop_response.json()
    assert payload["tracking_enabled"] is False
    assert payload["execution_controls"]["live_execution_enabled"] is False
    assert payload["execution_controls"]["execution_mode"] == "shadow"
    assert payload["broker_execution_state"]["status"] == "emergency_stop"


def test_pocket_option_execution_backend_detects_fixed_amount_controls() -> None:
    backend = PocketOptionBrokerExecutionBackend()
    payload = backend.read_surface(_synthetic_broker_window())

    assert payload["controls_ready"] is True
    assert payload["amount_lock"]["required"] == "preserve"
    assert payload["amount_lock"]["policy"] == "preserve_visible_broker_amount"
    assert payload["amount_lock"]["verified"] is True
    assert payload["buy_button"]["bbox"]
    assert payload["sell_button"]["bbox"]


def test_pocket_option_execution_backend_detects_narrow_real_gui_order_panel() -> None:
    backend = PocketOptionBrokerExecutionBackend()
    payload = backend.read_surface(_synthetic_full_pocket_option_gui())

    assert payload["controls_ready"] is True
    assert payload["order_panel"]["bbox"]
    assert payload["amount_lock"]["verified"] is True
    assert payload["expiry_lock"]["field_ready"] is True
    assert payload["buy_button"]["bbox"][0] > 1600
    assert payload["sell_button"]["bbox"][0] > 1600


def test_pocket_option_execution_backend_rejects_blank_calibrated_box_map_fallback() -> None:
    backend = PocketOptionBrokerExecutionBackend()
    payload = backend.read_surface(Image.new("RGB", (1938, 1038), (20, 20, 20)))

    assert payload["controls_ready"] is False
    assert payload["buy_button"] == {}
    assert payload["sell_button"] == {}
    assert payload["expiry_lock"]["field_ready"] is False


def test_pocket_option_execution_backend_anchors_time_field_to_surface_fallback_on_real_panel() -> None:
    backend = PocketOptionBrokerExecutionBackend()
    full_gui = _synthetic_full_pocket_option_gui()
    payload = backend.read_surface(full_gui)

    assert payload["controls_ready"] is True
    assert payload["time_field"]["source"] == "amount_relative"
    time_center = PocketOptionBrokerExecutionBackend.bbox_center(payload["time_field"]["bbox"])
    assert time_center is not None
    assert abs(time_center[0] - int(round(full_gui.width * 0.91125))) <= 20
    assert abs(time_center[1] - int(round(full_gui.height * 0.26685))) <= 40


def test_pocket_option_execution_backend_reads_broker_identity_from_header_adapter() -> None:
    backend = PocketOptionBrokerExecutionBackend(identity_adapter=_StubIdentityAdapter())
    payload = backend.read_surface(_synthetic_broker_window())

    assert payload["detected_market"] == "GBP/AUD OTC"
    assert payload["detected_timeframe"] == "M5"
    assert payload["identity_ready"] is True
    assert payload["market_confidence"] >= 0.9


def test_pocket_option_execution_backend_sets_expiry_before_live_click(monkeypatch: Any) -> None:
    class _FakeUser32:
        def __init__(self) -> None:
            self.cursor_points: list[list[int]] = []
            self.key_events: list[tuple[int, int]] = []

        def SetForegroundWindow(self, hwnd: Any) -> None:
            _ = hwnd

        def SetCursorPos(self, x: int, y: int) -> None:
            self.cursor_points.append([int(x), int(y)])

        def mouse_event(self, *_args: Any) -> None:
            pass

        def keybd_event(self, vk: int, _scan: int, flags: int, _extra: int) -> None:
            self.key_events.append((int(vk), int(flags)))

        def VkKeyScanW(self, codepoint: int) -> int:
            return int(codepoint)

        def WindowFromPoint(self, _point: Any) -> int:
            return 501

        def GetAncestor(self, _hwnd: Any, _flag: int) -> int:
            return 501

        def GetParent(self, _hwnd: Any) -> int:
            return 0

    backend = PocketOptionBrokerExecutionBackend()
    monkeypatch.setattr(backend, "is_supported", lambda: True)
    fake_user32 = _FakeUser32()
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=fake_user32), raising=False)
    window_image = _synthetic_broker_window()
    broker_surface = backend.read_surface(window_image)
    popup_controls = backend.expiry_popup_control_points(cast(Mapping[str, Any], broker_surface["time_field"]))
    popup_locks = {
        name: backend.control_lock(
            key=name,
            label=name,
            row={"bbox": [point[0] - 12, point[1] - 10, point[0] + 12, point[1] + 10], "confidence": 0.9, "source": "test_popup"},
            read_at="2026-04-30T00:00:00+00:00",
            image_width=window_image.width,
            image_height=window_image.height,
            role="expiry_popup",
        )
        for name, point in popup_controls.items()
    }
    def popup_visual_control_points(**_kwargs: object) -> dict[str, Any]:
        return {
            "controls": popup_controls,
            "execution_boxes": popup_locks,
            "geometry": {"source": "test_visual_popup_grid"},
        }

    def verify_expiry_popup_target(**kwargs: object) -> dict[str, Any]:
        target_seconds = cast(int, kwargs["target_seconds"])
        return {
            "status": "verified",
            "matches": True,
            "target_seconds": target_seconds,
            "visible_seconds": target_seconds,
            "visible_text": backend.format_expiry_text(target_seconds),
            "confidence": 1.0,
            "source": "test_timer",
        }

    def verify_trade_click_result(**kwargs: object) -> dict[str, Any]:
        return {
            "status": "confirmed",
            "confirmed": True,
            "side": kwargs["side"],
            "expiry_seconds": kwargs["expiry_seconds"],
            "message": "confirmed by test",
        }

    monkeypatch.setattr(backend, "expiry_popup_visual_control_points", popup_visual_control_points)
    monkeypatch.setattr(backend, "_verify_expiry_popup_target", verify_expiry_popup_target)
    monkeypatch.setattr(backend, "_verify_trade_click_result", verify_trade_click_result)

    result = backend.prepare_and_click(
        descriptor={"hwnd": 501, "bbox": [100, 200, 1060, 740]},
        window_image=window_image,
        side="BUY",
        amount="5",
        expiry_seconds=180,
        broker_surface=broker_surface,
    )

    assert result["status"] == "clicked"
    assert result["expiry_text"] == "00:03:00"
    assert result["time_point"] in fake_user32.cursor_points
    assert "amount_point" not in result
    assert result["button_point"] == fake_user32.cursor_points[-1]
    assert result["amount_preserved"] is True
    assert result["amount_commit"]["sent_enter"] is False
    assert result["amount_commit"]["sent_escape"] is False
    assert (0x0D, 0) not in fake_user32.key_events
    popup_clicks = result["expiry_popup_clicks"]
    assert popup_clicks[0]["name"] == "dismiss_existing_time_popup"
    assert popup_clicks[0]["method"] == "keyboard"
    assert popup_clicks[1]["name"] == "open_time_popup"
    assert any(row["name"] == "quick_m3" for row in popup_clicks)
    assert result["expiry_popup_locks"]["quick_m3"]["locked"] is True
    assert result["trade_verification"]["confirmed"] is True


def test_pocket_option_execution_backend_blocks_without_visual_popup_lock(monkeypatch: Any) -> None:
    class _FakeUser32:
        def SetForegroundWindow(self, hwnd: Any) -> None:
            _ = hwnd

        def SetCursorPos(self, _x: int, _y: int) -> None:
            pass

        def mouse_event(self, *_args: Any) -> None:
            pass

        def keybd_event(self, *_args: Any) -> None:
            pass

        def VkKeyScanW(self, codepoint: int) -> int:
            return int(codepoint)

        def WindowFromPoint(self, _point: Any) -> int:
            return 501

        def GetAncestor(self, _hwnd: Any, _flag: int) -> int:
            return 501

        def GetParent(self, _hwnd: Any) -> int:
            return 0

    backend = PocketOptionBrokerExecutionBackend()
    monkeypatch.setattr(backend, "is_supported", lambda: True)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=_FakeUser32()), raising=False)
    def empty_popup_visual_control_points(**_kwargs: object) -> dict[str, Any]:
        return {}

    monkeypatch.setattr(backend, "expiry_popup_visual_control_points", empty_popup_visual_control_points)
    window_image = _synthetic_broker_window()
    broker_surface = backend.read_surface(window_image)

    result = backend.prepare_and_click(
        descriptor={"hwnd": 501, "bbox": [100, 200, 1060, 740]},
        window_image=window_image,
        side="BUY",
        amount="5",
        expiry_seconds=180,
        broker_surface=broker_surface,
    )

    assert result["status"] == "blocked"
    assert "visually locked" in result["message"]
    assert result["expiry_verification"]["status"] == "unavailable"


def test_pocket_option_execution_backend_reports_unverified_click(monkeypatch: Any) -> None:
    class _FakeUser32:
        def __init__(self) -> None:
            self.cursor_points: list[list[int]] = []

        def SetForegroundWindow(self, hwnd: Any) -> None:
            _ = hwnd

        def SetCursorPos(self, x: int, y: int) -> None:
            self.cursor_points.append([int(x), int(y)])

        def mouse_event(self, *_args: Any) -> None:
            pass

        def keybd_event(self, *_args: Any) -> None:
            pass

        def VkKeyScanW(self, codepoint: int) -> int:
            return int(codepoint)

        def WindowFromPoint(self, _point: Any) -> int:
            return 501

        def GetAncestor(self, _hwnd: Any, _flag: int) -> int:
            return 501

        def GetParent(self, _hwnd: Any) -> int:
            return 0

    backend = PocketOptionBrokerExecutionBackend()
    monkeypatch.setattr(backend, "is_supported", lambda: True)
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(user32=_FakeUser32()), raising=False)
    window_image = _synthetic_broker_window()
    broker_surface = backend.read_surface(window_image)
    def popup_visual_control_points(**_kwargs: object) -> dict[str, Any]:
        return _test_popup_visual_payload(
            backend,
            window_image,
            cast(Mapping[str, Any], broker_surface["time_field"]),
        )

    def verify_expiry_popup_target(**kwargs: object) -> dict[str, Any]:
        target_seconds = cast(int, kwargs["target_seconds"])
        return {
            "status": "verified",
            "matches": True,
            "target_seconds": target_seconds,
            "visible_seconds": target_seconds,
            "visible_text": backend.format_expiry_text(target_seconds),
            "confidence": 1.0,
            "source": "test_timer",
        }

    def verify_trade_click_result(**kwargs: object) -> dict[str, Any]:
        return {
            "status": "unverified",
            "confirmed": False,
            "side": kwargs["side"],
            "expiry_seconds": kwargs["expiry_seconds"],
            "message": "no accepted-trade cue",
        }

    monkeypatch.setattr(backend, "expiry_popup_visual_control_points", popup_visual_control_points)
    monkeypatch.setattr(backend, "_verify_expiry_popup_target", verify_expiry_popup_target)
    monkeypatch.setattr(backend, "_verify_trade_click_result", verify_trade_click_result)

    result = backend.prepare_and_click(
        descriptor={"hwnd": 501, "bbox": [100, 200, 1060, 740]},
        window_image=window_image,
        side="SELL",
        amount="5",
        expiry_seconds="00:03:00",  # type: ignore[arg-type]
        broker_surface=broker_surface,
    )

    assert result["status"] == "click_sent_unverified"
    assert result["expiry_text"] == "00:03:00"
    assert result["trade_verification"]["confirmed"] is False
    assert result["click_diagnostics"][-1]["sent_input"] is True


def test_pocket_option_click_refuses_cursor_miss() -> None:
    class _FakeUser32:
        def SetCursorPos(self, _x: int, _y: int) -> None:
            pass

        def GetCursorPos(self, point_ref: Any) -> bool:
            point_ref._obj.x = 140
            point_ref._obj.y = 140
            return True

        def WindowFromPoint(self, _point: Any) -> int:
            return 501

        def GetAncestor(self, _hwnd: Any, _flag: int) -> int:
            return 501

        def GetParent(self, _hwnd: Any) -> int:
            return 0

        def mouse_event(self, *_args: Any) -> None:
            pass

    with pytest.raises(RuntimeError, match="cursor did not land"):
        PocketOptionBrokerExecutionBackend.click_screen_point(
            _FakeUser32(),
            100,
            100,
            expected_hwnd=501,
            target_name="BUY",
            target_bbox=[95, 95, 105, 105],
            physical_point=(100, 100),
        )


def test_pocket_option_visual_popup_grid_overrides_field_relative_points() -> None:
    backend = PocketOptionBrokerExecutionBackend()
    fallback = backend.expiry_popup_control_points({"bbox": [1636, 190, 1794, 255]})
    image = _surface(width=1938, height=1038)
    draw = ImageDraw.Draw(image)
    cols = [1522, 1582, 1642]
    rows = [350, 389, 428]
    labels = [["S3", "S15", "S30"], ["M1", "M3", "M5"], ["M30", "H1", "H4"]]
    for row_y, row_labels in zip(rows, labels):
        for col_x, label in zip(cols, row_labels):
            draw.rounded_rectangle((col_x - 28, row_y - 14, col_x + 28, row_y + 14), radius=5, fill=(26, 32, 52), outline=(45, 56, 82))
            draw.text((col_x - 13, row_y - 7), label, fill=(130, 166, 216))

    class _CaptureBackend:
        def capture_window(self, _descriptor: Mapping[str, Any]) -> Image.Image:
            return image

    original_capture = WindowsWindowCaptureBackend
    try:
        import phoenixguard.mobile_api.window_tracker as window_tracker_module

        window_tracker_module.WindowsWindowCaptureBackend = _CaptureBackend  # type: ignore[assignment]
        visual = backend.expiry_popup_visual_control_points(
            descriptor={"hwnd": 501, "bbox": [0, 0, 1938, 1038]},
            time_field={"bbox": [1636, 190, 1794, 255]},
            fallback=fallback,
        )
    finally:
        import phoenixguard.mobile_api.window_tracker as window_tracker_module

        window_tracker_module.WindowsWindowCaptureBackend = original_capture  # type: ignore[assignment]

    controls = cast(dict[str, Any], visual["controls"])
    geometry = cast(dict[str, Any], visual["geometry"])
    execution_boxes = cast(dict[str, Any], visual["execution_boxes"])
    assert geometry["source"] == "visual_popup_shortcut_grid"
    assert abs(controls["quick_m3"][0] - 1582) <= 8
    assert abs(controls["quick_m3"][1] - 389) <= 1
    assert abs(controls["quick_h1"][0] - 1582) <= 8
    assert abs(controls["quick_h1"][1] - 428) <= 1
    assert abs(controls["minute_plus"][0] - 1582) <= 8
    assert abs(controls["second_plus"][0] - 1642) <= 8
    assert execution_boxes["quick_m3"]["bbox"]
    assert execution_boxes["minute_plus"]["locked"] is True


def test_pocket_option_expiry_plan_uses_m3_shortcut_from_presets() -> None:
    plan_from_h4 = PocketOptionBrokerExecutionBackend.expiry_popup_click_plan(14400, 120)
    plan_from_m30 = PocketOptionBrokerExecutionBackend.expiry_popup_click_plan(1800, 120)
    plan_from_m1 = PocketOptionBrokerExecutionBackend.expiry_popup_click_plan(60, 120)
    plan_from_h2_to_m3 = PocketOptionBrokerExecutionBackend.expiry_popup_click_plan(7200, 180)
    plan_from_m3_to_m3 = PocketOptionBrokerExecutionBackend.expiry_popup_click_plan(180, 180)

    assert plan_from_h4 == ["quick_m1", "minute_plus"]
    assert plan_from_m30 == ["quick_m1", "minute_plus"]
    assert plan_from_m1 == ["minute_plus"]
    assert plan_from_h2_to_m3 == ["quick_m3"]
    assert plan_from_m3_to_m3 == []


def test_pocket_option_expiry_plan_resets_seconds_state_through_minute_shortcut() -> None:
    plan = PocketOptionBrokerExecutionBackend.expiry_popup_click_plan(30, 600)

    assert plan[0] == "quick_m5"
    assert plan.count("minute_plus") == 5
    assert PocketOptionBrokerExecutionBackend.format_expiry_text("00:03:05") == "00:03:05"


def test_pocket_option_expiry_plan_supports_exact_second_controls() -> None:
    assert PocketOptionBrokerExecutionBackend.expiry_popup_click_plan(15, 3) == ["quick_s3"]
    assert PocketOptionBrokerExecutionBackend.expiry_popup_click_plan(15, 15) == []
    plan_to_45 = PocketOptionBrokerExecutionBackend.expiry_popup_click_plan(15, 45)
    assert any(step.startswith("quick_s") or step.startswith("second_") for step in plan_to_45)
    assert PocketOptionBrokerExecutionBackend.expiry_popup_click_plan(15, 75) == ["minute_plus"]


def test_pocket_option_expiry_plan_uses_nearest_minute_anchor_for_long_non_preset() -> None:
    plan = PocketOptionBrokerExecutionBackend.expiry_popup_click_plan(60, 1200)

    assert plan[0] == "quick_m30"
    assert plan.count("minute_minus") == 10
    assert len(plan) == 11
    assert all(not step.startswith("quick_s") for step in plan)
    assert all(not step.startswith("second_") for step in plan)


def test_tracker_execution_controls_default_to_shadow_fixed_amount(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
        execution_backend=_FakeExecutionBackend(),
    )
    session = tracker.create_session(session_id="pocket-live")
    controls = session["execution_controls"]

    assert controls["live_execution_enabled"] is False
    assert controls["execution_mode"] == "shadow"
    assert controls["fixed_amount"] == "preserve"
    assert controls["amount_policy"] == "preserve_visible_broker_amount"
    assert controls["allow_countertrend_scalp"] is False
    assert controls["trade_profile"] == "HIGH_FREQUENCY"
    assert controls["high_frequency_enabled"] is True
    assert controls["swing_fallback_enabled"] is False
    assert int(controls["high_frequency_expiry_seconds"]) == 600
    assert float(session["capture_interval_sec"]) == 1.0
    assert float(controls["min_capture_interval_sec"]) == 0.5
    assert float(controls["max_capture_interval_sec"]) == 10.0
    assert float(controls["cooldown_sec"]) == 600.0

    updated = tracker.update_session_controls(
        str(session["session_id"]),
        live_execution_enabled=True,
        execution_mode="live",
        allow_countertrend_scalp=False,
    )

    assert updated["execution_controls"]["live_execution_enabled"] is True
    assert updated["execution_controls"]["execution_mode"] == "live"
    assert updated["execution_controls"]["allow_countertrend_scalp"] is False
    assert updated["execution_controls"]["fixed_amount"] == "preserve"
    assert updated["execution_controls"]["amount_policy"] == "preserve_visible_broker_amount"
    assert updated["broker_execution_state"]["status"] == "armed"


def test_high_frequency_cycle_keeps_active_candidate_side_when_forecasts_disagree() -> None:
    cycle = window_tracker_module.build_high_frequency_candle_cycle_context(
        signal={
            "execution_action": "SELL",
            "two_candle_study": {
                "status": "READY",
                "primary_pressure": "BUY",
                "confidence": 0.62,
                "next_candle_forecast": {"direction": "SELL", "confidence": 0.58},
                "second_next_candle_forecast": {"direction": "BUY", "confidence": 0.52},
            },
        },
        tracking={},
        controls={"trade_profile": "HIGH_FREQUENCY", "high_frequency_enabled": True},
        symbol="EUR/JPY OTC",
        timeframe="M5",
        now_epoch=1781643301.0,
    )

    assert cycle["ready"] is False
    assert cycle["candidate_side"] == "SELL"
    assert cycle["active_candidate_side"] == "SELL"
    assert cycle["pressure_side"] == "BUY"
    assert cycle["forecast_side"] == "HOLD"
    assert cycle["forecast_agreement"] is False


def test_high_frequency_cycle_does_not_let_hold_mask_candidate_side() -> None:
    cycle = window_tracker_module.build_high_frequency_candle_cycle_context(
        signal={
            "execution_action": "HOLD",
            "candidate_action": "SELL",
            "two_candle_study": {
                "status": "READY",
                "primary_pressure": "SELL",
                "confidence": 0.62,
                "next_candle_forecast": {"direction": "BUY", "confidence": 0.58},
                "second_next_candle_forecast": {"direction": "SELL", "confidence": 0.52},
            },
        },
        tracking={},
        controls={"trade_profile": "HIGH_FREQUENCY", "high_frequency_enabled": True},
        symbol="EUR/JPY OTC",
        timeframe="M5",
        now_epoch=1781643301.0,
    )

    assert cycle["ready"] is False
    assert cycle["candidate_side"] == "SELL"
    assert cycle["active_candidate_side"] == "SELL"
    assert cycle["pressure_side"] == "SELL"
    assert cycle["forecast_side"] == "HOLD"


def test_high_frequency_cycle_forecast_agreement_overrides_candidate_only_when_ready() -> None:
    cycle = window_tracker_module.build_high_frequency_candle_cycle_context(
        signal={
            "execution_action": "SELL",
            "two_candle_study": {
                "status": "READY",
                "primary_pressure": "BUY",
                "confidence": 0.64,
                "next_candle_forecast": {"direction": "BUY", "confidence": 0.64},
                "second_next_candle_forecast": {"direction": "BUY", "confidence": 0.62},
            },
        },
        tracking={},
        controls={"trade_profile": "HIGH_FREQUENCY", "high_frequency_enabled": True},
        symbol="EUR/JPY OTC",
        timeframe="M5",
        now_epoch=1781643301.0,
    )

    assert cycle["ready"] is True
    assert cycle["side"] == "BUY"
    assert cycle["candidate_side"] == "BUY"
    assert cycle["active_candidate_side"] == "SELL"
    assert cycle["forecast_side"] == "BUY"
    assert cycle["forecast_agreement"] is True


def test_tracker_disabled_execution_skips_broker_surface_scan(tmp_path: Path) -> None:
    execution_backend = _CountingIdentityExecutionBackend()
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
        execution_backend=execution_backend,
    )
    session_id = str(tracker.create_session(session_id="pocket-shadow")["session_id"])
    _focus_session_without_preview(tracker, session_id)
    payload = tracker.load_session_payload(session_id)
    controls = dict(payload["execution_controls"])
    controls.update({"live_execution_enabled": False, "execution_mode": "shadow"})
    payload["execution_controls"] = controls
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    write_json_atomic(tracker.session_dir(session_id) / "session.json", payload)

    _allow_next_capture(tracker, session_id)
    result = tracker.capture_once(session_id)

    broker_surface = cast(dict[str, Any], result["broker_surface"])
    broker_execution_state = cast(dict[str, Any], result["broker_execution_state"])
    assert execution_backend.read_count == 0
    assert broker_execution_state["status"] == "disabled"
    assert broker_surface["scan_skipped"] is True
    assert broker_surface["scan_skip_reason"] == "live_execution_disabled"
    assert str(broker_surface["broker_surface_hash"])


def test_tracker_emergency_stop_disables_live_execution_and_writes_event_log(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    controls = dict(payload["execution_controls"])
    controls.update({"live_execution_enabled": True, "execution_mode": "live"})
    payload["execution_controls"] = controls
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    stopped = tracker.emergency_stop_session(str(session["session_id"]), reason="test stop")

    assert stopped["tracking_enabled"] is False
    assert stopped["execution_controls"]["live_execution_enabled"] is False
    assert stopped["execution_controls"]["execution_mode"] == "shadow"
    assert stopped["broker_execution_state"]["status"] == "emergency_stop"
    event_log_path = Path(stopped["event_log_path"])
    assert event_log_path.exists()
    assert "emergency_stop" in event_log_path.read_text(encoding="utf-8")


def test_tracker_adaptive_timer_uses_subsecond_entry_sniper_and_default_bounds(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    session = tracker.create_session(session_id="pocket-live", capture_interval_sec=3.0)
    payload = tracker.load_session_payload(str(session["session_id"]))
    payload["manual_focus_region"] = {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]}
    payload["latest_signal"] = {
        "action": "BUY",
        "execution_action": "BUY",
        "entry_state": "ENTRY_READY",
        "actionable": True,
    }
    payload["tracking_summary"] = {"decision_kernel": {"state": "TRIGGERED", "p_trigger_next_1": 0.70}}

    entry_ready = tracker.adaptive_capture_interval_plan(payload)
    assert float(entry_ready["interval_sec"]) == 0.5
    assert entry_ready["reason"] == "entry_ready"

    payload["latest_signal"] = {"action": "BUY", "execution_action": "BUY", "entry_state": "SNIPER_READY"}
    payload["tracking_summary"] = {"decision_kernel": {"state": "ARMED", "p_trigger_next_3": 0.72}}
    sniper_ready = tracker.adaptive_capture_interval_plan(payload)
    assert float(sniper_ready["interval_sec"]) == 0.5

    payload["latest_signal"] = {"action": "HOLD", "execution_action": "HOLD", "entry_state": "WAIT"}
    payload["tracking_summary"] = {"decision_kernel": {"state": "IDLE"}}
    base = tracker.adaptive_capture_interval_plan(payload)
    assert float(base["interval_sec"]) == 3.0
    assert base["reason"] == "base_timer"

    payload["capture_interval_sec"] = 30.0
    capped = tracker.adaptive_capture_interval_plan(payload)
    assert float(capped["interval_sec"]) == 10.0
    assert capped["reason"] == "base_timer"

    payload["execution_controls"] = {"adaptive_timer_enabled": False}
    fixed = tracker.adaptive_capture_interval_plan(payload)
    assert float(fixed["interval_sec"]) == 30.0
    assert fixed["reason"] == "fixed_timer"


def test_tracker_execution_throttle_blocks_after_five_clicks_per_window(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    state: dict[str, Any] = {}
    controls: dict[str, Any] = {"max_executions_per_window": 5, "execution_window_sec": 300.0}
    now = 1000.0

    for _index in range(5):
        allowed, _message = tracker.execution_throttle_allows(state, controls, now_epoch=now)
        assert allowed is True
        tracker.record_execution_throttle(state, controls, now_epoch=now)
        now += 10.0

    allowed, message = tracker.execution_throttle_allows(state, controls, now_epoch=now)
    assert allowed is False
    assert "5/5" in message

    allowed_after_reset, _message_after_reset = tracker.execution_throttle_allows(state, controls, now_epoch=1301.0)
    assert allowed_after_reset is True


def test_tracker_live_execution_uses_fixed_amount_and_fake_click_backend(tmp_path: Path) -> None:
    execution_backend = _FakeExecutionBackend()
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
        execution_backend=execution_backend,
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    controls = dict(payload["execution_controls"])
    controls.update(
        {
            "live_execution_enabled": True,
            "execution_mode": "live",
            "require_memory_projection": False,
            "require_market_identity": False,
        }
    )
    payload["execution_controls"] = controls
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    result = tracker.capture_once(str(session["session_id"]))

    assert execution_backend.clicks == []
    assert result["broker_execution_state"]["status"] == "blocked_by_runtime"
    assert "Model Council V3 executable packet required" in result["broker_execution_state"]["message"]
    assert result["broker_execution_state"]["amount"] == "preserve"
    assert result["broker_execution_state"]["active_trade"] == {}


def test_tracker_live_execution_clicks_sell_with_swing_expiry(tmp_path: Path) -> None:
    execution_backend = _FakeExecutionBackend()
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("SELL"),
        execution_backend=execution_backend,
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    controls = dict(payload["execution_controls"])
    controls.update(
        {
            "live_execution_enabled": True,
            "execution_mode": "live",
            "require_memory_projection": False,
            "require_market_identity": False,
        }
    )
    payload["execution_controls"] = controls
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    result = tracker.capture_once(str(session["session_id"]))

    assert execution_backend.clicks == []
    assert result["latest_signal"]["required_seconds"] == 10 * 5 * 60
    assert result["latest_signal"]["execution_timing"]["target_horizon_candles"] == 10
    assert result["latest_signal"]["execution_timing"]["timing_class"] == "breakout_extension"
    assert result["broker_execution_state"]["status"] == "blocked_by_runtime"
    assert result["broker_execution_state"]["side"] == "SELL"


def test_tracker_selects_pullback_reload_lane_when_sniper_ready(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("SELL"),
    )
    latest_signal: dict[str, Any] = {
        "execution_action": "SELL",
        "actionable": True,
        "entry_state": "SNIPER_READY",
        "summary": "Pullback reload sniper gate is ready.",
    }
    tracking_summary: dict[str, Any] = {
        "decision_kernel": {
            "trade_mode": "PULLBACK_WAIT",
            "dominant_side": "sell",
            "major_trend_side": "sell",
            "target_horizon_candles": 10,
            "conflict_score": 0.12,
            "p_target_before_invalidation": 0.66,
            "hazard_invalidation": 0.12,
            "hazard_trigger": 0.48,
            "p_expire_before_trigger": 0.10,
            "next_most_likely_event": "trigger",
            "next_candle_bias": "sell",
            "p_next_buy": 0.12,
            "p_next_sell": 0.74,
            "expected_value_R": 1.3,
        },
        "candle_statistics": {"opposing_ratio": 0.10},
        "box_context": {"failure_risk": 0.16},
    }

    lane = tracker.select_execution_lane(
        latest_signal,
        tracking_summary,
        {"min_primary_target_candles": 10},
    )

    assert lane["actionable"] is True
    assert lane["side"] == "SELL"
    assert lane["lane"] == "PULLBACK_RELOAD"


def test_tracker_live_execution_persists_blocked_click_diagnostics(tmp_path: Path) -> None:
    execution_backend = _BlockingExecutionBackend()
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
        execution_backend=execution_backend,
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    controls = dict(payload["execution_controls"])
    controls.update(
        {
            "live_execution_enabled": True,
            "execution_mode": "live",
            "require_memory_projection": False,
            "require_market_identity": False,
        }
    )
    payload["execution_controls"] = controls
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    result = tracker.capture_once(str(session["session_id"]))

    state = cast(dict[str, Any], result["broker_execution_state"])
    last_result = cast(dict[str, Any], state["last_result"])
    assert state["status"] == "blocked_by_runtime"
    assert "Model Council V3 executable packet required" in state["message"]
    assert state["model_council_packet_validation"]["ok"] is False
    assert last_result == {}
    assert result["execution_debug"]["visible_expiry_text"] == ""
    assert str(result["execution_debug_log_path"]).endswith("events.jsonl")
    assert state["active_trade"] == {}


def test_expiry_verification_blocks_locked_click_plan_assumption_by_default() -> None:
    clicks: list[dict[str, Any]] = [
        {
            "name": "quick_m5",
            "diagnostic": {
                "sent_input": True,
                "owned_by_expected_window": True,
                "cursor_landed_in_target": True,
            },
        },
        {
            "name": "minute_plus",
            "diagnostic": {
                "sent_input": True,
                "owned_by_expected_window": True,
                "cursor_landed_in_target": True,
            },
        },
    ]

    verification = PocketOptionBrokerExecutionBackend.assume_expiry_from_locked_click_plan(
        target_seconds=900,
        verification={
            "status": "mismatch",
            "matches": False,
            "target_seconds": 900,
            "visible_seconds": 660,
            "visible_text": "00:11:00",
            "confidence": 0.60,
            "source": "time_field_ocr",
        },
        clicks=clicks,
        geometry={"source": "visual_popup_shortcut_grid"},
    )

    assert verification["matches"] is False
    assert verification["visible_text"] == "00:11:00"
    assert verification["assumption_blocked"] is True
    assert verification["assumption_block_reason"] == "emergency expiry assumption is disabled"


def test_expiry_verification_allows_locked_click_plan_only_when_emergency_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIXGUARD_ALLOW_EMERGENCY_EXPIRY_ASSUMPTION", "1")
    clicks: list[dict[str, Any]] = [
        {
            "name": "quick_m5",
            "diagnostic": {
                "sent_input": True,
                "owned_by_expected_window": True,
                "cursor_landed_in_target": True,
            },
        },
        {
            "name": "minute_plus",
            "diagnostic": {
                "sent_input": True,
                "owned_by_expected_window": True,
                "cursor_landed_in_target": True,
            },
        },
    ]

    verification = PocketOptionBrokerExecutionBackend.assume_expiry_from_locked_click_plan(
        target_seconds=900,
        verification={
            "status": "mismatch",
            "matches": False,
            "target_seconds": 900,
            "visible_seconds": 660,
            "visible_text": "00:11:00",
            "confidence": 0.60,
            "source": "time_field_ocr",
        },
        clicks=clicks,
        geometry={"source": "visual_popup_shortcut_grid"},
    )

    assert verification["matches"] is True
    assert verification["status"] == "assumed"
    assert verification["visible_text"] == "00:15:00"
    assert verification["ocr_mismatch"]["visible_text"] == "00:11:00"


def test_expiry_verification_still_blocks_reliable_ocr_mismatch() -> None:
    clicks: list[dict[str, Any]] = [
        {
            "name": "minute_plus",
            "diagnostic": {
                "sent_input": True,
                "owned_by_expected_window": True,
                "cursor_landed_in_target": True,
            },
        }
    ]

    verification = PocketOptionBrokerExecutionBackend.assume_expiry_from_locked_click_plan(
        target_seconds=900,
        verification={
            "status": "mismatch",
            "matches": False,
            "target_seconds": 900,
            "visible_seconds": 660,
            "visible_text": "00:11:00",
            "confidence": 0.91,
            "source": "time_field_ocr",
        },
        clicks=clicks,
        geometry={"source": "visual_popup_shortcut_grid"},
    )

    assert verification["matches"] is False
    assert verification["visible_text"] == "00:11:00"


def test_tracker_execution_reads_full_gui_when_chart_focus_excludes_order_panel(tmp_path: Path) -> None:
    execution_backend = _FakeExecutionBackend()
    full_gui = _synthetic_full_pocket_option_gui()
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([full_gui]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
        execution_backend=execution_backend,
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.06, 0.20, 0.78, 0.92], source="test_chart_only")
    payload = tracker.load_session_payload(str(session["session_id"]))
    controls = dict(payload["execution_controls"])
    controls.update(
        {
            "live_execution_enabled": True,
            "execution_mode": "live",
            "require_memory_projection": False,
            "require_market_identity": False,
        }
    )
    payload["execution_controls"] = controls
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    result = tracker.capture_once(str(session["session_id"]))

    assert execution_backend.clicks == []
    assert result["broker_execution_state"]["status"] == "blocked_by_runtime"
    capture_plane = result["broker_surface"]["capture_plane"]
    assert capture_plane["source"] == "full_window_gui"
    assert capture_plane["uses_manual_focus_crop"] is False
    assert capture_plane["width"] == full_gui.width
    assert result["broker_surface"]["buy_button"]["bbox"][0] > int(full_gui.width * 0.80)


def test_tracker_demo_random_trade_clicks_fixed_m3_expiry(tmp_path: Path) -> None:
    execution_backend = _FakeExecutionBackend()
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
        execution_backend=execution_backend,
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")

    result = tracker.execute_demo_random_trade(str(session["session_id"]), side="SELL", expiry_seconds=180, force=True)

    assert execution_backend.clicks == []
    assert result["broker_execution_state"]["status"] == "blocked_by_runtime"
    assert "Demo random live execution is disabled" in result["broker_execution_state"]["message"]
    assert result["broker_execution_state"]["active_trade"] == {}
    assert result["broker_surface"]["expiry_lock"]["configured_text"] == "00:03:00"
    assert float(result["broker_execution_state"]["cooldown_until_epoch"]) == 0.0


def test_tracker_demo_random_trade_force_uses_signal_side_when_timing_blocks_auto_side(tmp_path: Path) -> None:
    execution_backend = _FakeExecutionBackend()
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
        execution_backend=execution_backend,
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    payload["latest_signal"] = {
        "execution_action": "BUY",
        "action": "BUY",
        "candidate_action": "BUY",
        "actionable": True,
        "execution_timing": {
            "side": "HOLD",
            "entry_allowed": False,
            "block_reason": "Normal execution is waiting for a better historical area.",
        },
    }
    payload["tracking_summary"] = {
        "dominant_side": "BUY",
        "smart_money_context": {"dominant_side": "BUY"},
    }
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    result = tracker.execute_demo_random_trade(str(session["session_id"]), expiry_seconds=180, force=True)

    assert execution_backend.clicks == []
    assert result["broker_execution_state"]["status"] == "blocked_by_runtime"
    assert result["broker_execution_state"]["side"] == "BUY"


def test_tracker_demo_random_trade_respects_twenty_minute_cooldown(tmp_path: Path) -> None:
    execution_backend = _FakeExecutionBackend()
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
        execution_backend=execution_backend,
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")

    first = tracker.execute_demo_random_trade(str(session["session_id"]), side="SELL", expiry_seconds=180, force=True)
    payload = tracker.load_session_payload(str(session["session_id"]))
    state = cast(dict[str, Any], payload["broker_execution_state"])
    state["active_trade"] = {}
    payload["broker_execution_state"] = state
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    second = tracker.execute_demo_random_trade(str(session["session_id"]), side="SELL", expiry_seconds=180)

    assert first["broker_execution_state"]["status"] == "blocked_by_runtime"
    assert second["broker_execution_state"]["status"] == "blocked_by_runtime"
    assert "Demo random live execution is disabled" in second["broker_execution_state"]["message"]
    assert execution_backend.clicks == []


def test_tracker_demo_random_trade_persists_blocked_click_diagnostics(tmp_path: Path) -> None:
    execution_backend = _BlockingExecutionBackend()
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
        execution_backend=execution_backend,
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")

    result = tracker.execute_demo_random_trade(str(session["session_id"]), side="BUY", expiry_seconds=180)

    state = cast(dict[str, Any], result["broker_execution_state"])
    last_result = cast(dict[str, Any], state["last_result"])
    assert state["status"] == "blocked_by_runtime"
    assert last_result == {}
    assert execution_backend.attempts == 0
    assert state["active_trade"] == {}


def test_tracker_demo_random_trade_waits_after_blocked_expiry_adjustment(tmp_path: Path) -> None:
    execution_backend = _BlockingExecutionBackend()
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
        execution_backend=execution_backend,
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")

    first = tracker.execute_demo_random_trade(str(session["session_id"]), side="BUY", expiry_seconds=180)
    second = tracker.execute_demo_random_trade(str(session["session_id"]), side="BUY", expiry_seconds=180)

    assert first["broker_execution_state"]["status"] == "blocked_by_runtime"
    assert second["broker_execution_state"]["status"] == "blocked_by_runtime"
    assert execution_backend.attempts == 0
    assert second["broker_execution_state"]["retry_block_until"] == ""
    assert "Demo random live execution is disabled" in second["broker_execution_state"]["message"]


def test_broker_execution_state_normalization_clears_expired_demo_trade() -> None:
    state = normalize_broker_execution_state(
        {
            "status": "clicked",
            "message": "Clicked SELL while preserving amount.",
            "active_trade": {
                "side": "SELL",
                "lane": "DEMO_RANDOM_TEST",
                "opened_epoch": 1.0,
                "expires_epoch": 2.0,
                "expiry_seconds": 180,
            },
        }
    )

    assert state["status"] == "expired_unverified"
    assert state["active_trade"] == {}
    assert state["last_result"]["status"] == "expired_unverified"
    assert "Previous demo trade window expired" in state["last_result"]["message"]


def test_tracker_settles_expired_trade_with_chart_proxy_memory(tmp_path: Path) -> None:
    class _WinningPriceTrackingAdapter(_FakeTrackingAdapter):
        def study(self, image: Image.Image, *, session_payload: Mapping[str, Any] | None = None) -> TrackingStudy:
            study = super().study(image, session_payload=session_payload)
            study.tracking_summary["latest_price_proxy"] = 0.56
            study.latest_signal["latest_price_proxy"] = 0.56
            return study

    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_WinningPriceTrackingAdapter("BUY"),
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    now = time.time()
    payload["broker_execution_state"] = {
        "status": "clicked",
        "side": "BUY",
        "lane": "TREND_FOLLOW",
        "expiry_seconds": 180,
        "active_trade": {
            "side": "BUY",
            "lane": "TREND_FOLLOW",
            "amount": "5",
            "opened_epoch": now - 240,
            "expires_epoch": now - 60,
            "expiry_seconds": 180,
            "entry_price_proxy": 0.50,
            "execution_timing": {"recommended_expiry_seconds": 180, "global_extreme_risk": 0.10, "opposing_force_risk": 0.12},
        },
    }
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    result = tracker.capture_once(str(session["session_id"]))

    last_result = result["broker_execution_state"]["last_result"]
    assert last_result["status"] == "won"
    assert last_result["verification"] == "chart_proxy"
    assert last_result["timing_grade"] == "quick_capture"
    assert result["broker_execution_state"]["active_trade"] == {}
    assert (tracker.session_dir(str(session["session_id"])) / "trade_outcomes.jsonl").exists()


def test_tracker_loss_guard_pauses_same_side_after_recent_live_losses(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path / "loss-guard")
    session = tracker.create_session(session_id="pocket-live")
    outcomes_path = tracker.session_dir(str(session["session_id"])) / "trade_outcomes.jsonl"
    loss_rows: list[dict[str, Any]] = [
        {
            "status": "lost",
            "side": "BUY",
            "lane": "LIVE_MARKET_FLOW",
            "resolved_epoch": 9900.0,
        },
        {
            "status": "lost",
            "side": "BUY",
            "lane": "OPPOSING_FORCE_REACTION",
            "resolved_epoch": 9950.0,
        },
    ]
    outcomes_path.write_text(
        "\n".join(json.dumps(row) for row in loss_rows) + "\n",
        encoding="utf-8",
    )

    buy_guard = tracker.recent_live_loss_guard(
        str(session["session_id"]),
        side="BUY",
        lane="LIVE_MARKET_FLOW",
        controls={"loss_guard_enabled": True, "loss_guard_max_consecutive_losses": 2, "loss_guard_pause_sec": 600},
        now_epoch=10000.0,
    )
    sell_guard = tracker.recent_live_loss_guard(
        str(session["session_id"]),
        side="SELL",
        lane="LIVE_MARKET_FLOW",
        controls={"loss_guard_enabled": True, "loss_guard_max_consecutive_losses": 2, "loss_guard_pause_sec": 600},
        now_epoch=10000.0,
    )

    assert buy_guard["accepted"] is False
    assert buy_guard["consecutive_losses"] == 2
    assert sell_guard["accepted"] is True


def test_broker_execution_state_preserves_newer_active_trade_from_concurrent_save() -> None:
    now = time.time()
    candidate: dict[str, Any] = {
        "status": "watching",
        "message": "No executable lane is ready.",
        "active_trade": {},
    }
    persisted: dict[str, Any] = {
            "status": "clicked",
            "message": "Clicked BUY while preserving amount.",
        "last_trade_at": "2026-04-29T10:30:23+05:30",
        "last_trade_epoch": now,
        "cooldown_until_epoch": now + 45,
        "cooldown_until": "2026-04-29T10:31:08+05:30",
        "active_trade": {
            "side": "BUY",
            "lane": "DEMO_RANDOM_TEST",
            "amount": "preserve",
            "opened_epoch": now,
            "expires_epoch": now + 300,
            "expiry_seconds": 300,
        },
        "last_result": {"status": "clicked", "side": "BUY"},
    }

    merged = preserve_newer_active_execution_state(candidate, persisted)

    assert merged["status"] == "monitoring"
    assert merged["active_trade"]["side"] == "BUY"
    assert merged["last_result"]["status"] == "clicked"
    assert merged["cooldown_until_epoch"] == now + 45


def test_tracker_http_demo_random_trade_route_accepts_default_body(tmp_path: Path) -> None:
    execution_backend = _FakeExecutionBackend()
    tracker_service = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
        execution_backend=execution_backend,
    )
    app = create_app(window_tracker_service=tracker_service)
    client = TestClient(app)
    create_response = client.post(
        "/v1/mobile/window-tracker/sessions",
        json={"session_id": "pocket-live", "window_query": "Pocket Option"},
    )
    assert create_response.status_code == 201
    session_id = create_response.json()["session_id"]
    focus_response = client.put(
        f"/v1/mobile/window-tracker/sessions/{session_id}/focus-region",
        json={"normalized_bbox": [0.0, 0.0, 1.0, 1.0], "source": "test"},
    )
    assert focus_response.status_code == 200

    trade_response = client.post(f"/v1/mobile/window-tracker/sessions/{session_id}/demo-random-trade")

    assert trade_response.status_code == 200
    assert execution_backend.clicks == []
    assert trade_response.json()["broker_execution_state"]["status"] == "blocked_by_runtime"


def test_tracker_fuses_broker_identity_before_live_execution_gate(tmp_path: Path) -> None:
    execution_backend = _IdentityExecutionBackend()
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
        execution_backend=execution_backend,
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    controls = dict(payload["execution_controls"])
    controls.update(
        {
            "live_execution_enabled": True,
            "execution_mode": "live",
            "require_memory_projection": False,
            "require_market_identity": True,
        }
    )
    payload["execution_controls"] = controls
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    result = tracker.capture_once(str(session["session_id"]))

    assert execution_backend.clicks == []
    assert result["tracking_summary"]["detected_market"] == "GBP/AUD OTC"
    assert result["tracking_summary"]["detected_timeframe"] == "M5"
    assert result["latest_signal"]["market"] == "GBP/AUD OTC"
    assert result["broker_execution_state"]["status"] == "blocked_by_runtime"
    assert "Model Council V3 executable packet required" in result["broker_execution_state"]["message"]


def test_tracker_reuses_broker_identity_when_livepacket_is_not_executable(tmp_path: Path) -> None:
    execution_backend = _CountingIdentityExecutionBackend()
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window(), _synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
        execution_backend=execution_backend,
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    controls = dict(payload["execution_controls"])
    controls.update(
        {
            "live_execution_enabled": True,
            "execution_mode": "live",
            "require_memory_projection": False,
            "require_market_identity": True,
            "broker_surface_cache_sec": 30.0,
        }
    )
    payload["execution_controls"] = controls
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    first = tracker.capture_once(str(session["session_id"]))
    second = tracker.capture_once(str(session["session_id"]))

    assert execution_backend.clicks == []
    assert execution_backend.read_count == 1
    assert first["tracking_summary"]["detected_market"] == "GBP/AUD OTC"
    assert second["broker_surface"]["cached"] is True
    assert second["broker_surface"]["scan_skipped"] is True
    assert second["broker_surface"]["scan_skip_reason"] == "cached_identity_model_council_packet_not_executable"
    assert second["tracking_summary"]["detected_market"] == "GBP/AUD OTC"
    assert second["broker_execution_state"]["status"] == "blocked_by_runtime"


def test_tracker_live_execution_does_not_require_timeframe_ocr_by_default(tmp_path: Path) -> None:
    execution_backend = _IdentityExecutionBackend(timeframe="", timeframe_confidence=0.0)
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY", timeframe="", timeframe_confidence=0.0),
        execution_backend=execution_backend,
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    controls = dict(payload["execution_controls"])
    controls.update(
        {
            "live_execution_enabled": True,
            "execution_mode": "live",
            "require_memory_projection": False,
            "require_market_identity": True,
            "require_timeframe_identity": False,
        }
    )
    payload["execution_controls"] = controls
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    result = tracker.capture_once(str(session["session_id"]))

    assert execution_backend.clicks == []
    assert result["broker_execution_state"]["status"] == "blocked_by_runtime"
    assert result["tracking_summary"]["timeframe_confidence"] == 0.0


def test_tracker_blocks_live_execution_when_timeframe_identity_is_required(tmp_path: Path) -> None:
    execution_backend = _IdentityExecutionBackend(timeframe="", timeframe_confidence=0.0)
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY", timeframe="", timeframe_confidence=0.0),
        execution_backend=execution_backend,
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    controls = dict(payload["execution_controls"])
    controls.update(
        {
            "live_execution_enabled": True,
            "execution_mode": "live",
            "require_memory_projection": False,
            "require_market_identity": True,
            "require_timeframe_identity": True,
            "min_timeframe_confidence": 0.42,
        }
    )
    payload["execution_controls"] = controls
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    result = tracker.capture_once(str(session["session_id"]))

    assert execution_backend.clicks == []
    assert result["broker_execution_state"]["status"] == "blocked_by_runtime"
    assert "Model Council V3 executable packet required" in result["broker_execution_state"]["message"]


def test_tracker_blocks_locked_surface_identity_fallback_for_live_execution(tmp_path: Path) -> None:
    execution_backend = _IdentityExecutionBackend(market="", timeframe="", market_confidence=0.0, timeframe_confidence=0.0)
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
        execution_backend=execution_backend,
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    controls = dict(payload["execution_controls"])
    controls.update(
        {
            "live_execution_enabled": True,
            "execution_mode": "live",
            "require_memory_projection": False,
            "require_market_identity": True,
            "require_timeframe_identity": True,
            "allow_locked_surface_identity_fallback": True,
            "swing_fallback_enabled": True,
        }
    )
    payload["execution_controls"] = controls
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    result = tracker.capture_once(str(session["session_id"]))

    assert execution_backend.clicks == []
    assert result["broker_execution_state"]["status"] == "blocked_by_runtime"
    assert "Model Council V3 executable packet required" in result["broker_execution_state"]["message"]
    assert "model_council_packet" not in result or result["model_council_packet"].get("schema_version") != "PG_EXECUTION_PACKET_V3"


def test_tracker_live_execution_waits_after_blocked_click_attempt(tmp_path: Path) -> None:
    execution_backend = _BlockingExecutionBackend()
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window(), _synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
        execution_backend=execution_backend,
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    controls = dict(payload["execution_controls"])
    controls.update(
        {
            "live_execution_enabled": True,
            "execution_mode": "live",
            "require_memory_projection": False,
            "require_market_identity": False,
        }
    )
    payload["execution_controls"] = controls
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    first = tracker.capture_once(str(session["session_id"]))
    _allow_next_capture(tracker, str(session["session_id"]))
    second = tracker.capture_once(str(session["session_id"]))

    assert first["broker_execution_state"]["status"] == "blocked_by_runtime"
    assert second["broker_execution_state"]["status"] == "blocked_by_runtime"
    assert execution_backend.attempts == 0
    assert second["broker_execution_state"]["retry_block_until"] == ""


def test_tracker_live_execution_ignores_expired_demo_test_cooldown(tmp_path: Path) -> None:
    execution_backend = _FakeExecutionBackend()
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
        execution_backend=execution_backend,
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    controls = dict(payload["execution_controls"])
    controls.update(
        {
            "live_execution_enabled": True,
            "execution_mode": "live",
            "require_memory_projection": False,
            "require_market_identity": False,
        }
    )
    state = normalize_broker_execution_state(payload["broker_execution_state"])
    state["cooldown_until_epoch"] = time.time() + 600.0
    state["cooldown_until"] = "future-demo-cooldown"
    state["last_result"] = {
        "status": "expired_unverified",
        "trade": {"side": "SELL", "lane": "DEMO_RANDOM_TEST", "expiry_seconds": 180},
    }
    state["active_trade"] = {}
    payload["execution_controls"] = controls
    payload["broker_execution_state"] = state
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    result = tracker.capture_once(str(session["session_id"]))

    assert execution_backend.clicks == []
    assert result["broker_execution_state"]["status"] == "blocked_by_runtime"
    assert result["broker_execution_state"]["side"] == "BUY"


def test_tracker_blocks_live_execution_when_broker_identity_is_weak(tmp_path: Path) -> None:
    execution_backend = _IdentityExecutionBackend(market_confidence=0.12, timeframe_confidence=0.88)
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
        execution_backend=execution_backend,
    )
    session = tracker.create_session(session_id="pocket-live")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    controls = dict(payload["execution_controls"])
    controls.update(
        {
            "live_execution_enabled": True,
            "execution_mode": "live",
            "require_memory_projection": False,
            "require_market_identity": True,
            "min_market_confidence": 0.42,
        }
    )
    payload["execution_controls"] = controls
    write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    result = tracker.capture_once(str(session["session_id"]))

    assert execution_backend.clicks == []
    assert result["broker_execution_state"]["status"] == "blocked_by_runtime"
    assert "Model Council V3 executable packet required" in result["broker_execution_state"]["message"]


def test_window_tracker_atomic_writer_handles_concurrent_updates(tmp_path: Path) -> None:
    target_path = tmp_path / "session.json"

    def _writer(worker_id: int) -> None:
        for sequence in range(40):
            write_json_atomic(
                target_path,
                {
                    "worker_id": worker_id,
                    "sequence": sequence,
                    "updated_at": f"2026-04-23T10:{worker_id:02d}:{sequence:02d}+00:00",
                },
            )

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = [executor.submit(_writer, worker_id) for worker_id in range(6)]
        for future in futures:
            future.result()

    payload = json.loads(target_path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    assert "worker_id" in payload
    assert "sequence" in payload


def test_real_tracking_adapter_reads_buy_pressure_from_uptrend_surface() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()

    result = adapter.study(_synthetic_chart_surface("buy"))

    assert result.tracking_summary["chart_valid"] is True
    assert int(result.tracking_summary["visible_candle_count"]) >= 8
    history = cast(Sequence[Mapping[str, Any]], result.tracking_summary["historical_structure"])
    assert len(history) >= 2
    assert all("bbox" in segment for segment in history)
    assert all(len(cast(Sequence[Any], segment.get("line_points", []))) >= 2 for segment in history)
    assert all("path_bounds" in segment for segment in history)
    assert min(float(cast(Sequence[Any], segment["bbox"])[0]) for segment in history) < result.chart_image.width * 0.45
    assert max(float(cast(Sequence[Any], segment["bbox"])[2]) for segment in history) > result.chart_image.width * 0.55
    assert result.latest_signal["action"] == "BUY"
    assert "BUY" in str(result.latest_signal["setup"])


def test_real_tracking_adapter_reuses_cached_locked_shadow_selectors(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    image = _synthetic_chart_surface("buy")
    session_payload: dict[str, Any] = {
        "execution_controls": {"live_execution_enabled": False, "execution_mode": "shadow"},
        "manual_focus_region": {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]},
        "locked_window": {"hwnd": 123, "title": "Pocket Option"},
        "tracking_summary": {
            "detected_timeframe": "M5",
            "timeframe_confidence": 0.93,
            "detected_market": "EUR/JPY OTC",
            "market_confidence": 0.91,
            "chart_region": {"pixel_bbox": [0, 0, image.width, image.height], "confidence": 0.90},
        },
        "latest_signal": {
            "focus_timeframe": "M5",
            "focus_timeframe_confidence": 0.93,
            "market": "EUR/JPY OTC",
            "market_confidence": 0.91,
        },
    }
    warmup = adapter.study(image, session_payload=session_payload)
    selector_fingerprint = str(warmup.tracking_summary.get("market_selector_visual_fingerprint", ""))
    assert selector_fingerprint
    tracking_summary = cast(dict[str, Any], session_payload["tracking_summary"])
    latest_signal = cast(dict[str, Any], session_payload["latest_signal"])
    tracking_summary["market_selector_visual_fingerprint"] = selector_fingerprint
    latest_signal["market_selector_visual_fingerprint"] = selector_fingerprint

    def fail_detector(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("cached locked shadow study must not rescan selectors")

    monkeypatch.setattr(adapter, "_detect_timeframe_selector", fail_detector)
    monkeypatch.setattr(adapter, "_detect_market_selector", fail_detector)
    monkeypatch.setattr(adapter, "_detect_chart_bbox", fail_detector)

    result = adapter.study(
        image,
        session_payload=session_payload,
    )

    stages = [str(row.get("stage", "")) for row in result.tracking_summary["study_stage_timings"]]
    assert "cached_chart_bbox" in stages
    assert result.tracking_summary["detected_timeframe"] == "M5"
    assert result.latest_signal["market"] == "EUR/JPY OTC"


def test_real_tracking_adapter_pair_switch_fast_rebind_skips_slow_market_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    image = _synthetic_chart_surface("buy")
    session_payload: dict[str, Any] = {
        "execution_controls": {"live_execution_enabled": False, "execution_mode": "shadow"},
        "manual_focus_region": {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]},
        "locked_window": {"hwnd": 123, "title": "Pocket Option"},
        "tracking_summary": {
            "detected_timeframe": "M5",
            "timeframe_confidence": 0.93,
            "detected_market": "EUR/JPY OTC",
            "market_confidence": 0.91,
            "market_selector_visual_fingerprint": "previous-pair",
            "chart_region": {"pixel_bbox": [0, 0, image.width, image.height], "confidence": 0.90},
        },
        "latest_signal": {
            "focus_timeframe": "M5",
            "focus_timeframe_confidence": 0.93,
            "market": "EUR/JPY OTC",
            "market_confidence": 0.91,
            "market_selector_visual_fingerprint": "previous-pair",
        },
    }

    def fail_market_detector(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("pair-switch fast rebind must not block on slow market OCR")

    monkeypatch.setattr(adapter, "_detect_market_selector", fail_market_detector)

    result = adapter.study(image, session_payload=session_payload)

    stages = [str(row.get("stage", "")) for row in result.tracking_summary["study_stage_timings"]]
    assert "cached_chart_bbox" in stages
    assert result.tracking_summary["market_selector_visual_changed"] is True
    assert result.tracking_summary["market_selector_rebind_required"] is True
    assert result.tracking_summary["market_selector_studying_new_pair"] is True
    assert result.latest_signal["market_selector_studying_new_pair"] is True


def test_real_tracking_adapter_unknown_market_fast_locked_context_skips_slow_market_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    image = _synthetic_chart_surface("buy")
    selector_fingerprint = str(
        getattr(window_tracker_module, "_market_selector_visual_fingerprint")(image)
    )
    session_payload: dict[str, Any] = {
        "execution_controls": {"live_execution_enabled": False, "execution_mode": "shadow"},
        "manual_focus_region": {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]},
        "locked_window": {"hwnd": 123, "title": "Pocket Option"},
        "tracking_summary": {
            "detected_timeframe": "M5",
            "timeframe_confidence": 0.93,
            "detected_market": "",
            "market_selector_visual_fingerprint": selector_fingerprint,
            "chart_region": {"pixel_bbox": [0, 0, image.width, image.height], "confidence": 0.90},
        },
        "latest_signal": {
            "focus_timeframe": "M5",
            "focus_timeframe_confidence": 0.93,
            "market": "",
            "market_selector_visual_fingerprint": selector_fingerprint,
        },
    }

    def fail_market_detector(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("unknown locked market must not block on slow market OCR")

    monkeypatch.setattr(adapter, "_detect_market_selector", fail_market_detector)

    result = adapter.study(image, session_payload=session_payload)

    stages = [str(row.get("stage", "")) for row in result.tracking_summary["study_stage_timings"]]
    assert "cached_chart_bbox" in stages
    assert result.tracking_summary["market_source"] == "selector_skipped_fast_locked_context"
    assert result.tracking_summary["market_selector_rebind_required"] is False
    assert result.latest_signal["market"] == ""


def test_real_tracking_adapter_excludes_broker_order_panel_from_chart_bbox() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    image = _synthetic_full_pocket_option_gui()

    result = adapter.study(image)

    chart_bbox = cast(Sequence[Any], result.tracking_summary["chart_region"]["pixel_bbox"])
    assert int(chart_bbox[2]) < int(image.width * 0.82)
    assert result.tracking_summary["chart_valid"] is True
    assert int(result.tracking_summary["visible_candle_count"]) >= 8


def test_real_tracking_adapter_reads_sell_pressure_from_downtrend_surface() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()

    result = adapter.study(_synthetic_chart_surface("sell"))

    assert result.tracking_summary["chart_valid"] is True
    assert int(result.tracking_summary["visible_candle_count"]) >= 8
    assert result.latest_signal["action"] == "SELL"
    assert "SELL" in str(result.latest_signal["setup"])


def test_real_tracking_adapter_holds_on_blank_surface() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()

    result = adapter.study(_surface())

    assert result.tracking_summary["chart_valid"] is False
    assert result.latest_signal["action"] == "HOLD"
    assert result.latest_signal["status"] == "warming"
