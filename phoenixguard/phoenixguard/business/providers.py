from __future__ import annotations

import html
import json
import os
import smtplib
import ssl
import urllib.parse
import urllib.request
from dataclasses import dataclass
from email.message import EmailMessage
from typing import Any, Mapping

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


class StripeCheckoutProvider:
    plan_price_env = {
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
        missing = []
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
        missing = []
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
        form = {
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
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - network errors are environment-specific.
            raise ProviderRequestError(f"Stripe checkout request failed: {exc}") from exc
        if not isinstance(payload, Mapping) or not payload.get("url"):
            raise ProviderRequestError("Stripe checkout response did not include a hosted URL.")
        return dict(payload)


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
        missing = []
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
        payload = {
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
                response_payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:  # pragma: no cover - network errors are environment-specific.
            raise ProviderRequestError(f"Resend email request failed: {exc}") from exc
        if not isinstance(response_payload, Mapping):
            raise ProviderRequestError("Resend email response was not a JSON object.")
        return dict(response_payload)


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
        missing = []
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
