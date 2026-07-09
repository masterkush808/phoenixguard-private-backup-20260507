from __future__ import annotations
from typing import Any

import json

from fastapi.testclient import TestClient

import phoenixguard.business.store as business_store_module
from phoenixguard.business.providers import PayPalCheckoutProvider
from phoenixguard.business.store import FREE_PREVIEW_PLAN_CODE, BusinessStore, set_business_store_for_test
from phoenixguard.mobile_api.app import create_app


def _client() -> TestClient:
    set_business_store_for_test(BusinessStore())
    return TestClient(create_app())


def test_commercial_api_health_is_mounted_on_tracker_app() -> None:
    client = _client()

    response = client.get("/v1/business/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["mode"] == "production-ready"
    assert payload["payments_paused"] is True
    assert payload["provider_adapters"]["billing"]["provider"] == "stripe"


def test_package_catalog_exposes_certified_runtime_profiles() -> None:
    client = _client()

    response = client.get("/v1/packages")

    assert response.status_code == 200
    payload = response.json()
    assert payload["payments_paused"] is True
    packages = {item["code"]: item for item in payload["packages"]}
    assert packages["hybrid-free-2h"]["runtime_policy"]["daily_runtime_hours"] == 2
    assert packages["hybrid-standard-6h"]["runtime_policy"]["daily_runtime_hours"] == 6
    assert packages["hybrid-professional-24x7"]["runtime_policy"]["daily_runtime_hours"] == 24
    assert packages["hybrid-standard-6h"]["phoenix_guard_settings"]["requires_verified_email"] is True
    assert packages["hybrid-professional-24x7"]["certification_level"] == "professional-certified"
    assert "internal-family-lifetime" not in packages


def test_commercial_portal_snapshot_and_mock_login() -> None:
    client = _client()
    login = client.post(
        "/v1/auth/login",
        json={"email": "operator@808fx.mock", "password": "mock-password-2026!"},
    )

    assert login.status_code == 200
    token = login.json()["access_token"]
    response = client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["customer"]["email"] == "operator@808fx.mock"
    assert payload["licenses"][0]["status"] == "active"
    assert payload["broker_accounts"][0]["status"] == "active"


def test_registration_requires_email_verification_before_checkout() -> None:
    client = _client()
    registration = client.post(
        "/v1/auth/register",
        json={
            "email": "new.customer@example.test",
            "full_name": "New Customer",
            "password": "correct-horse-808",
        },
    )

    assert registration.status_code == 201
    payload = registration.json()
    assert payload["customer"]["email_verified"] is False
    assert "access_token" not in payload
    assert payload["email_verification"]["sent"] is False
    assert payload["email_verification"]["error"]["code"] == "email_configuration_required"

    login = client.post(
        "/v1/auth/login",
        json={"email": "new.customer@example.test", "password": "correct-horse-808"},
    )

    assert login.status_code == 403
    assert "Email verification required" in login.json()["detail"]


def test_registration_enforces_hardened_password_floor() -> None:
    client = _client()

    response = client.post(
        "/v1/auth/register",
        json={
            "email": "weak.password@example.test",
            "full_name": "Weak Password",
            "password": "short-808",
        },
    )

    assert response.status_code == 422


def test_login_rate_limit_fails_closed_after_repeated_failures() -> None:
    client = _client()

    for _ in range(8):
        response = client.post(
            "/v1/auth/login",
            json={"email": "operator@808fx.mock", "password": "wrong-password-2026"},
        )
        assert response.status_code == 401

    limited = client.post(
        "/v1/auth/login",
        json={"email": "operator@808fx.mock", "password": "wrong-password-2026"},
    )

    assert limited.status_code == 429
    assert limited.headers["retry-after"]
    assert limited.json()["detail"]["code"] == "rate_limited"


def test_email_verification_resend_has_cooldown_and_no_access_token_before_verify() -> None:
    client = _client()
    registration = client.post(
        "/v1/auth/register",
        json={
            "email": "resend.cooldown@example.test",
            "full_name": "Resend Cooldown",
            "password": "correct-horse-808",
        },
    )

    assert registration.status_code == 201
    assert "access_token" not in registration.json()

    resend = client.post("/v1/auth/verification/resend", json={"email": "resend.cooldown@example.test"})

    assert resend.status_code == 429
    assert resend.json()["detail"]["code"] == "verification_resend_cooldown"


def test_email_verified_customer_paid_package_is_staged_while_payments_are_paused() -> None:
    client = _client()
    registration = client.post(
        "/v1/auth/register",
        json={
            "email": "verified.customer@example.test",
            "full_name": "Verified Customer",
            "password": "correct-horse-808",
        },
    ).json()
    from phoenixguard.business.store import get_business_store

    token = get_business_store().email_verification_tokens[registration["customer"]["id"]]
    verified = client.post("/v1/auth/verify-email", json={"token": token})
    assert verified.status_code == 200
    access_token = verified.json()["access_token"]

    checkout = client.post(
        "/v1/public/checkout/start",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"plan_code": "hybrid-professional-24x7"},
    )

    assert checkout.status_code == 200
    payload = checkout.json()
    assert payload["provider"] == "payment-paused"
    assert payload["status"] == "pending_payment_receiver"
    assert payload["plan_code"] == "hybrid-professional-24x7"
    assert payload["runtime_policy"]["daily_runtime_hours"] == 24
    assert "license" not in payload

    onboarding = client.get(
        "/v1/onboarding/status",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()
    assert onboarding["allowed"] is False
    assert "subscription_active" in onboarding["blocked_reasons"]


def test_free_preview_requires_verified_email_and_exposes_runtime_policy() -> None:
    client = _client()
    registration = client.post(
        "/v1/auth/register",
        json={
            "email": "free.preview@example.test",
            "full_name": "Free Preview",
            "password": "correct-horse-808",
        },
    ).json()

    unverified = client.post(
        "/v1/auth/login",
        json={"email": "free.preview@example.test", "password": "correct-horse-808"},
    )

    assert unverified.status_code == 403

    token = business_store_module.get_business_store().email_verification_tokens[registration["customer"]["id"]]
    verified = client.post("/v1/auth/verify-email", json={"token": token})
    assert verified.status_code == 200
    access_token = verified.json()["access_token"]

    activated = client.post(
        "/v1/public/checkout/start",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"plan_code": FREE_PREVIEW_PLAN_CODE},
    )

    assert activated.status_code == 200
    payload = activated.json()
    assert payload["provider"] == "free-preview"
    assert payload["plan_code"] == FREE_PREVIEW_PLAN_CODE
    assert payload["runtime_policy"]["daily_runtime_hours"] == 2
    assert payload["license"]["plan_code"] == FREE_PREVIEW_PLAN_CODE
    assert payload["license"]["license_key"].startswith("PG-FREE-")
    assert payload["license"]["runtime_policy"]["runtime_label"] == "2 hours daily preview"
    assert payload["license"]["package_certification"]["level"] == "preview-certified"
    assert payload["license"]["phoenix_guard_settings"]["runtime_limit_seconds_daily"] == 7200

    licenses = client.get(
        "/v1/licenses",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()["licenses"]
    assert licenses[0]["plan_code"] == FREE_PREVIEW_PLAN_CODE
    assert licenses[0]["runtime_policy"]["daily_runtime_hours"] == 2
    assert licenses[0]["license_key"] == payload["license"]["license_key"]

    onboarding = client.get(
        "/v1/onboarding/status",
        headers={"Authorization": f"Bearer {access_token}"},
    ).json()
    assert onboarding["allowed"] is False
    assert "disclosure_accepted" in onboarding["blocked_reasons"]


def test_free_preview_package_can_complete_mock_onboarding_and_blocks_stale_device() -> None:
    client = _client()
    registration = client.post(
        "/v1/auth/register",
        json={
            "email": "free.complete@example.test",
            "full_name": "Free Complete",
            "password": "correct-horse-808",
        },
    ).json()
    token = business_store_module.get_business_store().email_verification_tokens[registration["customer"]["id"]]
    verified = client.post("/v1/auth/verify-email", json={"token": token})
    access_token = verified.json()["access_token"]

    activated = client.post(
        "/v1/public/checkout/start",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"plan_code": FREE_PREVIEW_PLAN_CODE},
    ).json()
    license_key = activated["license"]["license_key"]

    disclosure = client.post("/v1/disclosures/accept", headers={"Authorization": f"Bearer {access_token}"})
    assert disclosure.status_code == 204
    broker = client.post(
        "/v1/broker-accounts",
        headers={"Authorization": f"Bearer {access_token}"},
        json={"broker_server": "Mock-Demo-Server", "mt4_account_number": "8082026"},
    )
    assert broker.status_code == 201
    device = client.post(
        "/v1/device/register",
        json={
            "license_key": license_key,
            "device_fingerprint": "free-preview-device",
            "device_label": "Preview workstation",
            "connector_version": "2.0.0-mock",
        },
    )
    assert device.status_code == 201
    connector_token = device.json()["connector_token"]

    command = client.get("/v1/commands/latest", headers={"Authorization": f"Bearer {connector_token}"})
    assert command.status_code == 200
    assert command.json()["status"] == "EXECUTION_PACKET"

    store = business_store_module.get_business_store()
    registered_device = store.device_for_connector_token(f"Bearer {connector_token}")
    assert registered_device is not None
    registered_device.last_seen_at_epoch = 1.0

    stale_command = client.get("/v1/commands/latest", headers={"Authorization": f"Bearer {connector_token}"})
    assert stale_command.status_code == 200
    assert stale_command.json()["status"] == "SERVICE_UNAVAILABLE"
    assert stale_command.json()["command"]["execution_authority"] is False


