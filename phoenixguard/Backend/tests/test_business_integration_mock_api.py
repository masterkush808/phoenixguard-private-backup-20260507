from __future__ import annotations

from fastapi.testclient import TestClient

from Business.api.business_mock_api import (
    ACTIVE_CUSTOMER_EMAIL,
    ADMIN_EMAIL,
    EXPIRED_CUSTOMER_EMAIL,
    MOCK_PASSWORD,
    TRACKER_SESSION_ID,
    create_app,
)


def _login(client: TestClient, email: str) -> str:
    response = client.post(
        "/v1/auth/login",
        json={"email": email, "password": MOCK_PASSWORD},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    return str(payload["access_token"])


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _accept_disclosure_and_bind_broker(client: TestClient, token: str) -> None:
    disclosure = client.post(
        "/v1/disclosures/accept",
        json={"accepted": True, "version": "risk-disclosure-2026-06"},
        headers=_auth(token),
    )
    assert disclosure.status_code == 204

    broker = client.post(
        "/v1/broker-accounts",
        json={
            "broker_server": "PocketOption-Demo",
            "mt4_account_number": "8082026",
            "label": "QA mock account",
        },
        headers=_auth(token),
    )
    assert broker.status_code == 201
    payload = broker.json()
    assert payload["status"] == "bound"
    assert payload["broker_server_hash"].startswith("sha256:")
    assert payload["mt4_account_number_hash"].startswith("sha256:")


def test_mock_customer_flow_unlocks_active_license_command_and_tracker_gui() -> None:
    client = TestClient(create_app())
    token = _login(client, ACTIVE_CUSTOMER_EMAIL)

    _accept_disclosure_and_bind_broker(client, token)

    licenses = client.get("/v1/licenses", headers=_auth(token))
    assert licenses.status_code == 200
    license_payload = licenses.json()["licenses"][0]
    assert license_payload["status"] == "active"
    assert license_payload["is_active"] is True
    assert license_payload["requires_disclosure_acceptance"] is False
    assert license_payload["requires_broker_account_binding"] is False

    command_response = client.get("/v1/commands/latest", headers=_auth(token))
    assert command_response.status_code == 200
    command_payload = command_response.json()
    assert command_payload["status"] == "EXECUTION_PACKET"
    command = command_payload["command"]
    assert command["execution_authority"] is True
    assert command["side"] in {"BUY", "SELL"}
    assert command["signature_alg"] == "mock-ed25519-detached"
    assert str(command["signature"]).startswith("mocksig:")

    tracker_health = client.get(f"/v1/mobile/window-tracker/sessions/{TRACKER_SESSION_ID}/health")
    assert tracker_health.status_code == 200
    assert tracker_health.json()["alive"] is True

    dashboard = client.get(f"/v1/mobile/window-tracker/dashboard/{TRACKER_SESSION_ID}")
    assert dashboard.status_code == 200
    assert 'data-testid="tracker-gui"' in dashboard.text
    assert "alive: running" in dashboard.text


def test_mock_admin_route_denies_customer_and_accepts_admin() -> None:
    client = TestClient(create_app())
    customer_token = _login(client, ACTIVE_CUSTOMER_EMAIL)
    admin_token = _login(client, ADMIN_EMAIL)

    denied = client.get("/v1/admin/customers", headers=_auth(customer_token))
    assert denied.status_code == 403
    assert denied.json()["detail"] == "Admin access denied."

    allowed = client.get("/v1/admin/customers", headers=_auth(admin_token))
    assert allowed.status_code == 200
    customers = allowed.json()["customers"]
    assert {customer["email"] for customer in customers} == {
        ACTIVE_CUSTOMER_EMAIL,
        EXPIRED_CUSTOMER_EMAIL,
    }


def test_expired_customer_never_receives_executable_command() -> None:
    client = TestClient(create_app())
    token = _login(client, EXPIRED_CUSTOMER_EMAIL)
    _accept_disclosure_and_bind_broker(client, token)

    licenses = client.get("/v1/licenses", headers=_auth(token))
    assert licenses.status_code == 200
    assert licenses.json()["licenses"][0]["status"] == "expired"

    command_response = client.get("/v1/commands/latest", headers=_auth(token))
    assert command_response.status_code == 200
    payload = command_response.json()
    assert payload["status"] == "LICENSE_EXPIRED"
    command = payload["command"]
    assert command["status"] == "LICENSE_EXPIRED"
    assert command["execution_authority"] is False
    assert "side" not in command


def test_refresh_and_relogin_preserve_customer_gate_state() -> None:
    client = TestClient(create_app())
    first_token = _login(client, ACTIVE_CUSTOMER_EMAIL)
    _accept_disclosure_and_bind_broker(client, first_token)

    refreshed = client.get("/v1/me", headers=_auth(first_token))
    assert refreshed.status_code == 200
    refreshed_user = refreshed.json()["user"]
    assert refreshed_user["disclosure_accepted"] is True
    assert refreshed_user["broker_account_bound"] is True

    second_token = _login(client, ACTIVE_CUSTOMER_EMAIL)
    relogged = client.get("/v1/me", headers=_auth(second_token))
    assert relogged.status_code == 200
    relogged_user = relogged.json()["user"]
    assert relogged_user["disclosure_accepted"] is True
    assert relogged_user["broker_account_bound"] is True

    command_response = client.get("/v1/commands/latest", headers=_auth(second_token))
    assert command_response.status_code == 200
    assert command_response.json()["status"] == "EXECUTION_PACKET"
