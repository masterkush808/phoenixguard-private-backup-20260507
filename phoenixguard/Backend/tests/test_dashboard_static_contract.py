from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "Frontend" / "dashboard" / "static" / "window_tracker_dashboard.html"


def _dashboard_text() -> str:
    return DASHBOARD.read_text(encoding="utf-8")


def test_dashboard_uses_background_worker_pulse_for_hidden_heartbeat() -> None:
    text = _dashboard_text()

    assert "DASHBOARD_BACKGROUND_PULSE_INTERVAL_MS = 15000" in text
    assert "heartbeatWorker: null" in text
    assert "function startDashboardBackgroundPulse()" in text
    assert "new Worker(URL.createObjectURL(blob))" in text
    assert "phoenixguard-background-pulse" in text
    assert "pulseFrontendHeartbeat();" in text
    assert "startDashboardBackgroundPulse();" in text


def test_dashboard_heartbeat_fetch_uses_keepalive() -> None:
    text = _dashboard_text()

    assert 'keepalive: true' in text
    assert 'window.fetch("/v1/mobile/frontend/heartbeat/v3"' in text


def test_dashboard_overlay_payload_selection_requires_drawable_objects() -> None:
    text = _dashboard_text()

    assert "function overlayPayloadWithObjects(session = {})" in text
    assert "Array.isArray(topLevel.objects) || Array.isArray(topLevel.all_objects)" in text
    assert "Array.isArray(liveNested.objects) || Array.isArray(liveNested.all_objects)" in text
    assert "objectOrEmpty(session.overlays || liveState.overlays)" not in text