def test_runtime_limit_blocks_tracker_and_command_access() -> None:
    client = _client()
    store = business_store_module.get_business_store()
    customer = next(item for item in store.customers.values() if item.email == "operator@808fx.mock")
    license_record = store.customer_licenses(customer.id)[0]
    store.runtime_usage_seconds[license_record.id] = 6 * 3600

    access = client.get("/app/tracker", headers={"Authorization": "Bearer mock-customer-active"})

    assert access.status_code == 403
    detail = access.json()["detail"]
    assert "subscription" in detail["blocked_gates"]
    assert "daily runtime allowance" in detail["reason"]

    command = client.get("/v1/commands/latest", headers={"Authorization": "Bearer connector-active"})
    assert command.status_code == 200
    assert command.json()["status"] == "LICENSE_EXPIRED"
    assert command.json()["command"]["execution_authority"] is False


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


def test_internal_family_lifetime_license_is_admin_only_and_hidden_from_checkout() -> None:
    client = _client()
    store = business_store_module.get_business_store()
    customer = next(item for item in store.customers.values() if item.email == "operator@808fx.mock")

    denied_checkout = client.post(
        "/v1/public/checkout/start",
        headers={"Authorization": "Bearer mock-customer-active"},
        json={"plan_code": "internal-family-lifetime"},
    )
    denied_customer = client.post(
        f"/v1/admin/customers/{customer.id}/family-lifetime-license",
        headers={"Authorization": "Bearer mock-customer-active"},
    )
    granted = client.post(
        f"/v1/admin/customers/{customer.id}/family-lifetime-license",
        headers={"Authorization": "Bearer mock-admin"},
    )

    assert denied_checkout.status_code == 400
    assert "Unsupported package" in denied_checkout.json()["detail"]
    assert denied_customer.status_code == 403
    assert granted.status_code == 201
    payload = granted.json()
    assert payload["license"]["plan_code"] == "internal-family-lifetime"
    assert payload["license"]["runtime_policy"]["daily_runtime_hours"] == 24
    assert payload["license"]["license_key"].startswith("PG-FAMILY-")


