from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from phoenixguard.mobile_api.app import create_app


class _KillOnlyTracker:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def kill_external_source(self, session_id: str, *, reason: str) -> dict[str, Any]:
        self.calls.append((session_id, reason))
        if session_id == "missing-session":
            raise KeyError(session_id)
        return {
            "schema_version": "PG_CAPTURE_SOURCE_V3",
            "state": "KILLED",
            "fresh": False,
            "decision_usable": False,
            "display_name": "TradingView EURUSD",
            "message": reason,
        }


def test_local_dashboard_kill_route_fences_the_selected_source() -> None:
    tracker = _KillOnlyTracker()
    client: Any = TestClient(create_app(window_tracker_service=tracker))

    response = client.post(
        "/v1/mobile/window-tracker/sessions/chart-study/source-control/kill",
        json={"reason": "Stop this selected chart."},
    )

    assert response.status_code == 200
    assert tracker.calls == [("chart-study", "Stop this selected chart.")]
    payload = response.json()
    assert payload["schema_version"] == "PG_CAPTURE_SOURCE_KILLED_V1"
    assert payload["session_id"] == "chart-study"
    assert payload["capture_source_v3"]["state"] == "KILLED"
    assert payload["capture_source_v3"]["fresh"] is False
    assert payload["capture_source_v3"]["decision_usable"] is False


def test_local_dashboard_kill_route_reports_unknown_session() -> None:
    tracker = _KillOnlyTracker()
    client: Any = TestClient(create_app(window_tracker_service=tracker))

    response = client.post(
        "/v1/mobile/window-tracker/sessions/missing-session/source-control/kill",
        json={"reason": "Stop this selected chart."},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Window tracker session not found."
