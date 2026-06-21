from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping


def _stable_id(prefix: str, value: str) -> str:
    return f"{prefix}_{uuid.uuid5(uuid.NAMESPACE_URL, f'phoenixguard:{value}').hex[:16]}"


def _hash_secret(value: str) -> str:
    return hashlib.sha256(str(value).strip().encode("utf-8")).hexdigest()


def _mask_account(value: str) -> str:
    cleaned = "".join(ch for ch in str(value) if ch.isdigit())
    if len(cleaned) <= 4:
        return f"***{cleaned}"
    return f"{cleaned[:2]}***{cleaned[-4:]}"


@dataclass
class Customer:
    id: str
    email: str
    full_name: str
    status: str = "active"
    is_admin: bool = False
    disclosure_accepted: bool = False


@dataclass
class Subscription:
    id: str
    customer_id: str
    provider_subscription_id: str
    plan_code: str
    status: str
    current_period_end_epoch: float
    cancel_at_period_end: bool = False


@dataclass
class License:
    id: str
    customer_id: str
    subscription_id: str
    license_key_hash: str
    license_key_hint: str
    plan_code: str
    status: str
    expires_at_epoch: float
    revoked_at_epoch: float | None = None
    revoke_reason: str = ""


@dataclass
class BrokerAccount:
    id: str
    customer_id: str
    broker_server_hash: str
    broker_server_label: str
    account_number_hash: str
    account_number_masked: str
    status: str = "active"
    created_at_epoch: float = field(default_factory=time.time)


@dataclass
class Device:
    id: str
    customer_id: str
    license_id: str
    device_fingerprint_hash: str
    device_label: str
    connector_version: str
    status: str = "active"
    connector_token: str = ""
    registered_at_epoch: float = field(default_factory=time.time)
    last_seen_at_epoch: float | None = None


@dataclass
class ReleaseBuild:
    id: str
    channel: str
    ea_version: str
    connector_version: str
    minimum_connector_version: str
    status: str = "published"
    published_at_epoch: float = field(default_factory=time.time)


class MockEmailProvider:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, Any]] = []

    def send(self, *, customer: Customer, template_key: str, metadata: Mapping[str, Any] | None = None) -> dict[str, Any]:
        message = {
            "provider": "mock-resend",
            "message_id": _stable_id("msg", f"{customer.id}:{template_key}:{time.time()}"),
            "customer_id": customer.id,
            "template_key": template_key,
            "status": "sent",
            "metadata": dict(metadata or {}),
        }
        self.sent_messages.append(message)
        return message


class MockBillingProvider:
    def verify_webhook_signature(self, *, payload: bytes, signature_header: str) -> bool:
        secret = os.getenv("PHOENIXGUARD_STRIPE_WEBHOOK_SECRET", "").strip()
        if not secret:
            return "mock-valid" in signature_header
        timestamp = ""
        candidate = ""
        for part in signature_header.split(","):
            key, _, value = part.partition("=")
            if key == "t":
                timestamp = value
            elif key == "v1":
                candidate = value
        if not timestamp or not candidate:
            return False
        expected = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode("utf-8") + payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, candidate)


