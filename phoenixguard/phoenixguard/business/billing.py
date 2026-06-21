from __future__ import annotations

from datetime import datetime
import hashlib
import hmac
import json
import time
from typing import Any, Callable, Mapping

from .auth import MOCK_STRIPE_WEBHOOK_SECRET
from .repository import MockBusinessRepository, UTC


class BillingWebhookError(Exception):
    """Base billing webhook error."""


class BillingSignatureError(BillingWebhookError):
    """Raised when a billing webhook signature is missing or invalid."""


class BillingPayloadError(BillingWebhookError):
    """Raised when a billing webhook body cannot be handled."""


class StripeWebhookVerifier:
    """Small Stripe-compatible HMAC verifier for mock/test-mode webhooks."""

    def __init__(
        self,
        *,
        secret: str = MOCK_STRIPE_WEBHOOK_SECRET,
        tolerance_seconds: int | None = 300,
        now_epoch: Callable[[], float] | None = None,
    ) -> None:
        self._secret = secret
        self._tolerance_seconds = tolerance_seconds
        self._now_epoch = now_epoch or time.time

    def verify(self, *, payload: bytes, signature_header: str | None) -> None:
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
    ) -> None:
        self._repository = repository
        self._stripe_verifier = stripe_verifier or StripeWebhookVerifier()

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
        updated = self._repository.set_license_state_for_subscription(
            subscription_id=subscription_record.id,
            status=mapped_license_status,
            expires_at=subscription_record.current_period_end,
            revoke_reason="billing_subscription_deleted" if mapped_license_status == "revoked" else None,
        )
        return {
            "action": "subscription_state_applied",
            "target_type": "subscription",
            "target_id": subscription_record.id,
            "provider_subscription_id": provider_subscription_id,
            "subscription_status": stripe_status,
            "license_status": mapped_license_status,
            "updated_license_ids": [license_record.id for license_record in updated],
        }

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
        updated = self._repository.set_license_state_for_subscription(
            subscription_id=subscription_record.id,
            status=license_status,
            expires_at=subscription_record.current_period_end,
        )
        return {
            "action": "invoice_state_applied",
            "target_type": "subscription",
            "target_id": subscription_record.id,
            "provider_subscription_id": provider_subscription_id,
            "invoice_status": "paid" if paid else "payment_failed",
            "license_status": license_status,
            "updated_license_ids": [license_record.id for license_record in updated],
        }

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
