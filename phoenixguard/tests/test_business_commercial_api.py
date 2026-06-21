from __future__ import annotations

import json

from fastapi.testclient import TestClient

import phoenixguard.business.store as business_store_module
from phoenixguard.business.store import BusinessStore
from phoenixguard.mobile_api.app import create_app


def _client() -> TestClient:
    business_store_module._BUSINESS_STORE = BusinessStore()
    return TestClient(create_app())


def test_commercial_api_health_is_mounted_on_tracker_app() -> None:
    client = _client()

    response = client.get("/v1/business/health")

    assert response.status_code == 200
    assert response.json()["mode"] == "mock"


def test_commercial_portal_snapshot_and_mock_login() -> None:
    client = _client()
    login = client.post("/v1/auth/login", json={"email": "operator@808fx.mock"})

    assert login.status_code == 200
    token = login.json()["access_token"]
    response = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["customer"]["email"] == "operator@808fx.mock"
    assert payload["licenses"][0]["status"] == "active"
    assert payload["broker_accounts"][0]["status"] == "active"


def test_commercial_api_never_accepts_broker_passwords() -> None:
    client = _client()

    response = client.post(
        "/v1/broker-accounts",
        headers={"Authorization": "Bearer mock-customer-active"},
        json={
            "broker_server": "Mock-Demo-Server",
            "mt4_account_number": "123456",
            "broker_password": "never-store-this",
        },
    )

    assert response.status_code == 400


def test_connector_command_states_cover_success_and_blockers() -> None:
    client = _client()

    cases = {
        "connector-active": "EXECUTION_PACKET",
        "connector-expired": "LICENSE_EXPIRED",
        "connector-revoked": "DEVICE_REVOKED",
        "connector-unbound": "ACCOUNT_NOT_BOUND",
    }
    for token, expected_status in cases.items():
        response = client.get("/v1/commands/latest", headers={"Authorization": f"Bearer {token}"})

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == expected_status
        if expected_status != "EXECUTION_PACKET":
            assert payload["command"]["execution_authority"] is False


def test_customer_cannot_access_admin_surface_but_admin_can() -> None:
    client = _client()

    denied = client.get("/v1/admin/customers", headers={"Authorization": "Bearer mock-customer-active"})
    allowed = client.get("/v1/admin/customers", headers={"Authorization": "Bearer mock-admin"})

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert len(allowed.json()["customers"]) >= 4


def test_mock_stripe_signature_path_rejects_and_accepts_events() -> None:
    client = _client()
    event = {
        "type": "invoice.payment_failed",
        "data": {"object": {"subscription": "sub_mock_active"}},
    }

    rejected = client.post(
        "/v1/webhooks/stripe",
        content=json.dumps(event),
        headers={"Stripe-Signature": "t=mock,v1=bad"},
    )
    accepted = client.post(
        "/v1/webhooks/stripe",
        content=json.dumps(event),
        headers={"Stripe-Signature": "t=mock,v1=mock-valid"},
    )

    assert rejected.status_code == 400
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "accepted"
