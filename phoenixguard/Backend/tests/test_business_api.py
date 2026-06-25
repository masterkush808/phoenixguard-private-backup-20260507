from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from phoenixguard.business import MOCK_STRIPE_WEBHOOK_SECRET, create_business_app


ACTIVE_AUTH = {"Authorization": "Bearer pg_mock_active_customer"}
EXPIRED_AUTH = {"Authorization": "Bearer pg_mock_expired_customer"}
UNBOUND_AUTH = {"Authorization": "Bearer pg_mock_unbound_customer"}


def _client() -> TestClient:
    return TestClient(create_business_app())


def _register(
    client: TestClient,
    *,
    license_key: str,
    fingerprint: str,
    connector_version: str = "1.0.0",
) -> dict[str, Any]:
    response = client.post(
        "/v1/device/register",
        json={
            "license_key": license_key,
            "device_fingerprint": fingerprint,
            "device_label": f"{fingerprint}-device",
            "connector_version": connector_version,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _connector_auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _stripe_payload(event: dict[str, Any]) -> bytes:
    return json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _stripe_signature(payload: bytes, *, secret: str = MOCK_STRIPE_WEBHOOK_SECRET) -> str:
    timestamp = int(time.time())
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + payload,
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def _post_stripe(client: TestClient, event: dict[str, Any]) -> Any:
    payload = _stripe_payload(event)
    return client.post(
        "/v1/webhooks/stripe",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "Stripe-Signature": _stripe_signature(payload),
        },
    )


def _invoice_event(event_id: str, *, event_type: str, customer: str, subscription: str) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": event_type,
        "data": {
            "object": {
                "id": f"in_{event_id}",
                "customer": customer,
                "subscription": subscription,
                "metadata": {"plan_code": "business"},
                "lines": {
                    "data": [
                        {
                            "period": {
                                "start": 1780272000,
                                "end": 1811808000,
                            },
                            "price": {"lookup_key": "business"},
                        }
                    ]
                },
            }
        },
    }


def test_active_license_registers_device_heartbeats_and_returns_active_entitlement() -> None:
    client = _client()

    registration = _register(
        client,
        license_key="PG-ACTIVE-2026",
        fingerprint="active-laptop",
    )

    assert registration["license_id"] == "lic_active"
    assert registration["entitlement"]["status"] == "active"
    connector_headers = _connector_auth(registration["connector_token"])

    heartbeat = client.post(
        "/v1/device/heartbeat",
        headers=connector_headers,
        json={
            "connector_version": "1.0.0",
            "ea_version": "808.2.0",
            "mt4_terminal_build": "1415",
            "status": "ok",
        },
    )
    assert heartbeat.status_code == 204

    entitlement = client.get("/v1/entitlements/current", headers=connector_headers)
    assert entitlement.status_code == 200
    assert entitlement.json() == {
        "status": "active",
        "license_id": "lic_active",
        "plan_code": "business",
        "expires_at": "2027-06-01T00:00:00Z",
    }


@pytest.mark.parametrize(
    ("license_key", "fingerprint", "expected_status", "expected_reason"),
    [
        ("PG-EXPIRED-2026", "expired-laptop", "expired", "LICENSE_EXPIRED"),
        ("PG-REVOKED-2026", "revoked-laptop", "revoked", "LICENSE_REVOKED"),
        ("PG-UNBOUND-2026", "unbound-laptop", "grace", "ACCOUNT_NOT_BOUND"),
    ],
)
def test_entitlement_states_cover_expired_revoked_and_unbound_accounts(
    license_key: str,
    fingerprint: str,
    expected_status: str,
    expected_reason: str,
) -> None:
    client = _client()

    registration = _register(client, license_key=license_key, fingerprint=fingerprint)
    entitlement = registration["entitlement"]

    assert entitlement["status"] == expected_status
    assert entitlement["reason"] == expected_reason

    current = client.get(
        "/v1/entitlements/current",
        headers=_connector_auth(registration["connector_token"]),
    )
    assert current.status_code == 200
    assert current.json()["status"] == expected_status
    assert current.json()["reason"] == expected_reason


def test_broker_account_binding_moves_unbound_license_to_active_entitlement() -> None:
    client = _client()
    registration = _register(
        client,
        license_key="PG-UNBOUND-2026",
        fingerprint="unbound-before-binding",
    )
    connector_headers = _connector_auth(registration["connector_token"])

    assert client.get("/v1/entitlements/current", headers=connector_headers).json()["reason"] == "ACCOUNT_NOT_BOUND"

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

    entitlement = client.get("/v1/entitlements/current", headers=connector_headers)
    assert entitlement.status_code == 200
    assert entitlement.json()["status"] == "active"
    assert "reason" not in entitlement.json()


