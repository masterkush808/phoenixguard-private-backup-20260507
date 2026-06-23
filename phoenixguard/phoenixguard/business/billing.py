from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
import os
import time
from typing import Any, Callable, Mapping, Protocol
from urllib import error, parse, request

from .auth import MOCK_STRIPE_WEBHOOK_SECRET
from .repository import MockBusinessRepository, UTC


class BillingConfigurationError(Exception):
    """Raised when a real billing provider is not configured."""


class BillingProviderError(Exception):
    """Raised when a billing provider request fails."""


class BillingWebhookError(Exception):
    """Base billing webhook error."""


class BillingSignatureError(BillingWebhookError):
    """Raised when a billing webhook signature is missing or invalid."""


class BillingPayloadError(BillingWebhookError):
    """Raised when a billing webhook body cannot be handled."""


class HttpPost(Protocol):
    def __call__(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> tuple[int, Mapping[str, str], bytes]:
        ...


@dataclass(frozen=True, slots=True)
class StripeCheckoutSessionConfig:
    secret_key: str
    price_id: str
    success_url: str
    cancel_url: str
    mode: str = "subscription"
    quantity: int = 1
    api_base_url: str = "https://api.stripe.com"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "StripeCheckoutSessionConfig":
        values = env if env is not None else os.environ
        mode = _env_first(values, "STRIPE_CHECKOUT_MODE", "PHOENIXGUARD_STRIPE_CHECKOUT_MODE") or "subscription"
        if mode not in {"payment", "subscription"}:
            raise BillingConfigurationError("stripe_checkout_mode_invalid")
        quantity_raw = _env_first(values, "STRIPE_CHECKOUT_QUANTITY", "PHOENIXGUARD_STRIPE_CHECKOUT_QUANTITY") or "1"
        try:
            quantity = int(quantity_raw)
        except ValueError as exc:
            raise BillingConfigurationError("stripe_checkout_quantity_invalid") from exc
        if quantity < 1:
            raise BillingConfigurationError("stripe_checkout_quantity_invalid")
        return cls(
            secret_key=_required_env(values, "STRIPE_SECRET_KEY", "PHOENIXGUARD_STRIPE_SECRET_KEY"),
            price_id=_required_env(values, "STRIPE_PRICE_ID", "PHOENIXGUARD_STRIPE_PRICE_ID"),
            success_url=_required_env(
                values,
                "STRIPE_CHECKOUT_SUCCESS_URL",
                "PHOENIXGUARD_STRIPE_CHECKOUT_SUCCESS_URL",
                "PHOENIXGUARD_CHECKOUT_SUCCESS_URL",
            ),
            cancel_url=_required_env(
                values,
                "STRIPE_CHECKOUT_CANCEL_URL",
                "PHOENIXGUARD_STRIPE_CHECKOUT_CANCEL_URL",
                "PHOENIXGUARD_CHECKOUT_CANCEL_URL",
            ),
            mode=mode,
            quantity=quantity,
            api_base_url=(
                _env_first(values, "STRIPE_API_BASE_URL", "PHOENIXGUARD_STRIPE_API_BASE_URL")
                or "https://api.stripe.com"
            ).rstrip("/"),
        )


class StripeCheckoutSessionClient:
    """Creates Stripe-hosted Checkout Sessions without collecting card data."""

    def __init__(
        self,
        *,
        config: StripeCheckoutSessionConfig,
        http_post: HttpPost | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._config = config
        self._http_post = http_post or _urllib_http_post
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        http_post: HttpPost | None = None,
        timeout_seconds: float = 10.0,
    ) -> "StripeCheckoutSessionClient":
        return cls(
            config=StripeCheckoutSessionConfig.from_env(env),
            http_post=http_post,
            timeout_seconds=timeout_seconds,
        )

    def create_session(
        self,
        *,
        customer_email: str,
        customer_id: str | None = None,
        plan_code: str = "business",
        metadata: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        email = str(customer_email or "").strip()
        if not email:
            raise BillingPayloadError("checkout_customer_email_required")
        customer_reference = str(customer_id or "").strip()
        checkout_metadata: dict[str, str] = {
            "plan_code": str(plan_code or "business").strip() or "business",
        }
        if customer_reference:
            checkout_metadata["customer_id"] = customer_reference
        for key, value in (metadata or {}).items():
            normalized_key = str(key or "").strip()
            normalized_value = str(value or "").strip()
            if normalized_key and normalized_value:
                checkout_metadata[normalized_key] = normalized_value

        form_fields: list[tuple[str, str]] = [
            ("mode", self._config.mode),
            ("success_url", self._config.success_url),
            ("cancel_url", self._config.cancel_url),
            ("customer_email", email),
            ("line_items[0][price]", self._config.price_id),
            ("line_items[0][quantity]", str(self._config.quantity)),
        ]
        if customer_reference:
            form_fields.append(("client_reference_id", customer_reference))
        for key, value in sorted(checkout_metadata.items()):
            form_fields.append((f"metadata[{key}]", value))
            if self._config.mode == "subscription":
                form_fields.append((f"subscription_data[metadata][{key}]", value))

        body = parse.urlencode(form_fields).encode("utf-8")
        auth = base64.b64encode(f"{self._config.secret_key}:".encode("utf-8")).decode("ascii")
        headers = {
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        status_code, _, response_body = self._http_post(
            url=f"{self._config.api_base_url}/v1/checkout/sessions",
            headers=headers,
            body=body,
            timeout_seconds=self._timeout_seconds,
        )
        if status_code < 200 or status_code >= 300:
            raise BillingProviderError(f"stripe_checkout_session_failed:{status_code}")
        try:
            response_payload = json.loads(response_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise BillingProviderError("stripe_checkout_session_invalid_json") from exc
        if not isinstance(response_payload, Mapping):
            raise BillingProviderError("stripe_checkout_session_invalid_response")
        session_id = str(response_payload.get("id") or "").strip()
        checkout_url = str(response_payload.get("url") or "").strip()
        if not session_id or not checkout_url:
            raise BillingProviderError("stripe_checkout_session_missing_id_or_url")
        return {
            "provider": "stripe",
            "checkout_session_id": session_id,
            "checkout_url": checkout_url,
            "mode": self._config.mode,
            "livemode": bool(response_payload.get("livemode", False)),
        }


class StripeWebhookVerifier:
    """Small Stripe-compatible HMAC verifier for webhook raw bodies."""

    def __init__(
        self,
        *,
        secret: str | None = MOCK_STRIPE_WEBHOOK_SECRET,
        tolerance_seconds: int | None = 300,
        now_epoch: Callable[[], float] | None = None,
    ) -> None:
        self._secret = str(secret or "").strip()
        self._tolerance_seconds = tolerance_seconds
        self._now_epoch = now_epoch or time.time

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        tolerance_seconds: int | None = 300,
        now_epoch: Callable[[], float] | None = None,
    ) -> "StripeWebhookVerifier":
        values = env if env is not None else os.environ
        return cls(
            secret=_env_first(values, "STRIPE_WEBHOOK_SECRET", "PHOENIXGUARD_STRIPE_WEBHOOK_SECRET"),
            tolerance_seconds=tolerance_seconds,
            now_epoch=now_epoch,
        )

    def verify(self, *, payload: bytes, signature_header: str | None) -> None:
        if not self._secret:
            raise BillingSignatureError("stripe_webhook_secret_required")
        timestamp, signatures = self._parse_signature_header(signature_header)
        if self._tolerance_seconds is not None:
            now = int(self._now_epoch())
            if abs(now - timestamp) > self._tolerance_seconds:
                raise BillingSignatureError("stripe_signature_timestamp_out_of_tolerance")
        signed_payload = f"{timestamp}.".encode("utf-8") + payload
        expected = hmac.new(
            self._secret.encode("utf-8"),
            signed_payload,
            hashlib.sha256,
        ).hexdigest()
        if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
            raise BillingSignatureError("stripe_signature_invalid")

    def _parse_signature_header(self, signature_header: str | None) -> tuple[int, list[str]]:
        header = str(signature_header or "").strip()
        if not header:
            raise BillingSignatureError("stripe_signature_missing")
        fields: dict[str, list[str]] = {}
        for item in header.split(","):
            key, separator, value = item.strip().partition("=")
            if separator != "=":
                continue
            fields.setdefault(key, []).append(value)
        timestamps = fields.get("t") or []
        signatures = [value for value in fields.get("v1", []) if value]
        if not timestamps or not signatures:
            raise BillingSignatureError("stripe_signature_malformed")
        try:
            timestamp = int(timestamps[-1])
        except ValueError as exc:
            raise BillingSignatureError("stripe_signature_malformed") from exc
        return timestamp, signatures


class BillingService:
    """Billing provider webhook service backed by the mock repository."""

    def __init__(
        self,
        *,
        repository: MockBusinessRepository,
        stripe_verifier: StripeWebhookVerifier | None = None,
        email_confirmation_sender: Any | None = None,
    ) -> None:
        self._repository = repository
        self._stripe_verifier = stripe_verifier or StripeWebhookVerifier()
        self._email_confirmation_sender = email_confirmation_sender

    def handle_stripe_webhook(
        self,
        *,
        payload: bytes,
        signature_header: str | None,
        ip_address: str | None,
    ) -> dict[str, Any]:
        self._stripe_verifier.verify(payload=payload, signature_header=signature_header)
        try:
            event = json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise BillingPayloadError("stripe_payload_invalid_json") from exc
        if not isinstance(event, dict):
            raise BillingPayloadError("stripe_payload_invalid")
        event_id = str(event.get("id") or "").strip()
        event_type = str(event.get("type") or "").strip()
        if not event_id or not event_type:
            raise BillingPayloadError("stripe_event_missing_id_or_type")
        if not self._repository.mark_webhook_event_processed(event_id):
            return {
                "received": True,
                "event_id": event_id,
                "event_type": event_type,
                "action": "duplicate_ignored",
            }
        action_payload = self._apply_stripe_event(event_type=event_type, event=event)
        self._repository.append_audit_event(
            actor_type="stripe",
            actor_id=event_id,
            action=f"stripe.{event_type}",
            target_type=action_payload.get("target_type"),
            target_id=action_payload.get("target_id"),
            ip_address=ip_address,
            metadata=action_payload,
        )
        return {
            "received": True,
            "event_id": event_id,
            "event_type": event_type,
            **action_payload,
        }

    def _apply_stripe_event(self, *, event_type: str, event: Mapping[str, Any]) -> dict[str, Any]:
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        obj = data.get("object") if isinstance(data, Mapping) and isinstance(data.get("object"), Mapping) else {}
        stripe_object = dict(obj)
        if event_type in {
            "customer.subscription.created",
            "customer.subscription.updated",
            "customer.subscription.deleted",
        }:
            return self._apply_subscription_event(event_type=event_type, subscription=stripe_object)
        if event_type in {"invoice.paid", "invoice.payment_failed"}:
            paid = event_type == "invoice.paid"
            return self._apply_invoice_event(invoice=stripe_object, paid=paid)
        if event_type in {"charge.dispute.created", "radar.early_fraud_warning.created"}:
            return self._apply_revoke_event(event_type=event_type, stripe_object=stripe_object)
        return {
            "action": "ignored_unsupported_event",
            "target_type": "stripe_event",
            "target_id": event_type,
        }

    def _apply_subscription_event(
        self,
        *,
        event_type: str,
        subscription: Mapping[str, Any],
    ) -> dict[str, Any]:
        provider_customer_id = str(subscription.get("customer") or "").strip()
        provider_subscription_id = str(subscription.get("id") or "").strip()
        if not provider_customer_id or not provider_subscription_id:
            raise BillingPayloadError("stripe_subscription_missing_customer_or_id")
        stripe_status = str(subscription.get("status") or "active").strip()
        if event_type == "customer.subscription.deleted":
            stripe_status = "canceled"
        mapped_license_status = _license_status_from_subscription_status(stripe_status)
        subscription_record = self._repository.upsert_subscription_from_provider(
            provider="stripe",
            provider_customer_id=provider_customer_id,
            provider_subscription_id=provider_subscription_id,
            plan_code=_subscription_plan_code(subscription),
            status=stripe_status,
            current_period_start=_datetime_from_stripe_epoch(subscription.get("current_period_start")),
            current_period_end=_datetime_from_stripe_epoch(subscription.get("current_period_end")),
            cancel_at_period_end=bool(subscription.get("cancel_at_period_end", False)),
        )
        if subscription_record is None:
            return {
                "action": "ignored_unknown_customer",
                "target_type": "billing_customer",
                "target_id": provider_customer_id,
            }
        confirmation = self._send_payment_confirmation_if_required(
            provider_customer_id=provider_customer_id,
            provider_subscription_id=provider_subscription_id,
            subscription_id=subscription_record.id,
            plan_code=subscription_record.plan_code,
            license_status=mapped_license_status,
        )
        updated = self._repository.set_license_state_for_subscription(
            subscription_id=subscription_record.id,
            status=mapped_license_status,
            expires_at=subscription_record.current_period_end,
            revoke_reason="billing_subscription_deleted" if mapped_license_status == "revoked" else None,
        )
        payload: dict[str, Any] = {
            "action": "subscription_state_applied",
            "target_type": "subscription",
            "target_id": subscription_record.id,
            "provider_subscription_id": provider_subscription_id,
            "subscription_status": stripe_status,
            "license_status": mapped_license_status,
            "updated_license_ids": [license_record.id for license_record in updated],
        }
        if confirmation is not None:
            payload["email_confirmation"] = confirmation
        return payload

    def _apply_invoice_event(
        self,
        *,
        invoice: Mapping[str, Any],
        paid: bool,
    ) -> dict[str, Any]:
        provider_customer_id = str(invoice.get("customer") or "").strip()
        provider_subscription_id = _invoice_subscription_id(invoice)
        if not provider_customer_id or not provider_subscription_id:
            raise BillingPayloadError("stripe_invoice_missing_customer_or_subscription")
        status = "active" if paid else "past_due"
        subscription_record = self._repository.upsert_subscription_from_provider(
            provider="stripe",
            provider_customer_id=provider_customer_id,
            provider_subscription_id=provider_subscription_id,
            plan_code=_invoice_plan_code(invoice),
            status=status,
            current_period_start=_invoice_period_datetime(invoice, "start"),
            current_period_end=_invoice_period_datetime(invoice, "end"),
            cancel_at_period_end=False,
        )
        if subscription_record is None:
            return {
                "action": "ignored_unknown_customer",
                "target_type": "billing_customer",
                "target_id": provider_customer_id,
            }
        license_status = "active" if paid else "grace"
        confirmation = self._send_payment_confirmation_if_required(
            provider_customer_id=provider_customer_id,
            provider_subscription_id=provider_subscription_id,
            subscription_id=subscription_record.id,
            plan_code=subscription_record.plan_code,
            license_status=license_status,
        )
        updated = self._repository.set_license_state_for_subscription(
            subscription_id=subscription_record.id,
            status=license_status,
            expires_at=subscription_record.current_period_end,
        )
        payload: dict[str, Any] = {
            "action": "invoice_state_applied",
            "target_type": "subscription",
            "target_id": subscription_record.id,
            "provider_subscription_id": provider_subscription_id,
            "invoice_status": "paid" if paid else "payment_failed",
            "license_status": license_status,
            "updated_license_ids": [license_record.id for license_record in updated],
        }
        if confirmation is not None:
            payload["email_confirmation"] = confirmation
        return payload

    def _apply_revoke_event(
        self,
        *,
        event_type: str,
        stripe_object: Mapping[str, Any],
    ) -> dict[str, Any]:
        provider_customer_id = str(stripe_object.get("customer") or "").strip()
        if not provider_customer_id:
            raise BillingPayloadError("stripe_revoke_event_missing_customer")
        updated = self._repository.revoke_licenses_for_billing_customer(
            provider="stripe",
            provider_customer_id=provider_customer_id,
            reason=event_type,
        )
        return {
            "action": "licenses_revoked",
            "target_type": "billing_customer",
            "target_id": provider_customer_id,
            "updated_license_ids": [license_record.id for license_record in updated],
        }

    def _send_payment_confirmation_if_required(
        self,
        *,
        provider_customer_id: str,
        provider_subscription_id: str,
        subscription_id: str,
        plan_code: str,
        license_status: str,
    ) -> dict[str, Any] | None:
        if self._email_confirmation_sender is None or license_status not in {"active", "trialing"}:
            return None
        billing_customer = self._repository.find_billing_customer(
            provider="stripe",
            provider_customer_id=provider_customer_id,
        )
        if billing_customer is None:
            return None
        customer = self._repository.get_customer(billing_customer.customer_id)
        license_ids = [
            license_record.id
            for license_record in self._repository.list_licenses_for_customer(customer.id)
            if license_record.subscription_id == subscription_id
        ]
        send = getattr(self._email_confirmation_sender, "send_payment_confirmation", None)
        if send is None:
            raise BillingConfigurationError("email_confirmation_sender_invalid")
        message = send(
            customer_email=customer.email,
            customer_id=customer.id,
            plan_code=plan_code,
            provider_subscription_id=provider_subscription_id,
            license_ids=license_ids,
        )
        return dict(message or {})


def _license_status_from_subscription_status(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized in {"active"}:
        return "active"
    if normalized in {"trialing"}:
        return "trialing"
    if normalized in {"past_due", "unpaid", "incomplete"}:
        return "grace"
    if normalized in {"canceled", "incomplete_expired"}:
        return "expired"
    return "grace"


def _datetime_from_stripe_epoch(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=UTC)
    except (TypeError, ValueError):
        return None


def _subscription_plan_code(subscription: Mapping[str, Any]) -> str:
    metadata = subscription.get("metadata")
    if isinstance(metadata, Mapping) and metadata.get("plan_code"):
        return str(metadata["plan_code"])
    items = subscription.get("items")
    if isinstance(items, Mapping):
        data = items.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, Mapping):
                price = first.get("price")
                if isinstance(price, Mapping):
                    for key in ("lookup_key", "nickname"):
                        if price.get(key):
                            return str(price[key])
    return "business"


def _invoice_subscription_id(invoice: Mapping[str, Any]) -> str:
    direct = str(invoice.get("subscription") or "").strip()
    if direct:
        return direct
    parent = invoice.get("parent")
    if isinstance(parent, Mapping):
        details = parent.get("subscription_details")
        if isinstance(details, Mapping) and details.get("subscription"):
            return str(details["subscription"]).strip()
    return ""


def _invoice_plan_code(invoice: Mapping[str, Any]) -> str:
    metadata = invoice.get("metadata")
    if isinstance(metadata, Mapping) and metadata.get("plan_code"):
        return str(metadata["plan_code"])
    lines = invoice.get("lines")
    if isinstance(lines, Mapping):
        data = lines.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, Mapping):
                price = first.get("price")
                if isinstance(price, Mapping):
                    for key in ("lookup_key", "nickname"):
                        if price.get(key):
                            return str(price[key])
    return "business"


def _invoice_period_datetime(invoice: Mapping[str, Any], key: str) -> datetime | None:
    lines = invoice.get("lines")
    if not isinstance(lines, Mapping):
        return None
    data = lines.get("data")
    if not isinstance(data, list) or not data:
        return None
    first = data[0]
    if not isinstance(first, Mapping):
        return None
    period = first.get("period")
    if not isinstance(period, Mapping):
        return None
    return _datetime_from_stripe_epoch(period.get(key))


def _env_first(env: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = str(env.get(name, "") or "").strip()
        if value:
            return value
    return None


def _required_env(env: Mapping[str, str], *names: str) -> str:
    value = _env_first(env, *names)
    if value:
        return value
    raise BillingConfigurationError(f"{names[0].lower()}_required")


def _urllib_http_post(
    *,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout_seconds: float,
) -> tuple[int, Mapping[str, str], bytes]:
    http_request = request.Request(
        url=url,
        data=body,
        headers=dict(headers),
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            return int(response.status), dict(response.headers.items()), response.read()
    except error.HTTPError as exc:
        return int(exc.code), dict(exc.headers.items()), exc.read()
    except error.URLError as exc:
        raise BillingProviderError("stripe_checkout_session_request_failed") from exc
