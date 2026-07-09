from __future__ import annotations

import html
import json
import os
import base64
import smtplib
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Mapping, Protocol, cast

from .packages import package_profile_for_plan


class ProviderConfigurationError(RuntimeError):
    pass


class ProviderRequestError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProviderStatus:
    provider: str
    configured: bool
    mode: str
    missing: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "configured": self.configured,
            "mode": self.mode,
            "missing": list(self.missing),
        }


class CheckoutProvider(Protocol):
    def status(self) -> ProviderStatus:
        ...

    def create_subscription_checkout(
        self,
        *,
        customer_id: str,
        email: str,
        plan_code: str = "hybrid-standard-6h",
    ) -> dict[str, Any]:
        ...


class StripeCheckoutProvider:
    plan_price_env: dict[str, str] = {
        "hybrid-standard-6h": "STRIPE_PRICE_ID_STANDARD_6H",
        "hybrid-professional-24x7": "STRIPE_PRICE_ID_PRO_24X7",
        "hybrid-standard": "STRIPE_PRICE_ID_STANDARD_6H",
    }

    def __init__(self, *, opener: Any | None = None) -> None:
        self.secret_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
        self.price_id = os.getenv("STRIPE_PRICE_ID", "").strip()
        self.plan_price_ids = {
            plan_code: os.getenv(env_name, "").strip()
            for plan_code, env_name in self.plan_price_env.items()
        }
        self.success_url = os.getenv("PHOENIXGUARD_CHECKOUT_SUCCESS_URL", "").strip()
        self.cancel_url = os.getenv("PHOENIXGUARD_CHECKOUT_CANCEL_URL", "").strip()
        self.api_url = os.getenv("STRIPE_CHECKOUT_SESSIONS_URL", "https://api.stripe.com/v1/checkout/sessions").strip()
        self._opener = opener or urllib.request.urlopen

    def status(self) -> ProviderStatus:
        missing: list[str] = []
        if not self.secret_key:
            missing.append("STRIPE_SECRET_KEY")
        if not (self.plan_price_ids.get("hybrid-standard-6h") or self.price_id):
            missing.append("STRIPE_PRICE_ID_STANDARD_6H")
        if not (self.plan_price_ids.get("hybrid-professional-24x7") or self.price_id):
            missing.append("STRIPE_PRICE_ID_PRO_24X7")
        if not self.success_url:
            missing.append("PHOENIXGUARD_CHECKOUT_SUCCESS_URL")
        if not self.cancel_url:
            missing.append("PHOENIXGUARD_CHECKOUT_CANCEL_URL")
        return ProviderStatus(
            provider="stripe",
            configured=not missing,
            mode="live" if self.secret_key.startswith("sk_live_") else "test",
            missing=tuple(missing),
        )

    def _price_id_for_plan(self, plan_code: str) -> str:
        normalized = package_profile_for_plan(plan_code).code
        return self.plan_price_ids.get(normalized) or self.price_id

    def create_subscription_checkout(self, *, customer_id: str, email: str, plan_code: str = "hybrid-standard-6h") -> dict[str, Any]:
        profile = package_profile_for_plan(plan_code)
        normalized_plan = profile.code
        price_id = self._price_id_for_plan(normalized_plan)
        missing: list[str] = []
        if not self.secret_key:
            missing.append("STRIPE_SECRET_KEY")
        if not price_id:
            missing.append(self.plan_price_env.get(normalized_plan, "STRIPE_PRICE_ID"))
        if not self.success_url:
            missing.append("PHOENIXGUARD_CHECKOUT_SUCCESS_URL")
        if not self.cancel_url:
            missing.append("PHOENIXGUARD_CHECKOUT_CANCEL_URL")
        if missing:
            raise ProviderConfigurationError(f"Stripe checkout is not configured: missing {', '.join(missing)}.")
        runtime_limit = str(profile.daily_runtime_hours)
        form: dict[str, str] = {
            "mode": "subscription",
            "line_items[0][price]": price_id,
            "line_items[0][quantity]": "1",
            "customer_email": email,
            "client_reference_id": customer_id,
            "success_url": self.success_url,
            "cancel_url": self.cancel_url,
            "metadata[customer_id]": customer_id,
            "metadata[plan_code]": normalized_plan,
            "metadata[daily_runtime_hours]": runtime_limit,
            "metadata[product]": "808fx-standard-hybrid-system",
            "subscription_data[metadata][customer_id]": customer_id,
            "subscription_data[metadata][plan_code]": normalized_plan,
            "subscription_data[metadata][daily_runtime_hours]": runtime_limit,
        }
        body = urllib.parse.urlencode(form).encode("utf-8")
        request = urllib.request.Request(
            self.api_url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.secret_key}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with self._opener(request, timeout=20) as response:
                payload: object = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - network errors are environment-specific.
            raise ProviderRequestError(f"Stripe checkout request failed: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise ProviderRequestError("Stripe checkout response did not include a hosted URL.")
        payload_map = cast(Mapping[str, Any], payload)
        if not payload_map.get("url"):
            raise ProviderRequestError("Stripe checkout response did not include a hosted URL.")
        return dict(payload_map)


class PayPalCheckoutProvider:
    plan_amount_env: dict[str, str] = {
        "hybrid-standard-6h": "PAYPAL_AMOUNT_STANDARD_6H",
        "hybrid-professional-24x7": "PAYPAL_AMOUNT_PRO_24X7",
        "hybrid-standard": "PAYPAL_AMOUNT_STANDARD_6H",
    }

    plan_default_amounts: dict[str, str] = {
        "hybrid-standard-6h": "20.00",
        "hybrid-professional-24x7": "100.00",
        "hybrid-standard": "20.00",
    }

    def __init__(self, *, opener: Any | None = None) -> None:
        self.client_id = os.getenv("PAYPAL_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("PAYPAL_CLIENT_SECRET", "").strip()
        self.webhook_id = os.getenv("PAYPAL_WEBHOOK_ID", "").strip()
        self.mode = os.getenv("PAYPAL_MODE", "sandbox").strip().lower()
        self.currency = os.getenv("PAYPAL_CURRENCY", "USD").strip().upper() or "USD"
        self.merchant_email = os.getenv("PAYPAL_MERCHANT_EMAIL", "").strip()
        default_base = (
            "https://api-m.paypal.com"
            if self.mode == "live"
            else "https://api-m.sandbox.paypal.com"
        )
        self.api_base_url = os.getenv("PAYPAL_API_BASE_URL", default_base).strip().rstrip("/")
        self.success_url = (
            os.getenv("PAYPAL_SUCCESS_URL", "").strip()
            or os.getenv("PHOENIXGUARD_CHECKOUT_SUCCESS_URL", "").strip()
        )
        self.cancel_url = (
            os.getenv("PAYPAL_CANCEL_URL", "").strip()
            or os.getenv("PHOENIXGUARD_CHECKOUT_CANCEL_URL", "").strip()
        )
        self.plan_amounts = {
            plan_code: os.getenv(env_name, "").strip()
            for plan_code, env_name in self.plan_amount_env.items()
        }
        self._opener = opener or urllib.request.urlopen

    def status(self) -> ProviderStatus:
        missing: list[str] = []
        if not self.client_id:
            missing.append("PAYPAL_CLIENT_ID")
        if not self.client_secret:
            missing.append("PAYPAL_CLIENT_SECRET")
        if not self.success_url:
            missing.append("PAYPAL_SUCCESS_URL")
        if not self.cancel_url:
            missing.append("PAYPAL_CANCEL_URL")
        if not self.webhook_id:
            missing.append("PAYPAL_WEBHOOK_ID")
        if not self.merchant_email:
            missing.append("PAYPAL_MERCHANT_EMAIL")
        return ProviderStatus(
            provider="paypal",
            configured=not missing,
            mode="live" if self.mode == "live" else "sandbox",
            missing=tuple(missing),
        )

    def _amount_for_plan(self, plan_code: str) -> str:
        normalized = package_profile_for_plan(plan_code).code
        raw = self.plan_amounts.get(normalized) or self.plan_default_amounts.get(normalized, "20.00")
        try:
            value = float(raw)
        except ValueError as exc:
            raise ProviderConfigurationError(f"Invalid PayPal amount for {normalized}: {raw}") from exc
        if value <= 0:
            raise ProviderConfigurationError(f"Invalid PayPal amount for {normalized}: {raw}")
        return f"{value:.2f}"

    def _access_token(self) -> str:
        if not self.client_id or not self.client_secret:
            raise ProviderConfigurationError("PayPal checkout is not configured: missing PAYPAL_CLIENT_ID, PAYPAL_CLIENT_SECRET.")
        credentials = f"{self.client_id}:{self.client_secret}".encode("utf-8")
        auth = base64.b64encode(credentials).decode("ascii")
        request = urllib.request.Request(
            f"{self.api_base_url}/v1/oauth2/token",
            data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
        )
        try:
            with self._opener(request, timeout=20) as response:
                payload: object = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - network errors are environment-specific.
            raise ProviderRequestError(f"PayPal token request failed: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise ProviderRequestError("PayPal token response was not a JSON object.")
        token = str(cast(Mapping[str, Any], payload).get("access_token") or "").strip()
        if not token:
            raise ProviderRequestError("PayPal token response did not include access_token.")
        return token

    def create_subscription_checkout(self, *, customer_id: str, email: str, plan_code: str = "hybrid-standard-6h") -> dict[str, Any]:
        profile = package_profile_for_plan(plan_code)
        status = self.status()
        if not status.configured:
            raise ProviderConfigurationError(f"PayPal checkout is not configured: missing {', '.join(status.missing)}.")
        token = self._access_token()
        custom_id = f"pg:{customer_id}:{profile.code}"
        payload: dict[str, Any] = {
            "intent": "CAPTURE",
            "purchase_units": [
                {
                    "reference_id": custom_id,
                    "custom_id": custom_id,
                    "description": profile.name,
                    "amount": {
                        "currency_code": self.currency,
                        "value": self._amount_for_plan(profile.code),
                    },
                }
            ],
            "payment_source": {
                "paypal": {
                    "experience_context": {
                        "brand_name": "808Fx Standard Hybrid",
                        "landing_page": "LOGIN",
                        "user_action": "PAY_NOW",
                        "return_url": self.success_url,
                        "cancel_url": self.cancel_url,
                    }
                }
            },
        }
        request = urllib.request.Request(
            f"{self.api_base_url}/v2/checkout/orders",
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )
        try:
            with self._opener(request, timeout=20) as response:
                response_payload: object = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - network errors are environment-specific.
            raise ProviderRequestError(f"PayPal order request failed: {exc}") from exc
        if not isinstance(response_payload, Mapping):
            raise ProviderRequestError("PayPal order response was not a JSON object.")
        order = dict(cast(Mapping[str, Any], response_payload))
        approval_url = ""
        links = order.get("links")
        if isinstance(links, list):
            for raw_link in cast(list[Any], links):
                link = dict(cast(Mapping[str, Any], raw_link)) if isinstance(raw_link, Mapping) else {}
                if str(link.get("rel") or "").lower() in {"approve", "payer-action"}:
                    approval_url = str(link.get("href") or "").strip()
                    break
        if not approval_url:
            raise ProviderRequestError("PayPal order response did not include an approval URL.")
        order["provider"] = "paypal"
        order["url"] = approval_url
        order["plan_code"] = profile.code
        order["customer_id"] = customer_id
        order["customer_email"] = email
        order["custom_id"] = custom_id
        return order

    def verify_webhook_signature(self, *, payload: bytes, headers: Mapping[str, str]) -> bool:
        if os.getenv("PAYPAL_WEBHOOK_SIGNATURE_MODE", "").strip().lower() == "mock":
            return str(headers.get("paypal-transmission-sig") or headers.get("PayPal-Transmission-Sig") or "") == "mock-valid"
        if not self.webhook_id:
            return False
        token = self._access_token()
        try:
            webhook_event: object = json.loads(payload.decode("utf-8") or "{}")
        except ValueError:
            return False
        verification_payload = {
            "auth_algo": str(headers.get("paypal-auth-algo") or headers.get("PayPal-Auth-Algo") or ""),
            "cert_url": str(headers.get("paypal-cert-url") or headers.get("PayPal-Cert-Url") or ""),
            "transmission_id": str(headers.get("paypal-transmission-id") or headers.get("PayPal-Transmission-Id") or ""),
            "transmission_sig": str(headers.get("paypal-transmission-sig") or headers.get("PayPal-Transmission-Sig") or ""),
            "transmission_time": str(headers.get("paypal-transmission-time") or headers.get("PayPal-Transmission-Time") or ""),
            "webhook_id": self.webhook_id,
            "webhook_event": webhook_event,
        }
        request = urllib.request.Request(
            f"{self.api_base_url}/v1/notifications/verify-webhook-signature",
            data=json.dumps(verification_payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener(request, timeout=20) as response:
                response_payload: object = json.loads(response.read().decode("utf-8"))
        except Exception:  # pragma: no cover - network errors are environment-specific.
            return False
        if not isinstance(response_payload, Mapping):
            return False
        return str(cast(Mapping[str, Any], response_payload).get("verification_status") or "").upper() == "SUCCESS"


def checkout_provider_from_env() -> CheckoutProvider:
    provider = os.getenv("PHOENIXGUARD_PAYMENT_PROVIDER", "stripe").strip().lower()
    if provider == "paypal":
        return PayPalCheckoutProvider()
    return StripeCheckoutProvider()


class ResendEmailProvider:
    def __init__(self, *, opener: Any | None = None) -> None:
        self.provider = os.getenv("PHOENIXGUARD_EMAIL_PROVIDER", "").strip().lower()
        self.api_key = os.getenv("RESEND_API_KEY", "").strip()
        self.from_email = os.getenv("RESEND_FROM_EMAIL", "").strip()
        self.api_url = os.getenv("RESEND_EMAILS_URL", "https://api.resend.com/emails").strip()
        self._opener = opener or urllib.request.urlopen
        self._smtp = SmtpEmailProvider()

    def status(self) -> ProviderStatus:
        if self.provider == "smtp" or (self.provider == "" and self._smtp.has_any_configuration()):
            return self._smtp.status()
        missing: list[str] = []
        if not self.api_key:
            missing.append("RESEND_API_KEY")
        if not self.from_email:
            missing.append("RESEND_FROM_EMAIL")
        return ProviderStatus(
            provider="resend",
            configured=not missing,
            mode="transactional",
            missing=tuple(missing),
        )

    def send_email_verification(
        self,
        *,
        to_email: str,
        full_name: str,
        verification_url: str,
        verification_code: str = "",
    ) -> dict[str, Any]:
        status = self.status()
        if status.provider == "smtp":
            return self._smtp.send_email_verification(
                to_email=to_email,
                full_name=full_name,
                verification_url=verification_url,
                verification_code=verification_code,
            )
        if not status.configured:
            raise ProviderConfigurationError("Resend email is not configured.")
        code_block = (
            "<p>Your confirmation code is:</p>"
            f"<p style=\"font-size:18px;font-weight:700;letter-spacing:0.04em;\">{verification_code}</p>"
            if verification_code
            else ""
        )
        payload: dict[str, object] = {
            "from": self.from_email,
            "to": [to_email],
            "subject": "Confirm your 808Fx Standard Hybrid account",
            "html": (
                f"<p>Hello {full_name},</p>"
                "<p>Confirm your email before accessing PhoenixGuard services.</p>"
                f"{code_block}"
                f"<p><a href=\"{verification_url}\">Confirm email</a></p>"
                "<p>If you did not create this account, ignore this message.</p>"
            ),
        }
        request = urllib.request.Request(
            self.api_url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with self._opener(request, timeout=20) as response:
                response_payload: object = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - network errors are environment-specific.
            raise ProviderRequestError(f"Resend email request failed: {exc}") from exc
        if not isinstance(response_payload, Mapping):
            raise ProviderRequestError("Resend email response was not a JSON object.")
        return dict(cast(Mapping[str, Any], response_payload))


class SmtpEmailProvider:
    def __init__(self) -> None:
        self.host = _env_first("SMTP_HOST", "PHOENIXGUARD_SMTP_HOST")
        self.port = int(_env_first("SMTP_PORT", "PHOENIXGUARD_SMTP_PORT") or "587")
        self.username = _env_first("SMTP_USERNAME", "PHOENIXGUARD_SMTP_USERNAME", "GMAIL_ADDRESS")
        self.password = _env_first("SMTP_PASSWORD", "PHOENIXGUARD_SMTP_PASSWORD", "GMAIL_APP_PASSWORD")
        self.from_email = _env_first("SMTP_FROM_EMAIL", "PHOENIXGUARD_SMTP_FROM_EMAIL", "GMAIL_ADDRESS")
        self.from_name = _env_first("SMTP_FROM_NAME", "PHOENIXGUARD_SMTP_FROM_NAME") or "808Fx Standard Hybrid"
        self.use_ssl = _env_bool("SMTP_USE_SSL", "PHOENIXGUARD_SMTP_USE_SSL", default=False)
        self.starttls = _env_bool("SMTP_STARTTLS", "PHOENIXGUARD_SMTP_STARTTLS", default=True)
        if not self.host and (self.username or self.password or self.from_email):
            self.host = "smtp.gmail.com"

    def has_any_configuration(self) -> bool:
        return any([self.host, self.username, self.password, self.from_email])

    def status(self) -> ProviderStatus:
        missing: list[str] = []
        if not self.host:
            missing.append("SMTP_HOST")
        if not self.username:
            missing.append("SMTP_USERNAME")
        if not self.password:
            missing.append("SMTP_PASSWORD")
        if not self.from_email:
            missing.append("SMTP_FROM_EMAIL")
        return ProviderStatus(
            provider="smtp",
            configured=not missing,
            mode="transactional",
            missing=tuple(missing),
        )

    def send_email_verification(
        self,
        *,
        to_email: str,
        full_name: str,
        verification_url: str,
        verification_code: str = "",
    ) -> dict[str, Any]:
        status = self.status()
        if not status.configured:
            raise ProviderConfigurationError("SMTP email is not configured.")
        message = EmailMessage()
        message["Subject"] = "Confirm your 808Fx Standard Hybrid account"
        message["From"] = f"{self.from_name} <{self.from_email}>"
        message["To"] = to_email
        text = (
            f"Hello {full_name},\n\n"
            "Confirm your email before accessing PhoenixGuard services.\n\n"
            f"Confirmation code: {verification_code}\n\n"
            f"Confirmation link: {verification_url}\n\n"
            "If you did not create this account, ignore this message.\n"
        )
        message.set_content(text)
        escaped_name = html.escape(full_name)
        escaped_code = html.escape(verification_code)
        escaped_url = html.escape(verification_url, quote=True)
        message.add_alternative(
            (
                f"<p>Hello {escaped_name},</p>"
                "<p>Confirm your email before accessing PhoenixGuard services.</p>"
                "<p>Your confirmation code is:</p>"
                f"<p style=\"font-size:18px;font-weight:700;letter-spacing:0.04em;\">{escaped_code}</p>"
                f"<p><a href=\"{escaped_url}\">Confirm email</a></p>"
                "<p>If you did not create this account, ignore this message.</p>"
            ),
            subtype="html",
        )

        try:
            context = ssl.create_default_context()
            if self.use_ssl:
                with smtplib.SMTP_SSL(self.host, self.port, timeout=20, context=context) as smtp:
                    smtp.login(self.username, self.password)
                    smtp.send_message(message)
            else:
                with smtplib.SMTP(self.host, self.port, timeout=20) as smtp:
                    smtp.ehlo()
                    if self.starttls:
                        smtp.starttls(context=context)
                        smtp.ehlo()
                    smtp.login(self.username, self.password)
                    smtp.send_message(message)
        except Exception as exc:  # pragma: no cover - environment and network specific.
            raise ProviderRequestError(f"SMTP email request failed: {exc}") from exc
        return {"provider": "smtp", "status": "sent"}


def _env_first(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _env_bool(*names: str, default: bool) -> bool:
    value = _env_first(*names).lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}
