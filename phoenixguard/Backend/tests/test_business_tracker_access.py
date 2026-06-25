from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from phoenixguard.business import register_business_routes
from phoenixguard.business.store import BusinessStore, set_business_store_for_test
from phoenixguard.mobile_api.app import create_app


CONNECTOR_ACTIVE = {"Authorization": "Bearer connector-active"}
CUSTOMER_ACTIVE = {"Authorization": "Bearer mock-customer-active"}


class _FakeTracker:
    def latest_artifact_path(self, session_id: str, kind: str) -> Path:
        raise FileNotFoundError(kind)

    def capture_worker_health_v3(self, session_id: str) -> dict[str, str]:
        return {"status": "ok", "session_id": session_id}


def _mobile_client(
    store: BusinessStore | None = None,
    *,
    tracker: object | None = None,
) -> TestClient:
    set_business_store_for_test(store or BusinessStore())
    return TestClient(create_app(window_tracker_service=tracker))  # type: ignore[arg-type]


def _bare_business_client(store: BusinessStore | None = None) -> TestClient:
    app = FastAPI()
    register_business_routes(app, store=store or BusinessStore())
    return TestClient(app)


def _active_customer(store: BusinessStore):
    device = store.device_for_connector_token("Bearer connector-active")
    assert device is not None
    return store.customers[device.customer_id]


def _no_mutation(store: BusinessStore) -> None:
    return None


def _unverified_email(store: BusinessStore) -> None:
    _active_customer(store).email_verified = False


def _missing_disclosure(store: BusinessStore) -> None:
    _active_customer(store).disclosure_accepted = False


@pytest.mark.parametrize(
    ("headers", "mutate", "expected_status", "expected_gate"),
    [
        ({}, _no_mutation, 401, "registration"),
        (CONNECTOR_ACTIVE, _unverified_email, 403, "email"),
        ({"Authorization": "Bearer connector-expired"}, _no_mutation, 403, "subscription"),
        (CONNECTOR_ACTIVE, _missing_disclosure, 403, "disclosure"),
        ({"Authorization": "Bearer connector-unbound"}, _no_mutation, 403, "broker"),
        ({"Authorization": "Bearer connector-revoked"}, _no_mutation, 403, "device"),
    ],
)
def test_app_tracker_blocks_until_every_business_gate_passes(
    headers: dict[str, str],
    mutate: Callable[[BusinessStore], None],
    expected_status: int,
    expected_gate: str,
) -> None:
    store = BusinessStore()
    mutate(store)
    client = _mobile_client(store)

    response = client.get("/app/tracker", headers=headers)

    assert response.status_code == expected_status
    detail = response.json()["detail"]
    assert detail["allowed"] is False
    assert expected_gate in detail["blocked_gates"]
    assert detail["gates"][expected_gate]["passed"] is False


def test_app_tracker_allows_customer_or_connector_when_gates_pass() -> None:
    client = _mobile_client(BusinessStore())

    for headers in (CUSTOMER_ACTIVE, CONNECTOR_ACTIVE):
        response = client.get("/app/tracker", headers=headers)

        assert response.status_code == 200
        payload = response.json()
        assert payload["allowed"] is True
        assert payload["blocked_gates"] == []
        assert payload["dashboard_url"] == "/v1/mobile/window-tracker/dashboard"
        assert all(gate["passed"] for gate in payload["gates"].values())


@pytest.mark.parametrize(
    ("token", "mutate", "expected_gate", "expected_status"),
    [
        ("connector-active", _unverified_email, "email", "SERVICE_UNAVAILABLE"),
        ("connector-expired", _no_mutation, "subscription", "LICENSE_EXPIRED"),
        ("connector-active", _missing_disclosure, "disclosure", "NO_EXECUTION_PACKET"),
        ("connector-unbound", _no_mutation, "broker", "ACCOUNT_NOT_BOUND"),
        ("connector-revoked", _no_mutation, "device", "DEVICE_REVOKED"),
    ],
)
def test_connector_command_delivery_returns_non_executable_status_until_gates_pass(
    token: str,
    mutate: Callable[[BusinessStore], None],
    expected_gate: str,
    expected_status: str,
) -> None:
    store = BusinessStore()
    mutate(store)
    client = _mobile_client(store)

    response = client.get("/v1/commands/latest", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == expected_status
    assert payload["command"]["execution_authority"] is False
    assert expected_gate in payload["tracker_access"]["blocked_gates"]
    assert payload["tracker_access"]["gates"][expected_gate]["passed"] is False


def test_connector_command_delivery_requires_registered_connector_token() -> None:
    client = _mobile_client(BusinessStore())

    response = client.get("/v1/commands/latest", headers={"Authorization": "Bearer missing"})

    assert response.status_code == 401
    detail = response.json()["detail"]
    assert detail["allowed"] is False
    assert "registration" in detail["blocked_gates"]


def test_connector_command_delivery_allows_executable_only_when_gates_pass() -> None:
    client = _mobile_client(BusinessStore())

    response = client.get("/v1/commands/latest", headers=CONNECTOR_ACTIVE)

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "EXECUTION_PACKET"
    assert payload["command"]["execution_authority"] is True
    assert payload["command"]["side"] in {"BUY", "SELL"}


def test_tracker_health_aggregates_existing_mobile_tracker_health_endpoint() -> None:
    client = _mobile_client(BusinessStore(), tracker=_FakeTracker())

    response = client.get(
        "/v1/business/tracker/health?session_id=health-session",
        headers=CONNECTOR_ACTIVE,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["components"]["mobile_api"]["status"] == "ok"
    tracker_component = payload["components"]["tracker_session"]
    assert tracker_component["status"] == "ok"
    assert tracker_component["payload"]["session_id"] == "health-session"
    assert tracker_component["payload"]["capture_worker_v3"] == {
        "status": "ok",
        "session_id": "health-session",
    }


def test_tracker_health_is_explicit_config_required_without_tracker_health_routes() -> None:
    client = _bare_business_client(BusinessStore())

    response = client.get("/v1/business/tracker/health", headers=CONNECTOR_ACTIVE)

    assert response.status_code == 503
    payload = response.json()
    assert payload["status"] == "config-required"
    assert payload["components"]["mobile_api"]["status"] == "config-required"
    assert payload["components"]["tracker_session"]["status"] == "config-required"
    assert payload["components"]["tracker_session"]["reason"] == "health route is not registered on this FastAPI app"
