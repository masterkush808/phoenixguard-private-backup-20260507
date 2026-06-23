from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any, Mapping
from urllib.parse import parse_qs

import pytest
from fastapi.testclient import TestClient

from phoenixguard.business.billing import (
    BillingConfigurationError,
    BillingService,
    BillingSignatureError,
    StripeCheckoutSessionClient,
    StripeCheckoutSessionConfig,
    StripeWebhookVerifier,
)
from phoenixguard.business.email import (
    EmailConfigurationError,
    ResendEmailConfig,
    ResendEmailConfirmationAdapter,
)
from phoenixguard.business.repository import MockBusinessRepository
from phoenixguard.business.service import create_business_app


EXPIRED_AUTH = {"Authorization": "Bearer pg_mock_expired_customer"}


def _stripe_payload(event: dict[str, Any]) -> bytes:
    return json.dumps(event, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _stripe_signature(payload: bytes, *, secret: str, timestamp: int | None = None) -> str:
    timestamp = timestamp or int(time.time())
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode("utf-8") + payload,
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def _invoice_paid_event(event_id: str) -> dict[str, Any]:
    return {
        "id": event_id,
        "type": "invoice.paid",
        "data": {
            "object": {
                "id": f"in_{event_id}",
                "customer": "cus_stripe_expired",
                "subscription": "sub_pg_expired",
                "metadata": {"plan_code": "business"},
                "lines": {
                    "data": [
                        {
                            "period": {"start": 1780272000, "end": 1811808000},
                            "price": {"lookup_key": "business"},
                        }
                    ]
                },
            }
        },
    }


def test_provider_adapters_fail_when_required_config_is_missing() -> None:
    with pytest.raises(BillingConfigurationError, match="stripe_secret_key_required"):
        StripeCheckoutSessionConfig.from_env({})

    with pytest.raises(BillingSignatureError, match="stripe_webhook_secret_required"):
        StripeWebhookVerifier(secret="").verify(payload=b"{}", signature_header="t=1,v1=bad")

    with pytest.raises(EmailConfigurationError, match="resend_api_key_required"):
        ResendEmailConfig.from_env({})


def test_stripe_checkout_session_request_payload_shape() -> None:
    captured: dict[str, Any] = {}

    def fake_post(
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> tuple[int, Mapping[str, str], bytes]:
        captured.update(
            {
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )
        return 200, {}, json.dumps({"id": "cs_test_123", "url": "https://checkout.stripe.test/c/cs_test_123"}).encode()

    client = StripeCheckoutSessionClient(
        config=StripeCheckoutSessionConfig(
            secret_key="sk_test_123",
            price_id="price_business",
            success_url="https://phoenixguard.example/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://phoenixguard.example/cancel",
            api_base_url="https://api.stripe.test",
        ),
        http_post=fake_post,
    )

    result = client.create_session(
        customer_email="customer@example.test",
        customer_id="cus_local_123",
        plan_code="business",
    )

    assert result["checkout_session_id"] == "cs_test_123"
    assert captured["url"] == "https://api.stripe.test/v1/checkout/sessions"
    expected_auth = base64.b64encode(b"sk_test_123:").decode("ascii")
    assert captured["headers"]["Authorization"] == f"Basic {expected_auth}"
    assert captured["headers"]["Content-Type"] == "application/x-www-form-urlencoded"

    form = parse_qs(captured["body"].decode("utf-8"), keep_blank_values=True)
    assert form["mode"] == ["subscription"]
    assert form["success_url"] == ["https://phoenixguard.example/success?session_id={CHECKOUT_SESSION_ID}"]
    assert form["cancel_url"] == ["https://phoenixguard.example/cancel"]
    assert form["customer_email"] == ["customer@example.test"]
    assert form["client_reference_id"] == ["cus_local_123"]
    assert form["line_items[0][price]"] == ["price_business"]
    assert form["line_items[0][quantity]"] == ["1"]
    assert form["metadata[customer_id]"] == ["cus_local_123"]
    assert form["metadata[plan_code]"] == ["business"]
    assert form["subscription_data[metadata][customer_id]"] == ["cus_local_123"]
    assert form["subscription_data[metadata][plan_code]"] == ["business"]
    assert not any("card" in key or "password" in key for key in form)


def test_stripe_webhook_signature_validation_path() -> None:
    secret = "whsec_test_signature"
    payload = _stripe_payload({"id": "evt_valid", "type": "invoice.paid", "data": {"object": {}}})
    verifier = StripeWebhookVerifier(secret=secret, now_epoch=lambda: 1_800_000_000)
    verifier.verify(
        payload=payload,
        signature_header=_stripe_signature(payload, secret=secret, timestamp=1_800_000_000),
    )

    with pytest.raises(BillingSignatureError, match="stripe_signature_invalid"):
        verifier.verify(
            payload=payload,
            signature_header=_stripe_signature(payload, secret="whsec_wrong", timestamp=1_800_000_000),
        )


def test_resend_email_confirmation_send_path_with_mocked_http() -> None:
    captured: dict[str, Any] = {}

    def fake_post(
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> tuple[int, Mapping[str, str], bytes]:
        captured.update(
            {
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )
        return 200, {}, json.dumps({"id": "email_123"}).encode()

    adapter = ResendEmailConfirmationAdapter(
        config=ResendEmailConfig(
            api_key="re_test_123",
            from_email="PhoenixGuard <billing@phoenixguard.example>",
            api_base_url="https://api.resend.test",
        ),
        http_post=fake_post,
    )

    result = adapter.send_payment_confirmation(
        customer_email="customer@example.test",
        customer_id="cus_active",
        plan_code="business",
        provider_subscription_id="sub_pg_active",
        license_ids=["lic_active"],
    )

    assert result == {"provider": "resend", "message_id": "email_123", "status": "sent"}
    assert captured["url"] == "https://api.resend.test/emails"
    assert captured["headers"]["Authorization"] == "Bearer re_test_123"
    assert captured["headers"]["Content-Type"] == "application/json"
    assert captured["headers"]["Idempotency-Key"].startswith("phoenixguard_cus_active_sub_pg_active_business")
    body = json.loads(captured["body"].decode("utf-8"))
    assert body["from"] == "PhoenixGuard <billing@phoenixguard.example>"
    assert body["to"] == ["customer@example.test"]
    assert body["subject"] == "PhoenixGuard payment confirmed"
    assert {"name": "template", "value": "payment_confirmed"} in body["tags"]
    assert "lic_active" in body["text"]


def test_access_is_not_activated_until_verified_payment_state_is_valid() -> None:
    secret = "whsec_activation_guard"
    repo = MockBusinessRepository.seeded()
    client = TestClient(create_business_app(repository=repo, stripe_webhook_secret=secret))

    checkout_event = {
        "id": "evt_checkout_unpaid",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_unpaid",
                "customer": "cus_stripe_expired",
                "subscription": "sub_pg_expired",
                "payment_status": "unpaid",
            }
        },
    }
    checkout_payload = _stripe_payload(checkout_event)
    checkout = client.post(
        "/v1/webhooks/stripe",
        data=checkout_payload,
        headers={"Stripe-Signature": _stripe_signature(checkout_payload, secret=secret)},
    )

    assert checkout.status_code == 200
    assert checkout.json()["action"] == "ignored_unsupported_event"
    assert client.get("/v1/licenses", headers=EXPIRED_AUTH).json()["licenses"][0]["status"] == "expired"

    paid_payload = _stripe_payload(_invoice_paid_event("evt_invoice_paid_expired"))
    paid = client.post(
        "/v1/webhooks/stripe",
        data=paid_payload,
        headers={"Stripe-Signature": _stripe_signature(paid_payload, secret=secret)},
    )

    assert paid.status_code == 200, paid.text
    assert paid.json()["license_status"] == "active"
    assert client.get("/v1/licenses", headers=EXPIRED_AUTH).json()["licenses"][0]["status"] == "active"


def test_license_state_is_not_activated_when_payment_confirmation_send_fails() -> None:
    class FailingEmailConfirmation:
        def send_payment_confirmation(self, **_: Any) -> dict[str, Any]:
            raise BillingConfigurationError("resend_api_key_required")

    repo = MockBusinessRepository.seeded()
    service = BillingService(
        repository=repo,
        stripe_verifier=StripeWebhookVerifier(secret="whsec_unused"),
        email_confirmation_sender=FailingEmailConfirmation(),
    )
    event = _invoice_paid_event("evt_invoice_paid_email_missing")

    with pytest.raises(BillingConfigurationError, match="resend_api_key_required"):
        service._apply_stripe_event(event_type=event["type"], event=event)

    expired_license = repo.get_license("lic_expired")
    assert expired_license.status == "expired"
