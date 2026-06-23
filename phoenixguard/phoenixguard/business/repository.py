from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import hashlib
import hmac
import json
import threading
from typing import Any, Mapping


UTC = timezone.utc
MOCK_NOW = datetime(2026, 6, 21, 0, 0, 0, tzinfo=UTC)
MOCK_DISCLOSURE_VERSION = "risk-2026-06"


class RepositoryError(Exception):
    """Base error for the mock business repository."""


class AuthorizationError(RepositoryError):
    """Raised when a principal does not own the requested object."""


class NotFoundError(RepositoryError):
    """Raised when a requested object cannot be found."""


class ConflictError(RepositoryError):
    """Raised when a requested write conflicts with existing state."""


def utc_datetime(
    year: int,
    month: int,
    day: int,
    hour: int = 0,
    minute: int = 0,
    second: int = 0,
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=UTC)


def iso_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def stable_hash(namespace: str, value: str) -> str:
    normalized = str(value or "").strip().lower()
    digest = hashlib.sha256(f"{namespace}:{normalized}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def secret_hash(namespace: str, value: str) -> str:
    digest = hashlib.sha256(f"{namespace}:{str(value or '').strip()}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def email_verification_token_hash(value: str) -> str:
    return secret_hash("email-verification-token", value)


def license_key_hash(value: str) -> str:
    normalized = str(value or "").strip().upper()
    digest = hashlib.sha256(f"license-key:{normalized}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def token_hash(value: str) -> str:
    digest = hashlib.sha256(f"connector-token:{value}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def deterministic_id(prefix: str, *parts: str, length: int = 16) -> str:
    digest = hashlib.sha256("|".join(str(part) for part in parts).encode("utf-8")).hexdigest()
    return f"{prefix}_{digest[:length]}"


def mask_account_number(value: str) -> str:
    compact = "".join(ch for ch in str(value or "").strip() if ch.isalnum())
    if not compact:
        return "****"
    return f"****{compact[-4:]}"


@dataclass(slots=True)
class Customer:
    id: str
    email: str
    full_name: str
    country_code: str | None
    phone: str | None
    status: str
    created_at: datetime
    updated_at: datetime
    email_verified_at: datetime | None = None


@dataclass(slots=True)
class EmailVerificationToken:
    id: str
    customer_id: str
    token_hash: str
    expires_at: datetime
    created_at: datetime
    consumed_at: datetime | None = None
    revoked_at: datetime | None = None


@dataclass(slots=True)
class RiskDisclosure:
    id: str
    version: str
    title: str
    content_hash: str
    effective_at: datetime
    retired_at: datetime | None = None


@dataclass(slots=True)
class RiskDisclosureAcceptance:
    id: str
    customer_id: str
    disclosure_id: str
    accepted_at: datetime
    ip_address: str | None
    user_agent: str | None
    license_id: str | None = None


@dataclass(slots=True)
class BillingCustomer:
    id: str
    customer_id: str
    provider: str
    provider_customer_id: str
    created_at: datetime


@dataclass(slots=True)
class Subscription:
    id: str
    customer_id: str
    provider: str
    provider_subscription_id: str
    plan_code: str
    status: str
    current_period_start: datetime | None
    current_period_end: datetime | None
    cancel_at_period_end: bool
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class LicenseRecord:
    id: str
    customer_id: str
    subscription_id: str | None
    license_key_hash: str
    plan_code: str
    status: str
    expires_at: datetime | None
    revoked_at: datetime | None
    revoke_reason: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(slots=True)
class Mt4Account:
    id: str
    customer_id: str
    broker_server_hash: str
    broker_server_label: str | None
    account_number_hash: str
    account_number_masked: str
    status: str
    created_at: datetime


@dataclass(slots=True)
class LicenseAccountBinding:
    id: str
    license_id: str
    mt4_account_id: str
    status: str
    created_at: datetime


@dataclass(slots=True)
class DeviceRecord:
    id: str
    customer_id: str
    license_id: str
    device_fingerprint_hash: str
    device_label: str | None
    connector_version: str
    status: str
    registered_at: datetime
    last_seen_at: datetime | None
    revoked_at: datetime | None
    connector_token_hash: str | None


@dataclass(slots=True)
class ReleaseBuild:
    id: str
    channel: str
    ea_version: str
    connector_version: str
    minimum_connector_version: str | None
    manifest_json: dict[str, Any]
    sha256_manifest: str
    status: str
    published_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


@dataclass(slots=True)
class EntitlementSnapshot:
    id: str
    license_id: str
    device_id: str | None
    status: str
    reason: str | None
    snapshot_json: dict[str, Any]
    created_at: datetime


@dataclass(slots=True)
class ConnectorHeartbeat:
    id: str
    license_id: str
    device_id: str
    connector_version: str | None
    ea_version: str | None
    mt4_terminal_build: str | None
    status: str
    detail: str | None
    ip_address: str | None
    created_at: datetime


@dataclass(slots=True)
class AuditEvent:
    id: str
    actor_type: str
    actor_id: str | None
    action: str
    target_type: str | None
    target_id: str | None
    ip_address: str | None
    metadata: dict[str, Any]
    created_at: datetime


class MockBusinessRepository:
    """In-memory repository mirroring the commercial SQL schema concepts."""

    def __init__(
        self,
        *,
        now: datetime = MOCK_NOW,
        customers: Mapping[str, Customer],
        email_verification_tokens: Mapping[str, EmailVerificationToken] | None = None,
        risk_disclosures: Mapping[str, RiskDisclosure],
        disclosure_acceptances: Mapping[str, RiskDisclosureAcceptance] | None = None,
        billing_customers: Mapping[str, BillingCustomer],
        subscriptions: Mapping[str, Subscription],
        licenses: Mapping[str, LicenseRecord],
        mt4_accounts: Mapping[str, Mt4Account],
        license_account_bindings: Mapping[str, LicenseAccountBinding],
        devices: Mapping[str, DeviceRecord] | None = None,
        release_builds: Mapping[str, ReleaseBuild],
    ) -> None:
        self._now = now
        self._customers = dict(customers)
        self._email_verification_tokens = dict(email_verification_tokens or {})
        self._risk_disclosures = dict(risk_disclosures)
        self._disclosure_acceptances = dict(disclosure_acceptances or {})
        self._billing_customers = dict(billing_customers)
        self._subscriptions = dict(subscriptions)
        self._licenses = dict(licenses)
        self._mt4_accounts = dict(mt4_accounts)
        self._license_account_bindings = dict(license_account_bindings)
        self._devices = dict(devices or {})
        self._release_builds = dict(release_builds)
        self._entitlement_snapshots: dict[str, EntitlementSnapshot] = {}
        self._heartbeats: dict[str, ConnectorHeartbeat] = {}
        self._audit_events: dict[str, AuditEvent] = {}
        self._processed_webhook_event_ids: set[str] = set()
        self._lock = threading.RLock()

    @classmethod
    def seeded(cls) -> "MockBusinessRepository":
        now = MOCK_NOW
        customers = {
            "cus_active": Customer(
                id="cus_active",
                email="active.customer@example.test",
                full_name="Active Customer",
                country_code="US",
                phone=None,
                status="active",
                created_at=utc_datetime(2026, 6, 1),
                updated_at=utc_datetime(2026, 6, 1),
                email_verified_at=utc_datetime(2026, 6, 1),
            ),
            "cus_expired": Customer(
                id="cus_expired",
                email="expired.customer@example.test",
                full_name="Expired Customer",
                country_code="US",
                phone=None,
                status="active",
                created_at=utc_datetime(2026, 5, 1),
                updated_at=utc_datetime(2026, 6, 1),
                email_verified_at=utc_datetime(2026, 5, 1),
            ),
            "cus_revoked": Customer(
                id="cus_revoked",
                email="revoked.customer@example.test",
                full_name="Revoked Customer",
                country_code="US",
                phone=None,
                status="active",
                created_at=utc_datetime(2026, 5, 1),
                updated_at=utc_datetime(2026, 6, 1),
                email_verified_at=utc_datetime(2026, 5, 1),
            ),
            "cus_unbound": Customer(
                id="cus_unbound",
                email="unbound.customer@example.test",
                full_name="Unbound Customer",
                country_code="US",
                phone=None,
                status="active",
                created_at=utc_datetime(2026, 6, 10),
                updated_at=utc_datetime(2026, 6, 10),
                email_verified_at=utc_datetime(2026, 6, 10),
            ),
        }
        risk_disclosures = {
            "disc_risk_2026_06": RiskDisclosure(
                id="disc_risk_2026_06",
                version=MOCK_DISCLOSURE_VERSION,
                title="PhoenixGuard Risk Disclosure",
                content_hash=stable_hash("risk-disclosure", MOCK_DISCLOSURE_VERSION),
                effective_at=utc_datetime(2026, 6, 1),
            )
        }
        disclosure_acceptances = {
            "acc_cus_active_disc_risk_2026_06": RiskDisclosureAcceptance(
                id="acc_cus_active_disc_risk_2026_06",
                customer_id="cus_active",
                disclosure_id="disc_risk_2026_06",
                accepted_at=utc_datetime(2026, 6, 2, 12),
                ip_address="127.0.0.1",
                user_agent="phoenixguard-mock-seed",
                license_id="lic_active",
            )
        }
        billing_customers = {
            "bill_active": BillingCustomer(
                id="bill_active",
                customer_id="cus_active",
                provider="stripe",
                provider_customer_id="cus_stripe_active",
                created_at=utc_datetime(2026, 6, 1),
            ),
            "bill_expired": BillingCustomer(
                id="bill_expired",
                customer_id="cus_expired",
                provider="stripe",
                provider_customer_id="cus_stripe_expired",
                created_at=utc_datetime(2026, 5, 1),
            ),
            "bill_revoked": BillingCustomer(
                id="bill_revoked",
                customer_id="cus_revoked",
                provider="stripe",
                provider_customer_id="cus_stripe_revoked",
                created_at=utc_datetime(2026, 5, 1),
            ),
            "bill_unbound": BillingCustomer(
                id="bill_unbound",
                customer_id="cus_unbound",
                provider="stripe",
                provider_customer_id="cus_stripe_unbound",
                created_at=utc_datetime(2026, 6, 10),
            ),
        }
        subscriptions = {
            "sub_active": Subscription(
                id="sub_active",
                customer_id="cus_active",
                provider="stripe",
                provider_subscription_id="sub_pg_active",
                plan_code="business",
                status="active",
                current_period_start=utc_datetime(2026, 6, 1),
                current_period_end=utc_datetime(2027, 6, 1),
                cancel_at_period_end=False,
                created_at=utc_datetime(2026, 6, 1),
                updated_at=utc_datetime(2026, 6, 1),
            ),
            "sub_expired": Subscription(
                id="sub_expired",
                customer_id="cus_expired",
                provider="stripe",
                provider_subscription_id="sub_pg_expired",
                plan_code="business",
                status="canceled",
                current_period_start=utc_datetime(2025, 12, 1),
                current_period_end=utc_datetime(2026, 1, 1),
                cancel_at_period_end=False,
                created_at=utc_datetime(2025, 12, 1),
                updated_at=utc_datetime(2026, 1, 1),
            ),
            "sub_revoked": Subscription(
                id="sub_revoked",
                customer_id="cus_revoked",
                provider="stripe",
                provider_subscription_id="sub_pg_revoked",
                plan_code="business",
                status="active",
                current_period_start=utc_datetime(2026, 6, 1),
                current_period_end=utc_datetime(2027, 6, 1),
                cancel_at_period_end=False,
                created_at=utc_datetime(2026, 5, 1),
                updated_at=utc_datetime(2026, 6, 5),
            ),
            "sub_unbound": Subscription(
                id="sub_unbound",
                customer_id="cus_unbound",
                provider="stripe",
                provider_subscription_id="sub_pg_unbound",
                plan_code="business",
                status="active",
                current_period_start=utc_datetime(2026, 6, 10),
                current_period_end=utc_datetime(2027, 6, 10),
                cancel_at_period_end=False,
                created_at=utc_datetime(2026, 6, 10),
                updated_at=utc_datetime(2026, 6, 10),
            ),
        }
        licenses = {
            "lic_active": LicenseRecord(
                id="lic_active",
                customer_id="cus_active",
                subscription_id="sub_active",
                license_key_hash=license_key_hash("PG-ACTIVE-2026"),
                plan_code="business",
                status="active",
                expires_at=utc_datetime(2027, 6, 1),
                revoked_at=None,
                revoke_reason=None,
                created_at=utc_datetime(2026, 6, 1),
                updated_at=utc_datetime(2026, 6, 1),
            ),
            "lic_expired": LicenseRecord(
                id="lic_expired",
                customer_id="cus_expired",
                subscription_id="sub_expired",
                license_key_hash=license_key_hash("PG-EXPIRED-2026"),
                plan_code="business",
                status="expired",
                expires_at=utc_datetime(2026, 1, 1),
                revoked_at=None,
                revoke_reason=None,
                created_at=utc_datetime(2025, 12, 1),
                updated_at=utc_datetime(2026, 1, 1),
            ),
            "lic_revoked": LicenseRecord(
                id="lic_revoked",
                customer_id="cus_revoked",
                subscription_id="sub_revoked",
                license_key_hash=license_key_hash("PG-REVOKED-2026"),
                plan_code="business",
                status="revoked",
                expires_at=utc_datetime(2027, 6, 1),
                revoked_at=utc_datetime(2026, 6, 5),
                revoke_reason="chargeback",
                created_at=utc_datetime(2026, 5, 1),
                updated_at=utc_datetime(2026, 6, 5),
            ),
            "lic_unbound": LicenseRecord(
                id="lic_unbound",
                customer_id="cus_unbound",
                subscription_id="sub_unbound",
                license_key_hash=license_key_hash("PG-UNBOUND-2026"),
                plan_code="business",
                status="active",
                expires_at=utc_datetime(2027, 6, 10),
                revoked_at=None,
                revoke_reason=None,
                created_at=utc_datetime(2026, 6, 10),
                updated_at=utc_datetime(2026, 6, 10),
            ),
        }
        mt4_accounts = {
            "acct_active": Mt4Account(
                id="acct_active",
                customer_id="cus_active",
                broker_server_hash=stable_hash("broker-server", "Phoenix-Demo"),
                broker_server_label="Phoenix-Demo",
                account_number_hash=stable_hash("mt4-account", "10000001"),
                account_number_masked=mask_account_number("10000001"),
                status="active",
                created_at=utc_datetime(2026, 6, 1),
            ),
            "acct_expired": Mt4Account(
                id="acct_expired",
                customer_id="cus_expired",
                broker_server_hash=stable_hash("broker-server", "Phoenix-Demo"),
                broker_server_label="Phoenix-Demo",
                account_number_hash=stable_hash("mt4-account", "10000002"),
                account_number_masked=mask_account_number("10000002"),
                status="active",
                created_at=utc_datetime(2025, 12, 1),
            ),
            "acct_revoked": Mt4Account(
                id="acct_revoked",
                customer_id="cus_revoked",
                broker_server_hash=stable_hash("broker-server", "Phoenix-Demo"),
                broker_server_label="Phoenix-Demo",
                account_number_hash=stable_hash("mt4-account", "10000003"),
                account_number_masked=mask_account_number("10000003"),
                status="active",
                created_at=utc_datetime(2026, 5, 1),
            ),
        }
        license_account_bindings = {
            "bind_active": LicenseAccountBinding(
                id="bind_active",
                license_id="lic_active",
                mt4_account_id="acct_active",
                status="active",
                created_at=utc_datetime(2026, 6, 1),
            ),
            "bind_expired": LicenseAccountBinding(
                id="bind_expired",
                license_id="lic_expired",
                mt4_account_id="acct_expired",
                status="active",
                created_at=utc_datetime(2025, 12, 1),
            ),
            "bind_revoked": LicenseAccountBinding(
                id="bind_revoked",
                license_id="lic_revoked",
                mt4_account_id="acct_revoked",
                status="active",
                created_at=utc_datetime(2026, 5, 1),
            ),
        }
        manifest = {
            "release_id": "rel_2026_06_001",
            "channel": "stable",
            "ea_version": "808.2.0",
            "connector_version": "1.0.0",
            "minimum_connector_version": "1.0.0",
            "required_disclosure_version": MOCK_DISCLOSURE_VERSION,
            "sha256": {
                "ea": "mock-ea-sha256",
                "connector_installer": "mock-connector-installer-sha256",
            },
            "published_at": "2026-06-21T00:00:00Z",
        }
        release_builds = {
            "rel_2026_06_001": ReleaseBuild(
                id="rel_2026_06_001",
                channel="stable",
                ea_version="808.2.0",
                connector_version="1.0.0",
                minimum_connector_version="1.0.0",
                manifest_json=manifest,
                sha256_manifest=hashlib.sha256(
                    json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
                status="published",
                published_at=utc_datetime(2026, 6, 21),
                revoked_at=None,
                created_at=utc_datetime(2026, 6, 20),
            )
        }
        return cls(
            now=now,
            customers=customers,
            risk_disclosures=risk_disclosures,
            disclosure_acceptances=disclosure_acceptances,
            billing_customers=billing_customers,
            subscriptions=subscriptions,
            licenses=licenses,
            mt4_accounts=mt4_accounts,
            license_account_bindings=license_account_bindings,
            release_builds=release_builds,
        )

    @property
    def now(self) -> datetime:
        return self._now

    def get_customer(self, customer_id: str) -> Customer:
        with self._lock:
            customer = self._customers.get(customer_id)
            if customer is None:
                raise NotFoundError("customer_not_found")
            return replace(customer)

    def find_customer_by_email(self, email: str) -> Customer | None:
        normalized = str(email or "").strip().lower()
        with self._lock:
            for customer in self._customers.values():
                if customer.email.lower() == normalized:
                    return replace(customer)
            return None

    def create_customer(
        self,
        *,
        email: str,
        full_name: str,
        country_code: str | None,
        phone: str | None,
    ) -> Customer:
        normalized_email = str(email or "").strip().lower()
        normalized_name = str(full_name or "").strip()
        if not normalized_email or "@" not in normalized_email:
            raise ConflictError("customer_email_invalid")
        if not normalized_name:
            raise ConflictError("customer_full_name_required")
        with self._lock:
            for customer in self._customers.values():
                if customer.email.lower() == normalized_email:
                    raise ConflictError("customer_email_already_registered")
            customer = Customer(
                id=deterministic_id("cus", normalized_email),
                email=normalized_email,
                full_name=normalized_name,
                country_code=str(country_code).strip().upper() if country_code else None,
                phone=str(phone).strip() if phone else None,
                status="pending_email",
                created_at=self._now,
                updated_at=self._now,
                email_verified_at=None,
            )
            self._customers[customer.id] = customer
            return replace(customer)

    def ensure_customer_active(self, customer_id: str) -> Customer:
        customer = self.get_customer(customer_id)
        if customer.status != "active" or customer.email_verified_at is None:
            raise AuthorizationError("email_verification_required")
        return customer

    def create_email_verification_token(
        self,
        *,
        customer_id: str,
        token: str,
        expires_at: datetime,
    ) -> EmailVerificationToken:
        self.get_customer(customer_id)
        hashed = email_verification_token_hash(token)
        with self._lock:
            for token_record in self._email_verification_tokens.values():
                if token_record.customer_id == customer_id and token_record.consumed_at is None:
                    token_record.revoked_at = self._now
            token_id = f"emv_{len(self._email_verification_tokens) + 1:06d}"
            token_record = EmailVerificationToken(
                id=token_id,
                customer_id=customer_id,
                token_hash=hashed,
                expires_at=expires_at,
                created_at=self._now,
            )
            self._email_verification_tokens[token_record.id] = token_record
            return replace(token_record)

    def consume_email_verification_token(self, token: str) -> Customer:
        hashed = email_verification_token_hash(token)
        with self._lock:
            token_record: EmailVerificationToken | None = None
            for candidate in self._email_verification_tokens.values():
                if hmac.compare_digest(candidate.token_hash, hashed):
                    token_record = candidate
                    break
            if token_record is None:
                raise NotFoundError("email_verification_token_not_found")
            if token_record.revoked_at is not None:
                raise ConflictError("email_verification_token_revoked")
            if token_record.consumed_at is not None:
                raise ConflictError("email_verification_token_already_used")
            if token_record.expires_at <= self._now:
                raise AuthorizationError("email_verification_token_expired")
            customer = self._customers.get(token_record.customer_id)
            if customer is None:
                raise NotFoundError("customer_not_found")
            token_record.consumed_at = self._now
            customer.status = "active"
            customer.email_verified_at = customer.email_verified_at or self._now
            customer.updated_at = self._now
            return replace(customer)

    def list_email_verification_tokens(self, customer_id: str) -> list[EmailVerificationToken]:
        with self._lock:
            return [
                replace(token_record)
                for token_record in self._email_verification_tokens.values()
                if token_record.customer_id == customer_id
            ]

    def current_disclosure(self) -> RiskDisclosure:
        with self._lock:
            candidates = [
                disclosure
                for disclosure in self._risk_disclosures.values()
                if disclosure.retired_at is None
            ]
            if not candidates:
                raise NotFoundError("risk_disclosure_not_found")
            return replace(max(candidates, key=lambda disclosure: disclosure.effective_at))

    def disclosure_by_version(self, version: str) -> RiskDisclosure:
        with self._lock:
            for disclosure in self._risk_disclosures.values():
                if disclosure.version == version:
                    return replace(disclosure)
            raise NotFoundError("risk_disclosure_not_found")

    def has_accepted_current_disclosure(self, customer_id: str) -> bool:
        disclosure = self.current_disclosure()
        with self._lock:
            return any(
                acceptance.customer_id == customer_id
                and acceptance.disclosure_id == disclosure.id
                for acceptance in self._disclosure_acceptances.values()
            )

    def accept_disclosure(
        self,
        *,
        customer_id: str,
        version: str | None,
        ip_address: str | None,
        user_agent: str | None,
        license_id: str | None = None,
    ) -> RiskDisclosureAcceptance:
        disclosure = self.disclosure_by_version(version) if version else self.current_disclosure()
        if license_id is not None:
            self.get_license_for_customer(customer_id, license_id)
        acceptance_id = f"acc_{customer_id}_{disclosure.id}"
        with self._lock:
            existing = self._disclosure_acceptances.get(acceptance_id)
            if existing is not None:
                return replace(existing)
            acceptance = RiskDisclosureAcceptance(
                id=acceptance_id,
                customer_id=customer_id,
                disclosure_id=disclosure.id,
                accepted_at=self._now,
                ip_address=ip_address,
                user_agent=user_agent,
                license_id=license_id,
            )
            self._disclosure_acceptances[acceptance.id] = acceptance
            return replace(acceptance)

    def list_licenses_for_customer(self, customer_id: str) -> list[LicenseRecord]:
        with self._lock:
            return [
                replace(license_record)
                for license_record in self._licenses.values()
                if license_record.customer_id == customer_id
            ]

    def get_license(self, license_id: str) -> LicenseRecord:
        with self._lock:
            license_record = self._licenses.get(license_id)
            if license_record is None:
                raise NotFoundError("license_not_found")
            return replace(license_record)

    def get_license_for_customer(self, customer_id: str, license_id: str) -> LicenseRecord:
        license_record = self.get_license(license_id)
        if license_record.customer_id != customer_id:
            raise AuthorizationError("license_not_owned")
        return license_record

    def get_subscription(self, subscription_id: str) -> Subscription:
        with self._lock:
            subscription = self._subscriptions.get(subscription_id)
            if subscription is None:
                raise NotFoundError("subscription_not_found")
            return replace(subscription)

    def subscription_status_for_license(self, license_record: LicenseRecord) -> str | None:
        if license_record.subscription_id is None:
            return None
        subscription = self.get_subscription(license_record.subscription_id)
        return subscription.status

    def get_license_by_key(self, license_key: str) -> LicenseRecord:
        hashed = license_key_hash(license_key)
        with self._lock:
            for license_record in self._licenses.values():
                if license_record.license_key_hash == hashed:
                    return replace(license_record)
            raise NotFoundError("license_key_not_found")

    def list_accounts_for_license(self, license_id: str) -> list[Mt4Account]:
        with self._lock:
            accounts: list[Mt4Account] = []
            for binding in self._license_account_bindings.values():
                if binding.license_id != license_id or binding.status != "active":
                    continue
                account = self._mt4_accounts.get(binding.mt4_account_id)
                if account is not None:
                    accounts.append(replace(account))
            return accounts

    def license_has_active_account_binding(self, license_id: str) -> bool:
        return any(account.status == "active" for account in self.list_accounts_for_license(license_id))

    def create_or_get_broker_account(
        self,
        *,
        customer_id: str,
        broker_server: str,
        mt4_account_number: str,
        label: str | None,
    ) -> tuple[Mt4Account, bool]:
        server_hash = stable_hash("broker-server", broker_server)
        account_hash = stable_hash("mt4-account", mt4_account_number)
        with self._lock:
            for account in self._mt4_accounts.values():
                if (
                    account.customer_id == customer_id
                    and account.broker_server_hash == server_hash
                    and account.account_number_hash == account_hash
                ):
                    return replace(account), False
            account_id = deterministic_id("acct", customer_id, server_hash, account_hash)
            account = Mt4Account(
                id=account_id,
                customer_id=customer_id,
                broker_server_hash=server_hash,
                broker_server_label=label or str(broker_server).strip(),
                account_number_hash=account_hash,
                account_number_masked=mask_account_number(mt4_account_number),
                status="active",
                created_at=self._now,
            )
            self._mt4_accounts[account.id] = account
            return replace(account), True

    def bind_account_to_first_unbound_active_license(
        self,
        *,
        customer_id: str,
        account_id: str,
    ) -> LicenseAccountBinding | None:
        with self._lock:
            account = self._mt4_accounts.get(account_id)
            if account is None:
                raise NotFoundError("broker_account_not_found")
            if account.customer_id != customer_id:
                raise AuthorizationError("broker_account_not_owned")
            for license_record in sorted(self._licenses.values(), key=lambda item: item.created_at):
                if license_record.customer_id != customer_id:
                    continue
                if license_record.status not in {"active", "trialing", "grace"}:
                    continue
                if self.license_has_active_account_binding(license_record.id):
                    continue
                binding_id = deterministic_id("bind", license_record.id, account_id)
                binding = LicenseAccountBinding(
                    id=binding_id,
                    license_id=license_record.id,
                    mt4_account_id=account_id,
                    status="active",
                    created_at=self._now,
                )
                self._license_account_bindings[binding.id] = binding
                return replace(binding)
            return None

    def upsert_device(
        self,
        *,
        customer_id: str,
        license_id: str,
        device_fingerprint: str,
        device_label: str | None,
        connector_version: str,
    ) -> DeviceRecord:
        fingerprint_hash = stable_hash("device-fingerprint", device_fingerprint)
        device_id = deterministic_id("dev", license_id, fingerprint_hash)
        with self._lock:
            existing = self._devices.get(device_id)
            if existing is not None:
                existing.device_label = device_label or existing.device_label
                existing.connector_version = connector_version
                return replace(existing)
            device = DeviceRecord(
                id=device_id,
                customer_id=customer_id,
                license_id=license_id,
                device_fingerprint_hash=fingerprint_hash,
                device_label=device_label,
                connector_version=connector_version,
                status="active",
                registered_at=self._now,
                last_seen_at=None,
                revoked_at=None,
                connector_token_hash=None,
            )
            self._devices[device.id] = device
            return replace(device)

    def update_device_token_hash(self, device_id: str, connector_token_hash: str) -> DeviceRecord:
        with self._lock:
            device = self._devices.get(device_id)
            if device is None:
                raise NotFoundError("device_not_found")
            device.connector_token_hash = connector_token_hash
            return replace(device)

    def validate_connector_device(
        self,
        *,
        customer_id: str,
        license_id: str,
        device_id: str,
        connector_token_hash: str,
    ) -> tuple[DeviceRecord, LicenseRecord]:
        with self._lock:
            device = self._devices.get(device_id)
            if device is None:
                raise AuthorizationError("connector_device_not_found")
            if (
                device.customer_id != customer_id
                or device.license_id != license_id
                or device.id != device_id
                or device.connector_token_hash != connector_token_hash
            ):
                raise AuthorizationError("connector_token_not_bound_to_device")
            license_record = self._licenses.get(license_id)
            if license_record is None:
                raise AuthorizationError("connector_license_not_found")
            if license_record.customer_id != customer_id:
                raise AuthorizationError("connector_license_not_owned")
            return replace(device), replace(license_record)

    def update_device_last_seen(self, device_id: str, connector_version: str | None = None) -> DeviceRecord:
        with self._lock:
            device = self._devices.get(device_id)
            if device is None:
                raise NotFoundError("device_not_found")
            device.last_seen_at = self._now
            if connector_version:
                device.connector_version = connector_version
            return replace(device)

    def list_devices_for_license(self, license_id: str) -> list[DeviceRecord]:
        with self._lock:
            return [
                replace(device)
                for device in self._devices.values()
                if device.license_id == license_id
            ]

    def latest_release(self, channel: str = "stable") -> ReleaseBuild:
        with self._lock:
            candidates = [
                release
                for release in self._release_builds.values()
                if release.channel == channel
                and release.status == "published"
                and release.revoked_at is None
            ]
            if not candidates:
                raise NotFoundError("release_not_found")
            release = max(candidates, key=lambda item: item.published_at or item.created_at)
            return replace(release, manifest_json=deepcopy(release.manifest_json))

    def record_entitlement_snapshot(
        self,
        *,
        license_id: str,
        device_id: str | None,
        status: str,
        reason: str | None,
        snapshot_json: Mapping[str, Any],
    ) -> EntitlementSnapshot:
        with self._lock:
            snapshot_id = f"ent_{len(self._entitlement_snapshots) + 1:06d}"
            snapshot = EntitlementSnapshot(
                id=snapshot_id,
                license_id=license_id,
                device_id=device_id,
                status=status,
                reason=reason,
                snapshot_json=deepcopy(dict(snapshot_json)),
                created_at=self._now,
            )
            self._entitlement_snapshots[snapshot.id] = snapshot
            return replace(snapshot, snapshot_json=deepcopy(snapshot.snapshot_json))

    def record_heartbeat(
        self,
        *,
        license_id: str,
        device_id: str,
        connector_version: str | None,
        ea_version: str | None,
        mt4_terminal_build: str | None,
        status: str,
        detail: str | None,
        ip_address: str | None,
    ) -> ConnectorHeartbeat:
        with self._lock:
            heartbeat_id = f"hb_{len(self._heartbeats) + 1:06d}"
            heartbeat = ConnectorHeartbeat(
                id=heartbeat_id,
                license_id=license_id,
                device_id=device_id,
                connector_version=connector_version,
                ea_version=ea_version,
                mt4_terminal_build=mt4_terminal_build,
                status=status,
                detail=detail,
                ip_address=ip_address,
                created_at=self._now,
            )
            self._heartbeats[heartbeat.id] = heartbeat
            return replace(heartbeat)

    def find_billing_customer(self, *, provider: str, provider_customer_id: str) -> BillingCustomer | None:
        with self._lock:
            for billing_customer in self._billing_customers.values():
                if (
                    billing_customer.provider == provider
                    and billing_customer.provider_customer_id == provider_customer_id
                ):
                    return replace(billing_customer)
            return None

    def find_subscription(
        self,
        *,
        provider: str,
        provider_subscription_id: str,
    ) -> Subscription | None:
        with self._lock:
            for subscription in self._subscriptions.values():
                if (
                    subscription.provider == provider
                    and subscription.provider_subscription_id == provider_subscription_id
                ):
                    return replace(subscription)
            return None

    def upsert_subscription_from_provider(
        self,
        *,
        provider: str,
        provider_customer_id: str,
        provider_subscription_id: str,
        plan_code: str,
        status: str,
        current_period_start: datetime | None,
        current_period_end: datetime | None,
        cancel_at_period_end: bool,
    ) -> Subscription | None:
        billing_customer = self.find_billing_customer(
            provider=provider,
            provider_customer_id=provider_customer_id,
        )
        if billing_customer is None:
            return None
        with self._lock:
            existing_id: str | None = None
            for subscription_id, subscription in self._subscriptions.items():
                if (
                    subscription.provider == provider
                    and subscription.provider_subscription_id == provider_subscription_id
                ):
                    existing_id = subscription_id
                    break
            if existing_id is None:
                existing_id = deterministic_id(
                    "sub",
                    provider,
                    provider_customer_id,
                    provider_subscription_id,
                )
                self._subscriptions[existing_id] = Subscription(
                    id=existing_id,
                    customer_id=billing_customer.customer_id,
                    provider=provider,
                    provider_subscription_id=provider_subscription_id,
                    plan_code=plan_code,
                    status=status,
                    current_period_start=current_period_start,
                    current_period_end=current_period_end,
                    cancel_at_period_end=cancel_at_period_end,
                    created_at=self._now,
                    updated_at=self._now,
                )
            else:
                subscription = self._subscriptions[existing_id]
                if subscription.customer_id != billing_customer.customer_id:
                    raise AuthorizationError("billing_customer_subscription_mismatch")
                subscription.plan_code = plan_code
                subscription.status = status
                subscription.current_period_start = current_period_start
                subscription.current_period_end = current_period_end
                subscription.cancel_at_period_end = cancel_at_period_end
                subscription.updated_at = self._now
            return replace(self._subscriptions[existing_id])

    def set_license_state_for_subscription(
        self,
        *,
        subscription_id: str,
        status: str,
        expires_at: datetime | None,
        revoke_reason: str | None = None,
    ) -> list[LicenseRecord]:
        updated: list[LicenseRecord] = []
        with self._lock:
            for license_record in self._licenses.values():
                if license_record.subscription_id != subscription_id:
                    continue
                license_record.status = status
                license_record.expires_at = expires_at
                license_record.updated_at = self._now
                if status == "revoked":
                    license_record.revoked_at = self._now
                    license_record.revoke_reason = revoke_reason or "billing_provider"
                elif status != "revoked":
                    license_record.revoked_at = None
                    license_record.revoke_reason = None
                updated.append(replace(license_record))
        return updated

    def revoke_licenses_for_billing_customer(
        self,
        *,
        provider: str,
        provider_customer_id: str,
        reason: str,
    ) -> list[LicenseRecord]:
        billing_customer = self.find_billing_customer(
            provider=provider,
            provider_customer_id=provider_customer_id,
        )
        if billing_customer is None:
            return []
        updated: list[LicenseRecord] = []
        with self._lock:
            for license_record in self._licenses.values():
                if license_record.customer_id != billing_customer.customer_id:
                    continue
                license_record.status = "revoked"
                license_record.revoked_at = self._now
                license_record.revoke_reason = reason
                license_record.updated_at = self._now
                updated.append(replace(license_record))
        return updated

    def mark_webhook_event_processed(self, event_id: str) -> bool:
        with self._lock:
            if event_id in self._processed_webhook_event_ids:
                return False
            self._processed_webhook_event_ids.add(event_id)
            return True

    def append_audit_event(
        self,
        *,
        actor_type: str,
        actor_id: str | None,
        action: str,
        target_type: str | None,
        target_id: str | None,
        ip_address: str | None,
        metadata: Mapping[str, Any] | None = None,
    ) -> AuditEvent:
        with self._lock:
            audit_id = f"audit_{len(self._audit_events) + 1:06d}"
            audit = AuditEvent(
                id=audit_id,
                actor_type=actor_type,
                actor_id=actor_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                ip_address=ip_address,
                metadata=deepcopy(dict(metadata or {})),
                created_at=self._now,
            )
            self._audit_events[audit.id] = audit
            return replace(audit, metadata=deepcopy(audit.metadata))

    def list_audit_events(self) -> list[AuditEvent]:
        with self._lock:
            return [
                replace(audit, metadata=deepcopy(audit.metadata))
                for audit in self._audit_events.values()
            ]
