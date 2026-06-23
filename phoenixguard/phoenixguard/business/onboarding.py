from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
import os
import secrets
from typing import Any, Mapping, Protocol

from .auth import MockBusinessAuthProvider
from .repository import (
    ConflictError,
    MockBusinessRepository,
    NotFoundError,
    iso_datetime,
)


SENSITIVE_BROKER_KEYS = frozenset(
    {
        "password",
        "broker_password",
        "mt4_password",
        "terminal_password",
        "investor_password",
        "api_key",
        "api_secret",
        "secret",
    }
)


class BrokerSecretError(ValueError):
    """Raised when broker account binding tries to submit credentials."""


class EmailProviderConfigurationError(RuntimeError):
    """Raised when email delivery is not configured safely."""


class EmailVerificationProvider(Protocol):
    provider_name: str

    def send_verification(
        self,
        *,
        customer_id: str,
        email: str,
        verification_token: str,
        expires_at: datetime,
    ) -> dict[str, Any]:
        ...


def _empty_sent_messages() -> list[dict[str, Any]]:
    return []


@dataclass
class CapturingEmailVerificationProvider:
    """Local/test provider that captures email verification messages in memory."""

    provider_name: str = "memory"
    sent_messages: list[dict[str, Any]] = field(default_factory=_empty_sent_messages)

    def send_verification(
        self,
        *,
        customer_id: str,
        email: str,
        verification_token: str,
        expires_at: datetime,
    ) -> dict[str, Any]:
        message: dict[str, Any] = {
            "provider": self.provider_name,
            "message_id": f"emv_{len(self.sent_messages) + 1:06d}",
            "customer_id": customer_id,
            "email": email,
            "template_key": "customer_email_verification",
            "delivery_status": "captured",
            "verification_token": verification_token,
            "expires_at": iso_datetime(expires_at),
        }
        self.sent_messages.append(message)
        return {
            "provider": self.provider_name,
            "message_id": message["message_id"],
            "delivery_status": "captured",
        }


class FailClosedEmailVerificationProvider:
    provider_name = "fail-closed"

    def send_verification(
        self,
        *,
        customer_id: str,
        email: str,
        verification_token: str,
        expires_at: datetime,
    ) -> dict[str, Any]:
        raise EmailProviderConfigurationError("email_provider_not_configured")


class CustomerOnboardingService:
    def __init__(
        self,
        *,
        repository: MockBusinessRepository,
        auth_provider: MockBusinessAuthProvider,
        email_provider: EmailVerificationProvider,
        verification_token_ttl_seconds: int = 3600,
    ) -> None:
        self._repository = repository
        self._auth_provider = auth_provider
        self._email_provider = email_provider
        self._verification_token_ttl_seconds = max(300, int(verification_token_ttl_seconds))

    @property
    def email_provider(self) -> EmailVerificationProvider:
        return self._email_provider

    def register_customer(
        self,
        *,
        email: str,
        full_name: str,
        country_code: str | None,
        phone: str | None,
        ip_address: str | None,
    ) -> dict[str, Any]:
        customer = self._repository.create_customer(
            email=email,
            full_name=full_name,
            country_code=country_code,
            phone=phone,
        )
        delivery = self._issue_verification(customer_id=customer.id, email=customer.email)
        self._repository.append_audit_event(
            actor_type="customer",
            actor_id=customer.id,
            action="customer.registered",
            target_type="customer",
            target_id=customer.id,
            ip_address=ip_address,
            metadata={"email_provider": delivery["provider"]},
        )
        return {
            "customer": _public_customer(customer),
            "email_verification": delivery,
        }

    def resend_email_verification(
        self,
        *,
        email: str,
        ip_address: str | None,
    ) -> dict[str, Any]:
        customer = self._repository.find_customer_by_email(email)
        if customer is None:
            raise NotFoundError("customer_not_found")
        if customer.email_verified_at is not None and customer.status == "active":
            raise ConflictError("email_already_verified")
        delivery = self._issue_verification(customer_id=customer.id, email=customer.email)
        self._repository.append_audit_event(
            actor_type="customer",
            actor_id=customer.id,
            action="customer.email_verification_resent",
            target_type="customer",
            target_id=customer.id,
            ip_address=ip_address,
            metadata={"email_provider": delivery["provider"]},
        )
        return {
            "customer": _public_customer(customer),
            "email_verification": delivery,
        }

    def verify_email(
        self,
        *,
        verification_token: str,
        ip_address: str | None,
    ) -> dict[str, Any]:
        customer = self._repository.consume_email_verification_token(verification_token)
        access_token = self._auth_provider.issue_customer_token(customer_id=customer.id)
        self._repository.append_audit_event(
            actor_type="customer",
            actor_id=customer.id,
            action="customer.email_verified",
            target_type="customer",
            target_id=customer.id,
            ip_address=ip_address,
            metadata={},
        )
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "customer": _public_customer(customer),
        }

    def _issue_verification(self, *, customer_id: str, email: str) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        expires_at = self._repository.now + timedelta(seconds=self._verification_token_ttl_seconds)
        token_record = self._repository.create_email_verification_token(
            customer_id=customer_id,
            token=token,
            expires_at=expires_at,
        )
        delivery = self._email_provider.send_verification(
            customer_id=customer_id,
            email=email,
            verification_token=token,
            expires_at=expires_at,
        )
        return {
            **delivery,
            "expires_at": iso_datetime(token_record.expires_at),
        }


def build_email_verification_provider_from_env() -> EmailVerificationProvider:
    provider = os.getenv("PHOENIXGUARD_EMAIL_PROVIDER", "memory").strip().lower()
    if provider in {"", "memory", "mock", "test"}:
        return CapturingEmailVerificationProvider()
    if provider in {"disabled", "none"}:
        return FailClosedEmailVerificationProvider()
    if provider == "resend":
        if not os.getenv("PHOENIXGUARD_RESEND_API_KEY", "").strip():
            raise EmailProviderConfigurationError("email_provider_secret_missing")
        raise EmailProviderConfigurationError("email_provider_resend_not_wired")
    raise EmailProviderConfigurationError("email_provider_unsupported")


def reject_sensitive_broker_payload(payload: Mapping[str, Any]) -> None:
    provided = {str(key).strip().lower() for key in payload}
    blocked = provided & SENSITIVE_BROKER_KEYS
    if blocked:
        raise BrokerSecretError("broker_credentials_not_collected")


def _public_customer(customer: Any) -> dict[str, Any]:
    return {
        "id": customer.id,
        "email": customer.email,
        "full_name": customer.full_name,
        "country_code": customer.country_code,
        "phone": customer.phone,
        "status": customer.status,
        "email_verified": customer.email_verified_at is not None,
        "email_verified_at": iso_datetime(customer.email_verified_at),
        "created_at": iso_datetime(customer.created_at),
        "updated_at": iso_datetime(customer.updated_at),
    }