def test_customer_portal_endpoints_are_customer_isolated() -> None:
    client = _client()

    active_licenses = client.get("/v1/licenses", headers=ACTIVE_AUTH)
    assert active_licenses.status_code == 200
    active_license_ids = {item["license_id"] for item in active_licenses.json()["licenses"]}
    assert active_license_ids == {"lic_active"}

    expired_licenses = client.get("/v1/licenses", headers=EXPIRED_AUTH)
    assert expired_licenses.status_code == 200
    expired_license_ids = {item["license_id"] for item in expired_licenses.json()["licenses"]}
    assert expired_license_ids == {"lic_expired"}

    cross_customer_acceptance = client.post(
        "/v1/disclosures/accept",
        headers={**ACTIVE_AUTH, "X-Request-ID": "req_customer_isolation"},
        json={"version": "risk-2026-06", "license_id": "lic_expired"},
    )
    assert cross_customer_acceptance.status_code == 403
    assert cross_customer_acceptance.json() == {
        "error": "license_not_owned",
        "request_id": "req_customer_isolation",
    }


def test_latest_release_requires_customer_owned_active_license_and_disclosure() -> None:
    client = _client()

    release = client.get("/v1/releases/latest", headers=ACTIVE_AUTH)
    assert release.status_code == 200
    payload = release.json()
    assert payload["release_id"] == "rel_2026_06_001"
    assert payload["manifest"]["required_disclosure_version"] == "risk-2026-06"
    assert payload["signed_download_url"].startswith(
        "https://downloads.phoenixguard.example.test/rel_2026_06_001/cus_active"
    )

    expired_release = client.get("/v1/releases/latest", headers=EXPIRED_AUTH)
    assert expired_release.status_code == 403
    assert expired_release.json()["error"] == "no_release_eligible_license"


def test_stripe_webhook_rejects_invalid_signature() -> None:
    client = _client()
    event = _invoice_event(
        "evt_bad_signature",
        event_type="invoice.payment_failed",
        customer="cus_stripe_active",
        subscription="sub_pg_active",
    )
    payload = _stripe_payload(event)

    response = client.post(
        "/v1/webhooks/stripe",
        content=payload,
        headers={"Content-Type": "application/json", "Stripe-Signature": "t=1,v1=bad"},
    )

    assert response.status_code == 400
    assert response.json()["error"].startswith("stripe_signature_")


def test_stripe_invoice_webhooks_update_only_the_matching_customer_license() -> None:
    client = _client()

    failed = _post_stripe(
        client,
        _invoice_event(
            "evt_payment_failed_active",
            event_type="invoice.payment_failed",
            customer="cus_stripe_active",
            subscription="sub_pg_active",
        ),
    )
    assert failed.status_code == 200, failed.text
    assert failed.json()["license_status"] == "grace"
    assert failed.json()["updated_license_ids"] == ["lic_active"]

    active_license = client.get("/v1/licenses", headers=ACTIVE_AUTH).json()["licenses"][0]
    expired_license = client.get("/v1/licenses", headers=EXPIRED_AUTH).json()["licenses"][0]
    assert active_license["status"] == "grace"
    assert expired_license["status"] == "expired"

    duplicate = _post_stripe(
        client,
        _invoice_event(
            "evt_payment_failed_active",
            event_type="invoice.payment_failed",
            customer="cus_stripe_active",
            subscription="sub_pg_active",
        ),
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["action"] == "duplicate_ignored"

    paid = _post_stripe(
        client,
        _invoice_event(
            "evt_payment_paid_active",
            event_type="invoice.paid",
            customer="cus_stripe_active",
            subscription="sub_pg_active",
        ),
    )
    assert paid.status_code == 200
    assert paid.json()["license_status"] == "active"
    assert client.get("/v1/licenses", headers=ACTIVE_AUTH).json()["licenses"][0]["status"] == "active"


def test_stripe_revoke_event_immediately_revokes_existing_connector_entitlement() -> None:
    client = _client()
    registration = _register(
        client,
        license_key="PG-ACTIVE-2026",
        fingerprint="active-before-chargeback",
    )
    connector_headers = _connector_auth(registration["connector_token"])

    response = _post_stripe(
        client,
        {
            "id": "evt_chargeback_active",
            "type": "charge.dispute.created",
            "data": {"object": {"id": "dp_1", "customer": "cus_stripe_active"}},
        },
    )

    assert response.status_code == 200
    assert response.json()["action"] == "licenses_revoked"
    assert response.json()["updated_license_ids"] == ["lic_active"]

    entitlement = client.get("/v1/entitlements/current", headers=connector_headers)
    assert entitlement.status_code == 200
    assert entitlement.json()["status"] == "revoked"
    assert entitlement.json()["reason"] == "LICENSE_REVOKED"