def test_mock_stripe_signature_path_rejects_and_accepts_events() -> None:
    client = _client()
    event: dict[str, Any] = {
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


def test_paypal_checkout_provider_creates_order_payload(monkeypatch: Any) -> None:
    monkeypatch.setenv("PAYPAL_CLIENT_ID", "paypal_client_test")
    monkeypatch.setenv("PAYPAL_CLIENT_SECRET", "paypal_secret_test")
    monkeypatch.setenv("PAYPAL_WEBHOOK_ID", "paypal_webhook_test")
    monkeypatch.setenv("PAYPAL_SUCCESS_URL", "https://808fx.example/checkout/success")
    monkeypatch.setenv("PAYPAL_CANCEL_URL", "https://808fx.example/checkout/cancel")
    monkeypatch.setenv("PAYPAL_MERCHANT_EMAIL", "thabangkush.masoabi@gmail.com")
    captured: list[Any] = []

    class FakeResponse:
        def __init__(self, body: dict[str, Any]) -> None:
            self._body = body

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def read(self) -> bytes:
            return json.dumps(self._body).encode("utf-8")

    def fake_open(request: Any, timeout: int = 20) -> FakeResponse:
        captured.append(request)
        url = str(request.full_url)
        if url.endswith("/v1/oauth2/token"):
            return FakeResponse({"access_token": "access_token_test"})
        return FakeResponse(
            {
                "id": "paypal_order_123",
                "status": "CREATED",
                "links": [{"rel": "approve", "href": "https://paypal.test/checkoutnow?token=paypal_order_123"}],
            }
        )

    provider = PayPalCheckoutProvider(opener=fake_open)
    checkout = provider.create_subscription_checkout(
        customer_id="cus_paypal_test",
        email="customer@example.test",
        plan_code="hybrid-standard-6h",
    )

    assert checkout["provider"] == "paypal"
    assert checkout["url"].startswith("https://paypal.test/checkoutnow")
    assert checkout["custom_id"] == "pg:cus_paypal_test:hybrid-standard-6h"
    order_body = json.loads(captured[1].data.decode("utf-8"))
    assert order_body["intent"] == "CAPTURE"
    assert order_body["purchase_units"][0]["custom_id"] == "pg:cus_paypal_test:hybrid-standard-6h"
    assert order_body["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "20.00"}
    assert order_body["payment_source"]["paypal"]["experience_context"]["user_action"] == "PAY_NOW"


def test_paypal_webhook_capture_completed_activates_paid_license(monkeypatch: Any) -> None:
    monkeypatch.setenv("PHOENIXGUARD_PAYMENT_PROVIDER", "paypal")
    monkeypatch.setenv("PAYPAL_WEBHOOK_SIGNATURE_MODE", "mock")
    client = _client()
    store = business_store_module.get_business_store()
    customer = next(item for item in store.customers.values() if item.email == "expired@808fx.mock")
    before = len(store.customer_licenses(customer.id))
    event = {
        "id": "evt_paypal_completed",
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {
            "id": "capture_paypal_completed",
            "status": "COMPLETED",
            "custom_id": f"pg:{customer.id}:hybrid-standard-6h",
        },
    }

    response = client.post(
        "/v1/webhooks/paypal",
        content=json.dumps(event),
        headers={"PayPal-Transmission-Sig": "mock-valid"},
    )

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "accepted"
    licenses = store.customer_licenses(customer.id)
    assert len(licenses) == before + 1
    assert any(item.plan_code == "hybrid-standard-6h" and item.status == "active" for item in licenses)
