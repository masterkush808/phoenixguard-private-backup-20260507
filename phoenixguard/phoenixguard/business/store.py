from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .packages import (
    DEFAULT_PAID_PLAN_CODE,
    FREE_PREVIEW_PLAN_CODE,
    package_profile_for_plan,
    package_catalog_payload,
    phoenix_guard_settings_for_plan,
    runtime_policy_for_plan,
)


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
    email_verified: bool = False
    password_hash: str = ""
    created_at_epoch: float = field(default_factory=time.time)


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


PASSWORD_MIN_LENGTH = 15
PASSWORD_MAX_LENGTH = 128
EMAIL_VERIFICATION_TTL_SECONDS = 60 * 60
EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS = 60


class RateLimitExceeded(ValueError):
    def __init__(self, *, retry_after_seconds: int) -> None:
        super().__init__("rate_limited")
        self.retry_after_seconds = max(1, int(retry_after_seconds))


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
        try:
            event_time = int(timestamp)
        except ValueError:
            return False
        if abs(int(time.time()) - event_time) > 300:
            return False
        expected = hmac.new(secret.encode("utf-8"), f"{timestamp}.".encode("utf-8") + payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, candidate)


class BusinessStore:
    def __init__(self) -> None:
        self.customers: dict[str, Customer] = {}
        self.tokens: dict[str, str] = {}
        self.subscriptions: dict[str, Subscription] = {}
        self.licenses: dict[str, License] = {}
        self.license_keys: dict[str, str] = {}
        self.broker_accounts: dict[str, BrokerAccount] = {}
        self.devices: dict[str, Device] = {}
        self.connector_tokens: dict[str, str] = {}
        self.email_verification_tokens: dict[str, str] = {}
        self.email_verification_issued_at: dict[str, float] = {}
        self.email_verification_last_sent_at: dict[str, float] = {}
        self.checkout_sessions: dict[str, dict[str, Any]] = {}
        self.runtime_usage_seconds: dict[str, float] = {}
        self.processed_billing_event_ids: set[str] = set()
        self.rate_limit_events: dict[str, list[float]] = {}
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
        password: str | None = None,
    ) -> None:
        now = time.time()
        seeded_password = password or os.getenv("PHOENIXGUARD_BUSINESS_MOCK_PASSWORD", "mock-password-2026!")
        customer = Customer(
            id=_stable_id("cus", label),
            email=email,
            full_name=full_name,
            is_admin=is_admin,
            disclosure_accepted=disclosure_accepted,
            email_verified=True,
            password_hash=self._hash_password(seeded_password),
        )
        self.customers[customer.id] = customer
        self.tokens[token] = customer.id
        if is_admin:
            return
        subscription = Subscription(
            id=_stable_id("sub", label),
            customer_id=customer.id,
            provider_subscription_id=f"sub_mock_{label}",
            plan_code=DEFAULT_PAID_PLAN_CODE,
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
        self.license_keys[license_record.id] = license_key
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

    def _hash_password(self, password: str, *, salt: str | None = None) -> str:
        resolved_salt = salt or secrets.token_urlsafe(16)
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            str(password or "").encode("utf-8"),
            resolved_salt.encode("utf-8"),
            120_000,
        ).hex()
        return f"pbkdf2_sha256${resolved_salt}${digest}"

    def _verify_password(self, password: str, encoded: str) -> bool:
        if not encoded:
            return False
        try:
            algorithm, salt, digest = encoded.split("$", 2)
        except ValueError:
            return False
        if algorithm != "pbkdf2_sha256":
            return False
        expected = self._hash_password(password, salt=salt).split("$", 2)[2]
        return hmac.compare_digest(expected, digest)

    def check_rate_limit(self, *, scope: str, key: str, limit: int, window_seconds: int) -> None:
        resolved_key = f"{scope}:{str(key or 'unknown').strip().lower()}"
        now = time.time()
        window_start = now - max(1, int(window_seconds))
        events = [event for event in self.rate_limit_events.get(resolved_key, []) if event >= window_start]
        if len(events) >= limit:
            retry_after = int(max(1, (events[0] + max(1, int(window_seconds))) - now))
            self.rate_limit_events[resolved_key] = events
            raise RateLimitExceeded(retry_after_seconds=retry_after)
        events.append(now)
        self.rate_limit_events[resolved_key] = events

    def clear_rate_limit(self, *, scope: str, key: str) -> None:
        resolved_key = f"{scope}:{str(key or 'unknown').strip().lower()}"
        self.rate_limit_events.pop(resolved_key, None)

    def _validate_password(self, password: str) -> None:
        resolved = str(password or "")
        if len(resolved) < PASSWORD_MIN_LENGTH:
            raise ValueError(f"Password must be at least {PASSWORD_MIN_LENGTH} characters.")
        if len(resolved) > PASSWORD_MAX_LENGTH:
            raise ValueError(f"Password must be {PASSWORD_MAX_LENGTH} characters or fewer.")

    def _issue_email_verification_token(self, customer: Customer, *, force: bool = False) -> str:
        now = time.time()
        existing = self.email_verification_tokens.get(customer.id, "")
        issued_at = float(self.email_verification_issued_at.get(customer.id) or 0.0)
        if existing and not force and now - issued_at <= EMAIL_VERIFICATION_TTL_SECONDS:
            return existing
        verification_token = secrets.token_urlsafe(32)
        self.email_verification_tokens[customer.id] = verification_token
        self.email_verification_issued_at[customer.id] = now
        self.email_verification_last_sent_at[customer.id] = now
        return verification_token

    def resend_email_verification_token(self, *, email: str) -> tuple[Customer | None, str | None, int]:
        normalized_email = str(email or "").strip().lower()
        customer = next((item for item in self.customers.values() if item.email.lower() == normalized_email), None)
        if customer is None or customer.email_verified:
            return customer, None, 0
        now = time.time()
        last_sent = float(self.email_verification_last_sent_at.get(customer.id) or 0.0)
        wait = int(max(0, EMAIL_VERIFICATION_RESEND_COOLDOWN_SECONDS - (now - last_sent)))
        if wait > 0:
            return customer, None, wait
        token = self._issue_email_verification_token(customer, force=True)
        self.audit(
            actor_type="customer",
            actor_id=customer.id,
            action="auth.email_verification_resent",
            target_type="customer",
            target_id=customer.id,
        )
        return customer, token, 0

    def register_customer(self, *, email: str, full_name: str, password: str) -> tuple[Customer, str, str]:
        normalized_email = str(email or "").strip().lower()
        if len(normalized_email) > 254 or not normalized_email or "@" not in normalized_email or "." not in normalized_email.rsplit("@", 1)[-1]:
            raise ValueError("A valid email is required.")
        self._validate_password(password)
        existing = next((item for item in self.customers.values() if item.email.lower() == normalized_email), None)
        if existing is not None:
            raise ValueError("Registration could not be completed for this email.")
        customer = Customer(
            id=_stable_id("cus", normalized_email),
            email=normalized_email,
            full_name=str(full_name or normalized_email).strip()[:160],
            status="active",
            disclosure_accepted=False,
            email_verified=False,
            password_hash=self._hash_password(password),
        )
        self.customers[customer.id] = customer
        token = self.issue_customer_session_token(customer)
        verification_token = self._issue_email_verification_token(customer, force=True)
        self.audit(
            actor_type="customer",
            actor_id=customer.id,
            action="auth.registered",
            target_type="customer",
            target_id=customer.id,
        )
        return customer, token, verification_token

    def _token_for_customer(self, customer: Customer) -> str:
        existing = next((token for token, customer_id in self.tokens.items() if customer_id == customer.id), None)
        if existing:
            return existing
        token = f"customer-{secrets.token_urlsafe(24)}"
        self.tokens[token] = customer.id
        return token

    def issue_customer_session_token(self, customer: Customer) -> str:
        token = f"customer-{secrets.token_urlsafe(32)}"
        self.tokens[token] = customer.id
        return token

    def authenticate_customer(self, *, email: str, password: str | None = None) -> tuple[Customer, str] | None:
        normalized = str(email or "").strip().lower()
        customer = next((item for item in self.customers.values() if item.email.lower() == normalized), None)
        if customer is None:
            return None
        if not customer.password_hash or not self._verify_password(str(password or ""), customer.password_hash):
            return None
        return customer, self.issue_customer_session_token(customer)

    def verify_customer_email(self, *, token: str) -> Customer:
        normalized = str(token or "").strip()
        for customer_id, expected_token in list(self.email_verification_tokens.items()):
            if hmac.compare_digest(expected_token, normalized):
                issued_at = float(self.email_verification_issued_at.get(customer_id) or 0.0)
                if issued_at and time.time() - issued_at > EMAIL_VERIFICATION_TTL_SECONDS:
                    self.email_verification_tokens.pop(customer_id, None)
                    self.email_verification_issued_at.pop(customer_id, None)
                    self.audit(
                        actor_type="customer",
                        actor_id=customer_id,
                        action="auth.email_verification_expired",
                        target_type="customer",
                        target_id=customer_id,
                    )
                    raise KeyError("Email verification token expired.")
                customer = self.customers[customer_id]
                customer.email_verified = True
                self.email_verification_tokens.pop(customer_id, None)
                self.email_verification_issued_at.pop(customer_id, None)
                self.audit(
                    actor_type="customer",
                    actor_id=customer.id,
                    action="auth.email_verified",
                    target_type="customer",
                    target_id=customer.id,
                )
                return customer
        raise KeyError("Email verification token not found.")

    def record_checkout_session(self, *, customer: Customer, provider_payload: Mapping[str, Any]) -> dict[str, Any]:
        session_id = str(provider_payload.get("id") or _stable_id("checkout", f"{customer.id}:{time.time()}"))
        plan_code = str(provider_payload.get("plan_code") or DEFAULT_PAID_PLAN_CODE).strip()
        runtime_policy = runtime_policy_for_plan(plan_code)
        session = {
            "id": session_id,
            "customer_id": customer.id,
            "provider": str(provider_payload.get("provider") or "stripe"),
            "url": str(provider_payload.get("url") or ""),
            "status": str(provider_payload.get("status") or "created"),
            "plan_code": plan_code,
            "runtime_policy": runtime_policy,
            "package_profile": self.package_profile_payload(plan_code),
            "created_at_epoch": time.time(),
        }
        self.checkout_sessions[session_id] = session
        self.audit(
            actor_type="customer",
            actor_id=customer.id,
            action=str(provider_payload.get("audit_action") or "checkout.started"),
            target_type="checkout_session",
            target_id=session_id,
            metadata={
                "provider": session["provider"],
                "status": session["status"],
                "plan_code": plan_code,
                "runtime_policy": runtime_policy,
            },
        )
        return session

    def package_catalog(self) -> list[dict[str, Any]]:
        return package_catalog_payload()

    def package_profile_payload(self, plan_code: str | None) -> dict[str, Any]:
        try:
            return package_profile_for_plan(plan_code).public_payload()
        except KeyError:
            return {
                "code": str(plan_code or ""),
                "name": "Unsupported package",
                "tier": "blocked",
                "price_label": "",
                "billing_kind": "blocked",
                "self_service": False,
                "payment_required": True,
                "runtime_policy": runtime_policy_for_plan(plan_code),
                "phoenix_guard_settings": phoenix_guard_settings_for_plan(plan_code),
                "certification_level": "not-certified",
            }

    def stage_paid_package_selection(self, *, customer: Customer, plan_code: str) -> dict[str, Any]:
        profile = package_profile_for_plan(plan_code)
        session = self.record_checkout_session(
            customer=customer,
            provider_payload={
                "id": _stable_id("pkg", f"{customer.id}:{profile.code}:payment-paused"),
                "provider": "payment-paused",
                "status": "pending_payment_receiver",
                "plan_code": profile.code,
                "audit_action": "package.selection_paused",
            },
        )
        return {
            "checkout_url": "",
            "checkout_session_id": session["id"],
            "mode": "billing-paused",
            "provider": "payment-paused",
            "status": session["status"],
            "plan_code": profile.code,
            "package_profile": profile.public_payload(),
            "runtime_policy": profile.runtime_policy(),
            "message": (
                f"{profile.name} has been staged. Payment collection is paused until the payment receiver "
                "is connected, so no paid license is activated yet."
            ),
            "risk_warning": "Trading carries risk. PhoenixGuard does not guarantee profit and is not financial advice.",
        }

    def stage_review_package_selection(self, *, customer: Customer, plan_code: str) -> dict[str, Any]:
        profile = package_profile_for_plan(plan_code)
        session = self.record_checkout_session(
            customer=customer,
            provider_payload={
                "id": _stable_id("pkg", f"{customer.id}:{profile.code}:review"),
                "provider": "manual-review",
                "status": "pending_review",
                "plan_code": profile.code,
                "audit_action": "package.review_requested",
            },
        )
        return {
            "checkout_url": "",
            "checkout_session_id": session["id"],
            "mode": "manual-review",
            "provider": "manual-review",
            "status": session["status"],
            "plan_code": profile.code,
            "package_profile": profile.public_payload(),
            "runtime_policy": profile.runtime_policy(),
            "message": "Scale Review has been staged for manual approval before any rollout or paid license activation.",
            "risk_warning": "Trading carries risk. PhoenixGuard does not guarantee profit and is not financial advice.",
        }

    def grant_free_preview_license(self, *, customer: Customer) -> License:
        now = time.time()
        profile = package_profile_for_plan(FREE_PREVIEW_PLAN_CODE)
        existing_paid = next(
            (
                item
                for item in self.customer_licenses(customer.id)
                if item.plan_code != FREE_PREVIEW_PLAN_CODE
                and item.status in {"active", "trialing", "grace"}
                and float(item.expires_at_epoch or 0.0) > now
            ),
            None,
        )
        if existing_paid is not None:
            return existing_paid
        existing_preview = next(
            (
                item
                for item in self.customer_licenses(customer.id)
                if item.plan_code == FREE_PREVIEW_PLAN_CODE
                and item.status in {"active", "trialing", "grace"}
                and float(item.expires_at_epoch or 0.0) > now
            ),
            None,
        )
        if existing_preview is not None:
            return existing_preview

        provider_subscription_id = f"free_preview_{customer.id}"
        subscription = Subscription(
            id=_stable_id("sub", provider_subscription_id),
            customer_id=customer.id,
            provider_subscription_id=provider_subscription_id,
            plan_code=FREE_PREVIEW_PLAN_CODE,
            status=profile.subscription_status,
            current_period_end_epoch=now + 86400 * profile.license_duration_days,
        )
        self.subscriptions[subscription.id] = subscription
        license_key = f"PG-FREE-{secrets.token_hex(6).upper()}"
        license_record = License(
            id=_stable_id("lic", f"{customer.id}:{provider_subscription_id}"),
            customer_id=customer.id,
            subscription_id=subscription.id,
            license_key_hash=_hash_secret(license_key),
            license_key_hint=license_key[-6:],
            plan_code=subscription.plan_code,
            status=profile.license_status,
            expires_at_epoch=subscription.current_period_end_epoch,
        )
        self.licenses[license_record.id] = license_record
        self.license_keys[license_record.id] = license_key
        self.audit(
            actor_type="customer",
            actor_id=customer.id,
            action="license.free_preview_activated",
            target_type="license",
            target_id=license_record.id,
            metadata=profile.public_payload(),
        )
        return license_record

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

    def preferred_active_license(self, customer_id: str, *, now_epoch: float | None = None) -> License | None:
        current = float(now_epoch if now_epoch is not None else time.time())
        eligible = [
            license_record
            for license_record in self.customer_licenses(customer_id)
            if license_record.status in {"active", "trialing", "grace"}
            and float(license_record.expires_at_epoch or 0.0) > current
        ]
        if not eligible:
            return None
        return max(
            eligible,
            key=lambda license_record: (
                int(package_profile_for_plan(license_record.plan_code).payment_required),
                float(license_record.expires_at_epoch or 0.0),
                license_record.id,
            ),
        )

    def customer_accounts(self, customer_id: str) -> list[BrokerAccount]:
        return [account for account in self.broker_accounts.values() if account.customer_id == customer_id]

    def account_bound_for_license(self, license_record: License) -> bool:
        return any(account.customer_id == license_record.customer_id and account.status == "active" for account in self.broker_accounts.values())

    def active_devices_for_license(self, license_id: str) -> list[Device]:
        return [
            device
            for device in self.devices.values()
            if device.license_id == license_id and device.status == "active"
        ]

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
        account_id = _stable_id("acct", key)
        active_license = self.preferred_active_license(customer_id)
        if active_license is not None:
            profile = package_profile_for_plan(active_license.plan_code)
            active_accounts = [
                account
                for account in self.broker_accounts.values()
                if account.customer_id == customer_id and account.status == "active"
            ]
            if account_id not in self.broker_accounts and len(active_accounts) >= profile.max_broker_accounts:
                raise ValueError("Broker account limit reached for this package.")
        account = BrokerAccount(
            id=account_id,
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
        profile = package_profile_for_plan(license_record.plan_code)
        device_hash = _hash_secret(device_fingerprint)
        for device in self.devices.values():
            if device.license_id == license_record.id and device.device_fingerprint_hash == device_hash:
                return device, license_record
        active_devices = self.active_devices_for_license(license_record.id)
        if len(active_devices) >= profile.max_devices:
            raise ValueError("Device limit reached for this package.")
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
            metadata={"plan_code": license_record.plan_code, "package_profile": profile.code},
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

    def runtime_state_for_license(self, license_record: License, *, now_epoch: float | None = None) -> dict[str, Any]:
        current = float(now_epoch if now_epoch is not None else time.time())
        policy = runtime_policy_for_plan(license_record.plan_code)
        limit_seconds = int(policy.get("daily_runtime_seconds") or 0)
        used_seconds = float(self.runtime_usage_seconds.get(license_record.id, 0.0))
        remaining_seconds = max(0.0, float(limit_seconds) - used_seconds) if limit_seconds else 0.0
        return {
            "date_epoch": int(current // 86400) * 86400,
            "limit_seconds": limit_seconds,
            "used_seconds": used_seconds,
            "remaining_seconds": remaining_seconds,
            "available": bool(limit_seconds and remaining_seconds > 0),
            "policy": policy,
        }

    def runtime_available_for_license(self, license_record: License, *, now_epoch: float | None = None) -> bool:
        return bool(self.runtime_state_for_license(license_record, now_epoch=now_epoch)["available"])

    def device_is_fresh_for_license(
        self,
        device: Device,
        license_record: License,
        *,
        now_epoch: float | None = None,
    ) -> bool:
        profile = package_profile_for_plan(license_record.plan_code)
        if profile.heartbeat_freshness_seconds <= 0:
            return False
        current = float(now_epoch if now_epoch is not None else time.time())
        last_seen = float(device.last_seen_at_epoch or device.registered_at_epoch or 0.0)
        return current - last_seen <= profile.heartbeat_freshness_seconds

    def package_certification_for_license(self, license_record: License) -> dict[str, Any]:
        profile = package_profile_for_plan(license_record.plan_code)
        active = (
            license_record.status in {"active", "trialing", "grace"}
            and float(license_record.expires_at_epoch or 0.0) > time.time()
        )
        return {
            "certification_id": _stable_id("cert", license_record.id),
            "status": "certified" if active else "not_certified",
            "level": profile.certification_level,
            "plan_code": profile.code,
            "package_name": profile.name,
            "runtime_policy": profile.runtime_policy(),
            "phoenix_guard_settings": profile.phoenix_guard_settings(),
            "issued_at_epoch": license_record.expires_at_epoch - (profile.license_duration_days * 86400)
            if profile.license_duration_days
            else None,
            "expires_at_epoch": license_record.expires_at_epoch,
        }

    def entitlement_for_device(self, device: Device) -> dict[str, Any]:
        now = time.time()
        license_record = self.licenses[device.license_id]
        customer = self.customers[device.customer_id]
        subscription = self.subscriptions.get(license_record.subscription_id)
        runtime_state = self.runtime_state_for_license(license_record, now_epoch=now)
        device_fresh = self.device_is_fresh_for_license(device, license_record, now_epoch=now)
        reason = ""
        status = license_record.status
        if device.status != "active":
            status = "revoked"
            reason = "Device revoked."
        elif not customer.email_verified:
            status = "grace"
            reason = "Email verification required before executable commands."
        elif not customer.disclosure_accepted:
            status = "grace"
            reason = "Risk disclosure acceptance required before executable commands."
        elif not self.account_bound_for_license(license_record):
            status = "active"
            reason = "Broker account binding required before executable commands."
        elif not runtime_state["available"]:
            status = "grace"
            reason = "Daily runtime limit reached for this package."
        elif not device_fresh:
            status = "grace"
            reason = "Fresh device heartbeat required before command delivery."
        elif float(license_record.expires_at_epoch or 0.0) <= now:
            status = "expired"
            reason = "License period has ended."
        elif subscription and float(subscription.current_period_end_epoch or 0.0) <= now:
            status = "expired"
            reason = "Subscription period has ended."
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
            "runtime_policy": runtime_policy_for_plan(license_record.plan_code),
            "runtime_state": runtime_state,
            "package_certification": self.package_certification_for_license(license_record),
            "phoenix_guard_settings": phoenix_guard_settings_for_plan(license_record.plan_code),
            "reason": reason,
            "device_id": device.id,
            "account_bound": self.account_bound_for_license(license_record),
            "disclosure_accepted": customer.disclosure_accepted,
            "email_verified": customer.email_verified,
            "device_fresh": device_fresh,
            "subscription_status": subscription.status if subscription else "missing",
        }

    def access_gates_for_customer(self, customer: Customer) -> dict[str, Any]:
        now = time.time()
        active_license = self.preferred_active_license(customer.id, now_epoch=now)
        subscription = self.subscriptions.get(active_license.subscription_id) if active_license else None
        devices = [device for device in self.devices.values() if device.customer_id == customer.id and device.status == "active"]
        fresh_devices = [
            device
            for device in devices
            if active_license is not None and self.device_is_fresh_for_license(device, active_license, now_epoch=now)
        ]
        broker_bound = bool(active_license and self.account_bound_for_license(active_license))
        runtime_available = bool(active_license and self.runtime_available_for_license(active_license, now_epoch=now))
        gates = {
            "registered": True,
            "email_verified": customer.email_verified,
            "subscription_active": bool(
                subscription
                and subscription.status in {"active", "trialing"}
                and float(subscription.current_period_end_epoch or 0.0) > now
            ),
            "license_active": bool(active_license and active_license.status in {"active", "trialing", "grace"}),
            "runtime_available": runtime_available,
            "disclosure_accepted": customer.disclosure_accepted,
            "broker_bound": broker_bound,
            "device_registered": bool(fresh_devices),
        }
        allowed = all(gates.values())
        blocked_reasons = [key for key, value in gates.items() if not value]
        return {
            "allowed": allowed,
            "blocked_reasons": blocked_reasons,
            "gates": gates,
            "license_id": active_license.id if active_license else "",
            "selected_plan_code": active_license.plan_code if active_license else "",
            "runtime_state": self.runtime_state_for_license(active_license, now_epoch=now) if active_license else None,
            "package_certification": self.package_certification_for_license(active_license) if active_license else None,
            "next_action": blocked_reasons[0] if blocked_reasons else "open_tracker",
        }

    def current_mock_packet(self, *, device: Device) -> dict[str, Any] | None:
        entitlement = self.entitlement_for_device(device)
        if entitlement["status"] not in {"active", "trialing"}:
            return None
        if not entitlement["account_bound"] or not entitlement["disclosure_accepted"] or not entitlement.get("email_verified", False):
            return None
        if not entitlement.get("device_fresh", False) or not entitlement.get("runtime_state", {}).get("available", False):
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
        event_id = str(event.get("id") or "").strip()
        if event_id:
            if event_id in self.processed_billing_event_ids:
                return {"status": "ignored", "reason": "duplicate_billing_event"}
            self.processed_billing_event_ids.add(event_id)
        event_type = str(event.get("type") or "").strip()
        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        obj = data.get("object") if isinstance(data.get("object"), Mapping) else {}
        provider_subscription_id = str(obj.get("id") or obj.get("subscription") or "")
        metadata = obj.get("metadata") if isinstance(obj.get("metadata"), Mapping) else {}
        customer_id = str(obj.get("client_reference_id") or metadata.get("customer_id") or "")
        if event_type == "checkout.session.completed" and customer_id and customer_id in self.customers:
            now = time.time()
            provider_subscription_id = provider_subscription_id or str(obj.get("subscription") or f"sub_stripe_{customer_id}")
            profile = package_profile_for_plan(str(metadata.get("plan_code") or DEFAULT_PAID_PLAN_CODE))
            subscription = Subscription(
                id=_stable_id("sub", provider_subscription_id),
                customer_id=customer_id,
                provider_subscription_id=provider_subscription_id,
                plan_code=profile.code,
                status=profile.subscription_status,
                current_period_end_epoch=now + 86400 * profile.license_duration_days,
            )
            self.subscriptions[subscription.id] = subscription
            license_key = f"PG-{secrets.token_hex(8).upper()}"
            license_record = License(
                id=_stable_id("lic", f"{customer_id}:{provider_subscription_id}"),
                customer_id=customer_id,
                subscription_id=subscription.id,
                license_key_hash=_hash_secret(license_key),
                license_key_hint=license_key[-6:],
                plan_code=subscription.plan_code,
                status=profile.license_status,
                expires_at_epoch=subscription.current_period_end_epoch,
            )
            self.licenses[license_record.id] = license_record
            self.license_keys[license_record.id] = license_key
            return {
                "status": "accepted",
                "subscription_status": subscription.status,
                "license_id": license_record.id,
                "plan_code": profile.code,
                "runtime_policy": profile.runtime_policy(),
            }
        provider_subscription_id = provider_subscription_id or "sub_mock_active"
        matched_subscription = None
        for subscription in self.subscriptions.values():
            if subscription.provider_subscription_id == provider_subscription_id:
                matched_subscription = subscription
                break
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
        onboarding = self.access_gates_for_customer(customer)
        release = self.release_payload()
        if not onboarding["allowed"]:
            release.pop("download_url", None)
        return {
            "customer": asdict(customer),
            "licenses": licenses,
            "broker_accounts": accounts,
            "devices": devices,
            "release": release,
            "onboarding": onboarding,
            "package_catalog": self.package_catalog(),
            "package_selections": [
                dict(session)
                for session in self.checkout_sessions.values()
                if session.get("customer_id") == customer.id
            ],
        }

    def license_payload(self, license_record: License) -> dict[str, Any]:
        payload = asdict(license_record)
        payload.pop("license_key_hash", None)
        payload["license_key"] = self.license_keys.get(license_record.id, "")
        payload["runtime_policy"] = runtime_policy_for_plan(license_record.plan_code)
        payload["runtime_state"] = self.runtime_state_for_license(license_record)
        payload["package_profile"] = self.package_profile_payload(license_record.plan_code)
        payload["package_certification"] = self.package_certification_for_license(license_record)
        payload["phoenix_guard_settings"] = phoenix_guard_settings_for_plan(license_record.plan_code)
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
