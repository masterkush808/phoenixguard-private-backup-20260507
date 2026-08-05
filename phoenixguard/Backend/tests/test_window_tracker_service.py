from __future__ import annotations

import copy
from concurrent.futures import ThreadPoolExecutor
import json
import time
import ctypes
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Iterator, Mapping, Protocol, Sequence, cast

from fastapi.testclient import TestClient
import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageDraw
import pytest

import phoenixguard.mobile_api.window_tracker as window_tracker_module
from phoenixguard.execution.packet_v3 import build_execution_packet_v3
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
from tests.support.v3_packet_samples import complete_sequence_context_v3


@pytest.fixture(autouse=True)
def shutdown_tracker_services_after_test(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[list[ContinuousWindowTrackerService]]:
    services: list[ContinuousWindowTrackerService] = []
    original_init = ContinuousWindowTrackerService.__init__

    def tracked_init(
        service: ContinuousWindowTrackerService,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        original_init(service, *args, **kwargs)
        services.append(service)

    monkeypatch.setattr(ContinuousWindowTrackerService, "__init__", tracked_init)
    yield services
    for service in reversed(services):
        service.shutdown()


def test_market_study_objects_preserve_bounded_relationship_evidence() -> None:
    objects = PhoenixGuardWindowTrackingAdapter._market_study_objects_v3(  # noqa: SLF001
        [
            {
                "object_type": "crowded price area",
                "object_id": "zone-7",
                "direction": "BUY",
                "confidence": 0.84,
                "bounds": [0.2, 0.3, 0.6, 0.7],
                "points": [[0.2, 0.5], [0.6, 0.5], [900, 400]],
                "lifecycle": "ACTIVE",
                "first_seen": 11,
                "last_seen": 19,
                "age_frames": 8,
                "duration_candles": 3,
                "anchor_candle_id": "close-19",
                "bbox": [200, 300, 600, 700],
            }
        ]
    )

    assert objects == [
        {
            "object_type": "CROWDED_PRICE_AREA",
            "object_id": "zone-7",
            "identity_scope": "OBSERVATION_ONLY",
            "identity_stable": False,
            "direction": "BUY",
            "confidence": 0.84,
            "bounds": [0.2, 0.3, 0.6, 0.7],
            "coordinate_space": "NORMALIZED",
            "points": [[0.2, 0.5], [0.6, 0.5]],
            "lifecycle": "ACTIVE",
            "first_seen": 11,
            "last_seen": 19,
            "age_frames": 8,
            "duration_candles": 3,
            "candle_id": "close-19",
        }
    ]
    assert "bbox" not in objects[0]


def test_market_study_objects_keep_real_tracker_zones_and_normalize_geometry() -> None:
    objects = PhoenixGuardWindowTrackingAdapter._market_study_objects_v3(  # noqa: SLF001
        [
            {
                "key": "support_1",
                "role": "support",
                "zone_family": "DEMAND_ZONE",
                "direction": "BUY",
                "authority_score": 0.71,
                "bbox": [100, 200, 500, 240],
                "touch_points": [[120, 220], [480, 218]],
            },
            {
                "key": "support_2",
                "role": "support",
                "zone_family": "DEMAND_ZONE",
                "direction": "BUY",
                "authority_score": 0.83,
                "bbox": [200, 300, 700, 350],
                "anchor_wick_points": [[220, 325], [680, 327]],
            },
        ],
        image_size=(1000, 500),
    )

    assert [row["object_id"] for row in objects] == ["support_1", "support_2"]
    assert [row["object_type"] for row in objects] == ["DEMAND_ZONE", "DEMAND_ZONE"]
    assert [row["confidence"] for row in objects] == [0.71, 0.83]
    assert all(row["identity_scope"] == "OBSERVATION_ONLY" for row in objects)
    assert all(row["identity_stable"] is False for row in objects)
    assert objects[0]["bounds"] == [0.1, 0.4, 0.5, 0.48]
    assert objects[0]["points"] == [[0.12, 0.44], [0.48, 0.436]]
    assert objects[1]["bounds"] == [0.2, 0.6, 0.7, 0.7]
    assert objects[1]["points"] == [[0.22, 0.65], [0.68, 0.654]]
    assert all(row["coordinate_space"] == "NORMALIZED" for row in objects)
    assert all("bbox" not in row and "touch_points" not in row for row in objects)


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


@pytest.mark.parametrize(
    ("signal_freshness_sec", "expected_ttl_sec"),
    ((120.0, 300.0), (420.0, 420.0)),
)
def test_model_council_study_packet_respects_canonical_visibility_floor_when_synthesized(
    monkeypatch: pytest.MonkeyPatch,
    signal_freshness_sec: float,
    expected_ttl_sec: float,
) -> None:
    monkeypatch.setattr(window_tracker_module.time, "time", lambda: 200.0)

    packet = window_tracker_module.model_council_study_packet_from_payload(
        {
            "session_id": "pocket-live-8788",
            "state_version": 200123,
            "last_capture_epoch": 200.0,
            "latest_signal": {"freshness_window_sec": signal_freshness_sec},
            "model_council_result": {
                "execution": {"enabled": False, "state": "WATCHING", "side": "BUY"},
                "model_council": {"final_state": "WATCHING", "final_side": "BUY"},
            },
        }
    )

    assert packet["created_epoch"] == 200.0
    assert packet["valid_until_epoch"] == 200.0 + expected_ttl_sec
    assert packet["ttl_sec"] == expected_ttl_sec


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
        body_half_height = max(2, int(round(half_height * 0.58)))
        body_top = max(y0, int(round(center_y - body_half_height)))
        body_bottom = min(y1, int(round(center_y + body_half_height)))
        open_y = float(body_bottom if direction == "BUY" else body_top)
        close_y = float(body_top if direction == "BUY" else body_bottom)
        tracks.append(
            {
                "track_id": index,
                "bbox": [x0, y0, x1, y1],
                "center_x": center_x,
                "center_x_px": center_x,
                "center_y": float(center_y),
                "center_y_px": float(center_y),
                "wick_top_px": float(y0),
                "wick_bottom_px": float(y1),
                "body_top_px": float(body_top),
                "body_bottom_px": float(body_bottom),
                "open_y_px": open_y,
                "close_y_px": close_y,
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


def _book_rule_candle_tracks(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Build production-shaped candle rows with explicit OHLC/closure state."""

    tracks: list[dict[str, Any]] = []
    for index, source in enumerate(rows):
        direction = str(source.get("direction", "BUY") or "BUY").upper()
        wick_top = float(source.get("wick_top", 0.0) or 0.0)
        body_top = float(source.get("body_top", wick_top) or wick_top)
        body_bottom = float(source.get("body_bottom", body_top) or body_top)
        wick_bottom = float(source.get("wick_bottom", body_bottom) or body_bottom)
        center_x = 40.0 + index * 24.0
        open_y = float(
            source.get(
                "open_y_px",
                body_bottom if direction == "BUY" else body_top,
            )
        )
        close_y = float(
            source.get(
                "close_y_px",
                body_top if direction == "BUY" else body_bottom,
            )
        )
        tracks.append(
            {
                "track_id": index,
                "bbox": [center_x - 4.0, wick_top, center_x + 4.0, wick_bottom],
                "center_x": center_x,
                "center_x_px": center_x,
                "center_y": 0.5 * (wick_top + wick_bottom),
                "center_y_px": 0.5 * (wick_top + wick_bottom),
                "wick_top_px": wick_top,
                "wick_bottom_px": wick_bottom,
                "body_top_px": body_top,
                "body_bottom_px": body_bottom,
                "open_y_px": open_y,
                "close_y_px": close_y,
                "direction": direction,
                "color": "green" if direction == "BUY" else "magenta",
                "is_closed": bool(source.get("is_closed", True)),
            }
        )
    return tracks


def _derive_book_rule_smart_money(
    adapter: PhoenixGuardWindowTrackingAdapter,
    candles: Sequence[Mapping[str, Any]],
    *,
    zones: Sequence[Mapping[str, Any]] = (),
    candidate_action: str = "BUY",
    global_direction: str = "BUY",
    local_direction: str = "BUY",
    impulse_direction: str = "BUY",
    reversal_score: float = 0.0,
) -> dict[str, Any]:
    derive = cast(
        Callable[..., dict[str, Any]],
        getattr(adapter, "_derive_smart_money_context"),
    )
    return derive(
        candles,
        (640, 360),
        support_resistance_zones=zones,
        projection={},
        candidate_action=candidate_action,
        global_direction=global_direction,
        local_direction=local_direction,
        impulse_direction=impulse_direction,
        confidence=0.82,
        consolidation_score=0.12,
        continuation_score=0.74,
        reversal_score=reversal_score,
    )


def _memory_embed(seed: int) -> list[float]:
    rng = np.random.default_rng(seed)
    vec = rng.normal(size=(384,)).astype(np.float32)
    norm = float(np.linalg.norm(vec))
    return (vec / max(norm, 1e-8)).tolist()


class _StubPhoenixBank:
    is_loaded = True

    def __init__(self, rows: Sequence[MemoryEntry]) -> None:
        self.entries = list(rows)
        self.search_query_contexts: list[Mapping[str, Any] | None] = []

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
        self.search_query_contexts.append(query_context)
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


def test_memory_search_prefilter_avoids_duplicate_query_context_scoring() -> None:
    entries = _sample_memory_entries()
    bank = _StubPhoenixBank(entries)
    adapter = PhoenixGuardWindowTrackingAdapter()
    score_matches = cast(
        Callable[..., list[dict[str, Any]]],
        getattr(adapter, "_score_memory_side_matches"),
    )
    query_context: dict[str, Any] = {
        "late_interaction_tokens": [[0.1, 0.2, 0.3]],
        "trajectory_signature": [0.2, 0.4, 0.6],
        "style_signature": {"momentum": 0.7},
        "metric_profile": {"direction_probability": 0.8},
    }

    rows = score_matches(
        bank,
        np.asarray(_memory_embed(216), dtype=np.float32),
        desired_label="SELL",
        macro_trend="BEAR",
        local_phase="with_trend_push",
        chart_state=dict(entries[0].chart_state),
        query_context=query_context,
        limit=2,
    )

    assert bank.search_query_contexts == [None]
    assert len(rows) == 2
    assert all(str(getattr(row["entry"], "label", "")).upper() == "SELL" for row in rows)
    assert all("late_score" in row and "metric_score" in row for row in rows)


def test_memory_projection_warmup_initializes_text_embedder_without_pixels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    calls: list[tuple[dict[str, Any], Image.Image | None]] = []

    class WarmupBank:
        def embed_description(
            self,
            chart_state: dict[str, Any],
            image: Image.Image | None = None,
        ) -> NDArray[np.float32]:
            calls.append((chart_state, image))
            return np.zeros((384,), dtype=np.float32)

    monkeypatch.setattr(adapter, "_get_phoenixguard_memory_bank", lambda: WarmupBank())

    adapter.warmup_memory_projection()

    assert len(calls) == 1
    assert calls[0][0]["direction"] == "HOLD"
    assert calls[0][0]["projected_next_box"]["direction"] == "HOLD"
    assert calls[0][1] is None


def test_service_defers_memory_projection_warmup_when_launch_policy_disables_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = threading.Event()

    class DeferredWarmupAdapter:
        def warmup_memory_projection(self) -> None:
            called.set()

    monkeypatch.setattr(window_tracker_module.RUNTIME, "background_warmup_on_launch", False)
    monkeypatch.setattr(window_tracker_module, "_memory_projection_warmup_started", False)

    service = ContinuousWindowTrackerService(
        root_dir=tmp_path / "deferred-warmup",
        tracking_adapter=DeferredWarmupAdapter(),
    )

    assert service.memory_projection_warmup_started is False
    assert called.wait(timeout=0.05) is False


def test_service_starts_one_memory_projection_warmup_when_launch_policy_enables_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = threading.Event()

    class EnabledWarmupAdapter:
        def warmup_memory_projection(self) -> None:
            called.set()

    monkeypatch.setattr(window_tracker_module.RUNTIME, "background_warmup_on_launch", True)
    monkeypatch.setattr(window_tracker_module, "_memory_projection_warmup_started", False)

    service = ContinuousWindowTrackerService(
        root_dir=tmp_path / "enabled-warmup",
        tracking_adapter=EnabledWarmupAdapter(),
    )

    assert service.memory_projection_warmup_started is True
    assert called.wait(timeout=1.0) is True


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


class _RecoveringDuplicateCaptureBackend(_FakeCaptureBackend):
    def __init__(self, images: Sequence[Image.Image], recovered_image: Image.Image) -> None:
        super().__init__(images)
        self.recovered_image = recovered_image.convert("RGB")
        self.live_recovery_calls = 0

    def capture_window_live(self, descriptor: Mapping[str, Any]) -> Image.Image:
        _ = descriptor
        self.live_recovery_calls += 1
        return self.recovered_image.copy()


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


@pytest.mark.parametrize("model_packet_present", [False, True])
def test_external_study_source_publishes_model_without_broker_evaluation_or_scan(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    model_packet_present: bool,
) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path / f"external-study-fast-{int(model_packet_present)}",
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    session = tracker.create_session(
        session_id=f"external-study-fast-{int(model_packet_present)}",
        auto_start=False,
    )
    evaluation_calls: list[str] = []
    scan_calls: list[str] = []

    def forbid_broker_evaluation(*_args: Any, **_kwargs: Any) -> Any:
        evaluation_calls.append("called")
        raise AssertionError("study-only external frames must not evaluate broker execution")

    def forbid_broker_scan(*_args: Any, **_kwargs: Any) -> Any:
        scan_calls.append("called")
        raise AssertionError("study-only external frames must not scan broker controls")

    def publish_test_council(**_kwargs: Any) -> dict[str, Any]:
        return {
            "schema_version": "PG_MODEL_COUNCIL_RESULT_V3",
            "__external_fast_path_test_result": True,
            "model_council": {"final_side": "BUY", "final_state": "READY"},
            "model_council_study_packet": {
                "schema_version": "PG_MODEL_COUNCIL_STUDY_PACKET_V3",
                "side": "BUY",
            },
        }

    def extract_test_packet(
        value: Mapping[str, Any] | None,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        row = dict(value or {})
        if model_packet_present and row.get("__external_fast_path_test_result") is True:
            return {
                "schema_version": "PG_EXECUTION_PACKET_V3",
                "packet_id": "test-study-packet",
                "session_id": str(session["session_id"]),
                "side": "BUY",
                "action": "BUY",
            }
        return {}

    monkeypatch.setattr(tracker, "_evaluate_broker_execution", forbid_broker_evaluation)
    monkeypatch.setattr(tracker, "_read_broker_surface", forbid_broker_scan)
    monkeypatch.setattr(tracker, "_publish_model_council_v3_state", publish_test_council)
    monkeypatch.setattr(
        window_tracker_module,
        "_model_council_packet_from_payload",
        extract_test_packet,
    )

    accepted = tracker._capture_and_analyze(  # pyright: ignore[reportPrivateUsage]
        str(session["session_id"]),
        force=True,
        external_window_image=_surface(width=1280, height=720),
        external_source={
            "source_id": "external-study-feed",
            "source_type": "external_frame_feed",
            "coordinate_space": "external_frame_v1",
            "sequence_id": "external-study-sequence",
            "frame_id": 1,
            "metadata": {"source_render_fresh": True},
        },
        external_capture_epoch=time.time(),
    )

    assert accepted is True
    assert evaluation_calls == []
    assert scan_calls == []
    decision_paths = sorted(
        (tracker.session_dir(str(session["session_id"])) / "artifacts").glob(
            "*_decision.json"
        )
    )
    assert len(decision_paths) == 1
    decision = cast(
        dict[str, Any],
        json.loads(decision_paths[0].read_text(encoding="utf-8")),
    )
    execution = cast(dict[str, Any], decision["broker_execution_state"])
    surface = cast(dict[str, Any], decision["broker_surface"])
    assert execution["status"] == "study_source_only"
    assert execution["actionable"] is False
    assert execution["execution_authority"] == "NONE"
    assert execution["model_packet_present"] is model_packet_present
    assert surface["study_source_only"] is True
    assert surface["broker_click_safe"] is False
    assert surface["scan_skipped"] is True
    assert surface["scan_skip_reason"] == (
        "external_study_source_never_scans_broker_controls"
    )
    assert decision["latest_signal"]["pipeline_timing"][
        "partial_publish_reason"
    ] == "external_study_source_model_ready_broker_execution_skipped"
    assert decision["decision_artifact_state"] == (
        "external_study_source_model_published_no_execution_authority"
    )


def test_superseding_claim_during_external_study_drops_old_model_and_overlays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _FakeTrackingAdapter("BUY")
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path / "external-study-superseded",
        tracking_adapter=adapter,
    )
    session_id = "external-study-superseded"
    tracker.create_session(session_id=session_id, auto_start=False)
    first = tracker.claim_external_source(
        session_id,
        source_id="edge-chart-a",
        sequence_id="edge-sequence-a",
        source_type="browser_tab_roi_capture",
        selection_id="edge-selection-a",
        display_name="Chart A",
        coordinate_space="edge_tab_roi_v1",
    )
    surface_signature = cast(
        Callable[[Image.Image], str],
        getattr(window_tracker_module, "_surface_signature"),
    )

    def probe_identity(
        image: Image.Image,
        *,
        source: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        del source
        return {
            "detected_market": "CAD/JPY OTC",
            "market_confidence": 0.95,
            "market_source": "broker_header_text",
            "market_bbox": [80, 60, 220, 92],
            "detected_timeframe": "M5",
            "timeframe_confidence": 0.96,
            "timeframe_source": "broker_selector_chip",
            "timeframe_bbox": [250, 60, 292, 92],
            "broker_surface_hash": surface_signature(image),
        }

    monkeypatch.setattr(
        adapter,
        "probe_chart_identity_v3",
        probe_identity,
        raising=False,
    )
    second_claim: dict[str, Any] = {}

    def supersede_before_model_publication(**_kwargs: Any) -> dict[str, Any]:
        second_claim.update(
            tracker.claim_external_source(
                session_id,
                source_id="edge-chart-b",
                sequence_id="edge-sequence-b",
                source_type="browser_tab_roi_capture",
                selection_id="edge-selection-b",
                display_name="Chart B",
                coordinate_space="edge_tab_roi_v1",
            )
        )
        return {
            "schema_version": "PG_MODEL_COUNCIL_RESULT_V3",
            "model_council": {"final_side": "BUY", "final_state": "READY"},
            "model_council_study_packet": {
                "schema_version": "PG_MODEL_COUNCIL_STUDY_PACKET_V3",
                "side": "BUY",
            },
        }

    monkeypatch.setattr(
        tracker,
        "_publish_model_council_v3_state",
        supersede_before_model_publication,
    )
    try:
        accepted = tracker._capture_and_analyze(  # pyright: ignore[reportPrivateUsage]
            session_id,
            force=True,
            external_window_image=_surface(width=1280, height=720),
            external_source={
                "source_id": "edge-chart-a",
                "source_type": "browser_tab_roi_capture",
                "source_url": "https://pocketoption.com/en/cabinet/demo-quick-high-low/",
                "sequence_id": "edge-sequence-a",
                "coordinate_space": "edge_tab_roi_v1",
                "source_generation": int(first["source_generation"]),
                "frame_id": 1,
                "metadata": {
                    "source_lease_id": str(first["source_lease_id"]),
                    "source_render_fresh": True,
                    "extension_id": "edge-extension-test",
                    "locked_tab_id": "17",
                    "locked_tab_title": "The Most Innovative Trading Platform",
                    "locked_origin": "https://pocketoption.com",
                },
            },
            external_capture_epoch=time.time(),
        )

        assert accepted is True
        assert second_claim["source_generation"] == (
            int(first["source_generation"]) + 1
        )
        persisted = tracker.load_session_payload(session_id)
        current_source = cast(dict[str, Any], persisted["capture_source_v3"])
        assert current_source["source_id"] == "edge-chart-b"
        assert current_source["sequence_id"] == "edge-sequence-b"
        assert current_source["source_generation"] == second_claim[
            "source_generation"
        ]
        assert persisted["market"] == ""
        assert persisted["last_chart_path"] == ""
        assert persisted["last_overlay_path"] == ""
        assert persisted["last_full_overlay_path"] == ""
        council_result = cast(
            dict[str, Any], persisted["model_council_result"]
        )
        assert council_result.get("model_council") in (None, {})
        assert council_result["study_packet_present"] is False
        assert council_result["execution_packet_present"] is False
        assert persisted["model_council_study_packet"] == {}
        latest = cast(dict[str, Any], persisted["latest_signal"])
        assert latest["action"] == "HOLD"
        assert latest["market_source"] == "unconfirmed"
        assert "focus locked" in str(latest["summary"]).lower()
        assert persisted["external_evidence_lineage_v3"] == {}
        decision_paths = list(
            (tracker.session_dir(session_id) / "artifacts").glob("*_decision.json")
        )
        assert decision_paths == []
    finally:
        tracker.shutdown()


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
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "0")
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
    monkeypatch.setattr(backend, "foreground_window_hwnd", lambda: 101)

    def visible_pocket_descriptor(_hwnd: int) -> dict[str, Any]:
        return {
            "hwnd": 101,
            "title": "The Most Innovative Trading Platform - Microsoft Edge",
            "bbox": [0, 0, 1200, 760],
            "width": 1200,
            "height": 760,
        }

    monkeypatch.setattr(
        backend,
        "_visible_descriptor_for_hwnd",
        visible_pocket_descriptor,
    )

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


def test_windows_capture_backend_routes_pocket_visible_fallback_through_identity_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "0")
    backend = WindowsWindowCaptureBackend()
    calls: list[str] = []

    def capture_with_printwindow(hwnd: int, _descriptor: Mapping[str, Any]) -> Image.Image:
        calls.append(f"print:{hwnd}")
        return Image.new("RGB", (1200, 760), color=(36, 36, 36))

    def guarded_live_capture(_descriptor: Mapping[str, Any]) -> Image.Image:
        calls.append("guarded-live")
        raise CaptureSurfaceUnavailableError("foreground ownership changed during capture")

    monkeypatch.setattr(backend, "_is_windows", lambda: True)
    monkeypatch.setattr(backend, "_capture_window_printwindow", capture_with_printwindow)
    monkeypatch.setattr(backend, "capture_window_live", guarded_live_capture)

    with pytest.raises(CaptureSurfaceUnavailableError, match="broker/chart surface"):
        backend.capture_window(
            {
                "hwnd": 101,
                "title": "The Most Innovative Trading Platform - Microsoft Edge",
                "bbox": [0, 0, 1200, 760],
            }
        )

    assert calls == ["print:101", "guarded-live"]


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
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "0")
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


def test_background_only_pocket_capture_fails_closed_on_blank_offscreen_pixels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "1")
    backend = WindowsWindowCaptureBackend()
    calls: list[str] = []

    def capture_with_printwindow(
        hwnd: int,
        _descriptor: Mapping[str, Any],
    ) -> Image.Image:
        calls.append(f"print:{hwnd}")
        return Image.new("RGB", (1200, 760), color=(36, 36, 36))

    def unexpected_desktop_grab(_descriptor: Mapping[str, Any]) -> Image.Image:
        calls.append("desktop-grab")
        raise AssertionError("background-only capture must not read visible desktop pixels")

    monkeypatch.setattr(backend, "_is_windows", lambda: True)
    monkeypatch.setattr(
        backend,
        "_capture_window_printwindow",
        capture_with_printwindow,
    )
    monkeypatch.setattr(
        backend,
        "_capture_window_imagegrab",
        unexpected_desktop_grab,
    )

    with pytest.raises(
        CaptureSurfaceUnavailableError,
        match="broker/chart surface",
    ):
        backend.capture_window(
            {
                "hwnd": 101,
                "title": "The Most Innovative Trading Platform - Microsoft Edge",
                "bbox": [0, 0, 1200, 760],
            }
        )

    assert calls == ["print:101"]


def test_background_only_visible_activation_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "1")
    backend = WindowsWindowCaptureBackend()
    monkeypatch.setattr(backend, "_is_windows", lambda: True)

    assert backend._activate_window_for_visible_capture(101) is False  # noqa: SLF001


def test_background_only_locked_window_restore_is_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "1")
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface(width=1280, height=720)]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )

    assert tracker._restore_locked_window_descriptor(101) == {}  # noqa: SLF001


def test_windows_live_capture_rejects_dashboard_title_even_with_embedded_broker_pixels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "0")
    backend = WindowsWindowCaptureBackend()
    capture_calls = 0

    def capture_with_imagegrab(_descriptor: Mapping[str, Any]) -> Image.Image:
        nonlocal capture_calls
        capture_calls += 1
        return _synthetic_full_pocket_option_gui(width=1200, height=760)

    monkeypatch.setattr(backend, "_is_windows", lambda: True)
    monkeypatch.setattr(backend, "_ensure_dpi_awareness", lambda: None)
    monkeypatch.setattr(backend, "_activate_window_for_visible_capture", _activate_window_true)
    monkeypatch.setattr(backend, "foreground_window_hwnd", lambda: 101)

    def visible_dashboard_descriptor(_hwnd: int) -> dict[str, Any]:
        return {
            "hwnd": 101,
            "title": "808Fx Standard Hybrid System - Google Chrome",
            "bbox": [0, 0, 1200, 760],
            "width": 1200,
            "height": 760,
        }

    monkeypatch.setattr(
        backend,
        "_visible_descriptor_for_hwnd",
        visible_dashboard_descriptor,
    )
    monkeypatch.setattr(backend, "_capture_window_imagegrab", capture_with_imagegrab)

    with pytest.raises(CaptureSurfaceUnavailableError, match="active window is not Pocket Option"):
        backend.capture_window_live(
            {
                "hwnd": 101,
                "title": "The Most Innovative Trading Platform - Microsoft Edge",
                "bbox": [0, 0, 1200, 760],
            }
        )

    # The current OS title rejects the recursive dashboard before its embedded
    # broker screenshot can fool BUY/SELL pixel checks.
    assert capture_calls == 0


def test_windows_live_capture_rejects_foreground_change_after_grab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "0")
    backend = WindowsWindowCaptureBackend()
    foreground_handles = iter((101, 202))
    visible_descriptor = {
        "hwnd": 101,
        "title": "The Most Innovative Trading Platform - Microsoft Edge",
        "bbox": [0, 0, 1200, 760],
        "width": 1200,
        "height": 760,
    }

    monkeypatch.setattr(backend, "_is_windows", lambda: True)
    monkeypatch.setattr(backend, "_ensure_dpi_awareness", lambda: None)
    monkeypatch.setattr(backend, "_activate_window_for_visible_capture", _activate_window_true)
    monkeypatch.setattr(backend, "foreground_window_hwnd", lambda: next(foreground_handles))

    def stable_visible_descriptor(_hwnd: int) -> dict[str, Any]:
        return dict(visible_descriptor)

    def synthetic_visible_capture(_descriptor: Mapping[str, Any]) -> Image.Image:
        return _synthetic_full_pocket_option_gui(width=1200, height=760)

    monkeypatch.setattr(backend, "_visible_descriptor_for_hwnd", stable_visible_descriptor)
    monkeypatch.setattr(
        backend,
        "_capture_window_imagegrab",
        synthetic_visible_capture,
    )

    with pytest.raises(CaptureSurfaceUnavailableError, match="foreground ownership changed during capture"):
        backend.capture_window_live(visible_descriptor)


def test_display_fast_visible_capture_rejects_foreground_change_after_grab(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "0")

    class _ForegroundRaceBackend(_FastVisibleOnlyCaptureBackend):
        def __init__(self) -> None:
            super().__init__(_synthetic_full_pocket_option_gui(width=1280, height=720))
            self._foreground_handles = iter((501, 777))

        def foreground_window_hwnd(self) -> int:
            return next(self._foreground_handles)

    backend = _ForegroundRaceBackend()
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    monkeypatch.setenv("PHOENIXGUARD_DISPLAY_FAST_VISIBLE_CAPTURE", "1")
    monkeypatch.delenv("PHOENIXGUARD_DISPLAY_ALLOW_NATIVE_CAPTURE_FALLBACK", raising=False)

    with pytest.raises(CaptureSurfaceUnavailableError, match="foreground ownership changed during capture"):
        tracker._capture_display_snapshot_window(  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
            {
                "hwnd": 501,
                "title": "The Most Innovative Trading Platform - Microsoft Edge",
                "bbox": [0, 0, 1280, 720],
            }
        )

    assert backend.fast_capture_calls == 1


def test_background_only_display_snapshot_skips_fast_visible_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "1")
    monkeypatch.setenv("PHOENIXGUARD_DISPLAY_FAST_VISIBLE_CAPTURE", "1")
    backend = _FailingFastVisibleCaptureBackend(
        _synthetic_full_pocket_option_gui(width=1280, height=720)
    )
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )

    image = tracker._capture_display_snapshot_window(  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        {
            "hwnd": 501,
            "title": "The Most Innovative Trading Platform - Microsoft Edge",
            "bbox": [0, 0, 1280, 720],
        }
    )

    assert image.size == (1280, 720)
    assert backend.fast_capture_calls == 0
    assert backend.capture_calls == 1


def test_background_only_focus_selection_skips_fast_display_preview(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "1")
    monkeypatch.setenv("PHOENIXGUARD_FAST_FOCUS_PREVIEW", "1")
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface(width=1280, height=720)]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    session_id = str(tracker.create_session(session_id="pocket-live")["session_id"])
    ordinary_capture_calls: list[tuple[str, bool]] = []

    def unexpected_fast_preview(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("background-only focus selection must not publish a visible preview")

    def ordinary_capture(
        observed_session_id: str,
        *,
        force: bool = False,
        **_kwargs: Any,
    ) -> bool:
        ordinary_capture_calls.append((observed_session_id, force))
        return True

    monkeypatch.setattr(
        tracker,
        "_publish_display_snapshot_only",
        unexpected_fast_preview,
    )
    monkeypatch.setattr(tracker, "_capture_and_analyze", ordinary_capture)

    focused = tracker.set_focus_region(
        session_id,
        [0.10, 0.10, 0.88, 0.86],
        source="test",
    )

    assert focused["manual_focus_region"]["enabled"] is True
    assert ordinary_capture_calls
    assert all(call == (session_id, True) for call in ordinary_capture_calls)


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


def test_temporarily_missing_locked_window_preserves_last_market_forecast(
    tmp_path: Path,
) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_ListedWindowCaptureBackend([]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    session_id = str(tracker.create_session(session_id="pocket-live")["session_id"])
    _focus_session_without_preview(tracker, session_id)
    payload = tracker.load_session_payload(session_id)
    original_published_epoch = 100.0
    existing_chart = tmp_path / "existing_chart.png"
    _surface().save(existing_chart)
    payload.update(
        {
            "tracking_enabled": True,
            "status": "running",
            "frame_index": 4,
            "overlay_frame_id": 4,
            "market": "EUR/NZD OTC",
            "last_chart_path": str(existing_chart),
            "tracking_summary": {
                "chart_valid": True,
                "detected_market": "EUR/NZD OTC",
                "detected_timeframe": "M5",
                "market_identity_confirmed": True,
                "timeframe_identity_confirmed": True,
                "market_study_v3": {
                    "status": "STUDIED",
                    "symbol": "EUR/NZD OTC",
                    "timeframe": "M5",
                    "closed_candle_key": "closed-eurnzd-otc-m5",
                },
            },
            "latest_signal": {
                "status": "tracking",
                "market": "EUR/NZD OTC",
                "focus_timeframe": "M5",
                "market_identity_confirmed": True,
                "timeframe_identity_confirmed": True,
                "action": "BUY",
                "summary": "BUY leading 3–5 completed M5 candles after the anchor close",
                "published_epoch": original_published_epoch,
            },
        }
    )
    write_json_atomic(tracker.session_dir(session_id) / "session.json", payload)

    tracker._mark_capture_surface_unavailable(  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        session_id,
        "The locked broker window is not visible right now.",
        session_status="waiting_for_window",
        signal_status="waiting_for_window",
    )
    refreshed = tracker.get_session(session_id)

    assert refreshed["status"] == "running"
    assert refreshed["frame_index"] == 4
    assert refreshed["tracking_summary"]["detected_market"] == "EUR/NZD OTC"
    assert refreshed["tracking_summary"]["detected_timeframe"] == "M5"
    assert refreshed["tracking_summary"]["market_study_v3"]["closed_candle_key"] == (
        "closed-eurnzd-otc-m5"
    )
    assert refreshed["tracking_summary"]["source_capture_status"] == (
        "WAITING_FOR_SOURCE_PIXELS"
    )
    assert refreshed["latest_signal"]["market"] == "EUR/NZD OTC"
    assert refreshed["latest_signal"]["focus_timeframe"] == "M5"
    assert refreshed["latest_signal"]["action"] == "BUY"
    assert refreshed["latest_signal"]["published_epoch"] == original_published_epoch
    assert refreshed["latest_signal"]["status"] == "waiting_for_window"
    assert refreshed["latest_signal"]["source_capture_blocked_v3"]["status"] == (
        "WAITING_FOR_SOURCE_PIXELS"
    )


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

    # Fifteen completed M5 candles map to a 75-minute clock. JPCLF keeps the
    # exact mapped duration now that the bounded ceiling is two hours.
    assert expiry_for_swing == 75 * 60
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


def test_window_tracker_live_flow_trigger_respects_m15_floor() -> None:
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
    assert profile["recommended_expiry_seconds"] == 15 * 60
    assert profile["recommended_candles"] == 3.0
    assert profile["under_15_minutes_excluded"] is True


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
    assert normalize("EUR/NZDOTE") == "EUR/NZD OTC"
    assert normalize("EUR/NZDABC") == "EUR/NZD"
    assert normalize("W D0CR01ILJI . /JFW1 P IY W P 1") == ""


def _paint_realistic_market_selector(image: Image.Image, text: str) -> None:
    draw = ImageDraw.Draw(image)
    width, height = image.size
    draw.rectangle(
        (
            int(round(width * 0.055)),
            int(round(height * 0.10)),
            int(round(width * 0.21)),
            int(round(height * 0.19)),
        ),
        fill=(29, 38, 58),
    )
    draw.text(
        (int(round(width * 0.069)), int(round(height * 0.119))),
        text,
        fill=(235, 240, 248),
    )


def test_live_selector_lane_reads_pair_and_m5_despite_toolbar_clutter() -> None:
    cv2: Any = pytest.importorskip("cv2")
    width, height = 1628, 861
    pixels = np.full((height, width, 3), (21, 26, 38), dtype=np.uint8)

    # The first row is the broker watch-list and must never be concatenated
    # with the authoritative selected-pair control below it.
    cv2.putText(
        pixels,
        "AUD/CHF NZD/JPY OTC",
        (45, 48),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.70,
        (235, 240, 248),
        2,
        cv2.LINE_AA,
    )
    cv2.rectangle(pixels, (90, 86), (269, 164), (29, 38, 58), -1)
    cv2.putText(
        pixels,
        "GBP/USD OTC",
        (105, 137),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (235, 240, 248),
        2,
        cv2.LINE_AA,
    )

    # Model the compact M5 badge overlapping neighboring blue toolbar chrome.
    # Its tiny glyph must survive extraction without being morphed into a
    # solid M1/M3-looking block.
    cv2.rectangle(pixels, (263, 98), (335, 140), (25, 110, 210), -1)
    cv2.rectangle(pixels, (270, 104), (328, 136), (29, 38, 58), -1)
    cv2.rectangle(pixels, (297, 99), (323, 119), (25, 110, 210), -1)
    cv2.putText(
        pixels,
        "M5",
        (299, 115),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (248, 250, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.circle(pixels, (284, 126), 8, (248, 250, 255), 2, cv2.LINE_AA)

    surface = Image.fromarray(pixels, mode="RGB")
    adapter = PhoenixGuardWindowTrackingAdapter()
    detect_timeframe = cast(
        Callable[[Image.Image], dict[str, Any]],
        getattr(adapter, "_detect_timeframe_selector"),
    )
    detect_market = cast(
        Callable[..., dict[str, Any]],
        getattr(adapter, "_detect_market_selector"),
    )

    timeframe = detect_timeframe(surface)
    market = detect_market(surface, timeframe_selector=timeframe)

    assert timeframe["value"] == "M5"
    assert float(timeframe["confidence"]) >= 0.42
    assert market["value"] == "GBP/USD OTC"
    assert float(market["confidence"]) >= 0.42
    assert str(market["raw_text"]).replace(" ", "") == "GBP/USDOTC"


def test_market_selector_lane_tracks_letterboxed_edge_viewport_without_asset_tabs() -> None:
    cv2: Any = pytest.importorskip("cv2")
    width, content_height = 1628, 861

    def broker_surface(asset_text: str) -> Image.Image:
        pixels = np.full((content_height, width, 3), (21, 26, 38), dtype=np.uint8)
        cv2.putText(
            pixels,
            asset_text,
            (45, 70),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.70,
            (235, 240, 248),
            2,
            cv2.LINE_AA,
        )
        cv2.rectangle(pixels, (90, 86), (269, 164), (29, 38, 58), -1)
        cv2.putText(
            pixels,
            "GBP/USD OTC",
            (105, 137),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (235, 240, 248),
            2,
            cv2.LINE_AA,
        )
        return Image.fromarray(pixels, mode="RGB")

    focused = broker_surface("AUD/CHF NZD/JPY OTC")
    changed_tabs = broker_surface("EUR/USD GBP/NZD OTC")
    edge_viewport = Image.new("RGB", (width, content_height + 120), color=(0, 0, 0))
    edge_viewport.paste(changed_tabs, (0, 60))

    lane_bounds = cast(
        Callable[[Image.Image], tuple[int, int, int, int, str]],
        getattr(window_tracker_module, "_market_selector_lane_bounds"),
    )
    fingerprint = cast(
        Callable[[Image.Image], str],
        getattr(window_tracker_module, "_market_selector_visual_fingerprint"),
    )
    adapter = PhoenixGuardWindowTrackingAdapter()

    focused_lane = lane_bounds(focused)
    viewport_lane = lane_bounds(edge_viewport)
    assert focused_lane[4] == "focused_broker_surface"
    assert viewport_lane[4] == "edge_letterboxed_tab"
    assert viewport_lane[1] == focused_lane[1] + 60
    assert viewport_lane[3] == focused_lane[3] + 60
    assert fingerprint(focused) == fingerprint(edge_viewport)

    focused_market = adapter._detect_market_selector(focused)  # noqa: SLF001
    viewport_market = adapter._detect_market_selector(edge_viewport)  # noqa: SLF001
    assert focused_market["value"] == "GBP/USD OTC"
    assert viewport_market["value"] == "GBP/USD OTC"
    assert viewport_market["selector_layout"] == "edge_letterboxed_tab"
    viewport_bbox = cast(list[int], viewport_market["bbox"])
    assert 0.18 <= float(viewport_bbox[1]) / float(edge_viewport.height) <= 0.21


def test_live_selector_lane_reads_m5_below_full_browser_chrome() -> None:
    cv2: Any = pytest.importorskip("cv2")
    width, height = 1942, 1040
    pixels = np.full((height, width, 3), (21, 26, 38), dtype=np.uint8)

    # A complete Edge window places the toolbar and asset tabs above the chart.
    # The authoritative M5 chip therefore crosses the old 24% ROI cutoff and
    # was truncated before its complete glyph could be scored.
    offset_x, offset_y = 80, 141
    cv2.rectangle(
        pixels,
        (263 + offset_x, 98 + offset_y),
        (335 + offset_x, 140 + offset_y),
        (25, 110, 210),
        -1,
    )
    cv2.rectangle(
        pixels,
        (270 + offset_x, 104 + offset_y),
        (328 + offset_x, 136 + offset_y),
        (29, 38, 58),
        -1,
    )
    cv2.rectangle(
        pixels,
        (297 + offset_x, 99 + offset_y),
        (323 + offset_x, 119 + offset_y),
        (25, 110, 210),
        -1,
    )
    cv2.putText(
        pixels,
        "M5",
        (299 + offset_x, 115 + offset_y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (248, 250, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.circle(
        pixels,
        (284 + offset_x, 126 + offset_y),
        8,
        (248, 250, 255),
        2,
        cv2.LINE_AA,
    )

    adapter = PhoenixGuardWindowTrackingAdapter()
    detect_timeframe = cast(
        Callable[[Image.Image], dict[str, Any]],
        getattr(adapter, "_detect_timeframe_selector"),
    )
    timeframe = detect_timeframe(Image.fromarray(pixels, mode="RGB"))

    assert timeframe["value"] == "M5"
    assert float(timeframe["confidence"]) >= 0.56
    assert int(cast(list[int], timeframe["bbox"])[3]) >= int(height * 0.24) + 20


def _compact_live_m5_glyph() -> NDArray[np.uint8]:
    return np.asarray(
        [
            [0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 0],
            [1, 1, 0, 0, 0, 1, 1, 0, 1, 0, 0, 0],
            [1, 1, 1, 0, 1, 1, 1, 0, 1, 0, 0, 0],
            [1, 1, 1, 1, 1, 1, 1, 0, 1, 1, 1, 1],
            [1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 1],
            [1, 0, 1, 1, 1, 1, 1, 0, 0, 0, 0, 1],
            [1, 0, 0, 1, 0, 0, 1, 0, 1, 1, 1, 1],
        ],
        dtype=np.uint8,
    )


def _compact_live_m5_jpeg_glyph() -> NDArray[np.uint8]:
    """Five-row M5 topology captured from the live Edge tab JPEG.

    The three-column 5 keeps its top and middle bars after compression, but a
    two-column edge probe incorrectly counted the top bar as a 3 right stroke.
    """

    return np.asarray(
        [
            [1, 0, 0, 0, 1, 0, 0, 1, 1, 0],
            [1, 1, 0, 1, 1, 0, 0, 1, 1, 0],
            [1, 1, 0, 1, 1, 1, 0, 1, 1, 1],
            [1, 1, 1, 1, 1, 1, 0, 0, 0, 1],
            [1, 1, 1, 1, 1, 1, 0, 0, 1, 1],
        ],
        dtype=np.uint8,
    )


def test_compact_live_m5_jpeg_topology_does_not_become_m3() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()

    label, confidence = adapter._compact_timeframe_shape_hint(  # noqa: SLF001
        _compact_live_m5_jpeg_glyph()
    )

    assert label == "M5"
    assert confidence >= 0.68


def test_letterboxed_edge_timeframe_anchor_accepts_live_m5_position() -> None:
    cv2: Any = pytest.importorskip("cv2")
    pixels = np.zeros((1080, 1920, 3), dtype=np.uint8)
    pixels[60:1020, :] = (21, 26, 38)
    # Exact blue-chip geometry observed in live frame 71.  In the 806-pixel
    # timeframe ROI its center is at 33%, not the focused layout's 46% anchor.
    cv2.rectangle(pixels, (256, 193), (280, 215), (25, 110, 210), -1)
    compact_m5 = _compact_live_m5_glyph()
    glyph_y, glyph_x = np.nonzero(compact_m5)
    pixels[201 + glyph_y, 262 + glyph_x] = (248, 250, 255)
    adapter = PhoenixGuardWindowTrackingAdapter()

    timeframe = adapter._detect_timeframe_selector(Image.fromarray(pixels, mode="RGB"))  # noqa: SLF001

    assert timeframe["value"] == "M5"
    assert timeframe["bbox"] == [256, 193, 281, 216]
    assert timeframe["selector_layout"] == "edge_letterboxed_tab"
    assert float(timeframe["confidence"]) >= 0.85


def test_timeframe_selector_recovers_from_transient_empty_template_cache() -> None:
    cv2: Any = pytest.importorskip("cv2")
    pixels = np.zeros((1080, 1920, 3), dtype=np.uint8)
    pixels[60:1020, :] = (21, 26, 38)
    cv2.rectangle(pixels, (256, 193), (280, 215), (25, 110, 210), -1)
    compact_m5 = _compact_live_m5_glyph()
    glyph_y, glyph_x = np.nonzero(compact_m5)
    pixels[201 + glyph_y, 262 + glyph_x] = (248, 250, 255)
    adapter = PhoenixGuardWindowTrackingAdapter()
    # Reproduce the long-running API failure: cached_property had persisted an
    # empty bank after a transient startup initialization error.
    adapter.__dict__["_timeframe_template_bank"] = {}

    timeframe = adapter._detect_timeframe_selector(  # noqa: SLF001
        Image.fromarray(pixels, mode="RGB")
    )

    assert timeframe["value"] == "M5"
    assert timeframe["bbox"] == [256, 193, 281, 216]
    assert all(adapter._timeframe_template_bank.values())  # noqa: SLF001


def test_letterboxed_edge_timeframe_accepts_desaturated_live_capture_chip() -> None:
    cv2: Any = pytest.importorskip("cv2")
    pixels = np.zeros((1080, 1920, 3), dtype=np.uint8)
    pixels[60:1020, :] = (21, 26, 38)
    # First-generation tabCapture JPEGs can render this chip below the strict
    # saturation lane even though its exact M5 glyph remains sharp.
    cv2.rectangle(pixels, (256, 193), (280, 215), (78, 99, 126), -1)
    compact_m5 = _compact_live_m5_glyph()
    glyph_y, glyph_x = np.nonzero(compact_m5)
    pixels[201 + glyph_y, 262 + glyph_x] = (248, 250, 255)
    adapter = PhoenixGuardWindowTrackingAdapter()

    timeframe = adapter._detect_timeframe_selector(  # noqa: SLF001
        Image.fromarray(pixels, mode="RGB")
    )

    assert timeframe["value"] == "M5"
    assert timeframe["bbox"] == [256, 193, 281, 216]
    assert timeframe["selector_layout"] == "edge_letterboxed_tab"
    assert float(timeframe["confidence"]) >= 0.85


def test_letterboxed_edge_timeframe_keeps_m5_separate_from_dark_blue_toolbar() -> None:
    cv2: Any = pytest.importorskip("cv2")
    pixels = np.zeros((1080, 1920, 3), dtype=np.uint8)
    pixels[60:1020, :] = (21, 26, 38)
    # Full-viewport Pocket Option renders the selector inside a broad dark-blue
    # control lane.  That lane is blue enough for the relaxed saturation rule,
    # but too dark to be part of the authoritative selector chip.
    cv2.rectangle(pixels, (226, 195), (404, 272), (44, 49, 69), -1)
    cv2.rectangle(pixels, (256, 193), (280, 215), (25, 110, 210), -1)
    compact_m5 = _compact_live_m5_glyph()
    glyph_y, glyph_x = np.nonzero(compact_m5)
    pixels[201 + glyph_y, 262 + glyph_x] = (248, 250, 255)
    adapter = PhoenixGuardWindowTrackingAdapter()

    timeframe = adapter._detect_timeframe_selector(  # noqa: SLF001
        Image.fromarray(pixels, mode="RGB")
    )

    assert timeframe["value"] == "M5"
    assert timeframe["bbox"] == [256, 193, 281, 216]
    assert timeframe["selector_layout"] == "edge_letterboxed_tab"
    assert float(timeframe["confidence"]) >= 0.85


def test_letterboxed_edge_timeframe_ignores_later_toolbar_notification_badges() -> None:
    cv2: Any = pytest.importorskip("cv2")
    pixels = np.zeros((1080, 1920, 3), dtype=np.uint8)
    pixels[60:1020, :] = (21, 26, 38)

    # Current Pocket Option layout: the authoritative M5 selector is near
    # x=270 while blue notification badges near x=310 and x=349 can resemble
    # M3 after JPEG/downsample morphology. They must never compete as identity.
    cv2.rectangle(pixels, (256, 193), (280, 215), (25, 110, 210), -1)
    compact_m5 = _compact_live_m5_glyph()
    glyph_y, glyph_x = np.nonzero(compact_m5)
    pixels[201 + glyph_y, 262 + glyph_x] = (248, 250, 255)
    for left in (304, 340):
        cv2.rectangle(
            pixels,
            (left, 193),
            (left + 24, 217),
            (25, 110, 210),
            -1,
        )
        cv2.putText(
            pixels,
            "M3",
            (left + 2, 211),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.38,
            (248, 250, 255),
            1,
            cv2.LINE_AA,
        )

    adapter = PhoenixGuardWindowTrackingAdapter()
    timeframe = adapter._detect_timeframe_selector(  # noqa: SLF001
        Image.fromarray(pixels, mode="RGB")
    )

    assert timeframe["value"] == "M5"
    assert timeframe["bbox"] == [256, 193, 281, 216]
    assert int(timeframe["bbox"][0]) < 300
    assert float(timeframe["selector_anchor_distance_x"]) < 10.0


def test_chart_identity_probe_rebuilds_only_poisoned_ocr_template_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    healthy_timeframe_bank = adapter._timeframe_template_bank  # noqa: SLF001
    poisoned_ocr_bank: dict[str, list[NDArray[np.uint8]]] = {}
    adapter.__dict__["_ocr_char_template_bank"] = poisoned_ocr_bank
    timeframe_calls = 0

    def detect_timeframe(_image: Image.Image) -> dict[str, Any]:
        nonlocal timeframe_calls
        timeframe_calls += 1
        ocr_bank = adapter._ocr_char_template_bank  # noqa: SLF001
        if not ocr_bank or not all(ocr_bank.values()):
            return {}
        return {
            "value": "M5",
            "confidence": 0.91,
            "source": "broker_selector_chip",
            "bbox": [256, 193, 281, 216],
        }

    def detect_market(
        _image: Image.Image,
        timeframe_selector: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        assert timeframe_selector
        return {
            "value": "CAD/JPY OTC",
            "confidence": 0.88,
            "source": "visual_chart_header",
            "bbox": [120, 213, 215, 227],
        }

    monkeypatch.setattr(
        adapter,
        "_detect_timeframe_selector_unlocked",
        detect_timeframe,
    )
    monkeypatch.setattr(
        adapter,
        "_detect_market_selector_unlocked",
        detect_market,
    )

    result = adapter.probe_chart_identity_v3(Image.new("RGB", (640, 360)))

    assert result["identity_ready"] is True
    assert result["detected_timeframe"] == "M5"
    assert result["identity_probe_attempts"] == 2
    assert result["identity_probe_recovered"] is True
    assert result["identity_probe_rebuilt_caches"] == ["ocr"]
    assert timeframe_calls == 2
    assert adapter.__dict__["_timeframe_template_bank"] is healthy_timeframe_bank
    assert adapter.__dict__["_ocr_char_template_bank"] is not poisoned_ocr_bank
    assert all(adapter._ocr_char_template_bank.values())  # noqa: SLF001


def test_headerless_identity_probe_preserves_complete_template_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    healthy_timeframe_bank = adapter._timeframe_template_bank  # noqa: SLF001
    healthy_ocr_bank = adapter._ocr_char_template_bank  # noqa: SLF001
    timeframe_calls = 0

    def detect_timeframe(_image: Image.Image) -> dict[str, Any]:
        nonlocal timeframe_calls
        timeframe_calls += 1
        return {}

    def detect_market(
        _image: Image.Image,
        timeframe_selector: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        assert not timeframe_selector
        return {}

    monkeypatch.setattr(
        adapter,
        "_detect_timeframe_selector_unlocked",
        detect_timeframe,
    )
    monkeypatch.setattr(
        adapter,
        "_detect_market_selector_unlocked",
        detect_market,
    )

    result = adapter.probe_chart_identity_v3(Image.new("RGB", (640, 360)))

    assert result["identity_ready"] is False
    assert result["detected_timeframe"] == ""
    assert result["identity_probe_attempts"] == 1
    assert result["identity_probe_recovered"] is False
    assert result["identity_probe_rebuilt_caches"] == []
    assert timeframe_calls == 1
    assert adapter.__dict__["_timeframe_template_bank"] is healthy_timeframe_bank
    assert adapter.__dict__["_ocr_char_template_bank"] is healthy_ocr_bank


def test_chart_identity_probe_serializes_and_recovers_transient_detector_exception(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    state_lock = threading.Lock()
    state = {"active": 0, "maximum_active": 0, "timeframe_calls": 0}

    def enter_detector() -> None:
        with state_lock:
            state["active"] += 1
            state["maximum_active"] = max(
                state["maximum_active"],
                state["active"],
            )

    def leave_detector() -> None:
        with state_lock:
            state["active"] -= 1

    def detect_timeframe(_image: Image.Image) -> dict[str, Any]:
        enter_detector()
        try:
            time.sleep(0.01)
            with state_lock:
                state["timeframe_calls"] += 1
                call_number = state["timeframe_calls"]
            if call_number == 1:
                raise RuntimeError("transient visual detector failure")
            return {
                "value": "M5",
                "confidence": 0.91,
                "source": "broker_selector_chip",
                "bbox": [256, 193, 281, 216],
            }
        finally:
            leave_detector()

    def detect_market(
        _image: Image.Image,
        timeframe_selector: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        enter_detector()
        try:
            time.sleep(0.01)
            assert timeframe_selector
            return {
                "value": "CAD/JPY OTC",
                "confidence": 0.88,
                "source": "visual_chart_header",
                "bbox": [120, 213, 215, 227],
            }
        finally:
            leave_detector()

    monkeypatch.setattr(
        adapter,
        "_detect_timeframe_selector_unlocked",
        detect_timeframe,
    )
    monkeypatch.setattr(
        adapter,
        "_detect_market_selector_unlocked",
        detect_market,
    )

    def probe(_index: int) -> dict[str, Any]:
        return adapter.probe_chart_identity_v3(Image.new("RGB", (640, 360)))

    with caplog.at_level("WARNING"):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(probe, range(4)))

    assert state["maximum_active"] == 1
    assert state["timeframe_calls"] == 5
    assert all(row["identity_ready"] is True for row in results)
    assert all(row["detected_timeframe"] == "M5" for row in results)
    assert sum(bool(row["identity_probe_recovered"]) for row in results) == 1
    assert sorted(int(row["identity_probe_attempts"]) for row in results) == [1, 1, 1, 2]
    assert "Chart timeframe visual probe failed on attempt 1" in caplog.text


def test_tiny_live_m5_glyph_uses_shape_topology_instead_of_m1_guess() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    # Exact seven-pixel topology observed in the live broker's antialiased M5
    # selector.  The final four columns form a 5: top/middle/bottom bars,
    # upper-left stroke, and lower-right stroke.
    compact_m5 = _compact_live_m5_glyph()

    class UnexpectedTemplateAccess(dict[str, object]):
        def items(self):  # type: ignore[override]
            raise AssertionError("tiny M5 topology must bypass template banks")

        def get(self, *_args: Any, **_kwargs: Any) -> Any:  # type: ignore[override]
            raise AssertionError("tiny M5 topology must bypass template banks")

    adapter.__dict__["_timeframe_template_bank"] = UnexpectedTemplateAccess()
    adapter.__dict__["_ocr_char_template_bank"] = UnexpectedTemplateAccess()

    score_timeframe = cast(
        Callable[[NDArray[np.uint8]], tuple[str, float]],
        getattr(adapter, "_score_timeframe_label"),
    )
    label, confidence = score_timeframe(compact_m5)

    assert label == "M5"
    assert confidence >= 0.56


def test_market_ocr_domain_decoder_repairs_ambiguous_currency_glyphs_only() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    decode = cast(
        Callable[[Sequence[Sequence[tuple[str, float]]]], tuple[str, float, float]],
        getattr(adapter, "_decode_fx_market_rankings"),
    )
    observed = "EHF/JPYOTE"
    expected = "CHF/JPYOTC"
    rankings: list[list[tuple[str, float]]] = []
    for observed_label, expected_label in zip(observed, expected):
        if observed_label == expected_label:
            rankings.append([(expected_label, 0.82), ("X", 0.18)])
        else:
            rankings.append([(observed_label, 0.84), (expected_label, 0.79), ("X", 0.16)])

    decoded, score, margin = decode(rankings)

    assert decoded == "CHF/JPY OTC"
    assert score >= 0.60
    assert margin >= 0.012
    ambiguous = [[("X", 0.55), ("/", 0.54), ("O", 0.54), ("T", 0.54), ("C", 0.54)]] * 10
    assert decode(ambiguous)[0] == ""


def test_live_compact_cad_selector_splits_touching_ca_at_bounded_valley() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    # Exact per-column foreground counts from the merged ``CA`` contour in
    # live frame 71.  The baseline joins both glyphs, but the x=8 valley is
    # sparse enough to prove two median-sized characters without guessing.
    projection = [7, 9, 8, 4, 4, 4, 4, 4, 2, 3, 5, 7, 8, 6, 6, 8, 7, 6, 3]
    mask = np.zeros((11, 120), dtype=np.uint8)
    for x, count in enumerate(projection):
        mask[11 - count :, x] = 255
    components = [
        {"bbox": [0, 0, 19, 11]},
        {"bbox": [24, 0, 34, 11]},
        {"bbox": [36, 0, 41, 11]},
        {"bbox": [43, 0, 48, 11]},
        {"bbox": [50, 0, 58, 11]},
        {"bbox": [60, 0, 69, 11]},
        {"bbox": [71, 0, 82, 11]},
        {"bbox": [84, 0, 92, 11]},
        {"bbox": [94, 0, 103, 11]},
    ]

    split = adapter._split_market_wide_components(mask, components)  # noqa: SLF001

    assert len(split) == 10
    assert split[0]["bbox"] == [0, 0, 8, 11]
    assert split[1]["bbox"] == [8, 0, 19, 11]
    assert split[0]["split_from_wide_component"] is True
    assert split[1]["split_from_wide_component"] is True


def test_market_selector_fingerprint_ignores_live_candle_motion() -> None:
    fingerprint = cast(
        Callable[[Image.Image], str],
        getattr(window_tracker_module, "_market_selector_visual_fingerprint"),
    )
    buy_surface = _synthetic_chart_surface("buy", width=900, height=520)
    sell_surface = _synthetic_chart_surface("sell", width=900, height=520)
    _paint_realistic_market_selector(buy_surface, "CAD/JPY OTC")
    _paint_realistic_market_selector(sell_surface, "CAD/JPY OTC")

    buy_fingerprint = fingerprint(buy_surface)
    assert buy_fingerprint.startswith("selector_v2_")
    assert fingerprint(sell_surface) == buy_fingerprint


def test_market_selector_second_raster_lane_can_only_enrich_same_pair_to_otc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    surface = _synthetic_chart_surface("buy", width=900, height=520)
    reads: Iterator[dict[str, Any]] = iter(
        (
            {"value": "EUR/NZD", "source": "header_text", "confidence": 0.58},
            {"value": "EUR/NZD OTC", "source": "header_text", "confidence": 0.57},
        )
    )
    monkeypatch.setattr(adapter, "_detect_market_selector_once", lambda *_args, **_kwargs: next(reads))

    recovered = adapter._detect_market_selector(surface)  # noqa: SLF001

    assert recovered["value"] == "EUR/NZD OTC"
    assert recovered["specificity_recovery"] == "jpeg_raster_ocr_lane"
    assert recovered["specificity_recovered_from"] == "EUR/NZD"

    disagreeing_reads: Iterator[dict[str, Any]] = iter(
        (
            {"value": "EUR/NZD", "source": "header_text", "confidence": 0.58},
            {"value": "EUR/USD OTC", "source": "header_text", "confidence": 0.59},
        )
    )
    monkeypatch.setattr(
        adapter,
        "_detect_market_selector_once",
        lambda *_args, **_kwargs: next(disagreeing_reads),
    )

    disagreed = adapter._detect_market_selector(surface)  # noqa: SLF001

    assert disagreed["value"] == "EUR/NZD"
    assert "specificity_recovery" not in disagreed


def test_window_tracker_reuses_cached_market_only_when_selector_fingerprint_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    fingerprint = cast(
        Callable[[Image.Image], str],
        getattr(window_tracker_module, "_market_selector_visual_fingerprint"),
    )
    surface = _synthetic_chart_surface("buy", width=900, height=520)
    _paint_realistic_market_selector(surface, "AUD/NZD OTC")
    selector_fingerprint = fingerprint(surface)
    normalizer_version = str(
        getattr(window_tracker_module, "_FX_MARKET_NORMALIZER_VERSION")
    )

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
                "market_normalizer_version": normalizer_version,
            },
            "latest_signal": {
                "market": "AUD/NZD",
                "market_confidence": 0.91,
                "focus_timeframe": "M5",
                "focus_timeframe_confidence": 1.0,
                "market_selector_visual_fingerprint": selector_fingerprint,
                "market_normalizer_version": normalizer_version,
            },
        },
    )

    assert study.latest_signal["market"] == "AUD/NZD"
    assert study.latest_signal["market_source"] == "live_cached_selector"
    assert study.latest_signal["market_selector_visual_changed"] is False


def test_market_normalizer_upgrade_forces_cached_pair_rescan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    fingerprint = cast(
        Callable[[Image.Image], str],
        getattr(window_tracker_module, "_market_selector_visual_fingerprint"),
    )
    surface = _synthetic_chart_surface("buy", width=900, height=520)
    _paint_realistic_market_selector(surface, "EUR/NZD OTC")
    selector_fingerprint = fingerprint(surface)
    detector_calls = 0

    def detect_market_selector(
        image: Image.Image,
        timeframe_selector: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        nonlocal detector_calls
        _ = image
        _ = timeframe_selector
        detector_calls += 1
        return {
            "value": "EUR/NZDOTE",
            "source": "header_text",
            "confidence": 0.79,
        }

    monkeypatch.setattr(adapter, "_detect_market_selector", detect_market_selector)
    study = adapter.study(
        surface,
        session_payload={
            "session_id": "pocket-live",
            "manual_focus_region": {"enabled": True},
            "tracking_summary": {
                "detected_market": "EUR/NZD",
                "market_confidence": 0.91,
                "detected_timeframe": "M5",
                "timeframe_confidence": 1.0,
                "market_selector_visual_fingerprint": selector_fingerprint,
            },
            "latest_signal": {
                "market": "EUR/NZD",
                "market_confidence": 0.91,
                "focus_timeframe": "M5",
                "focus_timeframe_confidence": 1.0,
                "market_selector_visual_fingerprint": selector_fingerprint,
            },
        },
    )

    assert detector_calls == 1
    assert study.latest_signal["market"] == "EUR/NZD OTC"
    assert study.latest_signal["market_source"] == "header_text"
    assert study.latest_signal["market_normalizer_version"] == getattr(
        window_tracker_module,
        "_FX_MARKET_NORMALIZER_VERSION",
    )


def test_unsuffixed_forced_scan_gets_one_stability_rescan_before_cache_promotion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    fingerprint = cast(
        Callable[[Image.Image], str],
        getattr(window_tracker_module, "_market_selector_visual_fingerprint"),
    )
    surface = _synthetic_chart_surface("buy", width=900, height=520)
    _paint_realistic_market_selector(surface, "EUR/NZD OTC")
    selector_fingerprint = fingerprint(surface)
    detector_values = iter(("EUR/NZD", "EUR/NZD OTC"))
    detector_calls = 0

    def detect_market_selector(
        image: Image.Image,
        timeframe_selector: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        nonlocal detector_calls
        _ = image
        _ = timeframe_selector
        detector_calls += 1
        return {
            "value": next(detector_values),
            "source": "header_text",
            "confidence": 0.79,
        }

    monkeypatch.setattr(adapter, "_detect_market_selector", detect_market_selector)
    first = adapter.study(
        surface,
        session_payload={
            "session_id": "pocket-live",
            "manual_focus_region": {"enabled": True},
            "tracking_summary": {
                "detected_market": "EUR/NZD",
                "market_confidence": 0.91,
                "detected_timeframe": "M5",
                "timeframe_confidence": 1.0,
                "market_selector_visual_fingerprint": selector_fingerprint,
            },
            "latest_signal": {
                "market": "EUR/NZD",
                "market_confidence": 0.91,
                "focus_timeframe": "M5",
                "focus_timeframe_confidence": 1.0,
                "market_selector_visual_fingerprint": selector_fingerprint,
            },
        },
    )

    assert detector_calls == 1
    assert first.latest_signal["market"] == "EUR/NZD"
    assert first.latest_signal["market_selector_rebind_required"] is True
    assert first.latest_signal["market_identity_confirmed"] is False

    second = adapter.study(
        surface,
        session_payload={
            "session_id": "pocket-live",
            "manual_focus_region": {"enabled": True},
            "tracking_summary": first.tracking_summary,
            "latest_signal": first.latest_signal,
        },
    )

    assert detector_calls == 2
    assert second.latest_signal["market"] == "EUR/NZD OTC"
    assert second.latest_signal["market_selector_rebind_required"] is False
    assert second.latest_signal["market_identity_confirmed"] is True

    third = adapter.study(
        surface,
        session_payload={
            "session_id": "pocket-live",
            "manual_focus_region": {"enabled": True},
            "tracking_summary": second.tracking_summary,
            "latest_signal": second.latest_signal,
        },
    )

    assert detector_calls == 2
    assert third.latest_signal["market"] == "EUR/NZD OTC"
    assert third.latest_signal["market_source"] == "live_cached_selector"


def _wgc_identity_attestation(
    image: Image.Image,
    *,
    market: str = "CAD/CHF OTC",
    timeframe: str = "M5",
    market_confidence: float = 0.91,
    timeframe_confidence: float = 0.93,
    capture_epoch: float,
) -> dict[str, Any]:
    signature = cast(
        Callable[[Image.Image], str],
        getattr(window_tracker_module, "_surface_signature"),
    )(image)
    source = {
        "source_id": "windows-region-capture-v3",
        "source_type": "windows_graphics_capture_roi",
        "sequence_id": "wgc-sequence-7",
        "coordinate_space": "wgc_hwnd_roi_v1",
        "source_generation": 3,
        "metadata": {"source_lease_id": "lease-secret-7"},
    }
    source_lock = cast(
        Callable[..., dict[str, Any]],
        getattr(window_tracker_module, "_external_frame_source_lock_v3"),
    )(source, image, window_signature=signature)
    return cast(
        Callable[..., dict[str, Any]],
        getattr(
            window_tracker_module,
            "_external_wgc_broker_identity_attestation_v3",
        ),
    )(
        source,
        source_lock,
        {
            "detected_market": market,
            "market_confidence": market_confidence,
            "market_source": "broker_header_text",
            "detected_timeframe": timeframe,
            "timeframe_confidence": timeframe_confidence,
            "timeframe_source": "broker_selector_chip",
            "broker_surface_hash": signature,
        },
        window_signature=signature,
        capture_epoch=capture_epoch,
    )


def _edge_tab_identity_attestation(
    image: Image.Image,
    *,
    market: str = "CAD/JPY OTC",
    timeframe: str = "M5",
    capture_epoch: float,
) -> dict[str, Any]:
    signature = cast(
        Callable[[Image.Image], str],
        getattr(window_tracker_module, "_surface_signature"),
    )(image)
    source = {
        "source_id": "edge-chart-region-v3",
        "source_type": "browser_tab_roi_capture",
        "source_url": "https://pocketoption.com/en/cabinet/demo-quick-high-low/",
        "sequence_id": "edge-roi-17-sequence",
        "coordinate_space": "edge_tab_roi_v1",
        "source_generation": 4,
        "frame_id": 11,
        "metadata": {
            "source_lease_id": "edge-lease-secret-4",
            "source_render_fresh": True,
            "extension_id": "edge-extension-id",
            "locked_tab_id": "17",
            "locked_tab_title": "The Most Innovative Trading Platform",
            "locked_origin": "https://pocketoption.com",
        },
    }
    source_lock = cast(
        Callable[..., dict[str, Any]],
        getattr(window_tracker_module, "_external_frame_source_lock_v3"),
    )(source, image, window_signature=signature)
    return cast(
        Callable[..., dict[str, Any]],
        getattr(
            window_tracker_module,
            "_external_edge_tab_broker_identity_attestation_v3",
        ),
    )(
        source,
        source_lock,
        {
            "detected_market": market,
            "market_confidence": 0.91,
            "market_source": "broker_header_text",
            "market_bbox": [120, 213, 215, 227],
            "market_selector_visual_fingerprint": "selector_v3_cad_jpy_otc",
            "detected_timeframe": timeframe,
            "timeframe_confidence": 0.93,
            "timeframe_source": "broker_selector_chip",
            "timeframe_bbox": [256, 193, 281, 216],
            "broker_surface_hash": signature,
        },
        window_signature=signature,
        capture_epoch=capture_epoch,
    )


def test_edge_exact_capture_identity_bracket_binds_pair_without_ocr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    surface = Image.new("RGB", (1000, 600), color=(0, 0, 0))
    draw = ImageDraw.Draw(surface)
    draw.rectangle((0, 50, 999, 549), fill=(20, 27, 41))
    market_bbox_css = [80.0, 90.0, 220.0, 125.0]
    timeframe_bbox_css = [250.0, 90.0, 300.0, 125.0]
    draw.text((88, 145), "USD/CAD OTC", fill=(244, 246, 250))
    draw.text((258, 145), "M5", fill=(244, 246, 250))
    capture_epoch_ms = int(time.time() * 1000.0)
    observation = {
        "schema_version": "PG_EDGE_TAB_IDENTITY_OBSERVATION_V3",
        "capture_bracket_consistent": True,
        "locked_tab_id": 17,
        "locked_origin": "https://pocketoption.com",
        "sequence_id": "edge-roi-17-sequence",
        "before": {
            "symbol": "USD/CAD OTC",
            "timeframe": "M5",
            "market_bbox_css": market_bbox_css,
            "timeframe_bbox_css": timeframe_bbox_css,
            "viewport_css": {"width": 1000, "height": 500},
            "observed_epoch": capture_epoch_ms - 10,
        },
        "after": {
            "symbol": "USD/CAD OTC",
            "timeframe": "M5",
            "market_bbox_css": market_bbox_css,
            "timeframe_bbox_css": timeframe_bbox_css,
            "viewport_css": {"width": 1000, "height": 500},
            "observed_epoch": capture_epoch_ms + 10,
        },
    }
    source = {
        "source_type": "browser_tab_roi_capture",
        "coordinate_space": "edge_tab_roi_v1",
        "sequence_id": "edge-roi-17-sequence",
        "capture_epoch_ms": capture_epoch_ms,
        "metadata": {
            "locked_tab_id": "17",
            "locked_origin": "https://pocketoption.com",
            "source_surface_width": 1000,
            "source_surface_height": 600,
            "roi_source_pixels": {
                "x": 0,
                "y": 0,
                "width": 1000,
                "height": 600,
                "sourceWidth": 1000,
                "sourceHeight": 600,
            },
            "identity_observation_v3": observation,
        },
    }
    identity_reader = cast(
        Callable[[Image.Image, Mapping[str, Any]], dict[str, Any]],
        getattr(window_tracker_module, "_edge_tab_bracket_identity_surface_v3"),
    )

    identity = identity_reader(surface, source)

    assert identity["detected_market"] == "USD/CAD OTC"
    assert identity["detected_timeframe"] == "M5"
    assert identity["identity_ready"] is True
    assert identity["identity_probe_path"] == (
        "edge_exact_capture_dom_bracket_pixel_verified"
    )
    assert identity["market_selector_visual_fingerprint"].startswith(
        "selector_v3_"
    )
    assert 130 <= int(identity["market_bbox"][1]) <= 150
    adapter = PhoenixGuardWindowTrackingAdapter()

    def unexpected_visual_ocr(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("exact-capture bracket fell through to visual OCR")

    monkeypatch.setattr(adapter, "_detect_market_selector", unexpected_visual_ocr)
    monkeypatch.setattr(adapter, "_detect_timeframe_selector", unexpected_visual_ocr)
    probed = adapter.probe_chart_identity_v3(surface, source=source)
    assert probed["detected_market"] == "USD/CAD OTC"
    assert probed["detected_timeframe"] == "M5"

    tampered = copy.deepcopy(source)
    cast(dict[str, Any], cast(dict[str, Any], tampered["metadata"])["identity_observation_v3"])[
        "after"
    ]["symbol"] = "CHF/JPY OTC"
    assert identity_reader(surface, tampered) == {}


def test_legacy_edge_visual_ocr_rebinds_chf_jpy_to_usd_cad_m5(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An installed pre-DOM extension must still re-prove the visible pair.

    This models the operator's failure exactly: the preceding completed study
    belongs to CHF/JPY, while the next letterboxed Edge frame visibly selects
    USD/CAD OTC on M5.  The watch-list deliberately still contains CHF/JPY so
    neither the old study nor an unrelated tab label can be reused.
    """

    cv2: Any = pytest.importorskip("cv2")
    width, content_height = 1628, 861
    pixels = np.full((content_height, width, 3), (21, 26, 38), dtype=np.uint8)
    cv2.putText(
        pixels,
        "CHF/JPY OTC CAD/JPY OTC",
        (45, 48),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.70,
        (235, 240, 248),
        2,
        cv2.LINE_AA,
    )
    cv2.rectangle(pixels, (90, 86), (269, 164), (29, 38, 58), -1)
    cv2.putText(
        pixels,
        "USD/CAD OTC",
        (105, 137),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.65,
        (235, 240, 248),
        2,
        cv2.LINE_AA,
    )
    cv2.rectangle(pixels, (263, 98), (335, 140), (25, 110, 210), -1)
    cv2.rectangle(pixels, (270, 104), (328, 136), (29, 38, 58), -1)
    cv2.rectangle(pixels, (297, 99), (323, 119), (25, 110, 210), -1)
    cv2.putText(
        pixels,
        "M5",
        (299, 115),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (248, 250, 255),
        1,
        cv2.LINE_AA,
    )
    cv2.circle(pixels, (284, 126), 8, (248, 250, 255), 2, cv2.LINE_AA)
    broker_surface = Image.fromarray(pixels, mode="RGB")
    surface = Image.new("RGB", (width, content_height + 120), color=(0, 0, 0))
    surface.paste(broker_surface, (0, 60))

    capture_epoch = time.time()
    source = {
        "source_id": "edge-chart-region-v3",
        "source_type": "browser_tab_roi_capture",
        "source_url": "https://pocketoption.com/en/cabinet/demo-quick-high-low/",
        "sequence_id": "edge-legacy-ocr-sequence",
        "coordinate_space": "edge_tab_roi_v1",
        "source_generation": 4,
        "frame_id": 12,
        "metadata": {
            "source_lease_id": "edge-legacy-ocr-lease",
            "source_render_fresh": True,
            "extension_id": "edge-extension-id",
            "extension_version": "0.3.9",
            "locked_tab_id": "17",
            "locked_tab_title": "The Most Innovative Trading Platform",
            "locked_origin": "https://pocketoption.com",
        },
    }
    adapter = PhoenixGuardWindowTrackingAdapter()
    identity = adapter.probe_chart_identity_v3(surface, source=source)

    assert identity["detected_market"] == "USD/CAD OTC"
    assert identity["detected_timeframe"] == "M5"
    assert identity["identity_ready"] is True
    assert "header" in str(identity["market_source"])
    assert "selector" in str(identity["timeframe_source"])

    signature = cast(
        Callable[[Image.Image], str],
        getattr(window_tracker_module, "_surface_signature"),
    )(surface)
    source_lock = cast(
        Callable[..., dict[str, Any]],
        getattr(window_tracker_module, "_external_frame_source_lock_v3"),
    )(source, surface, window_signature=signature)
    attestation = cast(
        Callable[..., dict[str, Any]],
        getattr(
            window_tracker_module,
            "_external_edge_tab_broker_identity_attestation_v3",
        ),
    )(
        source,
        source_lock,
        identity,
        window_signature=signature,
        capture_epoch=capture_epoch,
    )
    assert attestation["market"] == "USD/CAD OTC"
    assert attestation["timeframe"] == "M5"

    def unexpected_cropped_selector_scan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("same-frame full-surface OCR must own the cropped study")

    monkeypatch.setattr(
        adapter,
        "_detect_market_selector",
        unexpected_cropped_selector_scan,
    )
    monkeypatch.setattr(
        adapter,
        "_detect_timeframe_selector",
        unexpected_cropped_selector_scan,
    )
    result = adapter.study(
        _synthetic_chart_surface("sell", width=900, height=520),
        session_payload={
            "session_id": "edge-legacy-ocr-pair-switch",
            "manual_focus_region": {"enabled": True},
            "_capture_started_epoch_v3": capture_epoch,
            "_broker_identity_attestation_v3": attestation,
            "tracking_summary": {
                "detected_market": "CHF/JPY OTC",
                "market_confidence": 0.94,
                "market_identity_confirmed": True,
                "detected_timeframe": "M5",
                "timeframe_confidence": 0.94,
                "timeframe_identity_confirmed": True,
                "market_selector_visual_fingerprint": "selector_v3_chf_jpy_otc",
            },
            "latest_signal": {
                "market": "CHF/JPY OTC",
                "market_confidence": 0.94,
                "market_identity_confirmed": True,
                "focus_timeframe": "M5",
                "focus_timeframe_confidence": 0.94,
                "timeframe_identity_confirmed": True,
                "market_selector_visual_fingerprint": "selector_v3_chf_jpy_otc",
            },
        },
    )

    assert result.latest_signal["market"] == "USD/CAD OTC"
    assert result.latest_signal["focus_timeframe"] == "M5"
    assert result.latest_signal["market_selector_pair_changed"] is True
    assert result.latest_signal["market_selector_rebind_required"] is False
    assert result.latest_signal["market_identity_confirmed"] is True
    assert result.latest_signal["timeframe_identity_confirmed"] is True


def test_new_external_frame_pending_identity_invalidates_prior_verified_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pair switch can never inherit the preceding frame's verified identity."""

    tracker = ContinuousWindowTrackerService(root_dir=tmp_path / "identity-veto")
    session_id = "identity-veto"
    tracker.create_session(session_id=session_id)
    claim = tracker.claim_external_source(
        session_id,
        source_id="edge-chart-region-v3",
        sequence_id="edge-identity-veto-sequence",
        source_type="browser_tab_roi_capture",
        selection_id="edge-identity-veto-selection",
        display_name="Pocket Option chart",
        coordinate_space="edge_tab_roi_v1",
    )
    seeded = tracker.load_session_payload(session_id)
    seeded["market"] = "CHF/JPY OTC"
    seeded["current_chart_identity_v3"] = {
        "schema_version": "PG_CURRENT_CHART_IDENTITY_V3",
        "state": "STUDY_IDENTITY_CONFIRMED",
        "symbol": "CHF/JPY OTC",
        "timeframe": "M5",
        "market_identity_confirmed": True,
        "timeframe_identity_confirmed": True,
        "market_confidence": 0.96,
        "timeframe_confidence": 0.96,
        "frame_id": 41,
    }
    seeded["tracking_summary"] = {
        "detected_market": "CHF/JPY OTC",
        "detected_timeframe": "M5",
        "market_identity_confirmed": True,
        "timeframe_identity_confirmed": True,
    }
    seeded["latest_signal"] = {
        "market": "CHF/JPY OTC",
        "focus_timeframe": "M5",
        "market_identity_confirmed": True,
        "timeframe_identity_confirmed": True,
    }
    save_session = cast(
        Callable[[dict[str, Any]], None],
        getattr(tracker, "_save_session"),
    )
    save_session(seeded)
    observed: dict[str, Any] = {}

    def inspect_pending_identity(*_args: Any, **_kwargs: Any) -> bool:
        during_ingest = tracker.load_session_payload(session_id)
        observed.update(
            cast(dict[str, Any], during_ingest["current_chart_identity_v3"])
        )
        assert during_ingest["market"] == ""
        return True

    monkeypatch.setattr(tracker, "_capture_and_analyze", inspect_pending_identity)
    tracker.ingest_external_frame(
        session_id,
        Image.new("RGB", (1280, 720), color=(21, 26, 38)),
        source_id="edge-chart-region-v3",
        source_url="https://pocketoption.com/en/cabinet/demo-quick-high-low/",
        sequence_id="edge-identity-veto-sequence",
        capture_epoch_ms=int(time.time() * 1000.0),
        frame_id=42,
        metadata={
            "source_type": "browser_tab_roi_capture",
            "coordinate_space": "edge_tab_roi_v1",
            "source_generation": int(claim["source_generation"]),
            "source_lease_id": str(claim["source_lease_id"]),
            "source_render_fresh": True,
            "selection_id": "edge-identity-veto-selection",
            "extension_id": "edge-extension-id",
            "extension_version": "0.3.9",
            "locked_tab_id": "17",
            "locked_tab_title": "The Most Innovative Trading Platform",
            "locked_origin": "https://pocketoption.com",
        },
    )

    assert observed["state"] == "FRAME_IDENTITY_PENDING"
    assert observed["symbol"] == ""
    assert observed["timeframe"] == ""
    assert observed["market_identity_confirmed"] is False
    assert observed["timeframe_identity_confirmed"] is False
    assert observed["prior_identity_invalidated"] is True
    assert observed["source_frame_id"] == 42
    assert observed["decision_authority"] is False
    assert "CHF/JPY" not in str(observed)


def test_external_source_heartbeat_uses_restart_safe_lease_sidecar(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_dir = tmp_path / "lease-sidecar"
    session_id = "lease-sidecar-session"
    service = ContinuousWindowTrackerService(root_dir=root_dir)
    service.create_session(session_id=session_id)
    claim = service.claim_external_source(
        session_id,
        source_id="edge-chart-region-v3",
        sequence_id="edge-sequence-lease-sidecar",
        source_type="browser_tab_roi_capture",
        selection_id="edge-selection-lease-sidecar",
        display_name="Pocket Option chart",
        coordinate_space="edge_tab_roi_v1",
    )
    sidecar_path = (
        root_dir
        / "sessions"
        / session_id
        / "external_source_lease_v3.json"
    )
    assert sidecar_path.is_file()

    def unexpected_full_session_read(_session_id: str) -> dict[str, Any]:
        raise AssertionError("heartbeat hot path parsed the full session")

    monkeypatch.setattr(service, "_require_session", unexpected_full_session_read)
    heartbeat = service.heartbeat_external_source(
        session_id,
        source_id="edge-chart-region-v3",
        sequence_id="edge-sequence-lease-sidecar",
        source_generation=int(claim["source_generation"]),
        source_lease_id=str(claim["source_lease_id"]),
        capture_epoch_ms=int(time.time() * 1000.0),
        source_render_fresh=True,
    )
    assert heartbeat["source_id"] == "edge-chart-region-v3"

    restarted = ContinuousWindowTrackerService(root_dir=root_dir)
    monkeypatch.setattr(restarted, "_require_session", unexpected_full_session_read)
    restarted_heartbeat = restarted.heartbeat_external_source(
        session_id,
        source_id="edge-chart-region-v3",
        sequence_id="edge-sequence-lease-sidecar",
        source_generation=int(claim["source_generation"]),
        source_lease_id=str(claim["source_lease_id"]),
        capture_epoch_ms=int(time.time() * 1000.0),
        source_render_fresh=True,
    )
    assert restarted_heartbeat["source_generation"] == int(
        claim["source_generation"]
    )

    with pytest.raises(window_tracker_module.ExternalSourceLeaseError) as rejected:
        restarted.heartbeat_external_source(
            session_id,
            source_id="edge-chart-region-v3",
            sequence_id="edge-sequence-lease-sidecar",
            source_generation=int(claim["source_generation"]),
            source_lease_id="superseded-lease",
            capture_epoch_ms=int(time.time() * 1000.0),
            source_render_fresh=True,
        )
    assert rejected.value.reason_code == "SOURCE_SUPERSEDED"


def test_same_frame_verified_edge_tab_identity_unblocks_cropped_chart_study(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    surface = _synthetic_chart_surface("buy", width=900, height=520)
    capture_epoch = time.time()
    attestation = _edge_tab_identity_attestation(
        surface,
        capture_epoch=capture_epoch,
    )
    assert attestation["schema_version"] == "PG_EDGE_TAB_BROKER_IDENTITY_ATTESTATION_V3"
    assert attestation["browser_tab_identity_verified"] is True
    assert attestation["broker_click_safe"] is False

    def unexpected_selector_scan(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("same-frame attestation must bypass chart-crop OCR")

    monkeypatch.setattr(adapter, "_detect_market_selector", unexpected_selector_scan)
    monkeypatch.setattr(adapter, "_detect_timeframe_selector", unexpected_selector_scan)
    result = adapter.study(
        surface,
        session_payload={
            "session_id": "edge-roi-live",
            "manual_focus_region": {"enabled": True},
            "_capture_started_epoch_v3": capture_epoch,
            "_broker_identity_attestation_v3": attestation,
            "tracking_summary": {
                "detected_market": "CHF/JPY OTC",
                "market_confidence": 0.92,
                "market_identity_confirmed": True,
                "detected_timeframe": "M5",
                "timeframe_confidence": 0.93,
                "timeframe_identity_confirmed": True,
                "market_selector_visual_fingerprint": "selector_v3_chf_jpy_otc",
            },
            "latest_signal": {
                "market": "CHF/JPY OTC",
                "market_confidence": 0.92,
                "market_identity_confirmed": True,
                "focus_timeframe": "M5",
                "focus_timeframe_confidence": 0.93,
                "timeframe_identity_confirmed": True,
                "market_selector_visual_fingerprint": "selector_v3_chf_jpy_otc",
            },
        },
    )

    assert result.latest_signal["market"] == "CAD/JPY OTC"
    assert result.latest_signal["focus_timeframe"] == "M5"
    assert (
        result.latest_signal["market_source"]
        == "same_frame_edge_tab_broker_identity"
    )
    assert result.latest_signal["market_selector_rebind_required"] is False
    assert result.latest_signal["market_selector_pair_changed"] is True
    assert result.latest_signal["market_selector_visual_fingerprint"] == (
        "selector_v3_cad_jpy_otc"
    )
    assert result.latest_signal["market_identity_confirmed"] is True
    assert result.latest_signal["timeframe_identity_confirmed"] is True


def test_edge_tab_ingest_preserves_authoritative_roi_for_identity_and_study(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    surface = _synthetic_chart_surface("buy", width=1920, height=1080)
    probe_sizes: list[tuple[int, int]] = []
    study_sizes: list[tuple[int, int]] = []
    study_attestations: list[dict[str, Any]] = []

    def probe_identity(
        image: Image.Image,
        *,
        source: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        del source
        probe_sizes.append(image.size)
        signature = cast(
            Callable[[Image.Image], str],
            getattr(window_tracker_module, "_surface_signature"),
        )(image)
        return {
            "schema_version": "PG_CHART_IDENTITY_PROBE_V3",
            "detected_market": "CAD/JPY OTC",
            "market_source": "broker_header_text",
            "market_confidence": 0.91,
            "market_bbox": [120, 213, 215, 227],
            "detected_timeframe": "M5",
            "timeframe_source": "broker_selector_chip",
            "timeframe_confidence": 0.93,
            "timeframe_bbox": [256, 193, 281, 216],
            "identity_ready": True,
            "identity_conflict": False,
            "study_source_only": True,
            "broker_click_safe": False,
            "broker_surface_hash": signature,
        }

    original_study = adapter.study

    def record_study(
        image: Image.Image,
        *,
        session_payload: Mapping[str, Any] | None = None,
    ) -> TrackingStudy:
        study_sizes.append(image.size)
        attestation = (session_payload or {}).get(
            "_broker_identity_attestation_v3",
            {},
        )
        study_attestations.append(
            dict(cast(Mapping[str, Any], attestation))
            if isinstance(attestation, Mapping)
            else {}
        )
        return original_study(image, session_payload=session_payload)

    monkeypatch.setattr(adapter, "probe_chart_identity_v3", probe_identity)
    monkeypatch.setattr(adapter, "_detect_market_selector", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(adapter, "_detect_timeframe_selector", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(adapter, "study", record_study)
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path / "edge-full-frame-identity",
        tracking_adapter=adapter,
    )

    def reject_secondary_chart_plane_crop(**_kwargs: Any) -> Any:
        raise AssertionError(
            "a leased Edge ROI is already the authoritative geometry plane"
        )

    monkeypatch.setattr(
        tracker,
        "_derive_study_surface",
        reject_secondary_chart_plane_crop,
    )
    session_id = "edge-full-frame-identity"
    tracker.create_session(session_id=session_id)
    claim = tracker.claim_external_source(
        session_id,
        source_id="edge-chart-region-v3",
        sequence_id="edge-roi-17-sequence",
        source_type="browser_tab_roi_capture",
        selection_id="edge-selection-17",
        display_name="The Most Innovative Trading Platform",
        coordinate_space="edge_tab_roi_v1",
    )
    capture_epoch = time.time()
    result = tracker.ingest_external_frame(
        session_id,
        surface,
        source_id="edge-chart-region-v3",
        source_url="https://pocketoption.com/en/cabinet/demo-quick-high-low/",
        sequence_id="edge-roi-17-sequence",
        capture_epoch_ms=int(capture_epoch * 1000.0),
        frame_id=1,
        metadata={
            "source_type": "browser_tab_roi_capture",
            "coordinate_space": "edge_tab_roi_v1",
            "source_generation": int(claim["source_generation"]),
            "source_lease_id": str(claim["source_lease_id"]),
            "source_render_fresh": True,
            "selection_id": "edge-selection-17",
            "extension_id": "edge-extension-id",
            "locked_tab_id": "17",
            "locked_tab_title": "The Most Innovative Trading Platform",
            "locked_origin": "https://pocketoption.com",
        },
    )

    assert result["frame_ingest"]["accepted"] is True
    assert probe_sizes == [(1920, 1080)]
    assert study_sizes == [(1920, 1080)]
    assert study_attestations[0]["schema_version"] == (
        "PG_EDGE_TAB_BROKER_IDENTITY_ATTESTATION_V3"
    )
    assert study_attestations[0]["identity_probe_derivation"] == (
        "same_frame_window_artifact_raster_v1"
    )
    assert study_attestations[0]["identity_probe_parent_surface_hash"]
    assert study_attestations[0]["identity_probe_raster_hash"]
    session = tracker.get_session(session_id)
    integrity = cast(
        Mapping[str, Any],
        session["tracking_summary"]["artifact_integrity"],
    )
    assert integrity["selected_plane"] == {"width": 1920, "height": 1080}
    assert integrity["study_plane"] == {"width": 1920, "height": 1080}
    assert integrity["matches_selected_plane"] is True
    assert session["tracking_summary"]["focus_region"][
        "study_surface_contract"
    ] == "authoritative_edge_tab_roi_v1"
    assert session["latest_signal"]["market"] == "CAD/JPY OTC"
    assert session["latest_signal"]["focus_timeframe"] == "M5"
    assert session["latest_signal"]["market_source"] == (
        "same_frame_edge_tab_broker_identity"
    )
    assert session["latest_signal"]["timeframe_identity_confirmed"] is True
    assert session["broker_surface"]["broker_click_safe"] is False

    second_surface = surface.copy()
    second_surface.putpixel((1000, 700), (245, 245, 245))
    second_result = tracker.ingest_external_frame(
        session_id,
        second_surface,
        source_id="edge-chart-region-v3",
        source_url="https://pocketoption.com/en/cabinet/demo-quick-high-low/",
        sequence_id="edge-roi-17-sequence",
        capture_epoch_ms=int(time.time() * 1000.0),
        frame_id=2,
        metadata={
            "source_type": "browser_tab_roi_capture",
            "coordinate_space": "edge_tab_roi_v1",
            "source_generation": int(claim["source_generation"]),
            "source_lease_id": str(claim["source_lease_id"]),
            "source_render_fresh": True,
            "selection_id": "edge-selection-17",
            "extension_id": "edge-extension-id",
            "locked_tab_id": "17",
            "locked_tab_title": "The Most Innovative Trading Platform",
            "locked_origin": "https://pocketoption.com",
        },
    )

    assert second_result["frame_ingest"]["accepted"] is True
    assert probe_sizes == [(1920, 1080), (1920, 1080)]
    assert study_sizes == [(1920, 1080), (1920, 1080)]
    assert len(study_attestations) == 2
    assert all(
        row["schema_version"] == "PG_EDGE_TAB_BROKER_IDENTITY_ATTESTATION_V3"
        for row in study_attestations
    )
    second_session = tracker.get_session(session_id)
    assert second_session["latest_signal"]["timeframe_identity_confirmed"] is True
    assert second_session["latest_signal"]["market_identity_confirmed"] is True


def test_external_frame_receipt_reports_processing_without_false_stale_gap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path / "edge-processing-heartbeat",
    )
    session_id = "edge-processing-heartbeat"
    tracker.create_session(session_id=session_id)
    claim = tracker.claim_external_source(
        session_id,
        source_id="edge-chart-region-v3",
        sequence_id="edge-processing-sequence",
        source_type="browser_tab_roi_capture",
        selection_id="edge-processing-selection",
        display_name="Pocket Option chart",
        coordinate_space="edge_tab_roi_v1",
    )
    observed_during_analysis: dict[str, Any] = {}

    def inspect_processing_state(*_args: Any, **_kwargs: Any) -> bool:
        observed_during_analysis.update(
            tracker.get_session_snapshot(session_id)["capture_source_v3"]
        )
        return True

    monkeypatch.setattr(tracker, "_capture_and_analyze", inspect_processing_state)
    result = tracker.ingest_external_frame(
        session_id,
        Image.new("RGB", (640, 360), color=(21, 26, 38)),
        source_id="edge-chart-region-v3",
        source_url="https://pocketoption.com/en/cabinet/demo-quick-high-low/",
        sequence_id="edge-processing-sequence",
        capture_epoch_ms=int(time.time() * 1000.0),
        frame_id=1,
        metadata={
            "source_type": "browser_tab_roi_capture",
            "coordinate_space": "edge_tab_roi_v1",
            "source_generation": int(claim["source_generation"]),
            "source_lease_id": str(claim["source_lease_id"]),
            "source_render_fresh": True,
            "selection_id": "edge-processing-selection",
        },
    )

    assert observed_during_analysis["state"] == "VALIDATING"
    assert observed_during_analysis["reason_code"] == "FRAME_PROCESSING"
    assert observed_during_analysis["fresh"] is True
    assert observed_during_analysis["decision_usable"] is False
    assert observed_during_analysis["stream"]["processing"] is True
    completed = result["capture_source_v3"]
    assert completed["state"] == "LIVE"
    assert completed["fresh"] is True
    assert completed["decision_usable"] is True
    assert completed["stream"]["processing"] is False
    assert completed["stream"]["last_analysis_completed_epoch"] > 0.0


def test_edge_tab_identity_attestation_fails_closed_on_contract_mutations() -> None:
    surface = _synthetic_chart_surface("buy", width=900, height=520)
    signature = cast(
        Callable[[Image.Image], str],
        getattr(window_tracker_module, "_surface_signature"),
    )(surface)
    capture_epoch = time.time()
    source = {
        "source_id": "edge-chart-region-v3",
        "source_type": "browser_tab_roi_capture",
        "source_url": "https://pocketoption.com/en/cabinet/demo-quick-high-low/",
        "sequence_id": "edge-roi-17-sequence",
        "coordinate_space": "edge_tab_roi_v1",
        "source_generation": 4,
        "frame_id": 11,
        "metadata": {
            "source_lease_id": "edge-lease-secret-4",
            "source_render_fresh": True,
            "extension_id": "edge-extension-id",
            "locked_tab_id": "17",
            "locked_tab_title": "The Most Innovative Trading Platform",
            "locked_origin": "https://pocketoption.com",
        },
    }
    build_lock = cast(
        Callable[..., dict[str, Any]],
        getattr(window_tracker_module, "_external_frame_source_lock_v3"),
    )
    build_attestation = cast(
        Callable[..., dict[str, Any]],
        getattr(
            window_tracker_module,
            "_external_edge_tab_broker_identity_attestation_v3",
        ),
    )
    lock = build_lock(source, surface, window_signature=signature)
    identity_surface = {
        "detected_market": "CAD/JPY OTC",
        "market_confidence": 0.91,
        "market_source": "broker_header_text",
        "market_bbox": [120, 213, 215, 227],
        "detected_timeframe": "M5",
        "timeframe_confidence": 0.93,
        "timeframe_source": "broker_selector_chip",
        "timeframe_bbox": [256, 193, 281, 216],
        "broker_surface_hash": signature,
    }

    assert build_attestation(
        source,
        lock,
        identity_surface,
        window_signature=signature,
        capture_epoch=capture_epoch,
    )
    invalid_contracts: list[
        tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]
    ] = []

    for evidence_key, invalid_value in (
        ("title_valid", False),
        ("url_valid", False),
        ("origin_matches", False),
        ("study_source_only", False),
        ("broker_click_safe", True),
    ):
        mutated_lock = copy.deepcopy(lock)
        cast(dict[str, Any], mutated_lock["evidence"])[evidence_key] = invalid_value
        invalid_contracts.append(
            (copy.deepcopy(source), mutated_lock, copy.deepcopy(identity_surface), evidence_key)
        )

    wrong_origin_source = copy.deepcopy(source)
    cast(dict[str, Any], wrong_origin_source["metadata"])["locked_origin"] = (
        "https://example.invalid"
    )
    invalid_contracts.append(
        (
            wrong_origin_source,
            build_lock(wrong_origin_source, surface, window_signature=signature),
            copy.deepcopy(identity_surface),
            "origin_mismatch",
        )
    )

    missing_lease_source = copy.deepcopy(source)
    cast(dict[str, Any], missing_lease_source["metadata"])["source_lease_id"] = ""
    invalid_contracts.append(
        (missing_lease_source, copy.deepcopy(lock), copy.deepcopy(identity_surface), "lease")
    )

    stale_source = copy.deepcopy(source)
    cast(dict[str, Any], stale_source["metadata"])["source_render_fresh"] = False
    invalid_contracts.append(
        (stale_source, copy.deepcopy(lock), copy.deepcopy(identity_surface), "freshness")
    )

    missing_extension_source = copy.deepcopy(source)
    missing_extension_lock = copy.deepcopy(lock)
    cast(dict[str, Any], missing_extension_lock["evidence"])["extension_id"] = ""
    invalid_contracts.append(
        (
            missing_extension_source,
            missing_extension_lock,
            copy.deepcopy(identity_surface),
            "extension",
        )
    )

    missing_tab_lock = copy.deepcopy(lock)
    cast(dict[str, Any], missing_tab_lock["evidence"])["locked_tab_id"] = ""
    invalid_contracts.append(
        (copy.deepcopy(source), missing_tab_lock, copy.deepcopy(identity_surface), "tab")
    )

    missing_frame_source = copy.deepcopy(source)
    missing_frame_source["frame_id"] = 0
    invalid_contracts.append(
        (missing_frame_source, copy.deepcopy(lock), copy.deepcopy(identity_surface), "frame_id")
    )

    wrong_contract_source = copy.deepcopy(source)
    wrong_contract_source["coordinate_space"] = "edge_tab_content_v1"
    invalid_contracts.append(
        (wrong_contract_source, copy.deepcopy(lock), copy.deepcopy(identity_surface), "space")
    )

    wrong_source_type = copy.deepcopy(source)
    wrong_source_type["source_type"] = "browser_extension_capture"
    invalid_contracts.append(
        (wrong_source_type, copy.deepcopy(lock), copy.deepcopy(identity_surface), "source_type")
    )

    wrong_frame_identity = copy.deepcopy(identity_surface)
    wrong_frame_identity["broker_surface_hash"] = "different-frame-signature"
    invalid_contracts.append(
        (copy.deepcopy(source), copy.deepcopy(lock), wrong_frame_identity, "frame_hash")
    )

    missing_visual_bbox = copy.deepcopy(identity_surface)
    missing_visual_bbox["timeframe_bbox"] = []
    invalid_contracts.append(
        (copy.deepcopy(source), copy.deepcopy(lock), missing_visual_bbox, "visual_bbox")
    )

    for candidate_source, candidate_lock, candidate_identity, case in invalid_contracts:
        assert build_attestation(
            candidate_source,
            candidate_lock,
            candidate_identity,
            window_signature=signature,
            capture_epoch=capture_epoch,
        ) == {}, case


def test_same_frame_wgc_identity_attestation_unblocks_cropped_chart_study(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    surface = _synthetic_chart_surface("buy", width=900, height=520)
    capture_epoch = time.time()
    attestation = _wgc_identity_attestation(
        surface,
        capture_epoch=capture_epoch,
    )
    assert attestation["source_verified"] is True
    assert attestation["broker_click_safe"] is False

    monkeypatch.setattr(adapter, "_detect_market_selector", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(adapter, "_detect_timeframe_selector", lambda *_args, **_kwargs: {})
    result = adapter.study(
        surface,
        session_payload={
            "session_id": "wgc-live",
            "manual_focus_region": {
                "enabled": True,
                "normalized_bbox": [0.0, 0.0, 1.0, 1.0],
            },
            "_capture_started_epoch_v3": capture_epoch,
            "_broker_identity_attestation_v3": attestation,
        },
    )

    assert result.latest_signal["market"] == "CAD/CHF OTC"
    assert result.latest_signal["focus_timeframe"] == "M5"
    assert result.latest_signal["market_source"] == "same_frame_wgc_broker_identity"
    assert result.latest_signal["market_selector_rebind_required"] is False
    assert result.latest_signal["market_selector_studying_new_pair"] is False
    assert result.latest_signal["market_identity_confirmed"] is True
    assert result.latest_signal["timeframe_identity_confirmed"] is True
    scene = cast(
        Mapping[str, Any],
        result.latest_signal["scene_forecast_contribution"],
    )
    assert scene["provider_status"] != "MARKET_IDENTITY_PENDING"


def test_same_frame_wgc_identity_attestation_accepts_standard_fx_chart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    surface = _synthetic_chart_surface("buy", width=900, height=520)
    capture_epoch = time.time()
    attestation = _wgc_identity_attestation(
        surface,
        market="EUR/USD",
        timeframe="M15",
        capture_epoch=capture_epoch,
    )
    assert attestation["market"] == "EUR/USD"
    assert attestation["timeframe"] == "M15"

    monkeypatch.setattr(adapter, "_detect_market_selector", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(adapter, "_detect_timeframe_selector", lambda *_args, **_kwargs: {})
    result = adapter.study(
        surface,
        session_payload={
            "session_id": "wgc-tradingview",
            "manual_focus_region": {"enabled": True},
            "_capture_started_epoch_v3": capture_epoch,
            "_broker_identity_attestation_v3": attestation,
        },
    )

    assert result.latest_signal["market"] == "EUR/USD"
    assert result.latest_signal["focus_timeframe"] == "M15"
    assert result.latest_signal["market_selector_rebind_required"] is False
    assert result.latest_signal["market_identity_confirmed"] is True
    assert result.latest_signal["timeframe_identity_confirmed"] is True


def test_source_agnostic_chart_identity_probe_reads_tradingview_window_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    surface = _synthetic_chart_surface("buy", width=900, height=520)
    monkeypatch.setattr(adapter, "_detect_market_selector", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(adapter, "_detect_timeframe_selector", lambda *_args, **_kwargs: {})

    identity = adapter.probe_chart_identity_v3(
        surface,
        source={
            "source_id": "windows-region-capture-v3",
            "metadata": {
                "window": {
                    "title": "EURUSD · M15 · Advanced chart — TradingView"
                }
            },
        },
    )

    assert identity["schema_version"] == "PG_CHART_IDENTITY_PROBE_V3"
    assert identity["detected_market"] == "EUR/USD"
    assert identity["detected_timeframe"] == "M15"
    assert identity["identity_ready"] is True
    assert identity["identity_conflict"] is False
    assert identity["study_source_only"] is True
    assert identity["broker_click_safe"] is False


def test_source_agnostic_chart_identity_probe_fails_closed_on_incomplete_or_conflicting_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    surface = _synthetic_chart_surface("buy", width=900, height=520)
    monkeypatch.setattr(
        adapter,
        "_detect_market_selector",
        lambda *_args, **_kwargs: {
            "value": "EUR/USD",
            "source": "visual_chart_header",
            "confidence": 0.91,
        },
    )
    monkeypatch.setattr(
        adapter,
        "_detect_timeframe_selector",
        lambda *_args, **_kwargs: {
            "value": "M5",
            "source": "visual_chart_header",
            "confidence": 0.91,
        },
    )

    conflicting = adapter.probe_chart_identity_v3(
        surface,
        source={
            "metadata": {
                "window": {"title": "GBPJPY · M15 · TradingView"}
            }
        },
    )
    assert conflicting["identity_conflict"] is True
    assert conflicting["identity_ready"] is False
    assert conflicting["detected_market"] == ""
    assert conflicting["detected_timeframe"] == ""

    monkeypatch.setattr(adapter, "_detect_market_selector", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(adapter, "_detect_timeframe_selector", lambda *_args, **_kwargs: {})
    incomplete = adapter.probe_chart_identity_v3(
        surface,
        source={"metadata": {"window": {"title": "EUR · M5 · chart"}}},
    )
    assert incomplete["identity_ready"] is False
    assert incomplete["detected_market"] == ""
    assert incomplete["detected_timeframe"] == "M5"


def test_wgc_non_otc_identity_survives_derived_chart_plane_and_publishes_operator_overlays(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from phoenixguard.mobile_api.live_state_v3 import (
        build_live_state_v3_from_tracker_service,
    )
    from phoenixguard.mobile_api.operator_workspace_v1 import (
        build_operator_workspace_v1,
    )

    adapter = PhoenixGuardWindowTrackingAdapter()
    # The derived candle plane deliberately has no chart header. The exact WGC
    # full-surface probe must therefore supply identity before study begins.
    monkeypatch.setattr(adapter, "_detect_market_selector", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(adapter, "_detect_timeframe_selector", lambda *_args, **_kwargs: {})
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path / "wgc-universal-identity",
        tracking_adapter=adapter,
    )
    session_id = "wgc-tradingview-non-otc"
    tracker.create_session(session_id=session_id)
    claim = tracker.claim_external_source(
        session_id,
        source_id="windows-region-capture-v3",
        sequence_id="wgc-sequence-tradingview-1",
        source_type="windows_graphics_capture_roi",
        selection_id="selection-tradingview-1",
        display_name="TradingView EURUSD M15",
        coordinate_space="wgc_hwnd_roi_v1",
    )
    capture_epoch = time.time()
    result = tracker.ingest_external_frame(
        session_id,
        _synthetic_chart_surface("buy", width=1280, height=720),
        source_id="windows-region-capture-v3",
        sequence_id="wgc-sequence-tradingview-1",
        capture_epoch_ms=int(capture_epoch * 1000.0),
        frame_id=1,
        metadata={
            "source_type": "windows_graphics_capture_roi",
            "coordinate_space": "wgc_hwnd_roi_v1",
            "source_generation": int(claim["source_generation"]),
            "source_lease_id": str(claim["source_lease_id"]),
            "source_render_fresh": True,
            "selection_id": "selection-tradingview-1",
            "window": {
                "title": "EURUSD · M15 · Advanced chart — TradingView"
            },
        },
    )

    assert result["frame_ingest"]["accepted"] is True
    session = tracker.get_session(session_id)
    assert session["latest_signal"]["market"] == "EUR/USD"
    assert session["latest_signal"]["focus_timeframe"] == "M15"
    assert session["latest_signal"]["market_selector_rebind_required"] is False
    assert session["latest_signal"]["market_identity_confirmed"] is True
    assert session["latest_signal"]["timeframe_identity_confirmed"] is True

    live_state = build_live_state_v3_from_tracker_service(
        tracker,
        session_id,
        now_epoch=capture_epoch + 0.1,
        overlay_mode="INSPECTOR",
    )
    assert live_state["market"] == "EUR/USD"
    assert live_state["timeframe"] == "M15"
    assert int(live_state["overlay_count"]) > 0
    assert live_state["overlay_objects"]
    workspace = build_operator_workspace_v1(
        live_state,
        now_epoch=capture_epoch + 0.1,
    )
    assert workspace["overlays"]
    assert all(
        int(row["frame_id"]) == int(workspace["surface"]["frame_id"])
        for row in cast(Sequence[Mapping[str, Any]], workspace["overlays"])
    )


def test_wgc_transport_heartbeat_with_identical_pixels_is_live_unchanged_not_stale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from phoenixguard.mobile_api.live_state_v3 import (
        build_live_state_v3_from_tracker_service,
    )
    from phoenixguard.mobile_api.operator_workspace_v1 import (
        build_operator_workspace_v1,
    )

    adapter = PhoenixGuardWindowTrackingAdapter()
    monkeypatch.setattr(adapter, "_detect_market_selector", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(adapter, "_detect_timeframe_selector", lambda *_args, **_kwargs: {})
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path / "wgc-live-unchanged",
        tracking_adapter=adapter,
    )
    session_id = "wgc-live-unchanged"
    tracker.create_session(session_id=session_id)
    claim = tracker.claim_external_source(
        session_id,
        source_id="windows-region-capture-v3",
        sequence_id="wgc-sequence-unchanged-1",
        source_type="windows_graphics_capture_roi",
        selection_id="selection-unchanged-1",
        display_name="TradingView EURUSD M15",
        coordinate_space="wgc_hwnd_roi_v1",
    )
    surface = _synthetic_chart_surface("buy", width=1280, height=720)
    capture_epoch = time.time()
    metadata = {
        "source_type": "windows_graphics_capture_roi",
        "coordinate_space": "wgc_hwnd_roi_v1",
        "source_generation": int(claim["source_generation"]),
        "source_lease_id": str(claim["source_lease_id"]),
        "source_render_fresh": True,
        "selection_id": "selection-unchanged-1",
        "window": {"title": "EURUSD · M15 · Advanced chart — TradingView"},
    }
    first = tracker.ingest_external_frame(
        session_id,
        surface,
        source_id="windows-region-capture-v3",
        sequence_id="wgc-sequence-unchanged-1",
        capture_epoch_ms=int(capture_epoch * 1000.0),
        frame_id=1,
        metadata=metadata,
    )
    baseline_model_frame = int(first["model_vote_frame_id"])
    baseline_capture_count = int(first["capture_count"])
    # The first full model pass may take longer than the source cadence. Use
    # the actual next transport time so the duplicate reaches the no-evidence
    # branch instead of being correctly discarded as an out-of-order frame.
    second_capture_epoch = time.time()

    second = tracker.ingest_external_frame(
        session_id,
        surface.copy(),
        source_id="windows-region-capture-v3",
        sequence_id="wgc-sequence-unchanged-1",
        capture_epoch_ms=int(second_capture_epoch * 1000.0),
        frame_id=2,
        metadata=metadata,
    )

    assert second["frame_ingest"]["accepted"] is True
    assert int(second["model_vote_frame_id"]) == baseline_model_frame
    assert int(second["capture_count"]) == baseline_capture_count
    assert second["capture_source_v3"]["state"] == "LIVE"
    assert second["capture_source_v3"]["fresh"] is True
    assert int(second["capture_source_v3"]["last_frame_id"]) == 2
    observation = cast(dict[str, Any], second["visual_observation_v3"])
    assert observation["status"] == "LIVE_FRAME_UNCHANGED"
    assert observation["transport_state"] == "LIVE"
    assert observation["transport_fresh"] is True
    assert observation["study_update_state"] == "UNCHANGED"
    assert observation["new_visual_evidence"] is False
    assert second["latest_signal"]["status"] == "live_frame_unchanged"
    assert second["latest_signal"]["execution_permission"] == "WAIT"
    assert second["decision_valid_until_epoch"] == 0.0

    live_state = build_live_state_v3_from_tracker_service(
        tracker,
        session_id,
        now_epoch=second_capture_epoch + 0.1,
        overlay_mode="INSPECTOR",
    )
    workspace = build_operator_workspace_v1(
        live_state,
        now_epoch=second_capture_epoch + 0.1,
    )
    assert workspace["freshness"]["state"] == "UNCHANGED"
    assert "Chart stream live" in str(workspace["freshness"]["label"])
    assert workspace["permission"]["allowed"] is False
    assert workspace["overlays"]
    overlay_lifecycles = {
        str(row["lifecycle"])
        for row in cast(Sequence[Mapping[str, Any]], workspace["overlays"])
    }
    assert "current" in overlay_lifecycles
    assert "stale_diagnostic" not in overlay_lifecycles


def test_same_frame_wgc_identity_attestation_fails_closed_on_selector_conflict() -> None:
    surface = _synthetic_chart_surface("buy", width=900, height=520)
    capture_epoch = time.time()
    attestation = _wgc_identity_attestation(
        surface,
        capture_epoch=capture_epoch,
    )
    reconcile = cast(
        Callable[..., tuple[dict[str, Any], dict[str, Any]]],
        getattr(
            window_tracker_module,
            "_reconcile_wgc_broker_identity_attestation_v3",
        ),
    )

    market_selector, timeframe_selector = reconcile(
        {
            "value": "EUR/USD OTC",
            "source": "header_text",
            "confidence": 0.94,
            "market_selector_rebind_required": False,
        },
        {"value": "M5", "source": "selector_chip", "confidence": 0.94},
        {
            "_capture_started_epoch_v3": capture_epoch,
            "_broker_identity_attestation_v3": attestation,
        },
        {"min_market_confidence": 0.42, "min_timeframe_confidence": 0.42},
    )

    assert market_selector["value"] == "EUR/USD OTC"
    assert market_selector["market_selector_rebind_required"] is True
    assert market_selector["studying_new_pair"] is True
    assert market_selector["broker_identity_attestation_conflict"] is True
    assert timeframe_selector["broker_identity_attestation_conflict"] is True


def test_wgc_identity_attestation_rejects_unleased_or_low_confidence_source() -> None:
    surface = _synthetic_chart_surface("buy", width=900, height=520)
    capture_epoch = time.time()
    low_confidence = _wgc_identity_attestation(
        surface,
        market_confidence=0.20,
        capture_epoch=capture_epoch,
    )
    assert low_confidence["market_confidence"] == pytest.approx(0.20)

    reconcile = cast(
        Callable[..., tuple[dict[str, Any], dict[str, Any]]],
        getattr(
            window_tracker_module,
            "_reconcile_wgc_broker_identity_attestation_v3",
        ),
    )
    market_selector, timeframe_selector = reconcile(
        {},
        {},
        {
            "_capture_started_epoch_v3": capture_epoch,
            "_broker_identity_attestation_v3": low_confidence,
        },
        {"min_market_confidence": 0.42, "min_timeframe_confidence": 0.42},
    )
    assert market_selector == {}
    assert timeframe_selector == {}

    signature = cast(
        Callable[[Image.Image], str],
        getattr(window_tracker_module, "_surface_signature"),
    )(surface)
    unleased_source = {
        "source_id": "windows-region-capture-v3",
        "source_type": "windows_graphics_capture_roi",
        "sequence_id": "wgc-sequence-7",
        "coordinate_space": "wgc_hwnd_roi_v1",
        "source_generation": 3,
        "metadata": {},
    }
    source_lock = cast(
        Callable[..., dict[str, Any]],
        getattr(window_tracker_module, "_external_frame_source_lock_v3"),
    )(unleased_source, surface, window_signature=signature)
    build_attestation = cast(
        Callable[..., dict[str, Any]],
        getattr(
            window_tracker_module,
            "_external_wgc_broker_identity_attestation_v3",
        ),
    )
    assert build_attestation(
        unleased_source,
        source_lock,
        {
            "detected_market": "CAD/CHF OTC",
            "market_confidence": 0.91,
            "detected_timeframe": "M5",
            "timeframe_confidence": 0.93,
            "broker_surface_hash": signature,
        },
        window_signature=signature,
        capture_epoch=capture_epoch,
    ) == {}

    assert _wgc_identity_attestation(
        surface,
        market="EUR",
        timeframe="M5",
        capture_epoch=capture_epoch,
    ) == {}


def test_wgc_identity_attestation_rejects_lock_and_frame_lineage_mismatch() -> None:
    surface = _synthetic_chart_surface("buy", width=900, height=520)
    capture_epoch = time.time()
    signature = cast(
        Callable[[Image.Image], str],
        getattr(window_tracker_module, "_surface_signature"),
    )(surface)
    source = {
        "source_id": "windows-region-capture-v3",
        "source_type": "windows_graphics_capture_roi",
        "sequence_id": "wgc-sequence-7",
        "coordinate_space": "wgc_hwnd_roi_v1",
        "source_generation": 3,
        "metadata": {"source_lease_id": "lease-secret-7"},
    }
    build_lock = cast(
        Callable[..., dict[str, Any]],
        getattr(window_tracker_module, "_external_frame_source_lock_v3"),
    )
    build_attestation = cast(
        Callable[..., dict[str, Any]],
        getattr(
            window_tracker_module,
            "_external_wgc_broker_identity_attestation_v3",
        ),
    )
    valid_lock = build_lock(source, surface, window_signature=signature)
    broker_surface = {
        "detected_market": "CAD/CHF OTC",
        "market_confidence": 0.91,
        "detected_timeframe": "M5",
        "timeframe_confidence": 0.93,
        "broker_surface_hash": signature,
    }

    mismatched_lock = copy.deepcopy(valid_lock)
    cast(dict[str, Any], mismatched_lock["evidence"])["sequence_id"] = (
        "another-sequence"
    )
    assert build_attestation(
        source,
        mismatched_lock,
        broker_surface,
        window_signature=signature,
        capture_epoch=capture_epoch,
    ) == {}

    mismatched_surface = dict(broker_surface)
    mismatched_surface["broker_surface_hash"] = "different-frame-signature"
    assert build_attestation(
        source,
        valid_lock,
        mismatched_surface,
        window_signature=signature,
        capture_epoch=capture_epoch,
    ) == {}

    wrong_coordinate_source = dict(source)
    wrong_coordinate_source["coordinate_space"] = "edge_tab_roi_v1"
    assert build_attestation(
        wrong_coordinate_source,
        valid_lock,
        broker_surface,
        window_signature=signature,
        capture_epoch=capture_epoch,
    ) == {}

    wrong_source_id = dict(source)
    wrong_source_id["source_id"] = "another-wgc-producer"
    assert build_attestation(
        wrong_source_id,
        valid_lock,
        broker_surface,
        window_signature=signature,
        capture_epoch=capture_epoch,
    ) == {}


def test_pending_unsuffixed_market_survives_one_empty_read_then_accepts_otc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    fingerprint = cast(
        Callable[[Image.Image], str],
        getattr(window_tracker_module, "_market_selector_visual_fingerprint"),
    )
    surface = _synthetic_chart_surface("buy", width=900, height=520)
    _paint_realistic_market_selector(surface, "EUR/NZD OTC")
    selector_fingerprint = fingerprint(surface)
    detector_values: Iterator[dict[str, Any]] = iter(
        (
            {"value": "EUR/NZD", "source": "header_text", "confidence": 0.79},
            {},
            {"value": "EUR/NZD OTC", "source": "header_text", "confidence": 0.79},
        )
    )

    def detect_market_selector(
        image: Image.Image,
        timeframe_selector: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        _ = image
        _ = timeframe_selector
        return next(detector_values)

    monkeypatch.setattr(adapter, "_detect_market_selector", detect_market_selector)
    payload: dict[str, Any] = {
        "session_id": "pocket-live",
        "manual_focus_region": {"enabled": True},
        "tracking_summary": {
            "detected_market": "EUR/NZD",
            "market_confidence": 0.91,
            "detected_timeframe": "M5",
            "timeframe_confidence": 1.0,
            "market_selector_visual_fingerprint": selector_fingerprint,
        },
        "latest_signal": {
            "market": "EUR/NZD",
            "market_confidence": 0.91,
            "focus_timeframe": "M5",
            "focus_timeframe_confidence": 1.0,
            "market_selector_visual_fingerprint": selector_fingerprint,
        },
    }

    first = adapter.study(surface, session_payload=payload)
    second = adapter.study(
        surface,
        session_payload={
            **payload,
            "tracking_summary": first.tracking_summary,
            "latest_signal": first.latest_signal,
        },
    )
    third = adapter.study(
        surface,
        session_payload={
            **payload,
            "tracking_summary": second.tracking_summary,
            "latest_signal": second.latest_signal,
        },
    )

    assert first.latest_signal["market_selector_rebind_required"] is True
    assert second.latest_signal["market"] == "EUR/NZD"
    assert second.latest_signal["market_selector_rebind_required"] is True
    assert second.latest_signal["market_identity_confirmed"] is False
    assert third.latest_signal["market"] == "EUR/NZD OTC"
    assert third.latest_signal["market_selector_rebind_required"] is False
    assert third.latest_signal["market_identity_confirmed"] is True


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
    _paint_realistic_market_selector(old_surface, "AUD/NZD OTC")
    _paint_realistic_market_selector(new_surface, "EUR/USD OTC")
    previous_fingerprint = fingerprint(old_surface)
    assert previous_fingerprint != fingerprint(new_surface)

    detector_calls = 0
    timeframe_detector_calls = 0

    def detect_timeframe_selector(image: Image.Image) -> dict[str, Any]:
        nonlocal timeframe_detector_calls
        _ = image
        timeframe_detector_calls += 1
        return {"value": "M5", "source": "selector_chip", "confidence": 0.93}

    def detect_market_selector(
        image: Image.Image,
        timeframe_selector: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        nonlocal detector_calls
        _ = image
        _ = timeframe_selector
        detector_calls += 1
        return {"value": "EUR/USD OTC", "source": "header_text", "confidence": 0.93}

    monkeypatch.setattr(adapter, "_detect_timeframe_selector", detect_timeframe_selector)
    monkeypatch.setattr(adapter, "_detect_market_selector", detect_market_selector)
    study = adapter.study(
        new_surface,
        session_payload={
            "session_id": "pocket-live",
            "manual_focus_region": {"enabled": True},
            "tracking_summary": {
                "detected_market": "AUD/NZD",
                "market_confidence": 0.91,
                "detected_timeframe": "M1",
                "timeframe_confidence": 1.0,
                "market_selector_visual_fingerprint": previous_fingerprint,
            },
            "latest_signal": {
                "market": "AUD/NZD",
                "market_confidence": 0.91,
                "focus_timeframe": "M1",
                "focus_timeframe_confidence": 1.0,
                "market_selector_visual_fingerprint": previous_fingerprint,
            },
        },
    )

    assert detector_calls == 1
    assert timeframe_detector_calls == 1
    assert study.latest_signal["market"] == "EUR/USD OTC"
    assert study.latest_signal["market_source"] == "header_text"
    assert study.latest_signal["focus_timeframe"] == "M5"
    assert study.latest_signal["market_selector_visual_changed"] is True
    assert study.tracking_summary["detected_market"] == "EUR/USD OTC"
    assert study.tracking_summary["detected_timeframe"] == "M5"


def test_window_tracker_fails_closed_and_retries_ocr_after_unread_pair_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    fingerprint = cast(
        Callable[[Image.Image], str],
        getattr(window_tracker_module, "_market_selector_visual_fingerprint"),
    )
    old_surface = _synthetic_chart_surface("buy", width=900, height=520)
    new_surface = _synthetic_chart_surface("sell", width=900, height=520)
    _paint_realistic_market_selector(old_surface, "AUD/NZD OTC")
    _paint_realistic_market_selector(new_surface, "EUR/USD OTC")
    previous_fingerprint = fingerprint(old_surface)
    detector_calls = 0
    timeframe_detector_calls = 0

    def confirmed_timeframe(_image: Image.Image) -> dict[str, Any]:
        nonlocal timeframe_detector_calls
        timeframe_detector_calls += 1
        return {"value": "M1", "source": "selector_chip", "confidence": 0.93}

    def unread_selector(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal detector_calls
        detector_calls += 1
        return {}

    monkeypatch.setattr(adapter, "_detect_timeframe_selector", confirmed_timeframe)
    monkeypatch.setattr(adapter, "_detect_market_selector", unread_selector)
    first = adapter.study(
        new_surface,
        session_payload={
            "session_id": "pocket-live",
            "manual_focus_region": {"enabled": True},
            "tracking_summary": {
                "detected_market": "AUD/NZD OTC",
                "market_confidence": 0.91,
                "detected_timeframe": "M1",
                "timeframe_confidence": 0.91,
                "market_selector_visual_fingerprint": previous_fingerprint,
            },
            "latest_signal": {
                "market": "AUD/NZD OTC",
                "market_confidence": 0.91,
                "focus_timeframe": "M1",
                "market_selector_visual_fingerprint": previous_fingerprint,
            },
        },
    )

    first_scene = cast(Mapping[str, Any], first.latest_signal["scene_forecast_contribution"])
    assert detector_calls == 1
    assert first.latest_signal["market"] == ""
    assert first.latest_signal["market_selector_rebind_required"] is True
    assert first.latest_signal["market_selector_studying_new_pair"] is True
    assert first_scene["provider_status"] == "MARKET_IDENTITY_PENDING"
    assert first_scene["forecast_available"] is False
    assert first_scene["line_points"] == []
    assert first_scene["forecast_candles"] == []

    def rebound_selector(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal detector_calls
        detector_calls += 1
        return {
            "value": "EUR/USD OTC",
            "source": "header_text",
            "confidence": 0.93,
        }

    monkeypatch.setattr(adapter, "_detect_market_selector", rebound_selector)
    second = adapter.study(
        new_surface,
        session_payload={
            "session_id": "pocket-live",
            "manual_focus_region": {"enabled": True},
            "tracking_summary": first.tracking_summary,
            "latest_signal": first.latest_signal,
        },
    )

    second_scene = cast(Mapping[str, Any], second.latest_signal["scene_forecast_contribution"])
    assert detector_calls == 2
    assert timeframe_detector_calls == 2
    assert second.latest_signal["market"] == "EUR/USD OTC"
    assert second.latest_signal["market_selector_rebind_required"] is False
    assert second.latest_signal["market_selector_studying_new_pair"] is False
    assert second.latest_signal["market_selector_identity_rebound"] is True
    assert second_scene["pair"] == "EUR/USD OTC"
    assert second_scene["timeframe"] == "M1"
    assert second_scene["identity_contract_status"] == "CONFIRMED"


def test_scene_forecast_uses_confirmed_chart_timeframe_not_hf_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    captured_lstm: dict[str, Any] = {}

    def capture_scene(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        line = [[0.40 + index * 0.02, 0.55 - index * 0.005] for index in range(13)]
        candles = [
            {
                "step": index,
                "label": f"E{index}",
                "x_norm": line[index][0],
                "open_y_norm": line[index - 1][1],
                "high_y_norm": min(line[index - 1][1], line[index][1]) - 0.002,
                "low_y_norm": max(line[index - 1][1], line[index][1]) + 0.002,
                "close_y_norm": line[index][1],
                "movement_side": "BUY",
                "body_bias": "BUY",
            }
            for index in range(1, 13)
        ]
        return {
            "schema_version": "PG_SCENE_FORECAST_CONTRIBUTION_V3",
            "path_side": "BUY",
            "side": "BUY",
            "probability_calibrated": False,
            "raw_side_probabilities": {"BUY": 0.58, "HOLD": 0.24, "SELL": 0.18},
            "line_points": line,
            "forecast_candles": candles,
            "forecast_scenarios": [
                {
                    "role": role,
                    "side": "BUY",
                    "probability": probability,
                    "selected": role == "base",
                    "line_points": line,
                    "forecast_candles": candles,
                }
                for role, probability in (("base", 0.58), ("bull", 0.24), ("bear", 0.18))
            ],
            "model_version": "TEST_SCENE_FORECASTER",
        }

    def capture_lstm(**kwargs: Any) -> dict[str, Any]:
        captured_lstm.update(kwargs)
        return {
            "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
            "artifact_available": True,
            "artifact_loaded": True,
            "artifact_production_gate_passed": False,
            "production_authorized": False,
            "forecast_available": True,
            "fresh": True,
            "path_side": "SELL",
            "side": "SELL",
            "selective_authorized": False,
            "selective_status": "NO_EDGE",
            "trade_authorization_status": "NO_EDGE",
            "contribution": 0.0,
            "forecast_path": [
                {
                    "step": 1,
                    "movement_direction": "SELL",
                    "expected_close_norm": 0.48,
                }
            ],
        }

    monkeypatch.setattr(
        window_tracker_module,
        "build_scene_forecast_contribution_v3",
        capture_scene,
    )
    monkeypatch.setattr(
        window_tracker_module,
        "build_lstm_candle_sequence_contribution",
        capture_lstm,
    )
    adapter = PhoenixGuardWindowTrackingAdapter()
    chart = _synthetic_chart_surface("buy")
    candles = _manual_candle_tracks(
        [300, 286, 272, 258, 244, 230, 216, 202],
        image_width=chart.width,
        image_height=chart.height,
        direction="BUY",
    )
    build_payloads = cast(_BuildSignalPayloads, getattr(adapter, "_build_signal_payloads"))

    tracking, signal = build_payloads(
        chart,
        {
            "confidence": 1.0,
            "pixel_bbox": [0, 0, chart.width, chart.height],
            "normalized_bbox": [0.0, 0.0, 1.0, 1.0],
            "width": chart.width,
            "height": chart.height,
        },
        candles,
        {"value": "M1", "source": "ocr", "confidence": 0.42},
        market_selector={"value": "NZD/USD OTC", "source": "header_text", "confidence": 0.42},
        session_payload={
            "session_id": "pocket-live",
            "frame_index": 940,
            "execution_controls": {"high_frequency_timeframe": "M5"},
        },
    )

    scene = cast(Mapping[str, Any], signal["scene_forecast_contribution"])
    lstm = cast(Mapping[str, Any], signal["lstm_contribution"])
    assert tracking["detected_timeframe"] == "M1"
    assert tracking["high_frequency_study_timeframe"] == "M5"
    assert captured["timeframe"] == "M1"
    assert captured["pair"] == "NZD/USD OTC"
    assert scene["timeframe"] == "M1"
    assert scene["pair"] == "NZD/USD OTC"
    assert scene["market_identity_confirmed"] is True
    assert scene["timeframe_identity_confirmed"] is True
    assert captured_lstm["timeframe"] == "M5"
    assert captured_lstm["chart_image"] is chart
    assert scene["schema_version"] == "PG_SCENE_FORECAST_CONTRIBUTION_V3"
    assert lstm["schema_version"] == "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3"
    assert lstm is not scene
    assert tracking["lstm_contribution"] == lstm
    assert lstm["market_identity_confirmed"] is True
    assert lstm["timeframe_identity_confirmed"] is True
    assert lstm["artifact_loaded"] is True
    assert lstm["production_authorized"] is False
    assert lstm["selective_authorized"] is False
    assert lstm["trade_authorization_status"] == "NO_EDGE"
    assert lstm["contribution"] == 0.0


def test_scene_resolver_hydrates_pair_dna_raw_sequence_after_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    line = [
        [0.40 + index * 0.02, 0.55 - index * 0.005]
        for index in range(13)
    ]
    forecast_candles = [
        {
            "step": index,
            "label": f"E{index}",
            "x_norm": line[index][0],
            "open_y_norm": line[index - 1][1],
            "high_y_norm": min(line[index - 1][1], line[index][1])
            - 0.002,
            "low_y_norm": max(line[index - 1][1], line[index][1])
            + 0.002,
            "close_y_norm": line[index][1],
            "movement_side": "BUY",
            "body_bias": "BUY",
        }
        for index in range(1, 13)
    ]

    def scene_stub(**_kwargs: Any) -> dict[str, Any]:
        return {
            "schema_version": "PG_SCENE_FORECAST_CONTRIBUTION_V3",
            "path_side": "BUY",
            "side": "BUY",
            "probability_calibrated": False,
            "raw_side_probabilities": {
                "BUY": 0.58,
                "HOLD": 0.24,
                "SELL": 0.18,
            },
            "line_points": line,
            "forecast_candles": forecast_candles,
            "forecast_scenarios": [
                {
                    "role": role,
                    "side": "BUY",
                    "probability": probability,
                    "selected": role == "base",
                    "line_points": line,
                    "forecast_candles": forecast_candles,
                }
                for role, probability in (
                    ("base", 0.58),
                    ("bull", 0.24),
                    ("bear", 0.18),
                )
            ],
            "model_version": "TEST_SCENE_FORECASTER",
        }

    monkeypatch.setattr(
        window_tracker_module,
        "build_scene_forecast_contribution_v3",
        scene_stub,
    )
    adapter = PhoenixGuardWindowTrackingAdapter()
    resolver_reads: list[tuple[object, object]] = []

    def resolver_order_state(symbol: object, timeframe: object) -> dict[str, Any]:
        resolver_reads.append((symbol, timeframe))
        return {
            "status": "READY",
            "order_domain": "TRACKER_EVENT_SEQUENCE_V3",
            "raw_sequence_high_watermark": 54,
            "durable_high_watermark": 129,
            "sequence_epoch": 3,
            "rebase_count": 2,
        }

    setattr(
        adapter,
        "_market_study_service",
        SimpleNamespace(resolver_order_state=resolver_order_state),
    )
    chart = _surface(width=620, height=420)
    candles = _manual_candle_tracks(
        [300, 286, 272, 258, 244, 230, 216, 202],
        image_width=chart.width,
        image_height=chart.height,
    )

    build_scene = cast(
        Callable[..., dict[str, Any]],
        getattr(adapter, "_build_scene_forecast_contribution"),
    )
    result = build_scene(
        candles=candles,
        chart_image=chart,
        timeframe="M5",
        market="CHF/JPY OTC",
        frame_id=700,
        capture_epoch=1_000.0,
        projection={},
        candle_statistics={},
        behavior_payload={},
        decision_kernel={},
        smart_money_context={},
        support_resistance_context={},
        support_resistance_zones=[],
        trend_slopes={},
        trend_directions={},
    )

    assert resolver_reads == [("CHF/JPY OTC", "M5")]
    assert result["closed_candle_sequence"] == 54
    assert result["closed_candle_identity_state"]["event_sequence"] == 54
    assert result["resolver_order_hydration_v3"] == {
        "status": "HYDRATED",
        "source": "PAIR_DNA_RAW_SEQUENCE_HIGH_WATERMARK",
        "raw_sequence_high_watermark": 54,
        "sequence_epoch": 3,
        "rebase_count": 2,
        "execution_authority": False,
    }


@pytest.mark.parametrize("identity_confidence", (0.0, 0.419))
def test_scene_forecast_low_confidence_identity_publishes_no_geometry(
    monkeypatch: pytest.MonkeyPatch,
    identity_confidence: float,
) -> None:
    def unexpected_scene(**_kwargs: Any) -> dict[str, Any]:
        raise AssertionError("low-confidence OCR must not invoke the scene forecaster")

    monkeypatch.setattr(
        window_tracker_module,
        "build_scene_forecast_contribution_v3",
        unexpected_scene,
    )

    def authorized_lstm(**_kwargs: Any) -> dict[str, Any]:
        return {
            "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
            "fresh": True,
            "forecast_available": True,
            "artifact_production_gate_passed": True,
            "production_authorized": True,
            "selective_side": "BUY",
            "selective_authorized": True,
            "selective_status": "AUTHORIZED",
            "trade_authorization_status": "AUTHORIZED",
            "contribution": 0.9,
            "effective_contribution": 0.9,
            "score_influence_allowed": True,
            "playbook_participation_allowed": True,
        }

    monkeypatch.setattr(
        window_tracker_module,
        "build_lstm_candle_sequence_contribution",
        authorized_lstm,
    )
    adapter = PhoenixGuardWindowTrackingAdapter()
    chart = _synthetic_chart_surface("buy")
    candles = _manual_candle_tracks(
        [300, 286, 272, 258, 244, 230, 216, 202],
        image_width=chart.width,
        image_height=chart.height,
        direction="BUY",
    )
    build_payloads = cast(_BuildSignalPayloads, getattr(adapter, "_build_signal_payloads"))

    _tracking, signal = build_payloads(
        chart,
        {"confidence": 1.0, "pixel_bbox": [0, 0, chart.width, chart.height]},
        candles,
        {"value": "M1", "source": "ocr", "confidence": identity_confidence},
        market_selector={
            "value": "NZD/USD OTC",
            "source": "header_text",
            "confidence": identity_confidence,
        },
        session_payload={"execution_controls": {"high_frequency_timeframe": "M5"}},
    )

    scene = cast(Mapping[str, Any], signal["scene_forecast_contribution"])
    lstm = cast(Mapping[str, Any], signal["lstm_contribution"])
    assert scene["provider_status"] == "MARKET_IDENTITY_PENDING"
    assert scene["identity_contract_status"] == "PENDING"
    assert scene["forecast_available"] is False
    assert scene["line_points"] == []
    assert scene["forecast_candles"] == []
    assert scene["market_identity_confirmed"] is False
    assert scene["timeframe_identity_confirmed"] is False
    assert lstm["market_identity_confirmed"] is False
    assert lstm["timeframe_identity_confirmed"] is False
    assert lstm["selective_authorized"] is False
    assert lstm["trade_authorization_status"] == "NO_EDGE"
    assert lstm["contribution"] == 0.0
    assert lstm["score_influence_allowed"] is False


def test_forecast_snapshot_does_not_revive_previous_pair_while_ocr_is_pending() -> None:
    snapshot_builder = cast(
        Callable[..., dict[str, Any]],
        getattr(window_tracker_module, "_forecast_snapshot_v3"),
    )
    old_line = [[index / 12.0, 0.5] for index in range(13)]
    old_candles = [{"step": index} for index in range(1, 13)]
    pending = cast(
        Callable[..., dict[str, Any]],
        getattr(window_tracker_module, "_scene_forecast_identity_pending_v3"),
    )(
        pair="",
        timeframe="M1",
        frame_id=941,
        reason="MARKET_OCR_NOT_CONFIRMED",
        market_identity_confirmed=False,
        timeframe_identity_confirmed=True,
    )

    snapshot = snapshot_builder(
        {
            "display_frame_id": 941,
            "frame_index": 941,
            "latest_signal": {
                "market": "",
                "focus_timeframe": "M1",
                "market_selector_rebind_required": True,
                "market_selector_studying_new_pair": True,
                "scene_forecast_contribution": pending,
            },
            "model_council_study_packet": {
                "scene_forecast_contribution": {
                    "pair": "NZD/USD OTC",
                    "timeframe": "M1",
                    "frame_id": 940,
                    "forecast_available": True,
                    "line_points": old_line,
                    "forecast_candles": old_candles,
                }
            },
        }
    )

    scene = cast(Mapping[str, Any], snapshot["scene_forecast_contribution"])
    assert snapshot["status"] == "MARKET_IDENTITY_PENDING"
    assert snapshot["source_frame_id"] == 941
    assert snapshot["identity_contract_status"] == "PENDING"
    assert scene.get("line_points", []) == []
    assert scene.get("forecast_candles", []) == []


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


def test_private_memory_projection_snapshots_persist_without_crossing_public_boundary(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
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

    public_predicted = tracker.run_memory_projection(
        str(session["session_id"]), mode="predict"
    )
    assert "memory_projection_active_mode" not in public_predicted
    assert "memory_projection_predict" not in public_predicted
    private_predicted = tracker.require_session(str(session["session_id"]))
    assert private_predicted["memory_projection_active_mode"] == "predict"
    assert private_predicted["memory_projection_predict"]["status"] == "ready"
    assert private_predicted["memory_projection_predict"]["mode"] == "predict"
    assert Path(
        str(private_predicted["memory_projection_predict"]["reference_image_path"])
    ).is_file()
    assert Path(
        str(private_predicted["memory_projection_predict"]["projection_image_path"])
    ).is_file()
    assert public_predicted["latest_signal"]["market"] == "GBP/JPY OTC"

    public_future = tracker.run_memory_projection(
        str(session["session_id"]), mode="future"
    )
    assert "memory_projection_active_mode" not in public_future
    assert "memory_projection_future" not in public_future
    private_future = tracker.require_session(str(session["session_id"]))
    assert private_future["memory_projection_active_mode"] == "future"
    assert private_future["memory_projection_future"]["status"] == "ready"
    assert private_future["memory_projection_future"]["mode"] == "future"
    assert Path(
        str(private_future["memory_projection_future"]["reference_image_path"])
    ).is_file()
    assert Path(
        str(private_future["memory_projection_future"]["projection_image_path"])
    ).is_file()

    public_refreshed = tracker.capture_once(str(session["session_id"]))
    assert "memory_projection_future" not in public_refreshed
    private_refreshed = tracker.require_session(str(session["session_id"]))
    assert private_refreshed["memory_projection_future"]["status"] == "ready"
    assert private_refreshed["memory_projection_future"]["is_current"] is False
    assert private_refreshed["memory_projection_future"]["snapshot_ready"] is True
    assert private_refreshed["memory_projection_future"]["source_frame_age"] >= 1
    assert private_refreshed["memory_projection_future"]["trade_authorized"] is False
    assert private_refreshed["memory_projection_future"]["actionable"] is False
    assert (
        private_refreshed["memory_projection_future"]["execution_permission"]
        == "WAIT_FOR_CONFIRMATION"
    )
    assert public_refreshed["latest_signal"]["market"] == "GBP/JPY OTC"


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


def test_window_tracker_candle_masks_share_the_v3_palette_contract() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    build_masks = cast(
        Callable[[NDArray[np.uint8]], tuple[NDArray[np.uint8], NDArray[np.uint8]]],
        getattr(adapter, "_build_candle_masks"),
    )
    pixels = np.asarray(
        [
            [
                (42, 190, 72),
                (45, 100, 230),
                (224, 58, 42),
                (235, 115, 30),
                (225, 45, 180),
            ]
        ],
        dtype=np.uint8,
    )

    buy_mask, sell_mask = build_masks(pixels)

    assert buy_mask.tolist() == [[255, 255, 0, 0, 0]]
    assert sell_mask.tolist() == [[0, 0, 255, 255, 255]]


def test_window_tracker_live_extractor_matches_raw_suite_palette_contract() -> None:
    pixels = np.full((260, 360, 3), 18, dtype=np.uint8)
    previous_close = 150
    expected_directions: list[str] = []
    for index in range(24):
        direction = "BUY" if index % 3 != 1 else "SELL"
        open_y = previous_close
        close_y = open_y - 8 if direction == "BUY" else open_y + 10
        center_x = 30 + index * 11
        color = (45, 100, 230) if direction == "BUY" else (42, 190, 72)
        body_top, body_bottom = sorted((open_y, close_y))
        pixels[max(0, body_top - 4) : min(pixels.shape[0], body_bottom + 5), center_x] = color
        pixels[body_top : body_bottom + 1, center_x - 3 : center_x + 4] = color
        expected_directions.append(direction)
        previous_close = close_y

    shared = window_tracker_module.extract_candle_tracks_adaptive_v3(
        pixels,
        minimum_track_length=6,
    )
    adapter = PhoenixGuardWindowTrackingAdapter()
    extract = cast(
        Callable[[Image.Image], list[dict[str, Any]]],
        getattr(adapter, "_extract_candle_tracks"),
    )
    live = extract(Image.fromarray(pixels))

    assert [row["direction"] for row in live] == expected_directions
    assert [row["direction"] for row in live] == [row["direction"] for row in shared]
    assert [row["palette"] for row in live] == [row["palette"] for row in shared]
    assert [row["center_x_px"] for row in live] == [row["center_x_px"] for row in shared]
    assert all(float(row["center_x"]) == float(row["center_x_px"]) for row in live)
    assert all(float(row["center_y"]) == float(row["center_y_px"]) for row in live)
    assert all("open_y_px" in row and "close_y_px" in row for row in live)


def test_window_tracker_chart_bbox_uses_regular_candles_not_blue_grid_lines() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    detect = cast(
        Callable[[Image.Image], tuple[list[int], float]],
        getattr(adapter, "_detect_chart_bbox"),
    )
    image = _synthetic_chart_surface("buy")

    bbox, confidence = detect(image)

    assert bbox[0] < 100
    assert bbox[2] > 700
    assert bbox[2] - bbox[0] > image.width * 0.60
    assert confidence > 0.70


def test_window_tracker_rescales_shared_ohlc_geometry_with_fast_capture() -> None:
    rescale = cast(
        Callable[..., list[dict[str, Any]]],
        getattr(window_tracker_module, "_rescale_candle_tracks"),
    )
    source = {
        "bbox": [10, 20, 16, 40],
        "center_x": 13.0,
        "center_x_px": 13.0,
        "center_y": 30.0,
        "center_y_px": 30.0,
        "wick_top_px": 20.0,
        "wick_bottom_px": 40.0,
        "body_top_px": 24.0,
        "body_bottom_px": 36.0,
        "open_y_px": 36.0,
        "close_y_px": 24.0,
        "body_height_pct": 0.6,
    }

    scaled = rescale([source], scale_x=2.0, scale_y=3.0)[0]

    assert scaled["bbox"] == [20, 60, 32, 120]
    assert scaled["center_x"] == scaled["center_x_px"] == 26.0
    assert scaled["center_y"] == scaled["center_y_px"] == 90.0
    assert scaled["wick_top_px"] == 60.0
    assert scaled["wick_bottom_px"] == 120.0
    assert scaled["body_top_px"] == 72.0
    assert scaled["body_bottom_px"] == 108.0
    assert scaled["open_y_px"] == 108.0
    assert scaled["close_y_px"] == 72.0
    assert scaled["body_height_pct"] == 0.6


def test_window_tracker_adds_top_broker_chrome_exclusion_for_locked_focus_surface() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    surface = Image.new("RGB", (1628, 861), color=(18, 24, 34))

    boxes = adapter.chart_space_broker_exclusion_boxes(
        surface,
        [0, 0, 1628, 861],
        session_payload={
            "manual_focus_region": {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]},
            "locked_window": {"hwnd": 123, "title": "Pocket Option"},
        },
    )

    assert any(box[0] <= 1 and box[1] <= 1 and box[2] >= 1620 and 80 <= box[3] <= 130 for box in boxes)


def test_window_tracker_filters_tall_broker_tab_spike_before_signal_build() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    real_tracks = _manual_candle_tracks(
        [620, 594, 568, 542, 516, 490, 464, 438],
        image_width=1628,
        image_height=861,
    )
    for index, track in enumerate(real_tracks):
        center_x = 260.0 + float(index) * 42.0
        track["center_x"] = center_x
        track["bbox"] = [int(center_x - 5.0), int(track["bbox"][1]), int(center_x + 5.0), int(track["bbox"][3])]
    tab_spike = {
        "track_id": 901,
        "bbox": [820, 0, 832, 324],
        "center_x": 826.0,
        "center_y": 162.0,
        "direction": "SELL",
        "color": "magenta",
        "width": 12,
        "height": 324,
    }

    filtered = adapter.filter_candle_tracks_against_broker_exclusions(
        [*real_tracks, tab_spike],
        [[0, 0, 1628, 102]],
    )

    assert len(filtered) == len(real_tracks)
    assert all(int(track["track_id"]) != 901 for track in filtered)


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


def test_smc_order_block_requires_displacement_then_closed_bms() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    base_rows: list[dict[str, Any]] = [
        {"direction": "BUY", "wick_top": 118, "body_top": 122, "body_bottom": 132, "wick_bottom": 138},
        {"direction": "BUY", "wick_top": 90, "body_top": 96, "body_bottom": 108, "wick_bottom": 116},
        {"direction": "SELL", "wick_top": 112, "body_top": 118, "body_bottom": 140, "wick_bottom": 146},
        {"direction": "BUY", "wick_top": 100, "body_top": 104, "body_bottom": 124, "wick_bottom": 130},
        {"direction": "BUY", "wick_top": 68, "body_top": 76, "body_bottom": 98, "wick_bottom": 104},
        {
            "direction": "SELL",
            "wick_top": 62,
            "body_top": 72,
            "body_bottom": 96,
            "wick_bottom": 102,
            "is_closed": False,
        },
    ]

    confirmed = _derive_book_rule_smart_money(adapter, _book_rule_candle_tracks(base_rows))

    order_blocks = cast(Sequence[Mapping[str, Any]], confirmed["order_blocks"])
    assert order_blocks
    assert int(order_blocks[0]["source_index"]) == 2
    assert order_blocks[0]["bms_confirmed"] is True
    assert order_blocks[0]["closed_candle_confirmed"] is True
    assert int(order_blocks[0]["bms_swing_index"]) == 1
    assert int(order_blocks[0]["bms_break_index"]) == 4

    no_break_rows = [dict(row) for row in base_rows]
    no_break_rows[4]["close_y_px"] = 96.0
    no_break = _derive_book_rule_smart_money(adapter, _book_rule_candle_tracks(no_break_rows))
    diagnostics = cast(Mapping[str, Any], no_break["order_block_diagnostics"])
    assert no_break["order_blocks"] == []
    assert int(diagnostics["suppressed_no_bms"]) >= 1

    forming_break_rows = [dict(row) for row in base_rows[:5]]
    forming_break_rows[4]["is_closed"] = False
    forming_break = _derive_book_rule_smart_money(
        adapter,
        _book_rule_candle_tracks(forming_break_rows),
    )
    forming_structure = cast(Mapping[str, Any], forming_break["market_structure_shift"])
    assert forming_break["order_blocks"] == []
    assert forming_structure["confirmed"] is False


def test_smc_fresh_order_block_ranks_ahead_of_mitigated_block() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    candles = _book_rule_candle_tracks(
        [
            {"direction": "BUY", "wick_top": 118, "body_top": 122, "body_bottom": 132, "wick_bottom": 138},
            {"direction": "BUY", "wick_top": 90, "body_top": 96, "body_bottom": 108, "wick_bottom": 116},
            {"direction": "SELL", "wick_top": 112, "body_top": 118, "body_bottom": 140, "wick_bottom": 146},
            {"direction": "BUY", "wick_top": 100, "body_top": 104, "body_bottom": 124, "wick_bottom": 130},
            {"direction": "BUY", "wick_top": 68, "body_top": 76, "body_bottom": 98, "wick_bottom": 104},
            {"direction": "SELL", "wick_top": 116, "body_top": 120, "body_bottom": 138, "wick_bottom": 144},
            {"direction": "BUY", "wick_top": 60, "body_top": 66, "body_bottom": 76, "wick_bottom": 84},
            {"direction": "SELL", "wick_top": 74, "body_top": 80, "body_bottom": 94, "wick_bottom": 100},
            {"direction": "BUY", "wick_top": 65, "body_top": 70, "body_bottom": 84, "wick_bottom": 90},
            {"direction": "BUY", "wick_top": 36, "body_top": 44, "body_bottom": 66, "wick_bottom": 72},
            {
                "direction": "SELL",
                "wick_top": 38,
                "body_top": 50,
                "body_bottom": 72,
                "wick_bottom": 80,
                "is_closed": False,
            },
        ]
    )

    context = _derive_book_rule_smart_money(adapter, candles)
    order_blocks = cast(Sequence[Mapping[str, Any]], context["order_blocks"])

    assert len(order_blocks) >= 2
    assert order_blocks[0]["mitigated"] is False
    older = next(item for item in order_blocks if int(item["source_index"]) == 2)
    assert older["mitigated"] is True
    assert int(older["mitigation_count"]) >= 1
    assert list(order_blocks).index(order_blocks[0]) < list(order_blocks).index(older)


def test_smc_liquidity_sweep_requires_wick_penetration_and_actual_closed_reclaim() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    support_zone = {
        "key": "support-120",
        "role": "support",
        "label": "SUPPORT",
        "line_y": 120.0,
        "zone_height_px": 10.0,
        "freshness_state": "FRESH",
        "significance_score": 0.78,
        "confidence": 0.78,
    }
    prefix = [
        {"direction": "SELL", "wick_top": 128, "body_top": 134, "body_bottom": 146, "wick_bottom": 152},
        {"direction": "BUY", "wick_top": 116, "body_top": 122, "body_bottom": 138, "wick_bottom": 144},
        {"direction": "SELL", "wick_top": 126, "body_top": 132, "body_bottom": 144, "wick_bottom": 150},
        {"direction": "BUY", "wick_top": 112, "body_top": 116, "body_bottom": 120, "wick_bottom": 124},
    ]
    reclaimed = prefix + [
        {"direction": "BUY", "wick_top": 90, "body_top": 112, "body_bottom": 136, "wick_bottom": 150}
    ]
    rejected_close = prefix + [
        {
            "direction": "SELL",
            "wick_top": 90,
            "body_top": 112,
            "body_bottom": 136,
            "wick_bottom": 150,
            "close_y_px": 136.0,
        }
    ]
    forming_reclaim = [dict(row) for row in reclaimed]
    forming_reclaim[-1]["is_closed"] = False

    closed_context = _derive_book_rule_smart_money(
        adapter,
        _book_rule_candle_tracks(reclaimed),
        zones=[support_zone],
    )
    rejected_context = _derive_book_rule_smart_money(
        adapter,
        _book_rule_candle_tracks(rejected_close),
        zones=[support_zone],
    )
    forming_context = _derive_book_rule_smart_money(
        adapter,
        _book_rule_candle_tracks(forming_reclaim),
        zones=[support_zone],
    )

    sweeps = cast(Sequence[Mapping[str, Any]], closed_context["liquidity_sweeps"])
    assert sweeps
    assert sweeps[0]["closed_reclaim_confirmed"] is True
    assert rejected_context["liquidity_sweeps"] == []
    assert forming_context["liquidity_sweeps"] == []


def test_smc_score_disagreement_cannot_promote_countertrend_without_closed_mss() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    candles = _book_rule_candle_tracks(
        [
            {"direction": "BUY", "wick_top": 150, "body_top": 156, "body_bottom": 170, "wick_bottom": 176},
            {"direction": "BUY", "wick_top": 132, "body_top": 138, "body_bottom": 152, "wick_bottom": 158},
            {"direction": "SELL", "wick_top": 140, "body_top": 146, "body_bottom": 158, "wick_bottom": 164},
            {"direction": "SELL", "wick_top": 146, "body_top": 152, "body_bottom": 164, "wick_bottom": 170},
            {
                "direction": "SELL",
                "wick_top": 150,
                "body_top": 158,
                "body_bottom": 178,
                "wick_bottom": 186,
                "is_closed": False,
            },
        ]
    )

    context = _derive_book_rule_smart_money(
        adapter,
        candles,
        candidate_action="SELL",
        global_direction="BUY",
        local_direction="SELL",
        impulse_direction="SELL",
        reversal_score=1.0,
    )
    structure = cast(Mapping[str, Any], context["market_structure_shift"])
    adjustment = cast(Mapping[str, Any], context["decision_adjustment"])

    assert context["countertrend_local_only"] is True
    assert context["dominant_side"] != "SELL"
    assert structure["active"] is False
    assert structure["confirmed"] is False
    assert adjustment["side"] == "HOLD"
    assert adjustment["execution_authority"] == "WITHHELD_FAIL_CLOSED"


def test_support_break_and_role_flip_require_actual_closed_close() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    support_rows: list[dict[str, Any]] = [
        {"direction": "BUY", "wick_top": 100, "body_top": 108, "body_bottom": 116, "wick_bottom": 121},
        {"direction": "SELL", "wick_top": 102, "body_top": 108, "body_bottom": 116, "wick_bottom": 120},
        {"direction": "BUY", "wick_top": 101, "body_top": 108, "body_bottom": 116, "wick_bottom": 122},
        {"direction": "SELL", "wick_top": 103, "body_top": 108, "body_bottom": 116, "wick_bottom": 121},
        {"direction": "BUY", "wick_top": 102, "body_top": 108, "body_bottom": 116, "wick_bottom": 120},
    ]

    def support_near_121(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any]:
        zones = adapter.derive_support_resistance_zones(
            _book_rule_candle_tracks(rows),
            (640, 360),
            candidate_action="SELL",
            max_zones_per_role=8,
            max_total_zones=16,
        )
        supports = [zone for zone in zones if str(zone.get("role", "")) == "support"]
        assert supports
        return min(supports, key=lambda zone: abs(float(zone.get("line_y", 0.0)) - 121.0))

    wick_only_breach = support_rows + [
        {
            "direction": "BUY",
            "wick_top": 115,
            "body_top": 118,
            "body_bottom": 170,
            "wick_bottom": 180,
            "close_y_px": 118.0,
        }
    ]
    closed_break = support_rows + [
        {
            "direction": "SELL",
            "wick_top": 115,
            "body_top": 118,
            "body_bottom": 170,
            "wick_bottom": 180,
            "close_y_px": 170.0,
        }
    ]
    forming_break = [dict(row) for row in closed_break]
    forming_break[-1]["is_closed"] = False
    confirmed_flip = closed_break + [
        {
            "direction": "SELL",
            "wick_top": 114,
            "body_top": 118,
            "body_bottom": 140,
            "wick_bottom": 146,
            "close_y_px": 140.0,
        }
    ]
    direction_only_flip = closed_break + [
        {
            "direction": "SELL",
            "wick_top": 114,
            "body_top": 115,
            "body_bottom": 140,
            "wick_bottom": 146,
            "close_y_px": 115.0,
        }
    ]

    assert support_near_121(wick_only_breach)["broken_after_touch"] is False
    assert support_near_121(forming_break)["broken_after_touch"] is False
    assert support_near_121(closed_break)["broken_after_touch"] is True
    assert support_near_121(confirmed_flip)["role_flip_confirmed"] is True
    assert support_near_121(direction_only_flip)["role_flip_confirmed"] is False


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


def test_start_session_reconciles_missing_worker_without_resetting_automatic_history(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root_dir = tmp_path / "window_tracker"
    recycled_process = ContinuousWindowTrackerService(
        root_dir=root_dir,
        capture_backend=_FakeCaptureBackend([_surface()]),
        tracking_adapter=_FakeTrackingAdapter(),
    )
    session = recycled_process.create_session(session_id="pocket-live")
    payload = recycled_process.load_session(str(session["session_id"]))
    assert payload is not None
    preserved_study = {
        "status": "STUDIED",
        "closed_candle_key": "NZD/JPY OTC|M5|closed-37",
        "symbol": "NZD/JPY OTC",
        "timeframe": "M5",
    }
    preserved_history_row = {
        "frame_id": 37,
        "summary": "preserve-me",
        "market_study_v3": preserved_study,
    }
    preserved_chart_path = tmp_path / "preserved-chart.png"
    _surface().save(preserved_chart_path)
    latest_signal = dict(payload.get("latest_signal", {}))
    latest_signal["market_study_v3"] = preserved_study
    payload.update(
        {
            "manual_focus_region": {
                "enabled": True,
                "normalized_bbox": [0.0, 0.0, 1.0, 1.0],
            },
            "status": "running",
            "tracking_enabled": True,
            "frame_index": 37,
            "capture_count": 37,
            "last_capture_epoch": 1234.5,
            "decision_valid_until_epoch": 1294.5,
            "stream_continuity_generation_v3": 7,
            "last_chart_path": str(preserved_chart_path),
            "latest_signal": latest_signal,
            "recent_studies": [preserved_history_row],
            "__control_write_v3": True,
            "model_council_study_packet": {
                "packet_id": "study-preserved",
                "packet_type": "STUDY_PACKET",
            },
        }
    )
    # This is the state a new API process reads from disk: active persisted
    # intent and history, with an empty process-local worker registry.
    recycled_process.save_session(payload)
    ensure_calls: list[tuple[str, bool]] = []

    def _ensure_worker_stub(session_id: str, *, capture_now: bool = False) -> None:
        ensure_calls.append((session_id, capture_now))

    monkeypatch.setattr(recycled_process, "_ensure_worker", _ensure_worker_stub)

    reconciled = recycled_process.start_session("pocket-live")

    assert ensure_calls == [("pocket-live", True)]
    assert reconciled["status"] == "running"
    assert reconciled["tracking_enabled"] is True
    assert reconciled["frame_index"] == 37
    assert reconciled["capture_count"] == 37
    assert reconciled["last_capture_epoch"] == 1234.5
    assert reconciled["recent_studies"] == [preserved_history_row]
    stored = recycled_process.load_session("pocket-live")
    assert stored is not None
    assert stored["stream_continuity_generation_v3"] == 8
    assert stored["decision_valid_until_epoch"] == 1294.5
    assert stored["model_council_study_packet"]["packet_id"] == "study-preserved"


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


def _revocation_test_execution_packet(*, session_id: str, now_epoch: float) -> dict[str, Any]:
    input_frame_hash = "revocation-frame-10"
    allowance_package: dict[str, Any] = {
        "schema_version": "PG_ALLOWANCE_PACKAGE_V1",
        "package_type": "INTRADAY_ENTER_NOW",
        "allowance_family": "INTRADAY",
        "execution_authority": "PLAYBOOK_FINAL_DECIDER_V3",
        "packet_authority": "PG_EXECUTION_PACKET_V3",
        "side": "BUY",
        "accepted": True,
        "decision_accepted": True,
        "execution_ready": True,
        "entry_now_allowed": True,
        "timing_mode": "ENTER_NOW",
        "selected_lane": "SNIPER_ZONE_ENTRY",
    }
    packet = build_execution_packet_v3(
        packet_id="pgpkt-revocation-frame-10",
        session_id=session_id,
        symbol="EUR/USD OTC",
        timeframe="M5",
        frame_id=10,
        capture_count=10,
        state_version=10_010,
        created_epoch=now_epoch - 0.1,
        valid_until_epoch=now_epoch + 60.0,
        side="BUY",
        expiry_seconds=300,
        input_frame_hash=input_frame_hash,
        live_integrity={
            "is_live": True,
            "frame_advancing": True,
            "capture_advancing": True,
            "state_advancing": True,
            "source": "model_council",
            "cache_status": "fresh",
            "input_frame_hash": input_frame_hash,
            "previous_frame_hash": "revocation-frame-9",
            "packet_age_ms": 100,
        },
        model_council={"final_state": "EXECUTABLE", "final_side": "BUY"},
        runtime_model_health={"all_required_models_awake": True, "council_status": "AWAKE"},
        sequence_context=complete_sequence_context_v3(
            sequence_id="seq-revocation-frame-10",
            session_id=session_id,
            side="BUY",
        ),
        allowance_package=allowance_package,
    )
    packet["trade_permission"] = {
        "permission_state": "GRANTED",
        "executable_allowed": True,
        "failed_reasons": [],
        "blocking_reasons": [],
    }
    packet["entry_quality"] = {
        "state": "ACCEPTABLE_ENTRY",
        "passes_executable_threshold": True,
    }
    packet["market_trap"] = {
        "detected": False,
        "executable_allowed": True,
        "active_traps": [],
    }
    packet["overlay_truth_audit"] = {
        "valid_for_execution": True,
        "execution_safe": True,
        "frame_id": 10,
        "capture_count": 10,
        "input_frame_hash": input_frame_hash,
        "objects": [],
    }
    return packet


class _RevocationCouncilSequence:
    def __init__(self, results: Sequence[Mapping[str, Any]]) -> None:
        self._results = [dict(result) for result in results]

    def evaluate(self, *_args: object, **_kwargs: object) -> dict[str, Any]:
        return self._results.pop(0)


def _assert_newer_council_frame_revokes_execution_packet(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
    side: str,
    expected_reason: str,
) -> None:
    now_epoch = 1_800_000_000.0
    monkeypatch.setattr(window_tracker_module, "_now_epoch", lambda: now_epoch)
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path)
    session = tracker.create_session(session_id="packet-revocation")
    session_id = str(session["session_id"])
    payload = tracker.load_session_payload(session_id)
    first_packet = _revocation_test_execution_packet(session_id=session_id, now_epoch=now_epoch)
    second_result: dict[str, Any] = {
        "schema_version": "PG_MODEL_COUNCIL_STUDY_V3",
        "packet_type": "STUDY_PACKET",
        "session_id": session_id,
        "frame_id": 11,
        "capture_count": 11,
        "execution": {"enabled": False, "state": state, "side": side},
        "model_council": {"final_state": state, "final_side": side},
        "promotion_trace": {
            "packet_result": "STUDY_PACKET_PUBLISHED",
            "denied_at": f"TEST_{state}",
        },
    }
    council = _RevocationCouncilSequence((first_packet, second_result))

    def council_for_session(_session_id: str) -> _RevocationCouncilSequence:
        return council

    monkeypatch.setattr(tracker, "_model_council_for_session", council_for_session)
    publish_council_state = cast(
        Callable[..., dict[str, Any]],
        getattr(tracker, "_publish_model_council_v3_state"),
    )

    first_audit = dict(cast(Mapping[str, Any], first_packet["overlay_truth_audit"]))
    tracking_summary: dict[str, Any] = {"overlay_truth_audit": first_audit}
    latest_signal: dict[str, Any] = {
        "overlay_truth_audit": first_audit,
        "execution_action": "BUY",
        "action": "BUY",
    }
    first_result = publish_council_state(
        payload=payload,
        tracking_summary=tracking_summary,
        latest_signal=latest_signal,
        frame_index=10,
        capture_count=10,
        input_frame_hash="revocation-frame-10",
        capture_started_epoch=now_epoch - 0.1,
    )
    assert first_result["execution_packet_present"] is True

    payload["model_council_result"] = first_result
    payload["model_council"] = dict(cast(Mapping[str, Any], first_result.get("model_council", {})))
    payload["tracking_summary"] = tracking_summary
    payload["latest_signal"] = latest_signal
    payload["frame_index"] = 10
    payload["capture_count"] = 10
    payload["model_vote_frame_id"] = 10
    payload["last_capture_epoch"] = now_epoch - 0.1
    tracker.save_session(payload)
    assert tracker.latest_model_council_packet(session_id)["packet_id"] == "pgpkt-revocation-frame-10"

    second_audit: dict[str, Any] = {
        "valid_for_execution": True,
        "execution_safe": True,
        "frame_id": 11,
        "capture_count": 11,
        "input_frame_hash": "revocation-frame-11",
        "objects": [],
    }
    tracking_summary["overlay_truth_audit"] = second_audit
    latest_signal["overlay_truth_audit"] = second_audit
    latest_signal["execution_action"] = side
    latest_signal["action"] = side

    revoked_result = publish_council_state(
        payload=payload,
        tracking_summary=tracking_summary,
        latest_signal=latest_signal,
        frame_index=11,
        capture_count=11,
        input_frame_hash="revocation-frame-11",
        capture_started_epoch=now_epoch,
    )
    payload["model_council_result"] = revoked_result
    payload["model_council"] = dict(cast(Mapping[str, Any], revoked_result.get("model_council", {})))
    payload["tracking_summary"] = tracking_summary
    payload["latest_signal"] = latest_signal
    payload["frame_index"] = 11
    payload["capture_count"] = 11
    payload["model_vote_frame_id"] = 11
    payload["last_capture_epoch"] = now_epoch
    tracker.save_session(payload)

    aliases = {
        "model_council_packet",
        "execution_packet",
        "latest_model_council_packet",
        "latest_execution_packet",
    }
    for container in (revoked_result, latest_signal, tracking_summary, payload):
        assert aliases.isdisjoint(container)
        assert container["execution_packet_present"] is False
        tombstone = cast(Mapping[str, Any], container["execution_packet_revocation_v3"])
        assert tombstone["revoked_packet_id"] == "pgpkt-revocation-frame-10"
        assert tombstone["frame_id"] == 11
        assert tombstone["capture_count"] == 11
        assert tombstone["reason"] == expected_reason

    with pytest.raises(KeyError):
        tracker.latest_model_council_packet(session_id)
    stored = tracker.load_session_payload(session_id)
    assert aliases.isdisjoint(stored)
    assert stored["execution_packet_present"] is False
    assert stored["execution_packet_revocation_v3"]["reason"] == expected_reason
    compact_live_state_path = cast(
        Callable[[str], Path],
        getattr(tracker, "_compact_live_state_path"),
    )
    compact = window_tracker_module.read_json(compact_live_state_path(session_id), {})
    assert compact["execution_packet_present"] is False
    assert compact["execution_packet_revocation_v3"]["revoked_packet_id"] == "pgpkt-revocation-frame-10"


def test_newer_watching_council_frame_revokes_previous_execution_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_newer_council_frame_revokes_execution_packet(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        state="WATCHING",
        side="BUY",
        expected_reason="NEWER_COUNCIL_WATCHING",
    )


def test_newer_opposite_council_frame_revokes_previous_execution_packet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_newer_council_frame_revokes_execution_packet(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        state="PREPARING",
        side="SELL",
        expected_reason="NEWER_COUNCIL_OPPOSITE_SIDE",
    )


def test_execution_opportunity_window_survives_session_persistence_and_snapshot_rebuild(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path)
    session = tracker.create_session(session_id="opportunity-window-persistence")
    session_id = str(session["session_id"])
    payload = tracker.load_session_payload(session_id)
    authority = {
        "schema_version": "PG_EXECUTION_OPPORTUNITY_WINDOW_V3",
        "opportunity_key": "pgopp_stable",
        "opportunity_id": "pgepisode_stable",
        "session_id": session_id,
        "symbol": "BROKER_LOCKED_ACTIVE_CHART",
        "timeframe": "M3",
        "side": "BUY",
        "candidate_id": "pgcand_stable",
        "opened_epoch_sec": 1_800_000_000.0,
        "valid_until_epoch_sec": 1_800_000_360.0,
        "duration_sec": 360.0,
        "remaining_sec": 120.0,
        "state": "OPEN",
        "last_seen_frame_id": 8,
        "last_seen_capture_count": 8,
    }
    payload["execution_opportunity_window_v3"] = authority
    payload["model_council_result"] = {"execution_opportunity_window_v3": authority}
    payload["frame_index"] = 8
    payload["capture_count"] = 8
    tracker.save_session(payload)

    restored = tracker.load_session_payload(session_id)
    assert restored["execution_opportunity_window_v3"] == authority
    compact_live_state_path = cast(
        Callable[[str], Path],
        getattr(tracker, "_compact_live_state_path"),
    )
    compact = window_tracker_module.read_json(compact_live_state_path(session_id), {})
    assert compact["execution_opportunity_window_v3"] == authority
    assert compact["model_council_result"]["execution_opportunity_window_v3"] == authority

    build_council_snapshot = cast(
        Callable[..., dict[str, Any]],
        getattr(tracker, "_build_model_council_v3_snapshot"),
    )
    snapshot = build_council_snapshot(
        payload=restored,
        tracking_summary={},
        latest_signal={},
        frame_index=9,
        capture_count=9,
        input_frame_hash="opportunity-window-frame-9",
        capture_started_epoch=1_800_000_100.0,
    )
    assert snapshot["execution_opportunity_window_v3"] == authority


@pytest.mark.parametrize(
    ("previous_symbol", "expected_opportunity_id", "expected_side"),
    (
        ("EUR/USD OTC", "pgepisode-sell-existing", "SELL"),
        ("CAD/CHF OTC", "pgepisode-buy-attempt", "BUY"),
    ),
)
def test_countertrend_thesis_block_only_restores_same_instrument_opportunity_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    previous_symbol: str,
    expected_opportunity_id: str,
    expected_side: str,
) -> None:
    now_epoch = 1_800_000_100.0
    monkeypatch.setattr(window_tracker_module, "_now_epoch", lambda: now_epoch)
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path)
    session = tracker.create_session(session_id="opportunity-window-countertrend")
    session_id = str(session["session_id"])
    payload = tracker.load_session_payload(session_id)
    previous_authority = {
        "schema_version": "PG_EXECUTION_OPPORTUNITY_WINDOW_V3",
        "opportunity_key": "pgopp-sell-existing",
        "opportunity_id": "pgepisode-sell-existing",
        "session_id": session_id,
        "symbol": previous_symbol,
        "timeframe": "M5",
        "side": "SELL",
        "candidate_id": "pgcand-sell-existing",
        "opened_epoch_sec": now_epoch - 30.0,
        "valid_until_epoch_sec": now_epoch + 270.0,
        "duration_sec": 300.0,
        "remaining_sec": 270.0,
        "state": "OPEN",
        "anchor_reused": True,
        "last_seen_frame_id": 9,
        "last_seen_capture_count": 9,
    }
    new_authority = {
        **previous_authority,
        "opportunity_key": "pgopp-buy-attempt",
        "opportunity_id": "pgepisode-buy-attempt",
        "symbol": "EUR/USD OTC",
        "side": "BUY",
        "candidate_id": "pgcand-buy-attempt",
        "opened_epoch_sec": now_epoch,
        "valid_until_epoch_sec": now_epoch + 300.0,
        "remaining_sec": 300.0,
        "anchor_reused": False,
        "last_seen_frame_id": 10,
        "last_seen_capture_count": 10,
    }
    packet = _revocation_test_execution_packet(session_id=session_id, now_epoch=now_epoch)
    packet["execution_opportunity_window_v3"] = new_authority
    packet["entry_window"] = {"duration_sec": 300.0, "remaining_sec": 300.0}
    packet["model_council"]["execution_opportunity_window_v3"] = new_authority
    council = _RevocationCouncilSequence((packet,))

    def council_for_session(_session_id: str) -> _RevocationCouncilSequence:
        return council

    monkeypatch.setattr(tracker, "_model_council_for_session", council_for_session)
    active_sell_thesis = {
        "schema_version": "PG_SIGNAL_THESIS_V3",
        "active": True,
        "status": "ACTIVE",
        "room_state": "ACTIVE",
        "thesis_id": "pgthesis-active-sell",
        "session_id": session_id,
        "symbol": "EUR/USD OTC",
        "symbol_key": "EUR/USD OTC",
        "timeframe": "M5",
        "side": "SELL",
        "effective_side": "SELL",
        "raw_read_side": "SELL",
        "created_epoch": now_epoch - 30.0,
        "updated_epoch": now_epoch - 1.0,
        "entry_frame_id": 9,
        "last_frame_id": 9,
        "invalidated": False,
    }
    payload["execution_opportunity_window_v3"] = previous_authority
    payload["signal_thesis_v3"] = active_sell_thesis
    signal_theses = cast(
        dict[str, dict[str, Any]],
        getattr(tracker, "_signal_theses"),
    )
    signal_theses[session_id] = active_sell_thesis
    overlay_audit = dict(cast(Mapping[str, Any], packet["overlay_truth_audit"]))
    tracking_summary: dict[str, Any] = {
        "overlay_truth_audit": overlay_audit,
        "detected_market": "EUR/USD OTC",
        "detected_timeframe": "M5",
        "market_identity_confirmed": True,
        "timeframe_identity_confirmed": True,
    }
    latest_signal: dict[str, Any] = {
        "overlay_truth_audit": overlay_audit,
        "market": "EUR/USD OTC",
        "focus_timeframe": "M5",
        "market_identity_confirmed": True,
        "timeframe_identity_confirmed": True,
        "execution_action": "BUY",
        "action": "BUY",
    }

    publish_council_state = cast(
        Callable[..., dict[str, Any]],
        getattr(tracker, "_publish_model_council_v3_state"),
    )
    result = publish_council_state(
        payload=payload,
        tracking_summary=tracking_summary,
        latest_signal=latest_signal,
        frame_index=10,
        capture_count=10,
        input_frame_hash="revocation-frame-10",
        capture_started_epoch=now_epoch - 0.1,
    )

    assert result["execution_packet_present"] is False
    assert result["model_council"]["true_blocker"] == "SIGNAL_THESIS_V3_COUNTERTREND_BLOCK"
    for container in (result, payload, latest_signal, tracking_summary):
        assert container["execution_opportunity_window_v3"]["opportunity_id"] == expected_opportunity_id
        assert container["execution_opportunity_window_v3"]["side"] == expected_side


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
    assert public["decision_version"] == public["trade_intent"]["state_version"]


def test_public_session_versions_advance_for_non_actionable_publishes(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path)
    payload: dict[str, Any] = {
        "session_id": "pocket-live",
        "status": "running",
        "tracking_enabled": True,
        "capture_count": 4,
        "frame_index": 4,
        "state_version": 1,
        "decision_version": 0,
        "last_capture_epoch": 1_000.0,
        "display_published_epoch": 1_001.0,
        "manual_focus_region": {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]},
        "latest_signal": {
            "signal_id": "study-4",
            "published_epoch": 1_000.0,
            "action": "BUY",
            "execution_action": "BUY",
            "actionable": False,
        },
        "tracking_summary": {},
    }

    first = tracker.public_session_payload(payload)
    payload.update(
        {
            "capture_count": 5,
            "frame_index": 5,
            "display_published_epoch": 1_002.0,
            "state_version": first["state_version"],
            "decision_version": 0,
        }
    )
    payload["latest_signal"] = dict(payload["latest_signal"], published_epoch=1_002.0)
    second = tracker.public_session_payload(payload)

    assert int(first["state_version"]) == 1_001_000
    assert first["decision_version"] == first["state_version"]
    assert first["trade_intent"] == {}
    assert int(second["state_version"]) > int(first["state_version"])
    assert second["decision_version"] == second["state_version"]


def test_save_session_persists_advancing_state_and_non_actionable_decision_versions(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path)
    session_id = str(tracker.create_session(session_id="pocket-live")["session_id"])
    payload = tracker.load_session_payload(session_id)
    payload.update(
        {
            "capture_count": 1,
            "frame_index": 1,
            "last_capture_epoch": 2_000.0,
            "display_published_epoch": 2_000.25,
            "manual_focus_region": {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]},
            "latest_signal": {
                "signal_id": "study-1",
                "published_epoch": 2_000.0,
                "execution_action": "HOLD",
                "actionable": False,
            },
        }
    )
    tracker.save_session(payload)
    first_persisted = json.loads(
        (tracker.session_dir(session_id) / "session.json").read_text(encoding="utf-8")
    )
    first = tracker.load_session_payload(session_id)

    payload = dict(first)
    payload.update(
        {
            "capture_count": 2,
            "frame_index": 2,
            "last_capture_epoch": 2_001.0,
            "display_published_epoch": 2_001.25,
            "decision_version": 0,
            "latest_signal": {
                "signal_id": "study-2",
                "published_epoch": 2_001.0,
                "execution_action": "HOLD",
                "actionable": False,
            },
        }
    )
    tracker.save_session(payload)
    second_persisted = json.loads(
        (tracker.session_dir(session_id) / "session.json").read_text(encoding="utf-8")
    )
    second = tracker.load_session_payload(session_id)
    compact = json.loads(
        (tracker.session_dir(session_id) / "compact_live_state.json").read_text(encoding="utf-8")
    )

    assert int(first["state_version"]) == 2_000_250
    assert first_persisted["latest_signal"]["state_version"] == first["state_version"]
    assert first["decision_version"] == first["state_version"]
    assert int(second["state_version"]) > int(first["state_version"])
    assert second_persisted["latest_signal"]["state_version"] == second["state_version"]
    assert second["decision_version"] == second["state_version"]
    assert compact["state_version"] == second["state_version"]
    assert compact["decision_version"] == second["decision_version"]


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


def test_new_forecast_frame_advances_retained_snapshot_epoch() -> None:
    snapshot = window_tracker_module._forecast_snapshot_v3(  # pyright: ignore[reportPrivateUsage]
        {
            "display_frame_id": 15,
            "model_vote_frame_id": 15,
            "model_capture_epoch": 200.0,
            "visual_observation_v3": {
                "status": "NEW_FRAME",
                "new_visual_evidence": True,
            },
            "tracking_summary": {
                "lstm_contribution": {
                    "frame_id": 15,
                    "forecast_available": True,
                    "path_side": "BUY",
                    "confidence": 0.73,
                }
            },
            "forecast_snapshot_v3": {
                "source_frame_id": 14,
                "observed_epoch": 100.0,
                "lstm_contribution": {
                    "frame_id": 14,
                    "forecast_available": True,
                    "path_side": "SELL",
                    "confidence": 0.62,
                },
            },
        }
    )

    assert snapshot["source_frame_id"] == 15
    assert snapshot["observed_epoch"] == 200.0
    assert snapshot["status"] == "CURRENT"
    assert cast(dict[str, Any], snapshot["lstm_contribution"])["path_side"] == "BUY"


def _complete_scene_snapshot_fixture(
    *,
    closed_candle_key: str = "closed-event-71",
    pair: str = "GBP/USD OTC",
    timeframe: str = "M5",
    frame_id: int = 71,
) -> dict[str, Any]:
    anchor_x = 0.58
    anchor_y = 0.52
    scenarios: list[dict[str, Any]] = []
    for role, direction, selected, y_step in (
        ("base", "SELL", True, 0.006),
        ("bull", "BUY", False, -0.004),
        ("bear", "SELL", False, 0.01),
    ):
        points = [
            [round(anchor_x + step * 0.012, 6), round(anchor_y + step * y_step, 6)]
            for step in range(13)
        ]
        candles = [
            {
                "step": step,
                "x_norm": points[step][0],
                "open_y_norm": points[step - 1][1],
                "high_y_norm": min(points[step - 1][1], points[step][1]) - 0.001,
                "low_y_norm": max(points[step - 1][1], points[step][1]) + 0.001,
                "close_y_norm": points[step][1],
                "movement_side": direction,
            }
            for step in range(1, 13)
        ]
        scenarios.append(
            {
                "role": role,
                "side": direction,
                "selected": selected,
                "line_points": points,
                "forecast_candles": candles,
            }
        )
    selected_scenario = scenarios[0]
    return {
        "schema_version": "PG_SCENE_FORECAST_CONTRIBUTION_V3",
        "provider": "SCENE_STATISTICAL_FALLBACK_V3",
        "provider_status": "AVAILABLE",
        "forecast_available": True,
        "pair": pair,
        "timeframe": timeframe,
        "market_identity_confirmed": True,
        "timeframe_identity_confirmed": True,
        "identity_contract_status": "CONFIRMED",
        "frame_id": frame_id,
        "display_frame_id": frame_id,
        "forecast_computed_frame_id": frame_id,
        "source_forecast_frame_id": frame_id,
        "geometry_projected_frame_id": frame_id,
        "geometry_frame_match_verified": True,
        "geometry_reprojected_from_cache": False,
        "frame_reused_without_reforecast": False,
        "same_event_cache_rebuild_required": False,
        "closed_candle_key": closed_candle_key,
        "closed_candle_sequence": 8,
        "path_side": "SELL",
        "side": "SELL",
        "forecast_anchor": {
            "x_norm": anchor_x,
            "y_norm": anchor_y,
            "verified_latest_close": True,
        },
        "line_points": copy.deepcopy(selected_scenario["line_points"]),
        "forecast_candles": copy.deepcopy(selected_scenario["forecast_candles"]),
        "forecast_scenarios": scenarios,
    }


def test_same_event_compact_scene_does_not_downgrade_retained_snapshot_geometry() -> None:
    scene = _complete_scene_snapshot_fixture()
    compact_payload = cast(
        Callable[[Mapping[str, Any]], dict[str, Any]],
        getattr(window_tracker_module, "_compact_session_persisted_payload"),
    )
    complete_geometry = cast(
        Callable[[Any], bool],
        getattr(window_tracker_module, "_complete_scene_forecast_geometry_v3"),
    )
    first = compact_payload(
        {
            "session_id": "same-event-scene-retention",
            "display_frame_id": 71,
            "frame_index": 71,
            "model_vote_frame_id": 71,
            "latest_signal": {"scene_forecast_contribution": scene},
            "tracking_summary": {"scene_forecast_contribution": scene},
        }
    )
    first_snapshot = cast(dict[str, Any], first["forecast_snapshot_v3"])
    first_scene = cast(dict[str, Any], first_snapshot["scene_forecast_contribution"])
    assert complete_geometry(first_scene) is True
    assert "line_points" not in cast(
        dict[str, Any],
        cast(dict[str, Any], first["latest_signal"])["scene_forecast_contribution"],
    )

    second = compact_payload(first)
    second_scene = cast(
        dict[str, Any],
        cast(dict[str, Any], second["forecast_snapshot_v3"])[
            "scene_forecast_contribution"
        ],
    )

    assert complete_geometry(second_scene) is True
    assert second_scene["closed_candle_key"] == "closed-event-71"
    assert second_scene["line_points"] == first_scene["line_points"]
    assert second_scene["forecast_candles"] == first_scene["forecast_candles"]
    retained_scenarios = cast(list[dict[str, Any]], second_scene["forecast_scenarios"])
    assert len(retained_scenarios) == 3
    assert sum(bool(row["selected"]) for row in retained_scenarios) == 1
    assert all(len(cast(list[Any], row["line_points"])) == 13 for row in retained_scenarios)
    assert all(len(cast(list[Any], row["forecast_candles"])) == 12 for row in retained_scenarios)


@pytest.mark.parametrize(
    ("field", "replacement"),
    (
        ("closed_candle_key", "different-closed-event"),
        ("pair", "USD/JPY OTC"),
    ),
)
def test_compact_scene_never_revives_geometry_for_another_event_or_pair(
    field: str,
    replacement: str,
) -> None:
    scene = _complete_scene_snapshot_fixture()
    compact_payload = cast(
        Callable[[Mapping[str, Any]], dict[str, Any]],
        getattr(window_tracker_module, "_compact_session_persisted_payload"),
    )
    first = compact_payload(
        {
            "display_frame_id": 71,
            "frame_index": 71,
            "latest_signal": {"scene_forecast_contribution": scene},
            "tracking_summary": {"scene_forecast_contribution": scene},
        }
    )
    changed = copy.deepcopy(first)
    for container_key in ("latest_signal", "tracking_summary"):
        container = cast(dict[str, Any], changed[container_key])
        compact_scene = cast(dict[str, Any], container["scene_forecast_contribution"])
        compact_scene[field] = replacement

    second = compact_payload(changed)
    second_scene = cast(
        dict[str, Any],
        cast(dict[str, Any], second["forecast_snapshot_v3"])[
            "scene_forecast_contribution"
        ],
    )
    assert second_scene.get(field) == replacement
    assert second_scene.get("line_points", []) == []
    assert second_scene.get("forecast_candles", []) == []
    assert second_scene.get("forecast_scenarios", []) == []


def test_malformed_same_event_scene_does_not_reuse_retained_geometry() -> None:
    scene = _complete_scene_snapshot_fixture()
    snapshot_builder = cast(
        Callable[[Mapping[str, Any]], dict[str, Any]],
        getattr(window_tracker_module, "_forecast_snapshot_v3"),
    )
    existing = snapshot_builder(
        {
            "display_frame_id": 71,
            "latest_signal": {"scene_forecast_contribution": scene},
        }
    )
    malformed = copy.deepcopy(scene)
    malformed["line_points"] = cast(list[Any], malformed["line_points"])[:-1]

    result = snapshot_builder(
        {
            "display_frame_id": 71,
            "latest_signal": {"scene_forecast_contribution": malformed},
            "forecast_snapshot_v3": existing,
        }
    )
    result_scene = cast(dict[str, Any], result["scene_forecast_contribution"])
    assert len(cast(list[Any], result_scene["line_points"])) == 12
    assert result_scene["line_points"] != cast(
        dict[str, Any], existing["scene_forecast_contribution"]
    )["line_points"]


def test_lstm_composite_forecast_survives_bounded_cold_persistence(tmp_path: Path) -> None:
    root_dir = tmp_path / "lstm-composite-cold-persistence"
    tracker = ContinuousWindowTrackerService(root_dir=root_dir)
    session_id = "lstm-composite-cold-persistence"
    tracker.create_session(session_id=session_id)

    anchor_location = 0.625
    interval_metadata = {
        "status": "READY",
        "calibrated": True,
        "method": "SOURCE_BLOCKED_PATHWISE_CONFORMAL",
        "quantile": 0.0475,
        "source_count": 42,
        "coverage": 0.902,
    }
    forecast_path = [
        {
            "step": step,
            "event": f"CANDLE_EVENT_{step}",
            "direction": "BUY",
            "direction_semantics": "CUMULATIVE_CLOSE_FROM_ANCHOR",
            "movement_direction": "BUY",
            "horizon_position_direction": "BUY",
            "path_buy_probability": round(0.70 + step * 0.01, 4),
            "path_sell_probability": round(0.30 - step * 0.01, 4),
            "confidence": round(0.80 - step * 0.01, 4),
            "expected_open_norm": anchor_location if step == 1 else round(anchor_location + (step - 1) * 0.01, 4),
            "expected_close_norm": round(anchor_location + step * 0.01, 4),
            "close_lower_90_norm": round(anchor_location + step * 0.01 - 0.0475, 4),
            "close_upper_90_norm": round(anchor_location + step * 0.01 + 0.0475, 4),
            "interval_calibrated": True,
            "interval_method": "SOURCE_BLOCKED_PATHWISE_CONFORMAL",
        }
        for step in range(1, 13)
    ]
    scenario_directions = {"BUY": 1.0, "SELL": -1.0, "HOLD": 0.0}
    scenario_probabilities = {"BUY": 0.52, "SELL": 0.31, "HOLD": 0.17}
    trajectory_scenarios: list[dict[str, Any]] = []
    expected_scenario_paths: dict[str, list[dict[str, Any]]] = {}
    for scenario_side, direction in scenario_directions.items():
        scenario_path = [
            {
                "step": step,
                "event": f"CANDLE_EVENT_{step}",
                "expected_close_norm": round(anchor_location + direction * step * 0.006, 6),
                "expected_delta_norm": round(direction * 0.006, 6),
                "expected_cumulative_delta_norm": round(direction * step * 0.006, 6),
                "cumulative_scale_norm": round(0.012 + step * 0.001, 6),
                # A forecast snapshot may retain drawing values, never the
                # decoder's unrelated internal representation.
                "latent_vector": [scenario_side, step, "private"],
            }
            for step in range(1, 13)
        ]
        expected_scenario_paths[scenario_side] = [
            {key: value for key, value in row.items() if key != "latent_vector"}
            for row in scenario_path
        ]
        trajectory_scenarios.append(
            {
                "side": scenario_side,
                "probability": scenario_probabilities[scenario_side],
                "probability_calibrated": False,
                "selected": scenario_side == "BUY",
                "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
                # Deliberately reversed: the snapshot owns ordered events.
                "forecast_path": list(reversed(scenario_path)),
                "artifact_path": rf"C:\private\{scenario_side.lower()}-decoder.pt",
                "raw_payload": {"secret": scenario_side},
            }
        )
    payload = tracker.load_session_payload(session_id)
    payload.update(
        {
            "frame_index": 42,
            "display_frame_id": 42,
            "model_vote_frame_id": 42,
            "model_capture_epoch": 1_720_000_042.0,
            "visual_observation_v3": {
                "status": "NEW_FRAME",
                "new_visual_evidence": True,
            },
            "tracking_summary": {
                "lstm_contribution": {
                    "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
                    "frame_id": 42,
                    "fresh": True,
                    "forecast_available": True,
                    "confidence": 0.8123,
                    "side": "BUY",
                    "path_side": "BUY",
                    "path_target_semantics": "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR",
                    "trajectory_modes": 3,
                    "trajectory_decoder_status": "AVAILABLE",
                    "trajectory_mode": "BUY",
                    "trajectory_mode_probabilities": scenario_probabilities,
                    "trajectory_mode_probability_calibrated": False,
                    "trajectory_scenarios": trajectory_scenarios,
                    "path_confidence": 0.823456,
                    "path_confidence_status": "READY",
                    "direction_conflict": False,
                    "selective_side": "BUY",
                    "selective_status": "AUTHORIZED",
                    "selective_authorized": True,
                    "horizon_steps": 12,
                    "source_image_size": [1632, 863],
                    "features": [
                        {
                            "index": index,
                            "relative_price_location": anchor_location if index == 19 else 0.4 + index * 0.01,
                        }
                        for index in range(20)
                    ],
                    # Exercise the persistence sorter rather than relying on
                    # the producer to have serialized the events in order.
                    "forecast_path": list(reversed(forecast_path)),
                    "trajectory_interval_status": "READY",
                    "trajectory_interval": interval_metadata,
                    "artifact_path": r"C:\private\lstm.pt",
                    "dense_history": ["x" * 1024 for _ in range(64)],
                    "unrelated_raw_payload": {"secret": "never persist"},
                }
            },
        }
    )
    full_lstm = cast(dict[str, Any], cast(dict[str, Any], payload["tracking_summary"])["lstm_contribution"])
    repeated_diagnostics = [
        {"index": index, "raw_payload": "diagnostic-only-" + ("x" * 2048)}
        for index in range(64)
    ]
    book_strategy = {
        "state": "WATCHING",
        "side": "BUY",
        "denied_at": "TIMING_WAIT",
        "next_required": "wait for the retest",
        "strategy_read": {
            "playbook": "BREAK_AND_RETEST",
            "side": "BUY",
            "playbook_ai_intelligence_v3": {"raw_payload": repeated_diagnostics},
        },
        "lstm_council_evidence_v3": full_lstm,
    }
    promotion_trace = {
        "candidate_stage": "WATCHING",
        "promotion_result": "BLOCKED",
        "denied_at": "TIMING_WAIT",
        "next_required": "wait for the retest",
        "promotion_failure_audit_v3": {"failed_gate": "TIMING_WAIT"},
        "book_strategy": book_strategy,
        "opportunity_maturity": {
            "state": "WATCHING",
            "book_strategy": book_strategy,
            "professional_trade_plan": {"raw_payload": repeated_diagnostics},
            "lstm_council_evidence_v3": full_lstm,
        },
        "allowance_package": {
            "accepted": False,
            "professional_trade_plan": {"raw_payload": repeated_diagnostics},
            "lstm_council_evidence_v3": full_lstm,
        },
        "lstm_council_evidence_v3": full_lstm,
    }
    sequence_context = {
        "sequence_id": "sequence-42",
        "sequence_status": "TRACKING",
        "tracking_summary": payload["tracking_summary"],
        "progression": [{"frame_id": index, "side": "BUY"} for index in range(32)],
    }
    model_council = {
        "candidate_stage": "WATCHING",
        "final_state": "WATCHING",
        "final_side": "BUY",
        "denied_at": "TIMING_WAIT",
        "next_required": "wait for the retest",
        "sequence_context": sequence_context,
        "promotion_trace": promotion_trace,
        "book_strategy": book_strategy,
        "strategy_read": book_strategy["strategy_read"],
        "lstm_contribution": full_lstm,
    }
    payload["latest_signal"] = {
        "status": "tracking",
        "action": "BUY",
        "lstm_contribution": full_lstm,
    }
    payload["model_council"] = model_council
    payload["model_council_result"] = {
        **model_council,
        "model_council": model_council,
        "opportunity_maturity": promotion_trace["opportunity_maturity"],
        "allowance_package": promotion_trace["allowance_package"],
    }
    payload["model_council_study_packet"] = {
        **model_council,
        "schema_version": "PG_MODEL_COUNCIL_STUDY_V3",
        "packet_type": "STUDY_PACKET",
        "packet_id": "study-42",
        "session_id": session_id,
    }
    tracker.save_session(payload)

    persisted_path = tracker.session_dir(session_id) / "session.json"
    persisted_bytes = persisted_path.read_bytes()
    assert len(persisted_bytes) < 1_000_000
    assert persisted_bytes.count(b'"forecast_path"') == 4
    assert persisted_bytes.count(b'"trajectory_scenarios"') == 1
    assert persisted_bytes.count(b'"features"') == 1

    compact_decision = window_tracker_module._compact_persisted_decision_payload(  # pyright: ignore[reportPrivateUsage]
        {
            "session_id": session_id,
            "frame_index": 42,
            "display_frame_id": 42,
            "model_vote_frame_id": 42,
            "model_capture_epoch": 1_720_000_042.0,
            "visual_observation_v3": payload["visual_observation_v3"],
            "tracking_summary": payload["tracking_summary"],
            "latest_signal": payload["latest_signal"],
            "model_council_result": payload["model_council_result"],
            "model_council": payload["model_council"],
            "model_council_study_packet": payload["model_council_study_packet"],
        }
    )
    decision_path = tracker.session_dir(session_id) / "artifacts" / "deduplicated_decision.json"
    write_json_atomic(decision_path, compact_decision)
    decision_bytes = decision_path.read_bytes()
    assert len(decision_bytes) < 1_000_000
    assert b'"forecast_path"' not in decision_bytes
    assert b'"trajectory_scenarios"' not in decision_bytes
    assert b'"forecast_snapshot_v3"' not in decision_bytes
    tracker.shutdown()

    cold_tracker = ContinuousWindowTrackerService(root_dir=root_dir)
    persisted = cold_tracker.load_session_payload(session_id)
    snapshot = cast(dict[str, Any], persisted["forecast_snapshot_v3"])
    lstm = cast(dict[str, Any], snapshot["lstm_contribution"])
    retained_path = cast(list[dict[str, Any]], lstm["forecast_path"])

    assert [row["step"] for row in retained_path] == list(range(1, 13))
    assert retained_path == forecast_path
    assert lstm["horizon_steps"] == 12
    assert lstm["path_target_semantics"] == "DIRECT_CUMULATIVE_CLOSE_FROM_ANCHOR"
    assert lstm["trajectory_modes"] == 3
    assert lstm["trajectory_decoder_status"] == "AVAILABLE"
    assert lstm["trajectory_mode"] == "BUY"
    assert lstm["trajectory_mode_probabilities"] == scenario_probabilities
    assert lstm["trajectory_mode_probability_calibrated"] is False
    assert lstm["path_confidence"] == 0.823456
    assert lstm["path_confidence_status"] == "READY"
    assert lstm["confidence"] == 0.8123
    assert lstm["trajectory_interval_status"] == "READY"
    assert lstm["trajectory_interval"] == interval_metadata
    retained_features = cast(list[dict[str, Any]], lstm["features"])
    assert len(retained_features) == 8
    assert retained_features[-1]["relative_price_location"] == anchor_location
    assert retained_path[0]["expected_open_norm"] == anchor_location
    assert "artifact_path" not in lstm
    assert "dense_history" not in lstm
    assert "unrelated_raw_payload" not in lstm
    assert cast(dict[str, Any], persisted["model_council_result"])["promotion_trace"]["next_required"] == (
        "wait for the retest"
    )
    compact_nested_council = cast(
        dict[str, Any],
        cast(dict[str, Any], persisted["model_council_result"])["model_council"],
    )
    assert cast(dict[str, Any], compact_nested_council["sequence_context"])["sequence_id"] == "sequence-42"
    assert "tracking_summary" not in cast(dict[str, Any], compact_nested_council["sequence_context"])

    retained_scenarios = cast(list[dict[str, Any]], lstm["trajectory_scenarios"])
    assert [scenario["side"] for scenario in retained_scenarios] == [
        "BUY",
        "SELL",
        "HOLD",
    ]
    for scenario in retained_scenarios:
        scenario_side = str(scenario["side"])
        scenario_path = cast(list[dict[str, Any]], scenario["forecast_path"])
        assert [row["step"] for row in scenario_path] == list(range(1, 13))
        assert scenario_path == expected_scenario_paths[scenario_side]
        assert "artifact_path" not in scenario
        assert "raw_payload" not in scenario
        assert all("latent_vector" not in row for row in scenario_path)

    compact_path = cold_tracker.session_dir(session_id) / "compact_live_state.json"
    compact = json.loads(compact_path.read_text(encoding="utf-8"))
    assert "forecast_snapshot_v3" not in compact
    assert "lstm_contribution" not in compact
    assert "forecast_path" not in compact
    assert compact_path.stat().st_size < 256 * 1024


def test_compact_persisted_scene_forecast_keeps_drawable_geometry() -> None:
    line_points = [[round(0.52 + index * 0.02, 6), round(0.48 - index * 0.003, 6)] for index in range(13)]
    forecast_candles = [
        {
            "step": index,
            "x_norm": line_points[index][0],
            "open_y_norm": line_points[index - 1][1],
            "high_y_norm": min(line_points[index - 1][1], line_points[index][1]) - 0.001,
            "low_y_norm": max(line_points[index - 1][1], line_points[index][1]) + 0.001,
            "close_y_norm": line_points[index][1],
            "movement_side": "BUY",
            "body_bias": "BUY",
        }
        for index in range(1, 13)
    ]
    payload = {
        "schema_version": "PG_SCENE_FORECAST_CONTRIBUTION_V3",
        "forecast_engine": "SCENE_FORECASTER_V3",
        "forecast_available": True,
        "path_side": "BUY",
        "geometry_frame_match_verified": True,
        "geometry_projected_frame_id": 7,
        "forecast_computed_frame_id": 4,
        "line_points": line_points,
        "forecast_path": [{"step": index, "expected_close_norm": line_points[index][1]} for index in range(1, 13)],
        "forecast_candles": forecast_candles,
        "forecast_scenarios": [
            {
                "role": "base",
                "side": "BUY",
                "selected": True,
                "line_points": line_points,
                "forecast_candles": forecast_candles,
            }
        ],
        "belief_tracker_checkpoint": {"large": "internal"},
    }

    compact = window_tracker_module._compact_persisted_forecast_summary(payload)  # pyright: ignore[reportPrivateUsage]

    assert compact["line_points"] == line_points
    assert [row["step"] for row in compact["forecast_path"]] == list(range(1, 13))
    assert [row["step"] for row in compact["forecast_candles"]] == list(range(1, 13))
    assert compact["forecast_scenarios"][0]["line_points"] == line_points


def _complete_scene_forecast_for_persistence(
    *,
    pair: str = "GBP/USD OTC",
    closed_candle_key: str = "closed-event-42",
) -> dict[str, Any]:
    line_points = [
        [round(0.52 + index * 0.02, 6), round(0.48 - index * 0.003, 6)]
        for index in range(13)
    ]
    forecast_candles = [
        {
            "step": index,
            "x_norm": line_points[index][0],
            "open_y_norm": line_points[index - 1][1],
            "high_y_norm": min(
                line_points[index - 1][1], line_points[index][1]
            )
            - 0.001,
            "low_y_norm": max(
                line_points[index - 1][1], line_points[index][1]
            )
            + 0.001,
            "close_y_norm": line_points[index][1],
        }
        for index in range(1, 13)
    ]
    return {
        "schema_version": "PG_SCENE_FORECAST_CONTRIBUTION_V3",
        "provider": "SCENE_STATISTICAL_FALLBACK_V3",
        "provider_status": "AVAILABLE",
        "forecast_available": True,
        "pair": pair,
        "timeframe": "M5",
        "market_identity_confirmed": True,
        "timeframe_identity_confirmed": True,
        "identity_contract_status": "CONFIRMED",
        "frame_id": 42,
        "display_frame_id": 43,
        "forecast_computed_frame_id": 42,
        "source_forecast_frame_id": 42,
        "geometry_projected_frame_id": 43,
        "geometry_frame_match_verified": True,
        "geometry_reprojected_from_cache": True,
        "frame_reused_without_reforecast": True,
        "closed_candle_key": closed_candle_key,
        "closed_candle_sequence": 7,
        "line_points": line_points,
        "forecast_candles": forecast_candles,
        "forecast_scenarios": [
            {
                "role": role,
                "side": side,
                "selected": role == "base",
                "line_points": line_points,
                "forecast_candles": forecast_candles,
            }
            for role, side in (("base", "BUY"), ("bull", "BUY"), ("bear", "SELL"))
        ],
    }


def test_same_event_control_save_retains_complete_scene_forecast_geometry() -> None:
    scene = _complete_scene_forecast_for_persistence()
    first = window_tracker_module._compact_session_persisted_payload(  # pyright: ignore[reportPrivateUsage]
        {
            "session_id": "pocket-live",
            "frame_index": 43,
            "display_frame_id": 43,
            "model_vote_frame_id": 43,
            "model_capture_epoch": 1_720_000_043.0,
            "tracking_summary": {"scene_forecast_contribution": scene},
            "latest_signal": {"scene_forecast_contribution": scene},
        }
    )
    assert not cast(dict[str, Any], first["latest_signal"])[
        "scene_forecast_contribution"
    ].get("line_points")

    second = window_tracker_module._compact_session_persisted_payload(first)  # pyright: ignore[reportPrivateUsage]
    retained = cast(
        dict[str, Any],
        cast(dict[str, Any], second["forecast_snapshot_v3"])[
            "scene_forecast_contribution"
        ],
    )

    assert retained["closed_candle_key"] == "closed-event-42"
    assert retained["pair"] == "GBP/USD OTC"
    assert len(retained["line_points"]) == 13
    assert len(retained["forecast_candles"]) == 12
    assert len(retained["forecast_scenarios"]) == 3
    assert all(
        len(scenario["line_points"]) == 13
        and len(scenario["forecast_candles"]) == 12
        for scenario in retained["forecast_scenarios"]
    )
    assert sum(
        bool(scenario.get("selected")) for scenario in retained["forecast_scenarios"]
    ) == 1


def test_changed_scene_identity_does_not_retain_previous_forecast_geometry() -> None:
    scene = _complete_scene_forecast_for_persistence()
    first = window_tracker_module._compact_session_persisted_payload(  # pyright: ignore[reportPrivateUsage]
        {
            "session_id": "pocket-live",
            "frame_index": 43,
            "display_frame_id": 43,
            "model_vote_frame_id": 43,
            "tracking_summary": {"scene_forecast_contribution": scene},
            "latest_signal": {"scene_forecast_contribution": scene},
        }
    )
    changed = dict(first)
    for surface_name in ("latest_signal", "tracking_summary"):
        surface = dict(cast(Mapping[str, Any], changed[surface_name]))
        summary_scene = dict(
            cast(Mapping[str, Any], surface["scene_forecast_contribution"])
        )
        summary_scene.update(
            {
                "pair": "EUR/USD OTC",
                "closed_candle_key": "closed-event-43",
                "closed_candle_sequence": 8,
            }
        )
        surface["scene_forecast_contribution"] = summary_scene
        changed[surface_name] = surface

    second = window_tracker_module._compact_session_persisted_payload(changed)  # pyright: ignore[reportPrivateUsage]
    current = cast(
        dict[str, Any],
        cast(dict[str, Any], second["forecast_snapshot_v3"])[
            "scene_forecast_contribution"
        ],
    )

    assert current["closed_candle_key"] == "closed-event-43"
    assert current["pair"] == "EUR/USD OTC"
    assert not current.get("line_points")
    assert not current.get("forecast_candles")
    assert not current.get("forecast_scenarios")


def test_order_positioning_source_snapshot_is_bounded_and_replaced_per_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_rows = [
        {
            "schema_version": "PG_V3_OVERLAY_OBJECT_V1",
            "overlay_id": f"source-{index}",
            "type": "DEMAND_ZONE",
            "frame_id": 81,
            "bounds": [0.10, 0.60, 0.20, 0.66],
        }
        for index in range(30)
    ]
    def source_rows_stub(
        _payload: Mapping[str, Any],
    ) -> list[dict[str, Any]]:
        return [dict(row) for row in source_rows]

    monkeypatch.setattr(
        window_tracker_module,
        "order_positioning_evidence_rows_v3",
        source_rows_stub,
    )
    snapshot_builder = cast(
        Callable[[Mapping[str, Any]], dict[str, Any]],
        getattr(
            window_tracker_module,
            "_tracking_summary_with_order_positioning_sources_v3",
        ),
    )

    first = snapshot_builder(
        {
            "frame_index": 81,
            "display_frame_id": 81,
            "chart_frame_id": 81,
            "overlay_frame_id": 81,
            "model_vote_frame_id": 81,
            "tracking_summary": {
                "order_positioning_sources_v3": {
                    "frame_id": 80,
                    "objects": [{"overlay_id": "stale-source"}],
                }
            },
        }
    )
    first_snapshot = cast(
        dict[str, Any], first["order_positioning_sources_v3"]
    )
    assert first_snapshot["frame_id"] == 81
    assert len(cast(list[Any], first_snapshot["objects"])) == 24
    assert all(
        row["overlay_id"] != "stale-source"
        for row in cast(list[dict[str, Any]], first_snapshot["objects"])
    )

    source_rows[:] = [
        {
            "schema_version": "PG_V3_OVERLAY_OBJECT_V1",
            "overlay_id": "source-next-frame",
            "type": "SUPPLY_ZONE",
            "frame_id": 82,
            "bounds": [0.10, 0.30, 0.20, 0.36],
        }
    ]
    second = snapshot_builder(
        {
            "frame_index": 82,
            "display_frame_id": 82,
            "chart_frame_id": 82,
            "overlay_frame_id": 82,
            "model_vote_frame_id": 82,
            "tracking_summary": first,
        }
    )
    second_snapshot = cast(
        dict[str, Any], second["order_positioning_sources_v3"]
    )
    assert second_snapshot["frame_id"] == 82
    assert cast(list[dict[str, Any]], second_snapshot["objects"]) == source_rows

    compacted = window_tracker_module._compact_session_persisted_payload(  # pyright: ignore[reportPrivateUsage]
        {"tracking_summary": first}
    )
    compact_snapshot = cast(
        dict[str, Any],
        cast(dict[str, Any], compacted["tracking_summary"])[
            "order_positioning_sources_v3"
        ],
    )
    assert len(cast(list[Any], compact_snapshot["objects"])) == 24


def test_stale_study_gate_recovers_after_watchdog_window() -> None:
    tracker = ContinuousWindowTrackerService()
    session_id = "stale-study"

    assert tracker._begin_study_gate(session_id) is True  # pyright: ignore[reportPrivateUsage]
    assert tracker._begin_study_gate(session_id) is False  # pyright: ignore[reportPrivateUsage]

    tracker.active_study_started_epoch[session_id] = time.time() - 180.0

    assert tracker._begin_study_gate(session_id) is True  # pyright: ignore[reportPrivateUsage]
    tracker._finish_study_gate(session_id)  # pyright: ignore[reportPrivateUsage]
    assert session_id not in tracker.active_studies


def test_duplicate_live_chart_pixels_do_not_advance_model_freshness(tmp_path: Path) -> None:
    frozen = _surface(width=1280, height=720)
    adapter = _FakeTrackingAdapter("BUY")
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([frozen, frozen.copy(), frozen.copy()]),
        tracking_adapter=adapter,
    )
    session = tracker.create_session(session_id="pocket-live")
    first = tracker.set_focus_region(str(session["session_id"]), [0.10, 0.10, 0.88, 0.86], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    tracking_summary = cast(dict[str, Any], payload["tracking_summary"])
    tracking_summary["two_candle_study"] = {
        "schema_version": "PG_TWO_CANDLE_STUDY_V3",
        "frame_id": int(first["frame_index"]),
        "status": "READY",
        "primary_pressure": "SELL",
        "next_candle_forecast": {"direction": "SELL", "confidence": 0.63},
    }
    tracking_summary["lstm_contribution"] = {
        "schema_version": "PG_LSTM_CANDLE_PATH_CONTRIBUTION_V3",
        "fresh": True,
        "forecast_available": True,
        "confidence": 0.77,
        "path_side": "SELL",
        "source_image_size": [1024, 576],
        "features": [{"relative_price_location": 0.52}],
        "forecast_path": [
            {
                "step": 1,
                "expected_close_norm": 0.49,
                "close_lower_90_norm": 0.45,
                "close_upper_90_norm": 0.53,
            }
        ],
        # These internal artifact locations must not leak into the retained
        # frame-aligned diagnostic snapshot.
        "artifact_path": r"C:\private\lstm.pt",
        "config_path": r"C:\private\lstm.json",
    }
    tracker.save_session(payload)

    baseline_frame = int(first["frame_index"])
    baseline_count = int(first["capture_count"])
    baseline_epoch = float(first["last_capture_epoch"])
    baseline_model_epoch = float(first["model_capture_epoch"])
    _allow_next_capture(tracker, str(session["session_id"]))
    duplicate = tracker.capture_once(str(session["session_id"]))

    assert int(duplicate["frame_index"]) == baseline_frame
    assert int(duplicate["capture_count"]) == baseline_count
    assert float(duplicate["last_capture_epoch"]) == baseline_epoch
    assert float(duplicate["model_capture_epoch"]) == baseline_model_epoch
    assert adapter.calls == 1
    assert duplicate["visual_observation_v3"]["status"] == "WAITING_FOR_NEW_FRAME"
    assert duplicate["visual_observation_v3"]["new_visual_evidence"] is False
    assert duplicate["latest_signal"]["execution_action"] == "HOLD"
    assert duplicate["latest_signal"]["actionable"] is False
    assert duplicate["latest_signal"]["execution_permission"] == "WAIT"
    assert duplicate["execution_packet_present"] is False
    assert duplicate["decision_valid_until_epoch"] == 0.0
    persisted = tracker.load_session_payload(str(session["session_id"]))
    forecast_snapshot = cast(dict[str, Any], persisted["forecast_snapshot_v3"])
    assert forecast_snapshot["source_frame_id"] == baseline_frame
    assert forecast_snapshot["status"] == "STALE_DIAGNOSTIC"
    assert forecast_snapshot["stale"] is True
    assert forecast_snapshot["diagnostic_only"] is True
    assert cast(dict[str, Any], forecast_snapshot["lstm_contribution"])["forecast_available"] is True
    assert cast(dict[str, Any], forecast_snapshot["two_candle_study"])["primary_pressure"] == "SELL"
    assert "artifact_path" not in cast(dict[str, Any], forecast_snapshot["lstm_contribution"])
    assert "config_path" not in cast(dict[str, Any], forecast_snapshot["lstm_contribution"])


def test_frozen_study_recovers_even_when_full_window_signature_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "0")
    frozen = _surface(width=1280, height=720)
    chrome_variants: list[Image.Image] = []
    for index in range(4):
        variant = frozen.copy()
        ImageDraw.Draw(variant).rectangle(
            (0, 0, 120, 48),
            fill=(35 + index * 20, 42, 54),
        )
        chrome_variants.append(variant)
    recovered = frozen.copy()
    ImageDraw.Draw(recovered).rectangle((180, 160, 260, 260), fill=(80, 210, 96))
    backend = _RecoveringDuplicateCaptureBackend(
        chrome_variants,
        recovered,
    )
    adapter = _FakeTrackingAdapter("SELL")
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=adapter,
    )
    monkeypatch.setenv("PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_THRESHOLD", "3")
    monkeypatch.setenv("PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_MIN_INTERVAL_SEC", "15")
    session = tracker.create_session(session_id="pocket-live")
    first = tracker.set_focus_region(str(session["session_id"]), [0.10, 0.10, 0.88, 0.86], source="test")
    payload = tracker.load_session_payload(str(session["session_id"]))
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    tracker.save_session(payload)

    for expected_duplicate_count in (1, 2):
        _allow_next_capture(tracker, str(session["session_id"]))
        waiting = tracker.capture_once(str(session["session_id"]))
        assert int(waiting["frame_index"]) == int(first["frame_index"])
        assert waiting["visual_observation_v3"]["duplicate_study_count"] == expected_duplicate_count
        assert waiting["visual_observation_v3"]["identical_window_count"] == 0
        assert backend.live_recovery_calls == 0

    _allow_next_capture(tracker, str(session["session_id"]))
    refreshed = tracker.capture_once(str(session["session_id"]))

    assert backend.live_recovery_calls == 1
    assert adapter.calls == 2
    assert int(refreshed["frame_index"]) == int(first["frame_index"]) + 1
    assert int(refreshed["capture_count"]) == int(first["capture_count"]) + 1
    assert refreshed["visual_observation_v3"]["status"] == "RECOVERED_NEW_FRAME"
    assert refreshed["visual_observation_v3"]["new_visual_evidence"] is True
    assert refreshed["visual_observation_v3"]["recovery_succeeded"] is True
    assert refreshed["visual_observation_v3"]["last_recovery_attempt_epoch"] == 0.0


def test_snapshot_watchdog_does_not_compete_with_cpu_stream_recovery_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = _surface(width=1280, height=720)
    recovered = frozen.copy()
    ImageDraw.Draw(recovered).rectangle(
        (180, 160, 260, 260),
        fill=(80, 210, 96),
    )
    backend = _RecoveringDuplicateCaptureBackend(
        [frozen, frozen.copy(), frozen.copy(), frozen.copy()],
        recovered,
    )
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=_FakeTrackingAdapter("SELL"),
    )
    monkeypatch.setenv("PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_THRESHOLD", "2")
    session = tracker.create_session(session_id="pocket-live")
    focused = tracker.set_focus_region(
        str(session["session_id"]),
        [0.10, 0.10, 0.88, 0.86],
        source="test",
    )
    session_id = str(session["session_id"])
    payload = tracker.load_session_payload(session_id)
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    tracker.save_session(payload)
    tracker._cpu_stream_failures[session_id] = {  # pyright: ignore[reportPrivateUsage]
        "status": "fallback_snapshot",
        "last_error": "test-owned CPU stream recovery",
    }

    def keep_snapshot_owner(_session_id: str) -> None:
        return None

    monkeypatch.setattr(tracker, "_cpu_stream_requested_v3", lambda: True)
    monkeypatch.setattr(tracker, "_ensure_cpu_stream_v3", keep_snapshot_owner)

    for _ in range(3):
        _allow_next_capture(tracker, session_id)
        observed = tracker.capture_once(session_id)
        assert int(observed["frame_index"]) == int(focused["frame_index"])

    assert backend.live_recovery_calls == 0


def test_unsafe_frozen_study_recovery_stays_waiting_and_throttles_immediate_retries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "0")

    class _UnavailableLiveRecoveryBackend(_FakeCaptureBackend):
        def __init__(self, images: Sequence[Image.Image]) -> None:
            super().__init__(images)
            self.live_recovery_calls = 0

        def capture_window_live(self, descriptor: Mapping[str, Any]) -> Image.Image:
            _ = descriptor
            self.live_recovery_calls += 1
            raise CaptureSurfaceUnavailableError(
                "Visible broker-frame recovery was blocked because foreground ownership changed during capture."
            )

    frozen = _surface(width=1280, height=720)
    chrome_variants: list[Image.Image] = []
    for index in range(7):
        variant = frozen.copy()
        ImageDraw.Draw(variant).rectangle(
            (0, 0, 120, 48),
            fill=(35 + index * 15, 42, 54),
        )
        chrome_variants.append(variant)
    backend = _UnavailableLiveRecoveryBackend(chrome_variants)
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=backend,
        tracking_adapter=_FakeTrackingAdapter("SELL"),
    )
    monkeypatch.setenv("PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_THRESHOLD", "2")
    monkeypatch.setenv("PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_MIN_INTERVAL_SEC", "120")
    session = tracker.create_session(session_id="pocket-live")
    first = tracker.set_focus_region(
        str(session["session_id"]),
        [0.10, 0.10, 0.88, 0.86],
        source="test",
    )
    payload = tracker.load_session_payload(str(session["session_id"]))
    payload["tracking_enabled"] = True
    payload["status"] = "running"
    tracker.save_session(payload)

    waits: list[dict[str, Any]] = []
    for _ in range(5):
        _allow_next_capture(tracker, str(session["session_id"]))
        waits.append(tracker.capture_once(str(session["session_id"])))

    attempted = waits[1]["visual_observation_v3"]
    last_wait = waits[-1]["visual_observation_v3"]
    assert backend.live_recovery_calls == 1
    assert attempted["recovery_attempted"] is True
    assert float(attempted["last_recovery_attempt_epoch"]) > 0.0
    assert last_wait["recovery_attempted"] is False
    assert last_wait["last_recovery_attempt_epoch"] == attempted["last_recovery_attempt_epoch"]
    assert int(waits[-1]["frame_index"]) == int(first["frame_index"])
    assert waits[-1]["latest_signal"]["execution_permission"] == "WAIT"
    assert waits[-1]["execution_packet_present"] is False


def test_tracker_live_mode_writes_fresh_hot_overlays_by_default(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.delenv("PHOENIXGUARD_LIVE_MINIMAL_HOT_ARTIFACTS", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_LIVE_FULL_OVERLAY_EVERY_N", raising=False)
    first_frame = _synthetic_chart_surface("buy", width=1280, height=720)
    changed_frame = first_frame.copy()
    # Fresh hot artifacts require fresh visual evidence. A byte-identical
    # second capture must stay WAITING_FOR_NEW_FRAME instead of minting a new
    # model epoch, so give this test a genuine in-chart pixel change.
    ImageDraw.Draw(changed_frame).rectangle((1030, 438, 1042, 462), fill=(43, 205, 92))
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend(
            [
                first_frame,
                changed_frame,
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
    first_frame = _synthetic_chart_surface("buy", width=1280, height=720)
    changed_frame = first_frame.copy()
    ImageDraw.Draw(changed_frame).rectangle((1030, 438, 1042, 462), fill=(43, 205, 92))
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend(
            [
                first_frame,
                changed_frame,
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
    assert Path(str(result["last_chart_path"])).name.startswith(
        f"{int(result['frame_index']):06d}_"
    )
    assert not Path(str(result["last_chart_path"])).name.startswith("hot_latest_chart")
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
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "0")
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
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "0")
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


def test_tracker_display_only_uses_validated_native_capture_fallback_when_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "0")
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
    monkeypatch.setenv("PHOENIXGUARD_DISPLAY_ALLOW_NATIVE_CAPTURE_FALLBACK", "1")

    result = tracker.capture_once(session_id, display_only=True)

    assert result["capture_once_result"]["ok"] is True
    assert backend.fast_capture_calls == 1
    assert backend.capture_calls == 1
    assert Path(str(result["last_display_window_path"])).exists()


def test_tracker_display_only_rejects_invalid_native_capture_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY", "0")
    backend = _FailingFastVisibleCaptureBackend(Image.new("RGB", (1280, 720), color=(0, 0, 0)))
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
    monkeypatch.setenv("PHOENIXGUARD_DISPLAY_ALLOW_NATIVE_CAPTURE_FALLBACK", "1")

    result = tracker.capture_once(session_id, display_only=True)

    assert result["capture_once_result"]["ok"] is False
    assert backend.fast_capture_calls == 1
    assert backend.capture_calls == 1
    assert "did not include Pocket Option pixels" in result["capture_once_result"]["error"]


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
    assert int(second["display_frame_id"]) <= int(first["display_frame_id"])
    assert second["last_display_window_path"] == first["last_display_window_path"]
    assert second["display_fast_path_v3"]["reuse_only_heartbeat"] is True
    assert second["display_fast_path_v3"]["heartbeat_published_epoch"] > 0.0
    assert second["frame_bundle_complete_v3"] is False
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
    assert int(second["display_frame_id"]) <= first_display
    assert second["last_display_window_path"] == first_path
    assert second["display_snapshot_busy_v3"] is True
    assert second["display_heartbeat_epoch"] == 1009.0
    assert second["display_busy_reuse_heartbeat_v3"]["heartbeat_published_epoch"] == 1009.0
    assert second["frame_bundle_complete_v3"] is False
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
            assert release.wait(20.0)
            return super().study(image, session_payload=session_payload)

    setattr(tracker, "tracking_adapter", _BlockingTrackingAdapter("BUY"))
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(tracker.capture_and_analyze, session_id, force=True)
        assert started.wait(20.0)
        tracker.capture_and_analyze(session_id, force=True)
        release.set()
        future.result(timeout=30.0)

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


def test_tracker_accepts_chart_study_when_live_broker_guard_blocks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    chart = _synthetic_chart_surface("buy", width=1280, height=720)
    backend = _ListedWindowCaptureBackend(
        [
            {
                "hwnd": 808,
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

    def _force_pocket_guard(_payload: Mapping[str, Any]) -> bool:
        return True

    monkeypatch.setattr(tracker, "_pocket_option_surface_guard_enabled", _force_pocket_guard)

    session = tracker.create_session(session_id="chart-live-study", window_query="The Most Innovative Trading Platform")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    tracker.update_session_controls(str(session["session_id"]), live_execution_enabled=True, execution_mode="live")

    payload = tracker.capture_once(str(session["session_id"]))

    tracking_summary = cast(dict[str, Any], payload["tracking_summary"])
    broker_source_lock = cast(dict[str, Any], tracking_summary["broker_source_lock"])
    broker_source = cast(dict[str, Any], tracking_summary["broker_source"])
    broker_surface = cast(dict[str, Any], payload["broker_surface"])
    broker_execution_state = cast(dict[str, Any], payload["broker_execution_state"])
    assert payload["status"] != "waiting_for_broker_surface"
    assert broker_source_lock["valid"] is True
    assert broker_source_lock["status"] == "VALID"
    assert "CHART_STUDY_SOURCE_LOCKED" in broker_source_lock["reason_codes"]
    assert broker_source["valid"] is True
    assert broker_source["wrong_surface"] is False
    assert broker_source["study_source_only"] is True
    assert broker_source["broker_click_safe"] is False
    assert broker_surface["study_source_only"] is True
    assert broker_surface["broker_click_safe"] is False
    assert broker_execution_state["status"] not in {"armed", "ready_to_click"}


def test_tracker_accepts_generic_chart_title_as_live_study_source(tmp_path: Path) -> None:
    chart = _synthetic_chart_surface("buy", width=1280, height=720)
    backend = _ListedWindowCaptureBackend(
        [
            {
                "hwnd": 909,
                "title": "AUDUSD 0.69380 - Microsoft Edge",
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

    session = tracker.create_session(session_id="audusd-live-study", window_query="AUDUSD")
    tracker.set_focus_region(str(session["session_id"]), [0.0, 0.0, 1.0, 1.0], source="test")
    tracker.update_session_controls(str(session["session_id"]), live_execution_enabled=True, execution_mode="live")

    payload = tracker.capture_once(str(session["session_id"]))

    tracking_summary = cast(dict[str, Any], payload["tracking_summary"])
    broker_source_lock = cast(dict[str, Any], tracking_summary["broker_source_lock"])
    broker_source = cast(dict[str, Any], tracking_summary["broker_source"])
    broker_surface = cast(dict[str, Any], payload["broker_surface"])
    broker_execution_state = cast(dict[str, Any], payload["broker_execution_state"])
    assert payload["status"] != "waiting_for_broker_surface"
    assert broker_source_lock["valid"] is True
    assert broker_source_lock["status"] == "VALID"
    assert broker_source_lock["reason_codes"] == ["CHART_STUDY_SOURCE_LOCKED"]
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
    assert payload.get("frame_bundle_complete_v3", False) is False
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


def test_tracker_worker_loop_does_not_reload_session_during_interval_waits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path)
    session_id = str(tracker.create_session(session_id="pocket-live", capture_interval_sec=1.0)["session_id"])
    payload = tracker.load_session_payload(session_id)
    payload["tracking_enabled"] = True
    payload["latest_signal"] = {"status": "tracking", "action": "SELL"}
    payload["tracking_summary"] = {"chart_valid": True}
    write_json_atomic(tracker.session_dir(session_id) / "session.json", payload)

    clock = {"now": 1000.0}
    captures: list[float] = []
    wait_timeouts: list[float] = []
    load_calls = {"count": 0}

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
            wait_timeout = float(timeout or 0.0)
            wait_timeouts.append(wait_timeout)
            clock["now"] += wait_timeout
            return self._is_set

    stop_evt = _LoopEvent()
    capture_now_evt = _LoopEvent(set_initially=True)
    original_load_session = tracker._load_session  # pyright: ignore[reportPrivateUsage]

    def counted_load_session(captured_session_id: str) -> dict[str, Any]:
        load_calls["count"] += 1
        return original_load_session(captured_session_id)

    def capture_stub(captured_session_id: str, *, force: bool = False) -> None:
        _ = (captured_session_id, force)
        captures.append(clock["now"])
        if len(captures) >= 2:
            stop_evt.set()

    monkeypatch.setattr(tracker, "_load_session", counted_load_session)
    monkeypatch.setattr(tracker, "capture_and_analyze", capture_stub)
    def fixed_tracking_plan(_payload: Mapping[str, Any]) -> dict[str, object]:
        return {"interval_sec": 1.0, "reason": "tracking"}

    monkeypatch.setattr(
        tracker,
        "adaptive_capture_interval_plan",
        fixed_tracking_plan,
    )
    monkeypatch.setattr(window_tracker_module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(window_tracker_module.time, "time", lambda: clock["now"])

    tracker.worker_loop(
        session_id,
        cast(threading.Event, stop_evt),
        cast(threading.Event, capture_now_evt),
    )

    assert captures == [1000.0, 1001.0]
    assert wait_timeouts == [0.25, 0.25, 0.25, 0.25]
    assert load_calls["count"] == 4


def test_tracker_worker_schedules_next_capture_after_slow_capture_completes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path)
    session_id = str(tracker.create_session(session_id="pocket-live", capture_interval_sec=0.5)["session_id"])
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
        del force
        capture_times.append(clock["now"])
        clock["now"] += 2.0
        current = tracker.load_session_payload(captured_session_id)
        current["tracking_enabled"] = True
        current["latest_signal"] = {"status": "tracking", "action": "SELL"}
        current["tracking_summary"] = {"chart_valid": True}
        write_json_atomic(tracker.session_dir(captured_session_id) / "session.json", current)
        if len(capture_times) >= 2:
            stop_evt.set()

    monkeypatch.setattr(tracker, "capture_and_analyze", capture_stub)
    def half_second_tracking_plan(_payload: Mapping[str, Any]) -> dict[str, object]:
        return {"interval_sec": 0.5, "reason": "tracking"}

    monkeypatch.setattr(
        tracker,
        "adaptive_capture_interval_plan",
        half_second_tracking_plan,
    )
    monkeypatch.setattr(window_tracker_module.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(window_tracker_module.time, "time", lambda: clock["now"])

    tracker.worker_loop(
        session_id,
        cast(threading.Event, stop_evt),
        cast(threading.Event, capture_now_evt),
    )

    assert capture_times == [1000.0, 1002.5]


def test_live_adaptive_timer_respects_configured_capture_interval(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(root_dir=tmp_path)
    session = tracker.create_session(session_id="pocket-live", capture_interval_sec=15.0)
    payload = tracker.load_session_payload(str(session["session_id"]))
    controls = dict(payload["execution_controls"])
    controls.update(
        {
            "adaptive_timer_enabled": True,
            "live_execution_enabled": True,
            "execution_mode": "live",
            "min_capture_interval_sec": 0.5,
            "max_capture_interval_sec": 15.0,
            "max_capture_interval_explicit_v3": True,
        }
    )
    payload["execution_controls"] = controls
    payload["tracking_enabled"] = True
    payload["manual_focus_region"] = {
        "enabled": True,
        "normalized_bbox": [0.0, 0.0, 1.0, 1.0],
    }

    plan = tracker.adaptive_capture_interval_plan(payload)

    assert float(plan["interval_sec"]) == 15.0
    assert plan["reason"] == "live_configured_interval"


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


def test_tracker_does_not_reacquire_unrelated_same_browser_family_for_pocket_option(tmp_path: Path) -> None:
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

    refreshed_payload = tracker.load_session_payload(str(session["session_id"]))
    resolved = tracker._resolve_window_descriptor(  # pyright: ignore[reportPrivateUsage]  # noqa: SLF001
        refreshed_payload
    )

    assert resolved is None


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
    display_frame_id = int(payload["display_frame_id"])
    assert display_frame_id > 0

    session_response = client.get(f"/v1/mobile/window-tracker/sessions/{session_id}")
    assert session_response.status_code == 200

    chart_response = client.get(
        f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-chart"
        f"?frame_id={display_frame_id}"
    )
    assert chart_response.status_code == 200
    assert chart_response.headers["content-type"].startswith("image/png")
    assert "no-store" in chart_response.headers["cache-control"]

    window_response = client.get(
        f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-window"
        f"?frame_id={display_frame_id}"
    )
    assert window_response.status_code == 200
    assert window_response.headers["content-type"].startswith("image/")
    assert "no-store" in window_response.headers["cache-control"]

    for artifact_kind in ("chart", "window"):
        stale_response = client.get(
            f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-{artifact_kind}"
            f"?frame_id={display_frame_id + 1}"
        )
        assert stale_response.status_code == 409
        assert stale_response.json() == {
            "detail": "Requested artifact frame is no longer current."
        }

    overlay_response = client.get(f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-overlay")
    assert overlay_response.status_code == 200
    assert overlay_response.headers["content-type"].startswith("image/png")
    assert "no-store" in overlay_response.headers["cache-control"]

    full_overlay_response = client.get(f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-full-overlay")
    assert full_overlay_response.status_code == 200
    assert full_overlay_response.headers["content-type"].startswith("image/png")
    assert "no-store" in full_overlay_response.headers["cache-control"]

    trigger_layer_response = client.get(
        f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-full-overlay?mode=TRIGGER&layers=trigger_zones"
    )
    assert trigger_layer_response.status_code == 200
    assert trigger_layer_response.headers["content-type"].startswith("image/png")
    assert trigger_layer_response.headers["x-phoenixguard-overlay-mode"] == "TRIGGER"
    assert trigger_layer_response.headers["x-phoenixguard-overlay-layers"] == "trigger_zones"

    dashboard_response = client.get(f"/v3/mobile/window-tracker/dashboard/{session_id}")
    assert dashboard_response.status_code == 200
    assert "<title>808Fx Standard Hybrid System Live Tracker</title>" in dashboard_response.text
    assert 'id="beginner-decision-shell"' in dashboard_response.text
    assert 'id="overlay-explorer" aria-label="Overlay views"' in dashboard_response.text
    assert "runtime_telemetry" not in dashboard_response.text


def test_tracker_artifact_route_serves_an_archived_exact_frame_after_latest_advances(
    tmp_path: Path,
) -> None:
    session_root = tmp_path / "session"
    artifact_dir = session_root / "artifacts"
    artifact_dir.mkdir(parents=True)
    old_chart = artifact_dir / "000011_old_chart.png"
    old_window = artifact_dir / "000011_old_window.png"
    current_chart = artifact_dir / "000012_current_chart.png"
    current_window = artifact_dir / "000012_current_window.png"
    for path, color in (
        (old_chart, (11, 11, 11)),
        (old_window, (12, 12, 12)),
        (current_chart, (21, 21, 21)),
        (current_window, (22, 22, 22)),
    ):
        Image.new("RGB", (64, 40), color=color).save(path)

    class ArchivedArtifactTracker:
        def latest_artifact_path(self, session_id: str, artifact_kind: str) -> Path:
            assert session_id == "archive-session"
            return current_chart if artifact_kind == "chart" else current_window

        def get_session_snapshot(self, session_id: str) -> dict[str, Any]:
            assert session_id == "archive-session"
            return {
                "session_id": session_id,
                "display_frame_id": 12,
                "chart_frame_id": 12,
                "frame_index": 12,
            }

        def session_dir(self, session_id: str) -> Path:
            assert session_id == "archive-session"
            return session_root

    client = TestClient(create_app(window_tracker_service=ArchivedArtifactTracker()))

    for artifact_kind in ("chart", "window"):
        archived = client.get(
            "/v1/mobile/window-tracker/sessions/archive-session/artifacts/"
            f"latest-{artifact_kind}?frame_id=11"
        )
        assert archived.status_code == 200
        assert archived.headers["content-type"].startswith("image/png")

        missing_future = client.get(
            "/v1/mobile/window-tracker/sessions/archive-session/artifacts/"
            f"latest-{artifact_kind}?frame_id=13"
        )
        assert missing_future.status_code == 409


def test_tracker_dashboard_prioritizes_decision_chart_and_history_without_technical_clutter() -> None:
    dashboard_html = (
        Path(__file__).resolve().parents[2]
        / "Frontend"
        / "dashboard"
        / "static"
        / "window_tracker_dashboard.html"
    ).read_text(encoding="utf-8")

    assert "<title>808Fx Standard Hybrid System Live Tracker</title>" in dashboard_html
    assert 'id="current-move-title"' in dashboard_html
    assert 'id="inner-trend-title"' in dashboard_html
    assert 'id="forecast-title"' not in dashboard_html
    assert 'id="permission-title"' in dashboard_html
    assert 'id="surface-stage"' in dashboard_html
    assert "object-fit: contain;" in dashboard_html
    assert 'id="market-history"' in dashboard_html
    assert "Regression study · candle by candle" in dashboard_html
    assert "Each row keeps the major trend, inner trend" in dashboard_html
    assert "voice-toggle" not in dashboard_html
    for removed_surface in (
        "Path Quality",
        "Entry Quality",
        "Top 3 Forecasts",
        "accepted_by",
        "memory retrieval running",
        "aggressive sniper",
        "Control Map",
        "Map Clock",
    ):
        assert removed_surface not in dashboard_html


def test_tracker_dashboard_fits_and_pans_the_same_interactive_surface() -> None:
    dashboard_html = (
        Path(__file__).resolve().parents[2]
        / "Frontend"
        / "dashboard"
        / "static"
        / "window_tracker_dashboard.html"
    ).read_text(encoding="utf-8")

    assert "function calculateFitScale()" in dashboard_html
    assert "Math.min(width / state.naturalWidth, height / state.naturalHeight)" in dashboard_html
    assert "function applySurfaceScale()" in dashboard_html
    assert "function setZoomMode(mode, value)" in dashboard_html
    assert 'id="zoom-fit"' in dashboard_html
    assert 'id="zoom-actual"' in dashboard_html
    assert 'id="zoom-in"' in dashboard_html
    assert 'id="zoom-out"' in dashboard_html
    assert 'id="mode-overlay"' in dashboard_html
    assert 'id="mode-raw"' in dashboard_html
    assert 'els.surfaceStage.addEventListener("pointerdown"' in dashboard_html
    assert "els.surfaceStage.scrollLeft = state.dragScrollLeft" in dashboard_html
    assert "/v1/mobile/operator/state/v1/" in dashboard_html
    assert "/v1/mobile/live/state/v3/" not in dashboard_html
    assert "overlay_source_window_signature" not in dashboard_html
    assert "chart_transform_id" not in dashboard_html


def test_tracker_dashboard_history_overlays_use_semantic_filters_and_collision_budget() -> None:
    dashboard_html = (
        Path(__file__).resolve().parents[2]
        / "Frontend"
        / "dashboard"
        / "static"
        / "window_tracker_dashboard.html"
    ).read_text(encoding="utf-8")

    assert 'data-overlay-view="history"' in dashboard_html
    assert 'data-overlay-family="history"' in dashboard_html
    assert 'data-overlay-family="market_context"' in dashboard_html
    assert 'data-overlay-family="lstm"' not in dashboard_html
    assert 'data-overlay-family="scene_forecaster"' not in dashboard_html
    assert ".surface-trendline.family-scene-forecaster" not in dashboard_html
    assert ".surface-trendline.family-lstm" not in dashboard_html
    assert "forecast: [\"two_candle\", \"scene_forecaster\", \"lstm\", \"prediction\"]" not in dashboard_html
    assert "RETIRED_FORECAST_FAMILIES" not in dashboard_html
    assert 'data-label-mode="on"' in dashboard_html
    assert 'data-label-mode="hover"' in dashboard_html
    assert 'data-label-mode="off"' in dashboard_html
    assert "function overlayPriority(overlay)" in dashboard_html
    assert "function resolveLabelCollisions(container)" in dashboard_html
    assert "window.resolveLabelCollisions = resolveLabelCollisions;" in dashboard_html
    assert "label-collision-hidden" in dashboard_html
    assert "body.labels-on.labels-show-all .surface-hotspot.label-policy-hidden span" in dashboard_html
    assert "body.labels-on.labels-show-all .surface-hotspot.label-collision-hidden span" in dashboard_html
    assert 'els.body.classList.toggle("labels-show-all", exhaustiveLabelModeActive());' in dashboard_html
    lowered = dashboard_html.lower()
    for private_term in (
        "smc",
        "liquidity",
        "order block",
        "order_block",
        "fair value gap",
        "fair_value_gap",
        "fvg",
    ):
        assert private_term not in lowered
    assert "REPLAY: {objects:" not in dashboard_html
    assert "FULL_HISTORY_READ: {objects:" not in dashboard_html


def test_tracker_dashboard_has_no_retired_lstm_route_renderer() -> None:
    dashboard_html = (
        Path(__file__).resolve().parents[2]
        / "Frontend"
        / "dashboard"
        / "static"
        / "window_tracker_dashboard.html"
    ).read_text(encoding="utf-8")

    for retired_renderer_trace in (
        "forecastRole",
        "createForecastComposite",
        "forecast_scenarios",
        "surface-forecast-step-node",
        "forecast_candles",
        "surface-forecast-candle-body",
        "surface-forecast-band",
        "forecast-boundary",
        "forecast-path-hit",
    ):
        assert retired_renderer_trace not in dashboard_html


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
    assert "if ($BrokerWindowHwnd -gt 0)" in launcher
    assert "--window-hwnd" in launcher
    assert "$liveClickArm = if ($ShooterMode -eq 'LIVE_READY'" not in launcher
    assert "--shooter-mode" not in launcher
    assert "--no-auto-open" not in launcher


def test_tracker_http_surface_has_no_manual_projection_actions(tmp_path: Path) -> None:
    tracker_service = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_chart_surface("sell")]),
        tracking_adapter=PhoenixGuardWindowTrackingAdapter(),
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
    assert predict_response.status_code == 404

    future_response = client.post(f"/v1/mobile/window-tracker/sessions/{session_id}/show-future")
    assert future_response.status_code == 404

    action_response = client.get(
        f"/v1/mobile/window-tracker/sessions/{session_id}"
        "/forecast-actions/retired-request"
    )
    assert action_response.status_code == 404
    assert client.get(
        f"/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-projection"
    ).status_code == 404
    public = tracker_service.get_session_snapshot(session_id)
    assert "memory_projection_active_mode" not in public
    assert "memory_projection_predict" not in public
    assert "memory_projection_future" not in public


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
            "require_market_identity": False,
            "require_timeframe_identity": True,
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
    assert payload["execution_controls"]["require_market_identity"] is False
    assert payload["execution_controls"]["require_timeframe_identity"] is True
    assert payload["execution_controls"]["adaptive_timer_enabled"] is False
    assert float(payload["execution_controls"]["min_capture_interval_sec"]) == 0.5
    assert float(payload["execution_controls"]["max_capture_interval_sec"]) == 10.0
    assert int(payload["execution_controls"]["max_executions_per_window"]) == 3
    assert float(payload["execution_controls"]["execution_window_sec"]) == 180.0
    assert float(payload["execution_controls"]["cooldown_sec"]) == 900.0
    assert float(payload["execution_controls"]["phoenix_report_interval_sec"]) == 24.0
    for private_key in (
        "auto_memory_projection",
        "require_memory_projection",
        "projection_focus",
    ):
        assert private_key not in payload["execution_controls"]


def test_tracker_http_rejects_private_projection_controls(tmp_path: Path) -> None:
    tracker_service = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_surface(width=1280, height=720)]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
    )
    client = TestClient(create_app(window_tracker_service=tracker_service))
    session_id = client.post(
        "/v1/mobile/window-tracker/sessions",
        json={"session_id": "private-control-boundary"},
    ).json()["session_id"]

    response = client.patch(
        f"/v1/mobile/window-tracker/sessions/{session_id}/controls",
        json={
            "auto_memory_projection": False,
            "require_memory_projection": False,
            "projection_focus": 0.7,
        },
    )

    assert response.status_code == 422
    rejected_fields = {
        str(error["loc"][-1]) for error in response.json().get("detail", [])
    }
    assert rejected_fields == {
        "auto_memory_projection",
        "require_memory_projection",
        "projection_focus",
    }
    public = client.get(
        f"/v1/mobile/window-tracker/sessions/{session_id}"
    ).json()
    public_controls = cast(dict[str, Any], public["execution_controls"])
    assert not rejected_fields.intersection(public_controls)


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


def test_tracker_execution_controls_default_to_live_fixed_amount(tmp_path: Path) -> None:
    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path,
        capture_backend=_FakeCaptureBackend([_synthetic_broker_window()]),
        tracking_adapter=_FakeTrackingAdapter("BUY"),
        execution_backend=_FakeExecutionBackend(),
    )
    session = tracker.create_session(session_id="pocket-live")
    controls = session["execution_controls"]

    assert controls["live_execution_enabled"] is True
    assert controls["execution_mode"] == "live"
    assert controls["fixed_amount"] == "preserve"
    assert controls["amount_policy"] == "preserve_visible_broker_amount"
    assert controls["allow_countertrend_scalp"] is False
    assert controls["trade_profile"] == "HIGH_FREQUENCY"
    assert controls["high_frequency_enabled"] is True
    assert controls["swing_fallback_enabled"] is False
    assert int(controls["high_frequency_expiry_seconds"]) == 900
    assert float(session["capture_interval_sec"]) == 30.0
    assert float(controls["min_capture_interval_sec"]) == 0.5
    assert float(controls["max_capture_interval_sec"]) == 30.0
    assert float(controls["cooldown_sec"]) == 900.0

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


def test_tracker_demo_random_trade_clamps_to_fifteen_minute_expiry(tmp_path: Path) -> None:
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
    assert result["broker_surface"]["expiry_lock"]["configured_text"] == "00:15:00"
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


def test_historical_structure_path_uses_body_center_not_wick_spike() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    candles: list[dict[str, Any]] = []
    for index in range(10):
        x_value = 80 + index * 30
        center_y = 420 + index * 12
        wick_top = 4 if index in {0, 9} else center_y - 42
        candles.append(
            {
                "index": index,
                "center_x": x_value,
                "center_y": center_y,
                "bbox": [x_value - 5, wick_top, x_value + 5, center_y + 42],
                "direction": "SELL",
                "price_proxy": 1.0 - index * 0.04,
            }
        )

    history = adapter.build_historical_structure_for_diagnostics(candles, (720, 640))
    points = [
        point
        for segment in history
        for point in cast(Sequence[Sequence[int]], segment.get("line_points", []))
        if len(point) >= 2
    ]

    assert points
    assert min(int(point[1]) for point in points) > 300
    assert all(int(point[1]) != 4 for point in points)


def test_real_tracking_adapter_reuses_cached_locked_shadow_selectors(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    image = _synthetic_chart_surface("buy")
    normalizer_version = getattr(
        window_tracker_module,
        "_FX_MARKET_NORMALIZER_VERSION",
    )
    # This cache contract requires visible selector evidence.  A chart-only
    # surface intentionally produces no identity fingerprint and must not be
    # allowed to make a stale cached pair authoritative.
    _paint_realistic_market_selector(image, "EUR/JPY OTC")
    session_payload: dict[str, Any] = {
        "execution_controls": {"live_execution_enabled": False, "execution_mode": "shadow"},
        "manual_focus_region": {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]},
        "locked_window": {"hwnd": 123, "title": "Pocket Option"},
        "tracking_summary": {
            "detected_timeframe": "M5",
            "timeframe_confidence": 0.93,
            "detected_market": "EUR/JPY OTC",
            "market_confidence": 0.91,
            "market_normalizer_version": normalizer_version,
            "chart_region": {"pixel_bbox": [0, 0, image.width, image.height], "confidence": 0.90},
        },
        "latest_signal": {
            "focus_timeframe": "M5",
            "focus_timeframe_confidence": 0.93,
            "market": "EUR/JPY OTC",
            "market_confidence": 0.91,
            "market_normalizer_version": normalizer_version,
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


def test_real_tracking_adapter_rechecks_zero_confidence_cached_timeframe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    image = _synthetic_chart_surface("buy")
    _paint_realistic_market_selector(image, "EUR/JPY OTC")
    detected_calls = 0

    def confirmed_timeframe(_image: Image.Image) -> dict[str, Any]:
        nonlocal detected_calls
        detected_calls += 1
        return {
            "value": "M5",
            "source": "selector_chip",
            "confidence": 0.91,
            "bbox": [120, 60, 150, 82],
        }

    monkeypatch.setattr(adapter, "_detect_timeframe_selector", confirmed_timeframe)
    result = adapter.study(
        image,
        session_payload={
            "execution_controls": {
                "live_execution_enabled": False,
                "execution_mode": "shadow",
                "min_timeframe_confidence": 0.42,
            },
            "manual_focus_region": {
                "enabled": True,
                "normalized_bbox": [0.0, 0.0, 1.0, 1.0],
            },
            "locked_window": {"hwnd": 123, "title": "Pocket Option"},
            "tracking_summary": {
                "detected_timeframe": "M5",
                "timeframe_confidence": 0.0,
                "detected_market": "EUR/JPY OTC",
                "market_confidence": 0.91,
            },
            "latest_signal": {
                "focus_timeframe": "M5",
                "focus_timeframe_confidence": 0.0,
                "market": "EUR/JPY OTC",
                "market_confidence": 0.91,
            },
        },
    )

    assert detected_calls == 1
    assert result.tracking_summary["detected_timeframe"] == "M5"
    assert abs(float(result.tracking_summary["timeframe_confidence"]) - 0.91) < 1e-9
    assert result.latest_signal["timeframe_identity_confirmed"] is True


def test_real_tracking_adapter_falls_back_when_fast_resize_merges_candles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    image = _synthetic_chart_surface("buy", width=1280, height=720)
    monkeypatch.setenv("PHOENIXGUARD_LIVE_CANDLE_MAX_WIDTH", "480")
    original_extract = cast(
        Callable[[Image.Image], list[dict[str, Any]]],
        getattr(adapter, "_extract_candle_tracks"),
    )

    def extract_with_resized_merge(image_arg: Image.Image) -> list[dict[str, Any]]:
        rows = original_extract(image_arg)
        if image_arg.width < image.width:
            return rows[:5]
        return rows

    monkeypatch.setattr(adapter, "_extract_candle_tracks", extract_with_resized_merge)

    result = adapter.study(
        image,
        session_payload={
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
        },
    )

    extraction = cast(Mapping[str, Any], result.tracking_summary["candle_extraction"])
    assert extraction["mode"] == "full_resolution_fallback"
    assert extraction["resized_track_count"] == 5
    assert int(result.tracking_summary["visible_candle_count"]) >= 8
    assert len(cast(Sequence[Mapping[str, Any]], result.tracking_summary["support_resistance_zones"])) > 0
    assert len(cast(Sequence[Mapping[str, Any]], result.tracking_summary["historical_structure"])) >= 2


def test_full_resolution_fallback_cannot_resurrect_stale_lane_after_resized_right_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    image = _synthetic_chart_surface("buy", width=1280, height=720)
    monkeypatch.setenv("PHOENIXGUARD_LIVE_CANDLE_MAX_WIDTH", "480")

    def extract_resolution_dependent_lane(image_arg: Image.Image) -> list[dict[str, Any]]:
        if image_arg.width < image.width:
            adapter._record_candle_lane_selection_audit(  # noqa: SLF001
                {
                    "schema_version": "phoenixguard.candle_lane_selection_audit.v1",
                    "selected_lane": "ambiguous_fail_closed",
                    "selection_reason": "disjoint_right_lane_track_underflow_fail_closed",
                    "right_candidate_disjoint": True,
                    "right_candidate_ambiguous": True,
                    "right_candidate_track_count": 7,
                }
            )
            return []
        adapter._record_candle_lane_selection_audit(  # noqa: SLF001
            {
                "schema_version": "phoenixguard.candle_lane_selection_audit.v1",
                "selected_lane": "default",
                "selection_reason": "right_candidate_track_underflow",
                "default_track_count": 27,
            }
        )
        return [
            {
                "track_id": index,
                "bbox": [30 + index * 8, 170, 35 + index * 8, 230],
                "center_x": 32.5 + index * 8,
                "center_y": 200.0,
                "direction": "BUY" if index % 2 == 0 else "SELL",
                "color": "green" if index % 2 == 0 else "red",
            }
            for index in range(27)
        ]

    monkeypatch.setattr(adapter, "_extract_candle_tracks", extract_resolution_dependent_lane)
    result = adapter.study(
        image,
        session_payload={
            "manual_focus_region": {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]},
            "locked_window": {"hwnd": 123, "title": "Pocket Option"},
            "tracking_summary": {
                "detected_timeframe": "M5",
                "timeframe_confidence": 0.93,
                "detected_market": "GBP/USD OTC",
                "market_confidence": 0.91,
                "chart_region": {"pixel_bbox": [0, 0, image.width, image.height], "confidence": 0.90},
            },
            "latest_signal": {
                "focus_timeframe": "M5",
                "focus_timeframe_confidence": 0.93,
                "market": "GBP/USD OTC",
                "market_confidence": 0.91,
            },
        },
    )

    extraction = cast(Mapping[str, Any], result.tracking_summary["candle_extraction"])
    assert extraction["mode"] == "fast_resized"
    assert extraction["full_resolution_fallback_count"] >= 8
    assert extraction["full_resolution_fallback_accepted"] is False
    assert extraction["full_resolution_fallback_rejection_reason"] == (
        "full_resolution_lane_did_not_preserve_resized_disjoint_right_evidence"
    )
    assert extraction["final_track_count"] == 0
    assert extraction["causal_lane_selection"]["selected_lane"] == "ambiguous_fail_closed"
    assert extraction["resized_causal_lane_selection"]["selected_lane"] == "ambiguous_fail_closed"
    assert extraction["full_resolution_causal_lane_selection"]["selected_lane"] == "default"
    assert result.tracking_summary["visible_candle_count"] == 0


def test_causal_right_lane_wins_over_longer_historical_lane_and_is_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    scan_bounds: list[tuple[float, float] | None] = []

    def tracks(start_x: int, count: int, spacing: int) -> list[dict[str, Any]]:
        return [
            {
                "track_id": index,
                "bbox": [start_x + index * spacing - 3, 180, start_x + index * spacing + 3, 230],
                "center_x_px": float(start_x + index * spacing),
                "center_y_px": 205.0,
                "direction": "BUY" if index % 2 == 0 else "SELL",
                "color": "green" if index % 2 == 0 else "red",
            }
            for index in range(count)
        ]

    def fake_adaptive_extract(
        _image: NDArray[np.uint8],
        *,
        x_bounds: tuple[float, float] | None = None,
        minimum_track_length: int = 6,
    ) -> list[dict[str, Any]]:
        del minimum_track_length
        scan_bounds.append(x_bounds)
        if x_bounds == (0.40, 0.92):
            return tracks(500, 12, 10)
        return tracks(40, 27, 8)

    monkeypatch.setattr(window_tracker_module, "extract_candle_tracks_adaptive_v3", fake_adaptive_extract)
    image = Image.new("RGB", (960, 508), color=(20, 26, 38))
    rows, metadata = adapter._extract_live_candle_tracks_incremental(  # noqa: SLF001
        image,
        cache_key="pocket-live|M5|GBP/USD OTC|selector_v2_pair_b|960x508",
    )

    lane_audit = cast(Mapping[str, Any], metadata["causal_lane_selection"])
    assert scan_bounds == [None, (0.40, 0.92)]
    assert len(rows) == 12
    assert max(float(row["center_x"]) for row in rows) == 610.0
    assert lane_audit["selected_lane"] == "causal_right"
    assert lane_audit["default_track_count"] == 27
    assert lane_audit["right_candidate_track_count"] == 12
    cached = adapter._live_candle_cache[  # noqa: SLF001
        "pocket-live|M5|GBP/USD OTC|selector_v2_pair_b|960x508"
    ]
    assert max(float(row["center_x"]) for row in cached["tracks"]) == 610.0
    assert cast(Mapping[str, Any], cached["lane_selection"])["selected_lane"] == "causal_right"


def test_disjoint_right_lane_underflow_fails_closed_instead_of_publishing_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()

    def tracks(start_x: int, count: int, spacing: int) -> list[dict[str, Any]]:
        return [
            {
                "track_id": index,
                "bbox": [start_x + index * spacing - 3, 180, start_x + index * spacing + 3, 230],
                "center_x_px": float(start_x + index * spacing),
                "center_y_px": 205.0,
                "direction": "BUY" if index % 2 == 0 else "SELL",
                "color": "green" if index % 2 == 0 else "red",
            }
            for index in range(count)
        ]

    def fake_adaptive_extract(
        _image: NDArray[np.uint8],
        *,
        x_bounds: tuple[float, float] | None = None,
        minimum_track_length: int = 6,
    ) -> list[dict[str, Any]]:
        del minimum_track_length
        if x_bounds == (0.40, 0.92):
            return tracks(500, 7, 15)
        return tracks(40, 27, 8)

    monkeypatch.setattr(window_tracker_module, "extract_candle_tracks_adaptive_v3", fake_adaptive_extract)
    rows, metadata = adapter._extract_live_candle_tracks_incremental(  # noqa: SLF001
        Image.new("RGB", (960, 508), color=(20, 26, 38)),
        cache_key="pocket-live|M5|GBP/USD OTC|selector_v2_pair_b|960x508",
    )

    audit = cast(Mapping[str, Any], metadata["causal_lane_selection"])
    assert rows == []
    assert audit["selected_lane"] == "ambiguous_fail_closed"
    assert audit["right_candidate_disjoint"] is True
    assert audit["right_candidate_ambiguous"] is True
    assert audit["selection_reason"] == "disjoint_right_lane_track_underflow_fail_closed"
    assert adapter._live_candle_cache[  # noqa: SLF001
        "pocket-live|M5|GBP/USD OTC|selector_v2_pair_b|960x508"
    ]["tracks"] == []


def test_pair_a_to_pair_b_full_refresh_keeps_pair_b_on_causal_right_lane(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    phase = {"pair": "A"}

    def tracks(start_x: int, count: int, spacing: int) -> list[dict[str, Any]]:
        return [
            {
                "track_id": index,
                "bbox": [start_x + index * spacing - 3, 180, start_x + index * spacing + 3, 230],
                "center_x_px": float(start_x + index * spacing),
                "center_y_px": 205.0,
                "direction": "BUY" if index % 2 == 0 else "SELL",
                "color": "green" if index % 2 == 0 else "red",
            }
            for index in range(count)
        ]

    def fake_adaptive_extract(
        _image: NDArray[np.uint8],
        *,
        x_bounds: tuple[float, float] | None = None,
        minimum_track_length: int = 6,
    ) -> list[dict[str, Any]]:
        del minimum_track_length
        if phase["pair"] == "A":
            return tracks(520, 12, 8)
        if x_bounds == (0.40, 0.92):
            return tracks(500, 12, 10)
        return tracks(40, 27, 8)

    monkeypatch.setattr(window_tracker_module, "extract_candle_tracks_adaptive_v3", fake_adaptive_extract)
    image = Image.new("RGB", (960, 508), color=(20, 26, 38))
    pair_a_key = "pocket-live|M5|CAD/CHF OTC|selector_v2_pair_a|960x508"
    pair_b_key = "pocket-live|M5|GBP/USD OTC|selector_v2_pair_b|960x508"

    pair_a_rows, _ = adapter._extract_live_candle_tracks_incremental(  # noqa: SLF001
        image,
        cache_key=pair_a_key,
    )
    phase["pair"] = "B"
    pair_b_rows, first_b_metadata = adapter._extract_live_candle_tracks_incremental(  # noqa: SLF001
        image,
        cache_key=pair_b_key,
    )
    refreshed_b_rows, refreshed_b_metadata = adapter._extract_live_candle_tracks_incremental(  # noqa: SLF001
        image,
        cache_key=pair_b_key,
    )

    assert max(float(row["center_x"]) for row in pair_a_rows) > 600.0
    assert max(float(row["center_x"]) for row in pair_b_rows) == 610.0
    assert first_b_metadata["causal_lane_selection"]["selected_lane"] == "causal_right"
    assert refreshed_b_metadata["full_refresh_reason"] == "insufficient_cached_history"
    assert refreshed_b_metadata["causal_lane_selection"]["selected_lane"] == "causal_right"
    assert max(float(row["center_x"]) for row in refreshed_b_rows) == 610.0
    assert max(float(row["center_x"]) for row in adapter._live_candle_cache[pair_b_key]["tracks"]) == 610.0  # noqa: SLF001


def test_compact_live_state_preserves_confirmed_instrument_identity_flags() -> None:
    normalizer_version = getattr(
        window_tracker_module,
        "_FX_MARKET_NORMALIZER_VERSION",
    )
    identity = {
        "detected_market": "GBP/USD OTC",
        "detected_timeframe": "M5",
        "market_identity_confirmed": True,
        "market_normalizer_version": normalizer_version,
        "timeframe_identity_confirmed": True,
        "market_selector_visual_fingerprint": "selector_v2_gbp_usd",
    }
    compact_market = window_tracker_module._compact_live_state_market_payload(identity)  # noqa: SLF001
    compact_signal = window_tracker_module._compact_live_state_latest_signal_payload(  # noqa: SLF001
        {
            "market": "GBP/USD OTC",
            "focus_timeframe": "M5",
            "market_identity_confirmed": True,
            "market_normalizer_version": normalizer_version,
            "timeframe_identity_confirmed": True,
            "market_selector_visual_fingerprint": "selector_v2_gbp_usd",
        }
    )

    assert compact_market["market_identity_confirmed"] is True
    assert (
        compact_market["market_normalizer_version"]
        == normalizer_version
    )
    assert compact_market["timeframe_identity_confirmed"] is True
    assert compact_signal["market_identity_confirmed"] is True
    assert (
        compact_signal["market_normalizer_version"]
        == normalizer_version
    )
    assert compact_signal["timeframe_identity_confirmed"] is True


def test_live_incremental_extraction_reuses_static_history_but_refreshes_latest_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    cache_identity = cast(
        Callable[
            [
                Mapping[str, Any],
                Mapping[str, Any],
                Mapping[str, Any],
                tuple[int, int],
            ],
            str,
        ],
        getattr(adapter, "_live_candle_cache_identity"),
    )
    cache_key = cache_identity(
        _leased_edge_source_payload(),
        {"value": "M5"},
        {
            "value": "EUR/JPY OTC",
            "market_selector_visual_fingerprint": "selector_v3_eur_jpy_otc",
        },
        (960, 508),
    )
    assert cache_key
    generation = {"value": 0}
    extraction_widths: list[int] = []
    absolute_x_values = [120 + index * 20 for index in range(24)]

    def fake_extract(image_arg: Image.Image) -> list[dict[str, Any]]:
        extraction_widths.append(int(image_arg.width))
        x_offset = 960 - int(image_arg.width)
        rows: list[dict[str, Any]] = []
        for index, absolute_x in enumerate(absolute_x_values):
            if absolute_x < x_offset + 6:
                continue
            local_x = absolute_x - x_offset
            center_y = 70.0 if index == len(absolute_x_values) - 1 and generation["value"] else 110.0
            rows.append(
                {
                    "track_id": index,
                    "bbox": [local_x - 3, int(center_y - 12), local_x + 3, int(center_y + 12)],
                    "center_x": float(local_x),
                    "center_x_px": float(local_x),
                    "center_y": center_y,
                    "center_y_px": center_y,
                    "direction": "BUY",
                    "color": "green",
                    "price_proxy": 1.0 - center_y / 508.0,
                }
            )
        return rows

    monkeypatch.setattr(adapter, "_extract_candle_tracks", fake_extract)
    first_image = Image.new("RGB", (960, 508), color=(20, 26, 38))
    incremental_extract = cast(
        Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]],
        getattr(adapter, "_extract_live_candle_tracks_incremental"),
    )
    first_rows, first_meta = incremental_extract(
        first_image,
        cache_key=cache_key,
    )

    generation["value"] = 1
    second_image = first_image.copy()
    ImageDraw.Draw(second_image).rectangle((880, 180, 940, 260), fill=(28, 38, 54))
    second_rows, second_meta = incremental_extract(
        second_image,
        cache_key=cache_key,
    )

    assert len(first_rows) == len(second_rows) == 24
    assert first_meta["enabled"] is True
    assert first_meta["history_reused"] is False
    assert first_meta["full_refresh_reason"] == "cold_cache"
    assert second_meta["history_reused"] is True
    assert second_meta["edge_recomputed"] is True
    assert extraction_widths[0] == 960
    assert extraction_widths[1] < 960
    assert float(first_rows[-1]["center_y"]) == 110.0
    assert float(second_rows[-1]["center_y"]) == 70.0


def test_live_incremental_extraction_full_refreshes_when_history_geometry_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    cache_key = "pocket-live|M5|EUR/JPY OTC|selector_v2_pair_a|960x508"
    extraction_widths: list[int] = []

    def fake_extract(image_arg: Image.Image) -> list[dict[str, Any]]:
        extraction_widths.append(int(image_arg.width))
        return [
            {
                "track_id": index,
                "bbox": [117 + index * 20, 98, 123 + index * 20, 122],
                "center_x": float(120 + index * 20),
                "center_x_px": float(120 + index * 20),
                "center_y": 110.0,
                "center_y_px": 110.0,
                "direction": "BUY",
                "color": "green",
            }
            for index in range(24)
        ]

    monkeypatch.setattr(adapter, "_extract_candle_tracks", fake_extract)
    incremental_extract = cast(
        Callable[..., tuple[list[dict[str, Any]], dict[str, Any]]],
        getattr(adapter, "_extract_live_candle_tracks_incremental"),
    )
    incremental_extract(
        Image.new("RGB", (960, 508), color=(20, 26, 38)),
        cache_key=cache_key,
    )
    _rows, moved_meta = incremental_extract(
        Image.new("RGB", (960, 508), color=(55, 61, 73)),
        cache_key=cache_key,
    )

    assert moved_meta["history_reused"] is False
    assert moved_meta["full_refresh_reason"] == "historical_geometry_changed"
    assert extraction_widths == [960, 960]


def test_live_candle_cache_identity_isolated_by_pair_and_timeframe() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    session = {"session_id": "pocket-live"}
    pair_a = {
        "value": "EUR/JPY OTC",
        "market_selector_visual_fingerprint": "selector_v2_pair_a",
    }
    pair_b = {
        "value": "CAD/JPY OTC",
        "market_selector_visual_fingerprint": "selector_v3_pair_b",
    }

    cache_identity = cast(
        Callable[[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], tuple[int, int]], str],
        getattr(adapter, "_live_candle_cache_identity"),
    )
    key_a_m5 = cache_identity(session, {"value": "M5"}, pair_a, (960, 508))
    key_b_m5 = cache_identity(session, {"value": "M5"}, pair_b, (960, 508))
    key_a_m1 = cache_identity(session, {"value": "M1"}, pair_a, (960, 508))
    legacy_key = cache_identity(
        session,
        {"value": "M5"},
        {"value": "EUR/JPY OTC", "market_selector_visual_fingerprint": "volatile-frame-hash"},
        (960, 508),
    )

    assert key_a_m5
    assert key_b_m5
    assert "selector_v3_pair_b" in key_b_m5
    assert len({key_a_m5, key_b_m5, key_a_m1}) == 3
    assert legacy_key == ""


def _leased_edge_source_payload(
    *,
    generation: int = 4,
    selection_id: str = "selection-17",
    sequence_id: str = "sequence-17",
) -> dict[str, Any]:
    return {
        "session_id": "pocket-live",
        "capture_source_v3": {
            "state": "LIVE",
            "source_id": "edge-chart-region-v3",
            "source_type": "browser_tab_roi_capture",
            "coordinate_space": "edge_tab_roi_v1",
            "source_generation": generation,
            "source_lease_id": f"lease-{generation}-{selection_id}",
            "selection_id": selection_id,
            "sequence_id": sequence_id,
        },
    }


def test_live_study_budget_rejects_starved_adapter_entry() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    call_started = time.monotonic() - 2.0

    with pytest.raises(
        window_tracker_module.LiveStudyLatencyBudgetExceeded
    ) as raised:
        adapter.study(
            Image.new("RGB", (640, 360), color=(20, 26, 38)),
            session_payload={
                "_study_call_started_monotonic_v3": call_started,
                "_study_deadline_monotonic_v3": call_started + 1.0,
                "_study_live_latency_budget_sec_v3": 1.0,
                "_study_latency_budget_enforced_v3": True,
            },
        )

    assert raised.value.stage == "adapter_entry"
    assert raised.value.elapsed_sec >= 2.0
    assert raised.value.budget_sec == 1.0


def test_external_live_study_budget_failure_is_published_as_discarded(
    tmp_path: Path,
) -> None:
    class _ExpiredStudyAdapter(_FakeTrackingAdapter):
        def study(
            self,
            image: Image.Image,
            *,
            session_payload: Mapping[str, Any] | None = None,
        ) -> TrackingStudy:
            del image, session_payload
            raise window_tracker_module.LiveStudyLatencyBudgetExceeded(
                stage="adapter_entry",
                elapsed_sec=46.0,
                budget_sec=45.0,
                adapter_entry_delay_sec=46.0,
            )

    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path / "expired-live-study",
        tracking_adapter=_ExpiredStudyAdapter("BUY"),
    )
    session_id = "expired-live-study"
    tracker.create_session(session_id=session_id, auto_start=False)
    claim = tracker.claim_external_source(
        session_id,
        source_id="edge-chart-region-v3",
        sequence_id="edge-expired-study-sequence",
        source_type="browser_tab_roi_capture",
        selection_id="edge-expired-study-selection",
        display_name="Pocket Option chart",
        coordinate_space="edge_tab_roi_v1",
    )

    result = tracker.ingest_external_frame(
        session_id,
        _surface(width=1280, height=720),
        source_id="edge-chart-region-v3",
        source_url="https://pocketoption.com/en/cabinet/demo-quick-high-low/",
        sequence_id="edge-expired-study-sequence",
        capture_epoch_ms=int(time.time() * 1000),
        frame_id=1,
        metadata={
            "source_type": "browser_tab_roi_capture",
            "coordinate_space": "edge_tab_roi_v1",
            "source_generation": int(claim["source_generation"]),
            "source_lease_id": str(claim["source_lease_id"]),
            "source_render_fresh": True,
            "extension_id": "edge-extension-test",
            "locked_tab_id": "17",
            "locked_tab_title": "The Most Innovative Trading Platform",
            "locked_origin": "https://pocketoption.com",
        },
    )

    assert result["frame_ingest"]["accepted"] is False
    assert (
        result["frame_ingest"]["failure_reason_code"]
        == "LIVE_STUDY_LATENCY_BUDGET_EXCEEDED"
    )
    assert (
        result["frame_ingest"]["failure_error_type"]
        == "LiveStudyLatencyBudgetExceeded"
    )
    persisted = tracker.load_session_payload(session_id)
    assert persisted["status"] == "external_source_error"
    assert persisted["capture_source_v3"]["state"] == "ERROR"
    assert persisted["capture_source_v3"]["decision_usable"] is False
    assert (
        persisted["capture_source_v3"]["reason_code"]
        == "LIVE_STUDY_LATENCY_BUDGET_EXCEEDED"
    )
    assert persisted["capture_source_v3"]["stream"]["processing"] is False
    assert persisted["capture_source_v3"]["stream"]["processing_frame_id"] == 0
    assert (
        persisted["capture_source_v3"]["stream"]["last_failure_error_type"]
        == "LiveStudyLatencyBudgetExceeded"
    )


def test_external_adapter_failure_is_bounded_and_never_publishes_secret_text(
    tmp_path: Path,
) -> None:
    class _BrokenStudyAdapter(_FakeTrackingAdapter):
        def study(
            self,
            image: Image.Image,
            *,
            session_payload: Mapping[str, Any] | None = None,
        ) -> TrackingStudy:
            del image, session_payload
            raise RuntimeError("api_key=super-secret-adapter-value")

    tracker = ContinuousWindowTrackerService(
        root_dir=tmp_path / "broken-live-study",
        tracking_adapter=_BrokenStudyAdapter("BUY"),
    )
    session_id = "broken-live-study"
    tracker.create_session(session_id=session_id, auto_start=False)
    claim = tracker.claim_external_source(
        session_id,
        source_id="edge-chart-region-v3",
        sequence_id="edge-broken-study-sequence",
        source_type="browser_tab_roi_capture",
        selection_id="edge-broken-study-selection",
        display_name="Pocket Option chart",
        coordinate_space="edge_tab_roi_v1",
    )

    result = tracker.ingest_external_frame(
        session_id,
        _surface(width=1280, height=720),
        source_id="edge-chart-region-v3",
        source_url="https://pocketoption.com/en/cabinet/demo-quick-high-low/",
        sequence_id="edge-broken-study-sequence",
        capture_epoch_ms=int(time.time() * 1000),
        frame_id=1,
        metadata={
            "source_type": "browser_tab_roi_capture",
            "coordinate_space": "edge_tab_roi_v1",
            "source_generation": int(claim["source_generation"]),
            "source_lease_id": str(claim["source_lease_id"]),
            "source_render_fresh": True,
            "extension_id": "edge-extension-test",
            "locked_tab_id": "17",
            "locked_tab_title": "The Most Innovative Trading Platform",
            "locked_origin": "https://pocketoption.com",
        },
    )

    assert result["frame_ingest"]["accepted"] is False
    assert result["frame_ingest"]["failure_reason_code"] == "TRACKER_STUDY_FAILED"
    assert result["frame_ingest"]["failure_error_type"] == "TrackingAdapterError"
    persisted = tracker.load_session_payload(session_id)
    assert persisted["status"] == "external_source_error"
    assert persisted["capture_source_v3"]["state"] == "ERROR"
    assert persisted["capture_source_v3"]["decision_usable"] is False
    assert persisted["capture_source_v3"]["stream"]["processing"] is False
    assert persisted["capture_source_v3"]["stream"]["processing_frame_id"] == 0
    combined_public_state = json.dumps(
        {"result": result, "persisted": persisted},
        sort_keys=True,
    )
    assert "super-secret-adapter-value" not in combined_public_state
    assert "api_key" not in combined_public_state


def test_live_chart_resolution_path_uses_incremental_candle_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    image = _synthetic_chart_surface("buy", width=640, height=360)
    source_payload = _leased_edge_source_payload()
    source_token = str(
        getattr(window_tracker_module, "_locked_stream_source_token_v3")(
            source_payload
        )
    )
    selector_fingerprint = "selector_v3_cad_jpy_otc"
    incremental_calls: list[tuple[int, int]] = []

    monkeypatch.setenv("PHOENIXGUARD_LIVE_CANDLE_MAX_WIDTH", "4096")
    monkeypatch.setattr(
        window_tracker_module,
        "_market_selector_visual_fingerprint",
        lambda _image: selector_fingerprint,
    )

    def incremental_extract(
        candle_image: Image.Image,
        *,
        cache_key: str,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        assert cache_key
        incremental_calls.append(candle_image.size)
        return (
            [],
            {
                "enabled": True,
                "history_reused": False,
                "edge_recomputed": True,
                "full_refresh_reason": "cold_cache",
                "reuse_count": 0,
                "causal_lane_selection": {},
            },
        )

    monkeypatch.setattr(
        adapter,
        "_extract_live_candle_tracks_incremental",
        incremental_extract,
    )
    monkeypatch.setattr(
        adapter,
        "_extract_candle_tracks",
        lambda _image: pytest.fail(
            "locked chart-resolution frames must use the incremental path"
        ),
    )
    monkeypatch.setattr(
        adapter,
        "_detect_chart_bbox",
        lambda _image: pytest.fail(
            "an authoritative Edge chart ROI must not be detected or cropped again"
        ),
    )
    result = adapter.study(
        image,
        session_payload={
            **source_payload,
            "_study_focus_region": {
                "pixel_bbox": [0, 0, image.width, image.height],
                "study_surface_contract": "authoritative_edge_tab_roi_v1",
                "geometry_authority": "external_source_lease",
            },
            "manual_focus_region": {
                "enabled": True,
                "normalized_bbox": [0.0, 0.0, 1.0, 1.0],
            },
            "tracking_summary": {
                "chart_valid": True,
                "chart_region": {
                    "pixel_bbox": [0, 0, image.width, image.height],
                    "confidence": 0.90,
                },
                "detected_market": "CAD/JPY OTC",
                "market_confidence": 0.93,
                "market_identity_confirmed": True,
                "market_normalizer_version": getattr(
                    window_tracker_module,
                    "_FX_MARKET_NORMALIZER_VERSION",
                ),
                "detected_timeframe": "M5",
                "timeframe_confidence": 0.94,
                "timeframe_identity_confirmed": True,
                "market_selector_visual_fingerprint": selector_fingerprint,
                "source_binding_token_v3": source_token,
            },
            "latest_signal": {
                "market": "CAD/JPY OTC",
                "market_confidence": 0.93,
                "market_identity_confirmed": True,
                "market_normalizer_version": getattr(
                    window_tracker_module,
                    "_FX_MARKET_NORMALIZER_VERSION",
                ),
                "focus_timeframe": "M5",
                "focus_timeframe_confidence": 0.94,
                "timeframe_identity_confirmed": True,
                "market_selector_visual_fingerprint": selector_fingerprint,
                "source_binding_token_v3": source_token,
            },
        },
    )

    extraction = cast(
        Mapping[str, Any],
        result.tracking_summary["candle_extraction"],
    )
    assert incremental_calls == [(640, 360)]
    assert extraction["mode"] == "chart_resolution"
    assert extraction["incremental_history"]["enabled"] is True
    assert result.chart_image.size == image.size
    assert result.chart_region["pixel_bbox"] == [0, 0, 640, 360]
    assert result.latest_signal["market"] == "CAD/JPY OTC"
    assert result.latest_signal["focus_timeframe"] == "M5"
    stages = [
        str(row.get("stage", ""))
        for row in result.tracking_summary["study_stage_timings"]
    ]
    assert "authoritative_edge_tab_roi" in stages
    assert "detect_chart_bbox" not in stages


def test_local_locked_focus_still_uses_chart_bbox_detector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    image = _synthetic_chart_surface("buy", width=640, height=360)
    detector_calls: list[tuple[int, int]] = []
    expected_bbox = [18, 16, 622, 352]

    monkeypatch.setattr(
        window_tracker_module,
        "_market_selector_visual_fingerprint",
        lambda _image: "selector_v3_local_cad_jpy",
    )
    monkeypatch.setattr(
        adapter,
        "_detect_timeframe_selector",
        lambda _image: {"value": "M5", "confidence": 0.94},
    )
    monkeypatch.setattr(
        adapter,
        "_detect_market_selector",
        lambda _image, **_kwargs: {
            "value": "CAD/JPY OTC",
            "confidence": 0.93,
        },
    )

    def detect_chart_bbox(chart_surface: Image.Image) -> tuple[list[int], float]:
        detector_calls.append(chart_surface.size)
        return list(expected_bbox), 0.91

    monkeypatch.setattr(adapter, "_detect_chart_bbox", detect_chart_bbox)
    monkeypatch.setattr(
        adapter,
        "_extract_live_candle_tracks_incremental",
        lambda _image, *, cache_key: (
            [],
            {
                "enabled": True,
                "history_reused": False,
                "edge_recomputed": True,
                "full_refresh_reason": "cold_cache",
                "reuse_count": 0,
                "causal_lane_selection": {},
            },
        ),
    )

    result = adapter.study(
        image,
        session_payload={
            "manual_focus_region": {
                "enabled": True,
                "normalized_bbox": [0.0, 0.0, 1.0, 1.0],
            }
        },
    )

    assert detector_calls == [image.size]
    assert result.chart_region["pixel_bbox"] == expected_bbox
    stages = [
        str(row.get("stage", ""))
        for row in result.tracking_summary["study_stage_timings"]
    ]
    assert "detect_chart_bbox" in stages
    assert "authoritative_edge_tab_roi" not in stages


def test_edge_stream_identity_changes_for_generation_selection_and_sequence() -> None:
    source_token = cast(
        Callable[[Mapping[str, Any]], str],
        getattr(window_tracker_module, "_locked_stream_source_token_v3"),
    )
    baseline = source_token(_leased_edge_source_payload())
    changed_generation = source_token(_leased_edge_source_payload(generation=5))
    changed_selection = source_token(
        _leased_edge_source_payload(selection_id="selection-18")
    )
    changed_sequence = source_token(
        _leased_edge_source_payload(sequence_id="sequence-18")
    )
    missing_lease = _leased_edge_source_payload()
    cast(dict[str, Any], missing_lease["capture_source_v3"])[
        "source_lease_id"
    ] = ""

    assert baseline
    assert len(
        {baseline, changed_generation, changed_selection, changed_sequence}
    ) == 4
    assert source_token(missing_lease) == ""


def test_live_candle_cache_identity_isolated_by_external_source_binding() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    selector = {
        "value": "CAD/JPY OTC",
        "market_selector_visual_fingerprint": "selector_v3_pair_cad_jpy",
    }
    timeframe = {"value": "M5"}
    cache_identity = cast(
        Callable[
            [
                Mapping[str, Any],
                Mapping[str, Any],
                Mapping[str, Any],
                tuple[int, int],
            ],
            str,
        ],
        getattr(adapter, "_live_candle_cache_identity"),
    )

    first = cache_identity(
        _leased_edge_source_payload(), timeframe, selector, (960, 508)
    )
    second = cache_identity(
        _leased_edge_source_payload(generation=5),
        timeframe,
        selector,
        (960, 508),
    )

    assert first
    assert second
    assert first != second


def test_external_source_binding_change_forces_identity_and_geometry_reproof(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    surface = _synthetic_chart_surface("buy")
    _paint_realistic_market_selector(surface, "CAD/JPY OTC")
    previous_source = _leased_edge_source_payload(generation=4)
    current_source = _leased_edge_source_payload(generation=5)
    source_token = cast(
        Callable[[Mapping[str, Any]], str],
        getattr(window_tracker_module, "_locked_stream_source_token_v3"),
    )
    previous_token = source_token(previous_source)
    timeframe_reads = 0
    market_reads = 0

    def read_timeframe(_image: Image.Image) -> dict[str, Any]:
        nonlocal timeframe_reads
        timeframe_reads += 1
        return {
            "value": "M5",
            "source": "selector_chip",
            "confidence": 0.94,
            "bbox": [256, 193, 281, 216],
        }

    def read_market(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal market_reads
        market_reads += 1
        return {
            "value": "CAD/JPY OTC",
            "source": "header_text",
            "confidence": 0.92,
            "bbox": [90, 86, 269, 164],
        }

    monkeypatch.setattr(adapter, "_detect_timeframe_selector", read_timeframe)
    monkeypatch.setattr(adapter, "_detect_market_selector", read_market)
    result = adapter.study(
        surface,
        session_payload={
            **current_source,
            "manual_focus_region": {
                "enabled": True,
                "normalized_bbox": [0.0, 0.0, 1.0, 1.0],
            },
            "tracking_summary": {
                "chart_valid": True,
                "detected_market": "CAD/JPY OTC",
                "market_confidence": 0.92,
                "detected_timeframe": "M5",
                "timeframe_confidence": 0.94,
                "market_identity_confirmed": True,
                "timeframe_identity_confirmed": True,
                "source_binding_token_v3": previous_token,
                "chart_region": {
                    "pixel_bbox": [0, 0, surface.width, surface.height],
                    "confidence": 0.90,
                },
            },
            "latest_signal": {
                "market": "CAD/JPY OTC",
                "market_confidence": 0.92,
                "focus_timeframe": "M5",
                "focus_timeframe_confidence": 0.94,
                "source_binding_token_v3": previous_token,
            },
        },
    )

    stages = [
        str(row.get("stage", ""))
        for row in result.tracking_summary["study_stage_timings"]
    ]
    assert timeframe_reads == 1
    assert market_reads == 1
    assert "detect_chart_bbox" in stages
    assert "cached_chart_bbox" not in stages
    assert result.latest_signal["source_binding_changed_v3"] is True
    assert result.latest_signal["source_binding_token_v3"] != previous_token
    assert result.latest_signal["market"] == "CAD/JPY OTC"
    assert result.latest_signal["focus_timeframe"] == "M5"


def test_real_tracking_adapter_visual_delta_requires_confirmed_pair_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    image = _synthetic_chart_surface("buy")
    _paint_realistic_market_selector(image, "EUR/JPY OTC")
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

    detector_calls = 0

    def confirm_same_market(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        nonlocal detector_calls
        detector_calls += 1
        return {
            "value": "EUR/JPY OTC",
            "source": "header_text",
            "confidence": 0.93,
        }

    monkeypatch.setattr(adapter, "_detect_market_selector", confirm_same_market)

    result = adapter.study(image, session_payload=session_payload)

    stages = [str(row.get("stage", "")) for row in result.tracking_summary["study_stage_timings"]]
    assert "cached_chart_bbox" in stages
    assert detector_calls == 1
    assert result.tracking_summary["market_selector_visual_changed"] is True
    assert result.tracking_summary["market_selector_rebind_required"] is False
    assert result.tracking_summary["market_selector_studying_new_pair"] is False
    assert result.latest_signal["market"] == "EUR/JPY OTC"


def test_real_tracking_adapter_unknown_market_fast_locked_context_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    image = _synthetic_chart_surface("buy")
    _paint_realistic_market_selector(image, "EUR/JPY OTC")
    selector_fingerprint = str(
        getattr(window_tracker_module, "_market_selector_visual_fingerprint")(image)
    )
    normalizer_version = getattr(
        window_tracker_module,
        "_FX_MARKET_NORMALIZER_VERSION",
    )
    session_payload: dict[str, Any] = {
        "execution_controls": {"live_execution_enabled": False, "execution_mode": "shadow"},
        "manual_focus_region": {"enabled": True, "normalized_bbox": [0.0, 0.0, 1.0, 1.0]},
        "locked_window": {"hwnd": 123, "title": "Pocket Option"},
        "tracking_summary": {
            "detected_timeframe": "M5",
            "timeframe_confidence": 0.93,
            "detected_market": "",
            "market_normalizer_version": normalizer_version,
            "market_selector_visual_fingerprint": selector_fingerprint,
            "chart_region": {"pixel_bbox": [0, 0, image.width, image.height], "confidence": 0.90},
        },
        "latest_signal": {
            "focus_timeframe": "M5",
            "focus_timeframe_confidence": 0.93,
            "market": "",
            "market_normalizer_version": normalizer_version,
            "market_selector_visual_fingerprint": selector_fingerprint,
        },
    }

    def fail_market_detector(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("unknown locked market must not block on slow market OCR")

    monkeypatch.setattr(adapter, "_detect_market_selector", fail_market_detector)

    result = adapter.study(image, session_payload=session_payload)

    stages = [str(row.get("stage", "")) for row in result.tracking_summary["study_stage_timings"]]
    assert "cached_chart_bbox" in stages
    assert result.tracking_summary["market_source"] == "selector_identity_rebind_pending"
    assert result.tracking_summary["market_selector_rebind_required"] is True
    assert result.tracking_summary["market_selector_studying_new_pair"] is True
    assert result.latest_signal["market"] == ""
    scene = cast(Mapping[str, Any], result.latest_signal["scene_forecast_contribution"])
    assert scene["provider_status"] == "MARKET_IDENTITY_PENDING"
    assert scene["line_points"] == []


def test_real_tracking_adapter_excludes_broker_order_panel_from_chart_bbox() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    image = _synthetic_full_pocket_option_gui()

    result = adapter.study(image)

    chart_bbox = cast(Sequence[Any], result.tracking_summary["chart_region"]["pixel_bbox"])
    assert int(chart_bbox[2]) < int(image.width * 0.82)
    assert result.tracking_summary["chart_valid"] is True
    assert int(result.tracking_summary["visible_candle_count"]) >= 8


def test_window_tracker_feeds_full_suite_to_closed_candle_scene_forecaster(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}
    captured_calls: list[dict[str, Any]] = []

    def capture_scene_context(**kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        captured_calls.append(dict(kwargs))
        anchor = [0.50, 0.50]
        line = [anchor, *[[0.50 + index * 0.02, 0.50 - index * 0.004] for index in range(1, 13)]]
        candles = [
            {
                "step": index,
                "label": f"E{index}",
                "x_norm": line[index][0],
                "open_y_norm": line[index - 1][1],
                "high_y_norm": min(line[index - 1][1], line[index][1]) - 0.002,
                "low_y_norm": max(line[index - 1][1], line[index][1]) + 0.002,
                "close_y_norm": line[index][1],
                "movement_side": "BUY",
                "body_bias": "BUY",
            }
            for index in range(1, 13)
        ]
        return {
            "path_side": "BUY",
            "side": "BUY",
            "probability_calibrated": False,
            "raw_side_probabilities": {"BUY": 0.58, "HOLD": 0.24, "SELL": 0.18},
            "line_points": line,
            "forecast_candles": candles,
            "forecast_scenarios": [
                {
                    "role": role,
                    "side": "BUY",
                    "probability": probability,
                    "selected": role == "base",
                    "line_points": line,
                    "forecast_candles": candles,
                }
                for role, probability in (("base", 0.58), ("bull", 0.24), ("bear", 0.18))
            ],
            "model_version": "TEST_SCENE_FORECASTER",
        }

    monkeypatch.setattr(
        window_tracker_module,
        "build_scene_forecast_contribution_v3",
        capture_scene_context,
    )
    adapter = PhoenixGuardWindowTrackingAdapter()
    build = cast(
        Callable[..., dict[str, Any]],
        getattr(adapter, "_build_scene_forecast_contribution"),
    )
    chart_image = _synthetic_chart_surface("buy")
    candles = [
        {
            "track_id": index,
            "direction": "BUY",
            "center_x": 40.0 + index * 12.0,
            "open_y_px": 130.0 - index * 2.0,
            "close_y_px": 128.0 - index * 2.0,
            "wick_top_px": 126.0 - index * 2.0,
            "wick_bottom_px": 132.0 - index * 2.0,
            "price_proxy": 0.4 + index * 0.01,
            "is_closed": index < 5,
        }
        for index in range(6)
    ]

    first = build(
        candles=candles,
        chart_image=chart_image,
        timeframe="M5",
        market="NZDUSD_OTC",
        frame_id=42,
        projection={"direction": "BUY"},
        candle_statistics={"buy_ratio": 0.8},
        behavior_payload={"current_state": "CONTINUATION"},
        decision_kernel={"dominant_side": "BUY"},
        smart_money_context={"dominant_side": "BUY"},
        support_resistance_context={"dominant_side": "BUY"},
        support_resistance_zones=[],
        trend_slopes={"global": 0.2},
        trend_directions={"global": "BUY"},
    )

    # A detector dropout/reclassification cannot advance the candle event
    # while the same right-edge forming candle is still present.
    candles[-2].update(
        {
            "direction": "SELL",
            "center_x": float(candles[-2]["center_x"]) - 5.9,
            "open_y_px": float(candles[-2]["open_y_px"]) + 40.0,
            "close_y_px": float(candles[-2]["close_y_px"]) - 20.0,
            "wick_bottom_px": float(candles[-2]["wick_bottom_px"]) + 30.0,
        }
    )
    candles[-1]["close_y_px"] = float(candles[-1]["close_y_px"]) + 12.0
    replay = build(
        candles=candles,
        chart_image=chart_image,
        timeframe="M5",
        market="NZDUSD_OTC",
        frame_id=43,
        projection={"direction": "BUY"},
        candle_statistics={"buy_ratio": 0.8},
        behavior_payload={"current_state": "CONTINUATION"},
        decision_kernel={"dominant_side": "BUY"},
        smart_money_context={"dominant_side": "BUY"},
        support_resistance_context={"dominant_side": "BUY"},
        support_resistance_zones=[],
        trend_slopes={"global": 0.2},
        trend_directions={"global": "BUY"},
    )

    assert captured["image_size"] == chart_image.size
    assert captured["timeframe"] == "M5"
    assert captured["pair"] == "NZDUSD_OTC"
    assert captured["projection"] == {"direction": "BUY"}
    assert captured["decision_kernel"] == {"dominant_side": "BUY"}
    assert captured["smart_money_context"] == {"dominant_side": "BUY"}
    assert captured["allow_foundation_model"] is False
    assert captured["event_key_override"] == first["closed_candle_key"]
    assert len(captured_calls) == 1
    assert replay["closed_candle_key"] == first["closed_candle_key"]
    assert replay["closed_candle_sequence"] == first["closed_candle_sequence"]
    assert replay["closed_candle_transition_observed"] is False
    assert replay["closed_candle_transition_reason"] == "FORMING_CANDLE_STILL_ACTIVE"
    assert replay["frame_reused_without_reforecast"] is True
    assert replay["frame_id"] == 43
    assert replay["display_frame_id"] == 43
    assert replay["forecast_computed_frame_id"] == 42
    assert replay["source_forecast_frame_id"] == 42
    assert replay["geometry_frame_match_verified"] is True
    assert replay["geometry_projected_frame_id"] == 43
    assert replay["geometry_reprojected_from_cache"] is True
    expected_anchor = [
        float(candles[-2]["center_x"]) / chart_image.width,
        float(candles[-2]["close_y_px"]) / chart_image.height,
    ]
    assert replay["line_points"][0] == expected_anchor
    assert replay["forecast_anchor"]["x_norm"] == expected_anchor[0]
    assert replay["forecast_anchor"]["y_norm"] == expected_anchor[1]
    assert replay["forecast_anchor"]["verified_latest_close"] is True
    assert len(replay["line_points"]) == 13
    assert len(replay["forecast_candles"]) == 12
    assert len(replay["forecast_scenarios"]) == 3
    assert all(
        scenario["line_points"][0] == expected_anchor
        for scenario in replay["forecast_scenarios"]
    )
    assert sum(
        bool(scenario.get("selected", False))
        for scenario in replay["forecast_scenarios"]
    ) == 1
    assert len({round(point[1], 12) for point in replay["line_points"][-8:]}) > 4
    assert len(replay["forecast_path"]) == 12
    assert replay["geometry_projection_provenance"]["verified"] is True
    assert replay["geometry_projection_provenance"]["projected_frame_id"] == 43


def test_scene_forecast_replaces_same_event_cache_after_detector_coverage_rebase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_calls: list[dict[str, Any]] = []

    def coverage_candles(count: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        close_y = 310.0
        for index in range(count):
            direction = "BUY" if index % 3 != 1 else "SELL"
            open_y = close_y
            close_y += -2.5 if direction == "BUY" else 1.8
            center_x = 20.0 + index * 8.0
            rows.append(
                {
                    "track_id": index,
                    "direction": direction,
                    "center_x": center_x,
                    "open_y_px": open_y,
                    "close_y_px": close_y,
                    "wick_top_px": min(open_y, close_y) - 1.5,
                    "wick_bottom_px": max(open_y, close_y) + 1.5,
                    "is_closed": index < count - 1,
                }
            )
        return rows

    def capture_scene_context(**kwargs: Any) -> dict[str, Any]:
        captured_calls.append(dict(kwargs))
        rows = cast(Sequence[Mapping[str, Any]], kwargs["candles"])
        image_width, image_height = cast(tuple[int, int], kwargs["image_size"])
        latest_closed = next(
            row for row in reversed(rows) if bool(row.get("is_closed", False))
        )
        anchor_x = float(latest_closed["center_x"]) / image_width
        anchor_y = float(latest_closed["close_y_px"]) / image_height
        anchor = [anchor_x, anchor_y]
        line = [
            anchor,
            *[
                [anchor_x + index * 0.02, anchor_y - index * 0.003]
                for index in range(1, 13)
            ],
        ]
        candles = [
            {
                "step": index,
                "label": f"E{index}",
                "x_norm": line[index][0],
                "open_y_norm": line[index - 1][1],
                "high_y_norm": min(line[index - 1][1], line[index][1]) - 0.001,
                "low_y_norm": max(line[index - 1][1], line[index][1]) + 0.001,
                "close_y_norm": line[index][1],
                "movement_side": "BUY",
                "body_bias": "BUY",
            }
            for index in range(1, 13)
        ]
        scenarios = [
            {
                "role": role,
                "side": "BUY",
                "probability": probability,
                "selected": role == "base",
                "line_points": line,
                "forecast_candles": candles,
            }
            for role, probability in (("base", 0.58), ("bull", 0.24), ("bear", 0.18))
        ]
        return {
            "path_side": "BUY",
            "side": "BUY",
            "probability_calibrated": False,
            "raw_side_probabilities": {"BUY": 0.58, "HOLD": 0.24, "SELL": 0.18},
            "forecast_anchor": {"x_norm": anchor_x, "y_norm": anchor_y},
            "line_points": line,
            "forecast_candles": candles,
            "forecast_scenarios": scenarios,
            "model_version": "TEST_SCENE_FORECASTER",
        }

    monkeypatch.setattr(
        window_tracker_module,
        "build_scene_forecast_contribution_v3",
        capture_scene_context,
    )
    adapter = PhoenixGuardWindowTrackingAdapter()
    build = cast(
        Callable[..., dict[str, Any]],
        getattr(adapter, "_build_scene_forecast_contribution"),
    )
    chart_image = _synthetic_chart_surface("buy")

    def run(rows: Sequence[Mapping[str, Any]], frame_id: int) -> dict[str, Any]:
        return build(
            candles=rows,
            chart_image=chart_image,
            timeframe="M5",
            market="CHFJPY_OTC",
            frame_id=frame_id,
            projection={"direction": "BUY"},
            candle_statistics={"sample_size": len(rows)},
            behavior_payload={"current_state": "CONTINUATION"},
            decision_kernel={"dominant_side": "BUY"},
            smart_money_context={"dominant_side": "BUY"},
            support_resistance_context={"dominant_side": "BUY"},
            support_resistance_zones=[],
            trend_slopes={"global": 0.2},
            trend_directions={"global": "BUY"},
        )

    initial = run(coverage_candles(39), 101)
    repaired = run(coverage_candles(64), 102)
    replay = run(coverage_candles(64), 103)
    degraded = run(coverage_candles(39), 104)
    restored = run(coverage_candles(64), 105)

    assert len(captured_calls) == 2
    assert len(cast(Sequence[Any], captured_calls[0]["candles"])) == 39
    assert len(cast(Sequence[Any], captured_calls[1]["candles"])) == 64
    assert repaired["closed_candle_key"] == initial["closed_candle_key"]
    assert repaired["closed_candle_sequence"] == initial["closed_candle_sequence"]
    assert repaired["closed_candle_transition_observed"] is False
    assert repaired["closed_candle_transition_reason"] == "DETECTOR_COVERAGE_REBASE"
    assert repaired["same_event_cache_rebuild_required"] is False
    assert repaired["detector_coverage_rebase_applied"] is True
    assert repaired["cache_replaced_for_detector_coverage_rebase"] is True
    assert repaired["cache_hit"] is False
    assert repaired["frame_reused_without_reforecast"] is False
    assert repaired["forecast_anchor"]["x_norm"] > initial["forecast_anchor"]["x_norm"]
    assert repaired["line_points"][0] == [
        repaired["forecast_anchor"]["x_norm"],
        repaired["forecast_anchor"]["y_norm"],
    ]
    assert replay["cache_hit"] is True
    assert replay["frame_reused_without_reforecast"] is True
    assert replay["same_event_cache_rebuild_required"] is False
    assert replay["cache_replaced_for_detector_coverage_rebase"] is True
    assert replay["detector_coverage_rebase_applied"] is True
    assert replay["frame_id"] == 103
    assert replay["display_frame_id"] == 103
    assert replay["forecast_computed_frame_id"] == 102
    assert replay["source_forecast_frame_id"] == 102
    assert replay["forecast_anchor"]["x_norm"] == repaired["forecast_anchor"]["x_norm"]
    assert replay["forecast_anchor"]["y_norm"] == repaired["forecast_anchor"]["y_norm"]
    assert replay["line_points"] == repaired["line_points"]
    assert degraded["cache_hit"] is True
    assert degraded["same_event_cache_rebuild_required"] is False
    assert degraded["closed_candle_match_scores"]["coverage_high_water_preserved"] is True
    assert degraded["geometry_frame_match_verified"] is True
    assert degraded["geometry_projected_frame_id"] == 104
    assert degraded["frame_id"] == 104
    assert degraded["display_frame_id"] == 104
    assert degraded["geometry_reprojected_from_cache"] is True
    assert degraded["geometry_projection_provenance"]["status"] == "DEGRADED_REANCHOR"
    assert degraded["geometry_projection_provenance"]["verified"] is True
    assert degraded["geometry_projection_provenance"]["reason"] == "DETECTOR_COVERAGE_DEGRADED"
    assert degraded["trade_authorized"] is False
    assert degraded["selective_authorized"] is False
    assert len(degraded["forecast_path"]) == 12
    assert len(degraded["line_points"]) == 13
    assert restored["cache_hit"] is True
    assert restored["same_event_cache_rebuild_required"] is False
    assert restored["closed_candle_match_scores"]["detected_candle_count_growth"] == 0
    assert restored["forecast_anchor"]["x_norm"] == repaired["forecast_anchor"]["x_norm"]
    assert restored["forecast_anchor"]["y_norm"] == repaired["forecast_anchor"]["y_norm"]
    assert restored["line_points"] == repaired["line_points"]
    assert restored["frame_id"] == 105
    assert restored["display_frame_id"] == 105
    assert restored["forecast_computed_frame_id"] == 102
    assert restored["source_forecast_frame_id"] == 102
    assert restored["geometry_frame_match_verified"] is True
    assert restored["geometry_projected_frame_id"] == 105


def test_scene_candle_identity_and_geometry_restore_across_process_restart() -> None:
    adapter = PhoenixGuardWindowTrackingAdapter()
    original_tracker = getattr(adapter, "_scene_belief_tracker")
    checkpoint = cast(
        dict[str, Any],
        getattr(original_tracker, "to_state_dict")(),
    )
    identity_state = {
        "schema_version": "PG_CLOSED_CANDLE_IDENTITY_STATE_V3",
        "pair": "NZDUSD_OTC",
        "timeframe": "M5",
        "event_key": "stable-closed-event",
        "event_sequence": 8,
        "latest_closed": {"track_id": "45", "side": "SELL"},
        "forming": {"track_id": "46", "side": "BUY"},
    }
    scene = {
        "closed_candle_identity_state": identity_state,
        "closed_candle_key": "stable-closed-event",
        "closed_candle_sequence": 8,
        "line_points": [[index / 12.0, 0.5] for index in range(13)],
        "forecast_candles": [{"step": index} for index in range(1, 13)],
        "forecast_scenarios": [
            {"role": "base"},
            {"role": "bull"},
            {"role": "bear"},
        ],
    }
    compact_scene = {
        "closed_candle_identity_state": identity_state,
        "closed_candle_key": "stable-closed-event",
        "closed_candle_sequence": 8,
        "belief_tracker_checkpoint": checkpoint,
    }

    restore = cast(
        Callable[[Mapping[str, Any]], None],
        getattr(adapter, "_restore_scene_belief_checkpoint"),
    )
    # An API child can receive one incomplete session view while the persisted
    # live state is being reattached.  That first view must not permanently
    # disable restoration and baseline the current candle as a false event.
    restore({"session_id": "pocket-live-8788"})
    assert getattr(adapter, "_scene_belief_restore_attempted") is False
    restore(
        {
            "tracking_summary": {
                "scene_forecast_contribution": compact_scene,
            },
            "forecast_snapshot_v3": {
                "scene_forecast_contribution": scene,
            }
        }
    )

    context_key = ("NZDUSD_OTC", "M5")
    identity_states = cast(
        dict[tuple[str, str], dict[str, Any]],
        getattr(adapter, "_scene_candle_identity_states"),
    )
    event_sequences = cast(
        dict[tuple[str, str], tuple[str, int]],
        getattr(adapter, "_scene_event_sequences"),
    )
    forecast_cache = cast(
        dict[tuple[str, str, str], dict[str, Any]],
        getattr(adapter, "_scene_forecast_cache"),
    )
    assert identity_states[context_key] == identity_state
    assert event_sequences[context_key] == (
        "stable-closed-event",
        8,
    )
    restored = forecast_cache[
        ("NZDUSD_OTC", "M5", "stable-closed-event")
    ]
    assert restored["line_points"] == scene["line_points"]
    assert restored["belief_tracker_checkpoint"] == checkpoint
    assert getattr(adapter, "_scene_belief_tracker") is not original_tracker
    assert getattr(adapter, "_scene_belief_restore_attempted") is True
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


def test_session_snapshot_read_does_not_wait_for_session_commit_lock(
    tmp_path: Path,
) -> None:
    """Dashboard polling stays live while another worker is committing state."""

    tracker = ContinuousWindowTrackerService(root_dir=tmp_path / "tracker")
    session_id = "read-while-writing"
    session_dir = tracker.session_dir(session_id)
    session_dir.mkdir(parents=True, exist_ok=True)
    # Deliberately incomplete persisted state forces in-memory normalization.
    # A read must not try to persist that normalization.
    write_json_atomic(
        session_dir / "session.json",
        {
            "session_id": session_id,
            "tracking_enabled": False,
            "status": "awaiting_focus",
        },
    )

    commit_lock = tracker._session_commit_lock_for(session_id)  # noqa: SLF001
    executor = ThreadPoolExecutor(max_workers=1)
    commit_lock.acquire()
    try:
        future = executor.submit(tracker.get_session_snapshot, session_id)
        snapshot = future.result(timeout=3.0)
    finally:
        commit_lock.release()
        executor.shutdown(wait=True)

    assert snapshot["session_id"] == session_id
    assert snapshot["status"] == "awaiting_focus"
