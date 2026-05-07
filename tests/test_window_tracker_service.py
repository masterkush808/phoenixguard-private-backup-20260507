from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import time
import ctypes
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Sequence, cast

from fastapi.testclient import TestClient
import numpy as np
from PIL import Image, ImageDraw
import pytest

import phoenixguard.mobile_api.window_tracker as window_tracker_module
from phoenixguard.memory.memory_ingest import MemoryEntry
from phoenixguard.mobile_api.app import create_app
from phoenixguard.mobile_api.window_tracker import (
    ContinuousWindowTrackerService,
    PocketOptionBrokerExecutionBackend,
    PhoenixGuardWindowTrackingAdapter,
    TrackingStudy,
    WindowsWindowCaptureBackend,
    _normalize_broker_execution_state,
    _preserve_newer_active_execution_state,
    _write_json_atomic,
)


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

    def embed_description(self, chart_state: Mapping[str, Any], image: Image.Image | None = None) -> np.ndarray:
        _ = chart_state
        _ = image
        return np.asarray(_memory_embed(216), dtype=np.float32)

    def search(
        self,
        query_embed: np.ndarray,
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
    ) -> dict[str, Any]:
        payload = {
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
            "raw_text": self.market,
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


def _test_popup_visual_payload(
    backend: PocketOptionBrokerExecutionBackend,
    image: Image.Image,
    time_field: Mapping[str, Any],
) -> dict[str, Any]:
    popup_controls = backend._expiry_popup_control_points(time_field)
    popup_locks = {
        name: backend._control_lock(
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
    ) -> dict[str, Any]:
        _ = descriptor
        _ = window_image
        _ = broker_surface
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
        tracking_summary = {
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
            "detected_timeframe": self.timeframe,
            "timeframe_source": "synthetic",
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
        }
        latest_signal = {
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
            "focus_timeframe": self.timeframe,
            "focus_timeframe_source": "synthetic",
            "market": "",
            "execution_permission": "EXECUTE",
            "actionable": True,
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
    _write_json_atomic(tracker.session_dir(session_id) / "session.json", payload)


def _allow_next_capture(tracker: ContinuousWindowTrackerService, session_id: str) -> None:
    last_capture_time = getattr(tracker, "_last_capture_time", None)
    if isinstance(last_capture_time, dict):
        last_capture_time.pop(session_id, None)


def test_windows_capture_backend_prefers_printwindow_for_pocket_option_browser_windows(monkeypatch: Any) -> None:
    backend = WindowsWindowCaptureBackend()
    calls: list[str] = []
    browser_image = Image.new("RGB", (120, 80), color=(20, 30, 40))

    def capture_with_imagegrab(descriptor: Mapping[str, Any]) -> Image.Image:
        calls.append(f"grab:{descriptor.get('title', '')}")
        return browser_image.copy()

    def capture_with_printwindow(hwnd: int, descriptor: Mapping[str, Any]) -> Image.Image:
        _ = descriptor
        calls.append(f"print:{hwnd}")
        return Image.new("RGB", (120, 80), color=(90, 100, 110))

    monkeypatch.setattr(backend, "_is_windows", lambda: True)
    monkeypatch.setattr(backend, "_capture_window_imagegrab", capture_with_imagegrab)
    monkeypatch.setattr(backend, "_capture_window_printwindow", capture_with_printwindow)

    captured = backend.capture_window(
        {
            "hwnd": 101,
            "title": "The Most Innovative Trading Platform - Microsoft Edge",
            "bbox": [0, 0, 120, 80],
        }
    )

    assert captured.size == (120, 80)
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


def test_window_tracker_blocks_new_trigger_when_target_zone_is_already_reached() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    candles = _manual_candle_tracks(
        [220, 198, 176, 154, 132, 112],
        image_width=620,
        image_height=420,
        direction="BUY",
        half_height=18,
    )
    projection = {
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


def test_window_tracker_expiry_uses_timeframe_multiplied_target_candles() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    expiry_for_swing = cast(Callable[..., int], getattr(adapter, "_execution_expiry_seconds"))(
        {"focus_timeframe": "M5"},
        {
            "detected_timeframe": "M5",
            "decision_kernel": {
                "hold_for_candles": 2,
                "eta_target_after_trigger_candles": 15,
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
                "eta_invalidation_candles": 5,
                "p_target_before_invalidation": 0.55,
            },
        },
        lane="COUNTERTREND_SCALP",
    )

    assert expiry_for_swing == 15 * 5 * 60
    assert expiry_for_scalp == 3 * 5 * 60


def test_window_tracker_rejects_garbled_broker_market_text() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    normalize = cast(Callable[[str], str], getattr(adapter, "_normalize_market_candidate"))

    assert normalize("AUD/CHF OTC") == "AUD/CHF OTC"
    assert normalize("GBPJPY OTC") == "GBP/JPY OTC"
    assert normalize("W D0CR01ILJI . /JFW1 P IY W P 1") == ""


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

    build_payloads = cast(
        Callable[
            [Image.Image, Mapping[str, Any], Sequence[Mapping[str, Any]], Mapping[str, Any]],
            tuple[dict[str, Any], dict[str, Any]],
        ],
        getattr(adapter, "_build_signal_payloads"),
    )

    tracking, signal = build_payloads(
        chart,
        {"confidence": 1.0, "pixel_bbox": [0, 0, chart.width, chart.height], "normalized_bbox": [0.0, 0.0, 1.0, 1.0], "width": chart.width, "height": chart.height},
        candles,
        {"value": "M5", "source": "test", "confidence": 1.0},
        market_selector={"value": "GBP/JPY OTC", "source": "header_text", "confidence": 0.91},
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
    monkeypatch.setattr(
        adapter,
        "_detect_market_selector",
        lambda image, timeframe_selector=None: {"value": "GBP/JPY OTC", "source": "header_text", "confidence": 0.92},
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
    projection = {
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
    projection = {
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
    projection = {
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
    top_noise = [
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
    derive_zones = cast(
        Callable[[Sequence[Mapping[str, Any]], tuple[int, int]], list[dict[str, Any]]],
        lambda candles, size: adapter._derive_support_resistance_zones(  # noqa: SLF001
            candles,
            size,
            candidate_action="BUY",
        ),
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
    assert any(bool(zone.get("nearest")) for zone in zones)


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
    assert float(payload["latest_signal"]["countdown_seconds"]) >= 0.0
    assert payload["tracking_summary"]["visible_candle_count"] == 12
    assert Path(str(payload["last_window_path"])).exists()
    assert Path(str(payload["last_chart_path"])).exists()
    assert Path(str(payload["last_overlay_path"])).exists()
    assert Path(str(payload["last_full_overlay_path"])).exists()
    assert Path(str(payload["last_decision_path"])).exists()
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

    tracker._capture_and_analyze(session_id)
    first = tracker.get_session(session_id)
    tracker._capture_and_analyze(session_id)
    duplicate = tracker.get_session(session_id)
    clock["now"] += 0.201
    tracker._capture_and_analyze(session_id)
    after_floor = tracker.get_session(session_id)

    assert int(first["capture_count"]) == 1
    assert int(duplicate["capture_count"]) == 1
    assert int(after_floor["capture_count"]) == 2
    assert backend.capture_calls == 2


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
    assert payload["broker_surface"]["control_visibility"]["buy_visible"] is True
    with Image.open(str(payload["last_chart_path"])) as chart_image:
        assert chart_image.size == (study_plane["width"], study_plane["height"])
    with Image.open(str(payload["last_full_overlay_path"])) as full_overlay_image:
        assert full_overlay_image.size == full_gui.size


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
    _write_json_atomic(tracker.session_dir(session_id) / "session.json", payload)
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
        _write_json_atomic(tracker.session_dir(captured_session_id) / "session.json", payload)
        if next_count >= 2:
            stop_evt.set()

    monkeypatch.setattr(tracker, "_capture_and_analyze", capture_stub)
    monkeypatch.setattr(
        tracker,
        "_adaptive_capture_interval_plan",
        lambda _payload: {"interval_sec": 0.5, "reason": "entry_ready"},
    )
    monkeypatch.setattr(window_tracker_module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(window_tracker_module.time, "time", lambda: clock["now"])

    tracker._worker_loop(session_id, stop_evt, capture_now_evt)
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

    overlay_response = client.get(f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-overlay")
    assert overlay_response.status_code == 200
    assert overlay_response.headers["content-type"].startswith("image/png")

    full_overlay_response = client.get(f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-full-overlay")
    assert full_overlay_response.status_code == 200
    assert full_overlay_response.headers["content-type"].startswith("image/png")

    dashboard_response = client.get(f"/v1/mobile/window-tracker/dashboard/{session_id}")
    assert dashboard_response.status_code == 200
    assert "Locked Broker Surface Tracker" in dashboard_response.text


def test_tracker_dashboard_prediction_images_use_uncropped_full_width_layout() -> None:
    dashboard_html = (
        Path(__file__).resolve().parents[1]
        / "phoenixguard"
        / "mobile_api"
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
        Path(__file__).resolve().parents[1]
        / "phoenixguard"
        / "mobile_api"
        / "static"
        / "window_tracker_dashboard.html"
    ).read_text(encoding="utf-8")

    assert "function surfaceFitViewportHeight()" in dashboard_html
    assert "stageHeight / size.height" in dashboard_html
    assert "stageWidth / size.width" in dashboard_html
    assert "full selected plane fitted" in dashboard_html
    assert "full broker window fitted" in dashboard_html
    assert "artifactUrl(\"full-overlay\")" in dashboard_html
    assert "state.surfaceUsesFullOverlay = true" in dashboard_html
    assert "else if (wantsOverlay && hasChart)" in dashboard_html


def test_memory_precision_allows_aggressive_stacked_primary_when_counter_is_probe() -> None:
    primary_fit = {
        "top_matches": [
            {"similarity": 0.83, "precision_score": 0.70},
            {"similarity": 0.80, "precision_score": 0.69},
            {"similarity": 0.79, "precision_score": 0.68},
        ],
        "high_precision_count": 1,
    }
    counter_fit = {
        "top_matches": [{"similarity": 0.87, "precision_score": 0.62}],
        "transition_bias": {
            "continue": 0.52,
            "pullback": 0.18,
            "reversal_attempt": 0.28,
            "fakeout": 0.02,
        },
    }

    precision = PhoenixGuardWindowTrackingAdapter._memory_precision_payload(primary_fit, counter_fit)

    assert precision["accepted"] is True
    assert precision["accepted_by"] == "stacked_favor"
    assert precision["quality"] == "aggressive_stacked"
    assert precision["counter_behavior"]["hard_counter_risk"] is False
    assert float(precision["precision_edge"]) >= 0.08


def test_live_dashboard_launcher_keeps_voice_bridge_opt_in() -> None:
    launcher = (Path(__file__).resolve().parents[1] / "start_live_dashboard.ps1").read_text(encoding="utf-8")

    assert "[switch]$EnableVoiceControl" in launcher
    assert "$voiceControlEnabled = [bool]$EnableVoiceControl -and -not [bool]$NoVoiceControl" in launcher
    assert "if (-not $NoVoiceControl)" not in launcher
    assert "Start-VoiceBridge -BindHost" in launcher


def test_tracker_http_surface_runs_memory_projection_actions(tmp_path: Path, monkeypatch: Any) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    entries = _materialize_memory_images(tmp_path / "memory-images", _sample_memory_entries())
    monkeypatch.setattr(adapter, "_get_phoenixguard_memory_bank", lambda: _StubPhoenixBank(entries))
    monkeypatch.setattr(
        adapter,
        "_detect_market_selector",
        lambda image, timeframe_selector=None: {"value": "GBP/JPY OTC", "source": "header_text", "confidence": 0.90},
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
    assert float(payload["execution_controls"]["cooldown_sec"]) == 12.0


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

    tracker.tracking_adapter = _BlockingTrackingAdapter("BUY")
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
    assert payload["amount_lock"]["required"] == "5"
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
    popup_controls = backend._expiry_popup_control_points(cast(Mapping[str, Any], broker_surface["time_field"]))
    popup_locks = {
        name: backend._control_lock(
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
    monkeypatch.setattr(
        backend,
        "_expiry_popup_visual_control_points",
        lambda **_kwargs: {
            "controls": popup_controls,
            "execution_boxes": popup_locks,
            "geometry": {"source": "test_visual_popup_grid"},
        },
    )
    monkeypatch.setattr(
        backend,
        "_verify_expiry_popup_target",
        lambda **kwargs: {
            "status": "verified",
            "matches": True,
            "target_seconds": kwargs["target_seconds"],
            "visible_seconds": kwargs["target_seconds"],
            "visible_text": backend._format_expiry_text(kwargs["target_seconds"]),
            "confidence": 1.0,
            "source": "test_timer",
        },
    )
    monkeypatch.setattr(
        backend,
        "_verify_trade_click_result",
        lambda **kwargs: {
            "status": "confirmed",
            "confirmed": True,
            "side": kwargs["side"],
            "expiry_seconds": kwargs["expiry_seconds"],
            "message": "confirmed by test",
        },
    )

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
    assert fake_user32.cursor_points[-2:] == [result["amount_point"], result["button_point"]]
    assert result["amount_commit"]["sent_enter"] is True
    assert result["amount_commit"]["sent_escape"] is True
    assert (0x0D, 0) in fake_user32.key_events
    assert (0x1B, 0) in fake_user32.key_events
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
    monkeypatch.setattr(backend, "_expiry_popup_visual_control_points", lambda **_kwargs: {})
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
    monkeypatch.setattr(
        backend,
        "_expiry_popup_visual_control_points",
        lambda **_kwargs: _test_popup_visual_payload(
            backend,
            window_image,
            cast(Mapping[str, Any], broker_surface["time_field"]),
        ),
    )
    monkeypatch.setattr(
        backend,
        "_verify_expiry_popup_target",
        lambda **kwargs: {
            "status": "verified",
            "matches": True,
            "target_seconds": kwargs["target_seconds"],
            "visible_seconds": kwargs["target_seconds"],
            "visible_text": backend._format_expiry_text(kwargs["target_seconds"]),
            "confidence": 1.0,
            "source": "test_timer",
        },
    )
    monkeypatch.setattr(
        backend,
        "_verify_trade_click_result",
        lambda **kwargs: {
            "status": "unverified",
            "confirmed": False,
            "side": kwargs["side"],
            "expiry_seconds": kwargs["expiry_seconds"],
            "message": "no accepted-trade cue",
        },
    )

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
        PocketOptionBrokerExecutionBackend._click_screen_point(
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
    fallback = backend._expiry_popup_control_points({"bbox": [1636, 190, 1794, 255]})
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
        visual = backend._expiry_popup_visual_control_points(
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
    plan_from_h4 = PocketOptionBrokerExecutionBackend._expiry_popup_click_plan(14400, 120)
    plan_from_m30 = PocketOptionBrokerExecutionBackend._expiry_popup_click_plan(1800, 120)
    plan_from_m1 = PocketOptionBrokerExecutionBackend._expiry_popup_click_plan(60, 120)
    plan_from_h2_to_m3 = PocketOptionBrokerExecutionBackend._expiry_popup_click_plan(7200, 180)
    plan_from_m3_to_m3 = PocketOptionBrokerExecutionBackend._expiry_popup_click_plan(180, 180)

    assert plan_from_h4 == ["quick_m1", "minute_plus"]
    assert plan_from_m30 == ["quick_m1", "minute_plus"]
    assert plan_from_m1 == ["minute_plus"]
    assert plan_from_h2_to_m3 == ["quick_m3"]
    assert plan_from_m3_to_m3 == []


def test_pocket_option_expiry_plan_resets_seconds_state_through_minute_shortcut() -> None:
    plan = PocketOptionBrokerExecutionBackend._expiry_popup_click_plan(30, 600)

    assert plan[0] == "quick_m5"
    assert plan.count("minute_plus") == 5
    assert PocketOptionBrokerExecutionBackend._format_expiry_text("00:03:05") == "00:03:05"


def test_pocket_option_expiry_plan_supports_exact_second_controls() -> None:
    assert PocketOptionBrokerExecutionBackend._expiry_popup_click_plan(15, 3) == ["quick_s3"]
    assert PocketOptionBrokerExecutionBackend._expiry_popup_click_plan(15, 15) == []
    plan_to_45 = PocketOptionBrokerExecutionBackend._expiry_popup_click_plan(15, 45)
    assert any(step.startswith("quick_s") or step.startswith("second_") for step in plan_to_45)
    assert PocketOptionBrokerExecutionBackend._expiry_popup_click_plan(15, 75) == ["minute_plus"]


def test_pocket_option_expiry_plan_uses_nearest_minute_anchor_for_long_non_preset() -> None:
    plan = PocketOptionBrokerExecutionBackend._expiry_popup_click_plan(60, 1200)

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
    assert controls["fixed_amount"] == "5"
    assert controls["allow_countertrend_scalp"] is False
    assert float(session["capture_interval_sec"]) == 3.0
    assert float(controls["min_capture_interval_sec"]) == 0.5
    assert float(controls["max_capture_interval_sec"]) == 10.0

    updated = tracker.update_session_controls(
        str(session["session_id"]),
        live_execution_enabled=True,
        execution_mode="live",
        allow_countertrend_scalp=False,
    )

    assert updated["execution_controls"]["live_execution_enabled"] is True
    assert updated["execution_controls"]["execution_mode"] == "live"
    assert updated["execution_controls"]["allow_countertrend_scalp"] is False
    assert updated["execution_controls"]["fixed_amount"] == "5"
    assert updated["broker_execution_state"]["status"] == "armed"


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
    _write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

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

    entry_ready = tracker._adaptive_capture_interval_plan(payload)
    assert float(entry_ready["interval_sec"]) == 0.5
    assert entry_ready["reason"] == "entry_ready"

    payload["latest_signal"] = {"action": "BUY", "execution_action": "BUY", "entry_state": "SNIPER_READY"}
    payload["tracking_summary"] = {"decision_kernel": {"state": "ARMED", "p_trigger_next_3": 0.72}}
    sniper_ready = tracker._adaptive_capture_interval_plan(payload)
    assert float(sniper_ready["interval_sec"]) == 0.5

    payload["latest_signal"] = {"action": "HOLD", "execution_action": "HOLD", "entry_state": "WAIT"}
    payload["tracking_summary"] = {"decision_kernel": {"state": "IDLE"}}
    base = tracker._adaptive_capture_interval_plan(payload)
    assert float(base["interval_sec"]) == 3.0
    assert base["reason"] == "base_timer"

    payload["capture_interval_sec"] = 30.0
    capped = tracker._adaptive_capture_interval_plan(payload)
    assert float(capped["interval_sec"]) == 10.0
    assert capped["reason"] == "base_timer"

    payload["execution_controls"] = {"adaptive_timer_enabled": False}
    fixed = tracker._adaptive_capture_interval_plan(payload)
    assert float(fixed["interval_sec"]) == 30.0
    assert fixed["reason"] == "fixed_timer"


def test_tracker_execution_throttle_blocks_after_five_clicks_per_window(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    state: dict[str, Any] = {}
    controls = {"max_executions_per_window": 5, "execution_window_sec": 300.0}
    now = 1000.0

    for _index in range(5):
        allowed, _message = tracker._execution_throttle_allows(state, controls, now_epoch=now)
        assert allowed is True
        tracker._record_execution_throttle(state, controls, now_epoch=now)
        now += 10.0

    allowed, message = tracker._execution_throttle_allows(state, controls, now_epoch=now)
    assert allowed is False
    assert "5/5" in message

    allowed_after_reset, _message_after_reset = tracker._execution_throttle_allows(state, controls, now_epoch=1301.0)
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
    _write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    result = tracker.capture_once(str(session["session_id"]))

    assert len(execution_backend.clicks) == 1
    assert execution_backend.clicks[0]["side"] == "BUY"
    assert execution_backend.clicks[0]["amount"] == "5"
    assert result["broker_execution_state"]["status"] == "clicked"
    assert result["broker_execution_state"]["active_trade"]["amount"] == "5"


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
    _write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    result = tracker.capture_once(str(session["session_id"]))

    state = cast(dict[str, Any], result["broker_execution_state"])
    last_result = cast(dict[str, Any], state["last_result"])
    assert state["status"] == "blocked"
    assert "Expiry verification blocked" in state["message"]
    assert last_result["expiry_verification"]["visible_text"] == "00:00:30"
    assert state["active_trade"] == {}


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
    _write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    result = tracker.capture_once(str(session["session_id"]))

    assert len(execution_backend.clicks) == 1
    assert execution_backend.clicks[0]["window_size"] == [full_gui.width, full_gui.height]
    assert result["broker_execution_state"]["status"] == "clicked"
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

    result = tracker.execute_demo_random_trade(str(session["session_id"]), side="SELL", expiry_seconds=180)

    assert len(execution_backend.clicks) == 1
    assert execution_backend.clicks[0]["side"] == "SELL"
    assert execution_backend.clicks[0]["amount"] == "5"
    assert execution_backend.clicks[0]["expiry_seconds"] == 180
    assert result["broker_execution_state"]["status"] == "clicked"
    assert result["broker_execution_state"]["active_trade"]["lane"] == "DEMO_RANDOM_TEST"
    assert result["broker_surface"]["expiry_lock"]["configured_text"] == "00:03:00"


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
    assert state["status"] == "blocked"
    assert last_result["status"] == "blocked"
    assert last_result["expiry_verification"]["visible_seconds"] == 30
    assert last_result["expiry_popup_geometry"]["source"] == "visual_popup_shortcut_grid"
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

    assert first["broker_execution_state"]["status"] == "blocked"
    assert second["broker_execution_state"]["status"] == "retry_wait"
    assert execution_backend.attempts == 1
    assert second["broker_execution_state"]["retry_block_until"]
    assert "cooling down" in second["broker_execution_state"]["message"]


def test_broker_execution_state_normalization_clears_expired_demo_trade() -> None:
    state = _normalize_broker_execution_state(
        {
            "status": "clicked",
            "message": "Clicked SELL with fixed $5.",
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


def test_broker_execution_state_preserves_newer_active_trade_from_concurrent_save() -> None:
    now = time.time()
    candidate = {
        "status": "watching",
        "message": "No executable lane is ready.",
        "active_trade": {},
    }
    persisted = {
        "status": "clicked",
        "message": "Clicked BUY with fixed $5.",
        "last_trade_at": "2026-04-29T10:30:23+05:30",
        "last_trade_epoch": now,
        "cooldown_until_epoch": now + 45,
        "cooldown_until": "2026-04-29T10:31:08+05:30",
        "active_trade": {
            "side": "BUY",
            "lane": "DEMO_RANDOM_TEST",
            "amount": "5",
            "opened_epoch": now,
            "expires_epoch": now + 300,
            "expiry_seconds": 300,
        },
        "last_result": {"status": "clicked", "side": "BUY"},
    }

    merged = _preserve_newer_active_execution_state(candidate, persisted)

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
    assert len(execution_backend.clicks) == 1
    assert execution_backend.clicks[0]["side"] in {"BUY", "SELL"}
    assert execution_backend.clicks[0]["expiry_seconds"] == 180
    assert trade_response.json()["broker_execution_state"]["status"] == "clicked"


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
    _write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    result = tracker.capture_once(str(session["session_id"]))

    assert len(execution_backend.clicks) == 1
    assert result["tracking_summary"]["detected_market"] == "GBP/AUD OTC"
    assert result["tracking_summary"]["detected_timeframe"] == "M5"
    assert result["latest_signal"]["market"] == "GBP/AUD OTC"
    assert result["broker_execution_state"]["status"] == "clicked"


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
    _write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    result = tracker.capture_once(str(session["session_id"]))

    assert len(execution_backend.clicks) == 1
    assert result["broker_execution_state"]["status"] == "clicked"
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
    _write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    result = tracker.capture_once(str(session["session_id"]))

    assert execution_backend.clicks == []
    assert result["broker_execution_state"]["status"] == "blocked"
    assert "Timeframe identity confidence" in result["broker_execution_state"]["message"]


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
    _write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    first = tracker.capture_once(str(session["session_id"]))
    _allow_next_capture(tracker, str(session["session_id"]))
    second = tracker.capture_once(str(session["session_id"]))

    assert first["broker_execution_state"]["status"] == "blocked"
    assert second["broker_execution_state"]["status"] == "retry_wait"
    assert execution_backend.attempts == 1
    assert second["broker_execution_state"]["retry_block_until"]


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
    _write_json_atomic(tracker.session_dir(str(session["session_id"])) / "session.json", payload)

    _allow_next_capture(tracker, str(session["session_id"]))
    result = tracker.capture_once(str(session["session_id"]))

    assert execution_backend.clicks == []
    assert result["broker_execution_state"]["status"] == "blocked"
    assert "Market identity confidence" in result["broker_execution_state"]["message"]


def test_window_tracker_atomic_writer_handles_concurrent_updates(tmp_path: Path) -> None:
    target_path = tmp_path / "session.json"

    def _writer(worker_id: int) -> None:
        for sequence in range(40):
            _write_json_atomic(
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
    assert min(float(cast(Sequence[Any], segment["bbox"])[0]) for segment in history) < result.chart_image.width * 0.45
    assert max(float(cast(Sequence[Any], segment["bbox"])[2]) for segment in history) > result.chart_image.width * 0.55
    assert result.latest_signal["action"] == "BUY"
    assert "BUY" in str(result.latest_signal["setup"])


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
