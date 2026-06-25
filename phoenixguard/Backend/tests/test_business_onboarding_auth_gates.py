from __future__ import annotations

from typing import Any, cast

import pytest
from fastapi.testclient import TestClient

from phoenixguard.business import create_business_app
from phoenixguard.business.auth import BusinessAuthError, MockBusinessAuthProvider
from phoenixguard.business.onboarding import CapturingEmailVerificationProvider


ACTIVE_AUTH = {"Authorization": "Bearer pg_mock_active_customer"}
EXPIRED_AUTH = {"Authorization": "Bearer pg_mock_expired_customer"}
UNBOUND_AUTH = {"Authorization": "Bearer pg_mock_unbound_customer"}


def _client() -> TestClient:
    return TestClient(create_business_app(email_provider=CapturingEmailVerificationProvider()))


def _latest_verification_token(client: TestClient) -> str:
    provider = cast(Any, client.app).state.business_email_provider
    return str(provider.sent_messages[-1]["verification_token"])


def _register_device(client: TestClient, *, license_key: str, fingerprint: str) -> dict[str, Any]:
    response = client.post(
        "/v1/device/register",
        json={
            "license_key": license_key,
            "device_fingerprint": fingerprint,
            "device_label": f"{fingerprint} connector",
            "connector_version": "1.0.0",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _connector_auth(registration: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {registration['connector_token']}"}


def test_registration_requires_email_verification_and_tokens_are_single_use() -> None:
    client = _client()

    registered = client.post(
        "/v1/auth/register",
        json={
            "email": "new.customer@example.test",
            "full_name": "New Customer",
            "country_code": "us",
        },
    )

    assert registered.status_code == 201, registered.text
    payload = registered.json()
    assert payload["customer"]["status"] == "pending_email"
    assert payload["customer"]["email_verified"] is False
    assert "verification_token" not in payload["email_verification"]
    first_token = _latest_verification_token(client)

    resent = client.post(
        "/v1/auth/verification/resend",
        json={"email": "new.customer@example.test"},
    )
    assert resent.status_code == 200, resent.text
    second_token = _latest_verification_token(client)
    assert second_token != first_token

    revoked = client.post("/v1/auth/verify-email", json={"verification_token": first_token})
    assert revoked.status_code == 409
    assert revoked.json()["error"] == "email_verification_token_revoked"

    verified = client.post("/v1/auth/verify-email", json={"verification_token": second_token})
    assert verified.status_code == 200, verified.text
    access_token = verified.json()["access_token"]
    assert access_token.startswith("pgcust_")
    assert verified.json()["customer"]["email_verified"] is True

    reused = client.post("/v1/auth/verify-email", json={"verification_token": second_token})
    assert reused.status_code == 409
    assert reused.json()["error"] == "email_verification_token_already_used"

    licenses = client.get("/v1/licenses", headers={"Authorization": f"Bearer {access_token}"})
    assert licenses.status_code == 200
    assert licenses.json()["licenses"] == []


def test_download_tracker_and_command_gates_require_disclosure_and_broker_binding() -> None:
    client = _client()
    registration = _register_device(
        client,
        license_key="PG-UNBOUND-2026",
        fingerprint="unbound-gate-device",
    )
    connector_headers = _connector_auth(registration)

    command_before_disclosure = client.get("/v1/commands/latest", headers=connector_headers)
    assert command_before_disclosure.status_code == 200
    assert command_before_disclosure.json()["status"] == "SERVICE_UNAVAILABLE"
    assert command_before_disclosure.json()["command"]["execution_authority"] is False

    release_before_disclosure = client.get("/v1/releases/latest", headers=UNBOUND_AUTH)
    assert release_before_disclosure.status_code == 403
    assert release_before_disclosure.json()["error"] == "risk_disclosure_required"

    tracker_before_disclosure = client.get("/v1/tracker/access", headers=UNBOUND_AUTH)
    assert tracker_before_disclosure.status_code == 403
    assert tracker_before_disclosure.json()["error"] == "risk_disclosure_required"

    disclosure = client.post(
        "/v1/disclosures/accept",
        headers=UNBOUND_AUTH,
        json={"version": "risk-2026-06", "license_id": "lic_unbound"},
    )
    assert disclosure.status_code == 204

    release_after_disclosure = client.get("/v1/releases/latest", headers=UNBOUND_AUTH)
    assert release_after_disclosure.status_code == 200
    assert release_after_disclosure.json()["signed_download_url"].startswith(
        "https://downloads.phoenixguard.example.test/rel_2026_06_001/cus_unbound"
    )

    tracker_before_broker = client.get("/v1/tracker/access", headers=UNBOUND_AUTH)
    assert tracker_before_broker.status_code == 403
    assert tracker_before_broker.json()["error"] == "broker_account_binding_required"

    rejected_secret = client.post(
        "/v1/broker-accounts",
        headers=UNBOUND_AUTH,
        json={
            "broker_server": "Phoenix-Live",
            "mt4_account_number": "99001122",
            "broker_password": "never-send-this",
        },
    )
    assert rejected_secret.status_code == 400
    assert rejected_secret.json()["error"] == "broker_credentials_not_collected"

    binding = client.post(
        "/v1/broker-accounts",
        headers=UNBOUND_AUTH,
        json={
            "broker_server": "Phoenix-Live",
            "mt4_account_number": "99001122",
            "label": "Primary MT4",
        },
    )
    assert binding.status_code == 201
    assert binding.json()["binding"]["license_id"] == "lic_unbound"

    tracker_after_broker = client.get("/v1/tracker/access", headers=UNBOUND_AUTH)
    assert tracker_after_broker.status_code == 200
    assert tracker_after_broker.json()["gates"]["broker_account_bound"] is True

    command_after_broker = client.get("/v1/commands/latest", headers=connector_headers)
    assert command_after_broker.status_code == 200
    assert command_after_broker.json()["status"] == "NO_EXECUTION_PACKET"
    assert command_after_broker.json()["command"]["execution_authority"] is False


def test_license_state_and_object_authorization_fail_closed() -> None:
    client = _client()
    registration = _register_device(
        client,
        license_key="PG-EXPIRED-2026",
        fingerprint="expired-gate-device",
    )

    expired_release = client.get("/v1/releases/latest", headers=EXPIRED_AUTH)
    assert expired_release.status_code == 403
    assert expired_release.json()["error"] == "no_release_eligible_license"

    expired_command = client.get("/v1/commands/latest", headers=_connector_auth(registration))
    assert expired_command.status_code == 200
    assert expired_command.json()["status"] == "LICENSE_EXPIRED"
    assert expired_command.json()["command"]["execution_authority"] is False

    cross_customer_acceptance = client.post(
        "/v1/disclosures/accept",
        headers={**ACTIVE_AUTH, "X-Request-ID": "req_onboarding_object_auth"},
        json={"version": "risk-2026-06", "license_id": "lic_unbound"},
    )
    assert cross_customer_acceptance.status_code == 403
    assert cross_customer_acceptance.json() == {
        "error": "license_not_owned",
        "request_id": "req_onboarding_object_auth",
    }


def test_auth_provider_requires_env_secrets_in_strict_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOENIXGUARD_BUSINESS_REQUIRE_ENV_SECRETS", "1")
    monkeypatch.delenv("PHOENIXGUARD_BUSINESS_CONNECTOR_TOKEN_SECRET", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_BUSINESS_CUSTOMER_TOKEN_SECRET", raising=False)

    with pytest.raises(BusinessAuthError, match="phoenixguard_business_connector_token_secret_missing"):
        MockBusinessAuthProvider()
