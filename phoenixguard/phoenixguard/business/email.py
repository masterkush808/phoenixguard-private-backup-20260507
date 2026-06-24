from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Any, Mapping, Protocol, cast
from urllib import error, request


class EmailConfigurationError(Exception):
    """Raised when a real email provider is not configured."""


class EmailProviderError(Exception):
    """Raised when an email provider request fails."""


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
class ResendEmailConfig:
    api_key: str
    from_email: str
    api_base_url: str = "https://api.resend.com"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "ResendEmailConfig":
        values = env if env is not None else os.environ
        return cls(
            api_key=_required_env(values, "RESEND_API_KEY"),
            from_email=_required_env(values, "RESEND_FROM_EMAIL", "PHOENIXGUARD_RESEND_FROM_EMAIL"),
            api_base_url=(_env_first(values, "RESEND_API_BASE_URL") or "https://api.resend.com").rstrip("/"),
        )


class ResendEmailConfirmationAdapter:
    """Sends PhoenixGuard payment confirmations through Resend."""

    def __init__(
        self,
        *,
        config: ResendEmailConfig,
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
    ) -> "ResendEmailConfirmationAdapter":
        return cls(
            config=ResendEmailConfig.from_env(env),
            http_post=http_post,
            timeout_seconds=timeout_seconds,
        )

    def send_payment_confirmation(
        self,
        *,
        customer_email: str,
        customer_id: str,
        plan_code: str,
        provider_subscription_id: str,
        license_ids: list[str] | tuple[str, ...],
    ) -> dict[str, Any]:
        recipient = str(customer_email or "").strip()
        if not recipient:
            raise EmailProviderError("resend_customer_email_required")
        safe_customer_id = _tag_value(customer_id)
        safe_plan_code = _tag_value(plan_code or "business")
        first_license = str(next(iter(license_ids), "") or "").strip()
        license_line = f"License: {first_license}" if first_license else "License provisioning is pending."
        payload: dict[str, Any] = {
            "from": self._config.from_email,
            "to": [recipient],
            "subject": "PhoenixGuard payment confirmed",
            "text": (
                "Your PhoenixGuard payment is confirmed.\n\n"
                f"Plan: {plan_code}\n"
                f"{license_line}\n\n"
                "Open the PhoenixGuard customer portal to complete risk disclosure and connector setup."
            ),
            "html": (
                "<p>Your PhoenixGuard payment is confirmed.</p>"
                f"<p>Plan: <strong>{_escape_html(plan_code)}</strong></p>"
                f"<p>{_escape_html(license_line)}</p>"
                "<p>Open the PhoenixGuard customer portal to complete risk disclosure and connector setup.</p>"
            ),
            "tags": [
                {"name": "customer_id", "value": safe_customer_id},
                {"name": "plan_code", "value": safe_plan_code},
                {"name": "template", "value": "payment_confirmed"},
            ],
        }
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
            "Idempotency-Key": _idempotency_key(
                customer_id=customer_id,
                provider_subscription_id=provider_subscription_id,
                plan_code=plan_code,
            ),
        }
        status_code, _, response_body = self._http_post(
            url=f"{self._config.api_base_url}/emails",
            headers=headers,
            body=json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            timeout_seconds=self._timeout_seconds,
        )
        if status_code < 200 or status_code >= 300:
            raise EmailProviderError(f"resend_email_send_failed:{status_code}")
        try:
            decoded_payload: object = json.loads(response_body.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise EmailProviderError("resend_email_send_invalid_json") from exc
        if not isinstance(decoded_payload, Mapping):
            raise EmailProviderError("resend_email_send_invalid_response")
        response_payload = cast(Mapping[str, Any], decoded_payload)
        message_id = str(response_payload.get("id") or "").strip()
        if not message_id:
            raise EmailProviderError("resend_email_send_missing_id")
        return {
            "provider": "resend",
            "message_id": message_id,
            "status": "sent",
        }


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
    raise EmailConfigurationError(f"{names[0].lower()}_required")


def _tag_value(value: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", str(value or "").strip())
    return (normalized or "unknown")[:256]


def _idempotency_key(*, customer_id: str, provider_subscription_id: str, plan_code: str) -> str:
    raw = f"phoenixguard:{customer_id}:{provider_subscription_id}:{plan_code}"
    return _tag_value(raw)[:256]


def _escape_html(value: str) -> str:
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


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
        raise EmailProviderError("resend_email_send_request_failed") from exc