class BusinessStore:
    def __init__(self) -> None:
        self.customers: dict[str, Customer] = {}
        self.tokens: dict[str, str] = {}
        self.subscriptions: dict[str, Subscription] = {}
        self.licenses: dict[str, License] = {}
        self.broker_accounts: dict[str, BrokerAccount] = {}
        self.devices: dict[str, Device] = {}
        self.connector_tokens: dict[str, str] = {}
        self.heartbeats: list[dict[str, Any]] = []
        self.audit_events: list[dict[str, Any]] = []
        self.email_provider = MockEmailProvider()
        self.billing_provider = MockBillingProvider()
        self.release = ReleaseBuild(
            id=_stable_id("rel", "stable-2026-06"),
            channel="stable",
            ea_version="2.0.0-mock",
            connector_version="2.0.0-mock",
            minimum_connector_version="2.0.0-mock",
        )
        self._seed()

    def _seed_customer(
        self,
        *,
        label: str,
        email: str,
        full_name: str,
        token: str,
        license_key: str,
        subscription_status: str,
        license_status: str,
        disclosure_accepted: bool,
        bound_account: bool,
        device_status: str = "active",
        is_admin: bool = False,
    ) -> None:
        now = time.time()
        customer = Customer(
            id=_stable_id("cus", label),
            email=email,
            full_name=full_name,
            is_admin=is_admin,
            disclosure_accepted=disclosure_accepted,
        )
        self.customers[customer.id] = customer
        self.tokens[token] = customer.id
        if is_admin:
            return
        subscription = Subscription(
            id=_stable_id("sub", label),
            customer_id=customer.id,
            provider_subscription_id=f"sub_mock_{label}",
            plan_code="hybrid-standard",
            status=subscription_status,
            current_period_end_epoch=now + (86400 * 30 if subscription_status == "active" else -86400),
        )
        self.subscriptions[subscription.id] = subscription
        license_record = License(
            id=_stable_id("lic", label),
            customer_id=customer.id,
            subscription_id=subscription.id,
            license_key_hash=_hash_secret(license_key),
            license_key_hint=license_key[-6:],
            plan_code=subscription.plan_code,
            status=license_status,
            expires_at_epoch=subscription.current_period_end_epoch,
            revoked_at_epoch=now if license_status == "revoked" else None,
            revoke_reason="Mock chargeback state." if license_status == "revoked" else "",
        )
        self.licenses[license_record.id] = license_record
        if bound_account:
            account = self.create_broker_account(
                customer_id=customer.id,
                broker_server="Mock-Demo-Server",
                mt4_account_number="8082026",
                label="Primary MT4 demo binding",
                status="active",
            )
            self.audit(
                actor_type="system",
                actor_id="seed",
                action="broker_account.seeded",
                target_type="broker_account",
                target_id=account.id,
            )
        device_id = _stable_id("dev", label)
        connector_token = f"connector-{label}"
        device = Device(
            id=device_id,
            customer_id=customer.id,
            license_id=license_record.id,
            device_fingerprint_hash=_hash_secret(f"mock-device-{label}"),
            device_label=f"{full_name} mock connector",
            connector_version="2.0.0-mock",
            status=device_status,
            connector_token=connector_token,
            last_seen_at_epoch=now,
        )
        self.devices[device.id] = device
        self.connector_tokens[connector_token] = device.id

    def _seed(self) -> None:
        self._seed_customer(
            label="active",
            email="operator@808fx.mock",
            full_name="808Fx Mock Operator",
            token="mock-customer-active",
            license_key="PG-MOCK-ACTIVE",
            subscription_status="active",
            license_status="active",
            disclosure_accepted=True,
            bound_account=True,
        )
        self._seed_customer(
            label="expired",
            email="expired@808fx.mock",
            full_name="Expired Mock Customer",
            token="mock-customer-expired",
            license_key="PG-MOCK-EXPIRED",
            subscription_status="past_due",
            license_status="expired",
            disclosure_accepted=True,
            bound_account=True,
        )
        self._seed_customer(
            label="revoked",
            email="revoked@808fx.mock",
            full_name="Revoked Mock Customer",
            token="mock-customer-revoked",
            license_key="PG-MOCK-REVOKED",
            subscription_status="canceled",
            license_status="revoked",
            disclosure_accepted=True,
            bound_account=True,
            device_status="revoked",
        )
        self._seed_customer(
            label="unbound",
            email="unbound@808fx.mock",
            full_name="Unbound Mock Customer",
            token="mock-customer-unbound",
            license_key="PG-MOCK-UNBOUND",
            subscription_status="active",
            license_status="active",
            disclosure_accepted=True,
            bound_account=False,
        )
        self._seed_customer(
            label="admin",
            email="admin@808fx.mock",
            full_name="PhoenixGuard Admin",
            token="mock-admin",
            license_key="",
            subscription_status="active",
            license_status="active",
            disclosure_accepted=True,
            bound_account=False,
            is_admin=True,
        )

    def reset(self) -> None:
        self.__init__()

    def audit(
        self,
        *,
        actor_type: str,
        actor_id: str,
        action: str,
        target_type: str = "",
        target_id: str = "",
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.audit_events.append(
            {
                "actor_type": actor_type,
                "actor_id": actor_id,
                "action": action,
                "target_type": target_type,
                "target_id": target_id,
                "metadata": dict(metadata or {}),
                "created_at_epoch": time.time(),
            }
        )

    def customer_for_token(self, authorization: str | None) -> Customer | None:
        token = str(authorization or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        customer_id = self.tokens.get(token)
        if not customer_id:
            return None
        return self.customers.get(customer_id)

    def device_for_connector_token(self, authorization: str | None) -> Device | None:
        token = str(authorization or "").strip()
        if token.lower().startswith("bearer "):
            token = token[7:].strip()
        device_id = self.connector_tokens.get(token)
        if not device_id:
            return None
        return self.devices.get(device_id)

    def customer_licenses(self, customer_id: str) -> list[License]:
        return [license_record for license_record in self.licenses.values() if license_record.customer_id == customer_id]

    def customer_accounts(self, customer_id: str) -> list[BrokerAccount]:
        return [account for account in self.broker_accounts.values() if account.customer_id == customer_id]

    def account_bound_for_license(self, license_record: License) -> bool:
        return any(account.customer_id == license_record.customer_id and account.status == "active" for account in self.broker_accounts.values())

    def create_broker_account(
        self,
        *,
        customer_id: str,
        broker_server: str,
        mt4_account_number: str,
        label: str = "",
        status: str = "active",
    ) -> BrokerAccount:
        server_label = str(label or broker_server or "MT4 account").strip()
        key = f"{customer_id}:{broker_server}:{mt4_account_number}"
        account = BrokerAccount(
            id=_stable_id("acct", key),
            customer_id=customer_id,
            broker_server_hash=_hash_secret(broker_server),
            broker_server_label=server_label,
            account_number_hash=_hash_secret(mt4_account_number),
            account_number_masked=_mask_account(mt4_account_number),
            status=status,
        )
        self.broker_accounts[account.id] = account
        self.audit(
            actor_type="customer",
            actor_id=customer_id,
            action="broker_account.bound",
            target_type="broker_account",
            target_id=account.id,
        )
        return account

    def find_license_by_key(self, license_key: str) -> License | None:
        license_key_hash = _hash_secret(license_key)
        for license_record in self.licenses.values():
            if hmac.compare_digest(license_record.license_key_hash, license_key_hash):
                return license_record
        return None

    def register_device(
        self,
        *,
        license_key: str,
        device_fingerprint: str,
        device_label: str,
        connector_version: str,
    ) -> tuple[Device, License]:
        license_record = self.find_license_by_key(license_key)
        if license_record is None:
            raise KeyError("License key not found.")
        device_hash = _hash_secret(device_fingerprint)
        for device in self.devices.values():
            if device.license_id == license_record.id and device.device_fingerprint_hash == device_hash:
                return device, license_record
        device_id = _stable_id("dev", f"{license_record.id}:{device_hash}")
        token = f"connector-{device_id[-10:]}"
        device = Device(
            id=device_id,
            customer_id=license_record.customer_id,
            license_id=license_record.id,
            device_fingerprint_hash=device_hash,
            device_label=str(device_label or "MT4 connector").strip(),
            connector_version=str(connector_version or "unknown").strip(),
            connector_token=token,
            last_seen_at_epoch=time.time(),
        )
        self.devices[device.id] = device
        self.connector_tokens[token] = device.id
        self.audit(
            actor_type="connector",
            actor_id=device.id,
            action="device.registered",
            target_type="device",
            target_id=device.id,
        )
        return device, license_record

    def record_heartbeat(self, *, device: Device, payload: Mapping[str, Any] | None = None) -> None:
        device.last_seen_at_epoch = time.time()
        heartbeat = {
            "license_id": device.license_id,
            "device_id": device.id,
            "connector_version": device.connector_version,
            "status": device.status,
            "detail": str((payload or {}).get("detail") or "mock heartbeat accepted"),
            "created_at_epoch": device.last_seen_at_epoch,
        }
        self.heartbeats.append(heartbeat)
        self.audit(
            actor_type="connector",
            actor_id=device.id,
            action="device.heartbeat",
            target_type="device",
            target_id=device.id,
            metadata={"status": device.status},
        )

    def entitlement_for_device(self, device: Device) -> dict[str, Any]:
        license_record = self.licenses[device.license_id]
        customer = self.customers[device.customer_id]
        subscription = self.subscriptions.get(license_record.subscription_id)
        reason = ""
        status = license_record.status
        if device.status != "active":
            status = "revoked"
            reason = "Device revoked."
        elif not customer.disclosure_accepted:
            status = "grace"
            reason = "Risk disclosure acceptance required before executable commands."
        elif not self.account_bound_for_license(license_record):
            status = "active"
            reason = "Broker account binding required before executable commands."
        elif subscription and subscription.status not in {"active", "trialing"}:
            status = "expired"
            reason = f"Subscription status is {subscription.status}."
        elif license_record.status == "revoked":
            reason = license_record.revoke_reason or "License revoked."
        elif license_record.status == "expired":
            reason = "License expired."
        return {
            "status": status,
            "license_id": license_record.id,
            "plan_code": license_record.plan_code,
            "expires_at_epoch": license_record.expires_at_epoch,
            "reason": reason,
            "device_id": device.id,
            "account_bound": self.account_bound_for_license(license_record),
            "disclosure_accepted": customer.disclosure_accepted,
            "subscription_status": subscription.status if subscription else "missing",
        }

    def current_mock_packet(self, *, device: Device) -> dict[str, Any] | None:
        entitlement = self.entitlement_for_device(device)
        if entitlement["status"] not in {"active", "trialing", "grace"}:
            return None
        if not entitlement["account_bound"] or not entitlement["disclosure_accepted"]:
            return None
        now = time.time()
        return {
            "packet_id": f"mock-packet-{device.id[-8:]}",
            "stream_sequence": int(now // 5),
            "side": "BUY",
            "symbol": "EURUSD",
            "timeframe": "M1",
            "confidence": 0.78,
            "expiry_seconds": 60,
            "valid_until_epoch": now + 8.0,
        }

    def apply_stripe_event(self, event: Mapping[str, Any]) -> dict[str, Any]:
        event_type = str(event.get("type") or "").strip()
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        obj = data.get("object") if isinstance(data.get("object"), Mapping) else {}
        provider_subscription_id = str(obj.get("id") or obj.get("subscription") or "sub_mock_active")
        matched_subscription = None
        for subscription in self.subscriptions.values():
            if subscription.provider_subscription_id == provider_subscription_id:
                matched_subscription = subscription
                break
        if matched_subscription is None:
            matched_subscription = self.subscriptions.get(_stable_id("sub", "active"))
        if matched_subscription is None:
            return {"status": "ignored", "reason": "subscription not found"}
        if event_type in {"customer.subscription.updated", "checkout.session.completed", "invoice.payment_succeeded"}:
            matched_subscription.status = "active"
        elif event_type in {"invoice.payment_failed"}:
            matched_subscription.status = "past_due"
        elif event_type in {"customer.subscription.deleted", "charge.dispute.created"}:
            matched_subscription.status = "canceled"
            for license_record in self.customer_licenses(matched_subscription.customer_id):
                license_record.status = "revoked" if event_type == "charge.dispute.created" else "expired"
                license_record.revoke_reason = "Chargeback event received." if event_type == "charge.dispute.created" else ""
        self.audit(
            actor_type="provider",
            actor_id="stripe",
            action=f"stripe.{event_type or 'unknown'}",
            target_type="subscription",
            target_id=matched_subscription.id,
            metadata={"provider_subscription_id": provider_subscription_id},
        )
        return {"status": "accepted", "subscription_status": matched_subscription.status}

    def snapshot_customer(self, customer: Customer) -> dict[str, Any]:
        licenses = [self.license_payload(item) for item in self.customer_licenses(customer.id)]
        accounts = [asdict(item) for item in self.customer_accounts(customer.id)]
        devices = [asdict(item) for item in self.devices.values() if item.customer_id == customer.id]
        for device in devices:
            device.pop("connector_token", None)
            device.pop("device_fingerprint_hash", None)
        return {
            "customer": asdict(customer),
            "licenses": licenses,
            "broker_accounts": accounts,
            "devices": devices,
            "release": self.release_payload(),
        }

    def license_payload(self, license_record: License) -> dict[str, Any]:
        payload = asdict(license_record)
        payload.pop("license_key_hash", None)
        return payload

    def release_payload(self) -> dict[str, Any]:
        manifest = asdict(self.release)
        manifest.update(
            {
                "download_url": f"https://downloads.phoenixguard.mock/{self.release.channel}/PhoenixGuard_MT4_Executioner.ex4?signature=mock",
                "sha256_manifest": hashlib.sha256(json.dumps(asdict(self.release), sort_keys=True).encode("utf-8")).hexdigest(),
                "email_provider": "mock-resend",
            }
        )
        return manifest


_BUSINESS_STORE: BusinessStore | None = None


def get_business_store() -> BusinessStore:
    global _BUSINESS_STORE
    if _BUSINESS_STORE is None:
        _BUSINESS_STORE = BusinessStore()
    return _BUSINESS_STORE
