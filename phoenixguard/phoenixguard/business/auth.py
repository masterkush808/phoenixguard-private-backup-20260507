from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
import hmac
import json
from typing import Mapping


MOCK_CUSTOMER_TOKENS: dict[str, str] = {
    "pg_mock_active_customer": "cus_active",
    "pg_mock_expired_customer": "cus_expired",
    "pg_mock_revoked_customer": "cus_revoked",
    "pg_mock_unbound_customer": "cus_unbound",
}
MOCK_CONNECTOR_TOKEN_SECRET = "pg_mock_connector_secret"
MOCK_STRIPE_WEBHOOK_SECRET = "whsec_phoenixguard_mock_test"


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
        connector_secret: str = MOCK_CONNECTOR_TOKEN_SECRET,
    ) -> None:
        self._customer_tokens = dict(customer_tokens or MOCK_CUSTOMER_TOKENS)
        self._connector_secret = connector_secret

    def authenticate_customer_header(self, authorization_header: str | None) -> CustomerPrincipal:
        token = extract_bearer_token(authorization_header)
        customer_id = self._customer_tokens.get(token)
        if customer_id is None:
            raise BusinessAuthError("invalid_customer_token")
        return CustomerPrincipal(customer_id=customer_id, token=token)

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


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))
