from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
import os
import time
from typing import Mapping


MOCK_CUSTOMER_TOKENS: dict[str, str] = {
    "pg_mock_active_customer": "cus_active",
    "pg_mock_expired_customer": "cus_expired",
    "pg_mock_revoked_customer": "cus_revoked",
    "pg_mock_unbound_customer": "cus_unbound",
}
MOCK_CONNECTOR_TOKEN_SECRET = "pg_mock_connector_secret"
MOCK_CUSTOMER_SESSION_SECRET = "pg_mock_customer_session_secret"
MOCK_STRIPE_WEBHOOK_SECRET = "whsec_phoenixguard_mock_test"
STRICT_SECRET_ENV = "PHOENIXGUARD_BUSINESS_REQUIRE_ENV_SECRETS"


class BusinessAuthError(Exception):
    """Raised when a bearer token cannot be authenticated."""


@dataclass(frozen=True, slots=True)
class CustomerPrincipal:
    customer_id: str
    token: str
    scopes: tuple[str, ...] = ("customer",)


@dataclass(frozen=True, slots=True)
class ConnectorPrincipal:
    customer_id: str
    license_id: str
    device_id: str
    token: str
    scopes: tuple[str, ...] = ("connector",)


def extract_bearer_token(authorization_header: str | None) -> str:
    header = str(authorization_header or "").strip()
    if not header:
        raise BusinessAuthError("missing_authorization")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise BusinessAuthError("invalid_authorization_scheme")
    return token.strip()


def hash_connector_token(token: str) -> str:
    digest = hashlib.sha256(f"connector-token:{token}".encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


class MockBusinessAuthProvider:
    """Mock/test-mode auth provider for portal and connector tokens."""

    def __init__(
        self,
        *,
        customer_tokens: Mapping[str, str] | None = None,
        connector_secret: str | None = None,
        customer_session_secret: str | None = None,
        customer_session_ttl_seconds: int = 86400,
    ) -> None:
        self._customer_tokens = dict(customer_tokens or MOCK_CUSTOMER_TOKENS)
        self._connector_secret = connector_secret or _env_secret(
            "PHOENIXGUARD_BUSINESS_CONNECTOR_TOKEN_SECRET",
            fallback=MOCK_CONNECTOR_TOKEN_SECRET,
        )
        self._customer_session_secret = customer_session_secret or _env_secret(
            "PHOENIXGUARD_BUSINESS_CUSTOMER_TOKEN_SECRET",
            fallback=MOCK_CUSTOMER_SESSION_SECRET,
        )
        self._customer_session_ttl_seconds = max(60, int(customer_session_ttl_seconds))

    def authenticate_customer_header(self, authorization_header: str | None) -> CustomerPrincipal:
        token = extract_bearer_token(authorization_header)
        customer_id = self._customer_tokens.get(token)
        if customer_id is None and token.startswith("pgcust_"):
            customer_id = self._verify_customer_session_token(token)
        if customer_id is None:
            raise BusinessAuthError("invalid_customer_token")
        return CustomerPrincipal(customer_id=customer_id, token=token)

    def issue_customer_token(self, *, customer_id: str) -> str:
        issued_at = int(time.time())
        payload = {
            "cid": str(customer_id).strip(),
            "iat": issued_at,
            "exp": issued_at + self._customer_session_ttl_seconds,
        }
        body = _base64url_encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signature = hmac.new(
            self._customer_session_secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"pgcust_{body}.{signature[:32]}"

    def issue_connector_token(
        self,
        *,
        customer_id: str,
        license_id: str,
        device_id: str,
    ) -> str:
        payload = {
            "cid": customer_id,
            "lid": license_id,
            "did": device_id,
        }
        body = _base64url_encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        signature = hmac.new(
            self._connector_secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"pgconn_{body}.{signature[:32]}"

    def authenticate_connector_header(self, authorization_header: str | None) -> ConnectorPrincipal:
        token = extract_bearer_token(authorization_header)
        if not token.startswith("pgconn_"):
            raise BusinessAuthError("invalid_connector_token")
        body_and_signature = token.removeprefix("pgconn_")
        body, separator, supplied_signature = body_and_signature.partition(".")
        if separator != "." or not body or not supplied_signature:
            raise BusinessAuthError("invalid_connector_token")
        expected_signature = hmac.new(
            self._connector_secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:32]
        if not hmac.compare_digest(expected_signature, supplied_signature):
            raise BusinessAuthError("invalid_connector_token_signature")
        try:
            payload = json.loads(_base64url_decode(body).decode("utf-8"))
        except Exception as exc:  # pragma: no cover - defensive malformed-token guard
            raise BusinessAuthError("invalid_connector_token_payload") from exc
        if not isinstance(payload, dict):
            raise BusinessAuthError("invalid_connector_token_payload")
        customer_id = str(payload.get("cid") or "").strip()
        license_id = str(payload.get("lid") or "").strip()
        device_id = str(payload.get("did") or "").strip()
        if not customer_id or not license_id or not device_id:
            raise BusinessAuthError("invalid_connector_token_payload")
        return ConnectorPrincipal(
            customer_id=customer_id,
            license_id=license_id,
            device_id=device_id,
            token=token,
        )

    def _verify_customer_session_token(self, token: str) -> str | None:
        body_and_signature = token.removeprefix("pgcust_")
        body, separator, supplied_signature = body_and_signature.partition(".")
        if separator != "." or not body or not supplied_signature:
            raise BusinessAuthError("invalid_customer_token")
        expected_signature = hmac.new(
            self._customer_session_secret.encode("utf-8"),
            body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()[:32]
        if not hmac.compare_digest(expected_signature, supplied_signature):
            raise BusinessAuthError("invalid_customer_token_signature")
        try:
            payload = json.loads(_base64url_decode(body).decode("utf-8"))
        except Exception as exc:  # pragma: no cover - defensive malformed-token guard
            raise BusinessAuthError("invalid_customer_token_payload") from exc
        if not isinstance(payload, dict):
            raise BusinessAuthError("invalid_customer_token_payload")
        expires_at = int(payload.get("exp") or 0)
        if expires_at <= int(time.time()):
            raise BusinessAuthError("customer_token_expired")
        customer_id = str(payload.get("cid") or "").strip()
        if not customer_id:
            raise BusinessAuthError("invalid_customer_token_payload")
        return customer_id


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _env_secret(name: str, *, fallback: str) -> str:
    value = os.getenv(name, "").strip()
    if value:
        return value
    strict = os.getenv(STRICT_SECRET_ENV, "").strip().lower() in {"1", "true", "yes", "on"}
    if strict:
        raise BusinessAuthError(f"{name.lower()}_missing")
    return fallback
