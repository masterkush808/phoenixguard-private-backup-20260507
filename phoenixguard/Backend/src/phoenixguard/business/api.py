from __future__ import annotations

import os
import json
from typing import Any, Mapping, cast

from fastapi import Body, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .packages import package_profile_for_plan, payments_are_paused
from .commands import latest_command_for_context
from .providers import ProviderConfigurationError, ProviderRequestError, ResendEmailProvider, StripeCheckoutProvider
from .store import (
    EMAIL_VERIFICATION_TTL_SECONDS,
    FREE_PREVIEW_PLAN_CODE,
    PASSWORD_MAX_LENGTH,
    PASSWORD_MIN_LENGTH,
    BusinessStore,
    Customer,
    Device,
    RateLimitExceeded,
    get_business_store,
    runtime_policy_for_plan,
)
from .tracker_access import (
    TRACKER_DASHBOARD_ROUTE,
    blocked_command_response,
    evaluate_tracker_access,
    register_business_tracker_access_routes,
    tracker_access_http_exception,
)


class LoginRequest(BaseModel):
    email: str = Field(default="operator@808fx.mock", max_length=254)
    password: str | None = Field(default=None)


class RegisterRequest(BaseModel):
    email: str = Field(max_length=254)
    full_name: str = Field(min_length=1, max_length=160)
    password: str = Field(min_length=PASSWORD_MIN_LENGTH, max_length=PASSWORD_MAX_LENGTH)


class VerifyEmailRequest(BaseModel):
    token: str | None = Field(default=None, max_length=256)
    verification_token: str | None = Field(default=None, max_length=256)


class ResendVerificationRequest(BaseModel):
    email: str = Field(max_length=254)


class CheckoutStartRequest(BaseModel):
    plan_code: str = Field(default="hybrid-standard-6h")


class BrokerAccountInput(BaseModel):
    broker_server: str
    mt4_account_number: str
    label: str | None = None

    model_config = {"extra": "allow"}


class DeviceRegisterInput(BaseModel):
    license_key: str
    device_fingerprint: str
    connector_version: str
    device_label: str | None = None


class HeartbeatInput(BaseModel):
    connector_version: str | None = None
    ea_version: str | None = None
    mt4_terminal_build: str | None = None
    detail: str | None = None


def _customer_by_email(store: BusinessStore, email: str) -> Customer | None:
    normalized = str(email or "").strip().lower()
    for customer in store.customers.values():
        if customer.email.lower() == normalized:
            return customer
    return None


def _public_customer(customer: Customer) -> dict[str, Any]:
    return {
        "id": customer.id,
        "email": customer.email,
        "full_name": customer.full_name,
        "status": customer.status,
        "is_admin": customer.is_admin,
        "disclosure_accepted": customer.disclosure_accepted,
        "email_verified": customer.email_verified,
    }


def _require_customer(store: BusinessStore, authorization: str | None) -> Customer:
    customer = store.customer_for_token(authorization)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Customer bearer token required.")
    if customer.status != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customer is not active.")
    return customer


def _require_admin(store: BusinessStore, authorization: str | None) -> Customer:
    customer = _require_customer(store, authorization)
    if not customer.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access required.")
    return customer


def _require_connector(store: BusinessStore, authorization: str | None) -> Device:
    device = store.device_for_connector_token(authorization)
    if device is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Connector bearer token required.")
    return device


def _reject_sensitive_broker_payload(payload: Mapping[str, Any]) -> None:
    blocked_keys = {"password", "broker_password", "mt4_password", "terminal_password", "investor_password"}
    provided = {str(key).lower() for key in payload}
    if provided & blocked_keys:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Broker passwords are never collected.")


def _client_fingerprint(request: Request, *parts: str) -> str:
    forwarded_for = str(request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    host = forwarded_for or (request.client.host if request.client else "unknown")
    user_agent = str(request.headers.get("user-agent") or "unknown")[:96]
    suffix = ":".join(str(part or "").strip().lower() for part in parts if str(part or "").strip())
    return f"{host}:{user_agent}:{suffix}"


def _enforce_rate_limit(
    store: BusinessStore,
    *,
    scope: str,
    key: str,
    limit: int,
    window_seconds: int,
) -> None:
    try:
        store.check_rate_limit(scope=scope, key=key, limit=limit, window_seconds=window_seconds)
    except RateLimitExceeded as exc:
        store.audit(
            actor_type="anonymous",
            actor_id="rate-limit",
            action=f"{scope}.rate_limited",
            metadata={"key": key, "retry_after_seconds": exc.retry_after_seconds},
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"code": "rate_limited", "retry_after_seconds": exc.retry_after_seconds},
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc


def _dev_auth_tools_enabled() -> bool:
    return os.getenv("PHOENIXGUARD_DEV_AUTH_TOOLS", "").strip().lower() in {"1", "true", "yes", "on"}


def _email_verification_payload(
    *,
    sent: bool,
    provider: str,
    configuration: Mapping[str, Any],
    error: Mapping[str, Any] | None = None,
    verification_token: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "sent": sent,
        "provider": provider,
        "configuration": dict(configuration),
        "expires_in_seconds": EMAIL_VERIFICATION_TTL_SECONDS,
        "error": dict(error) if error else None,
    }
    if _dev_auth_tools_enabled() and verification_token:
        payload["development_token"] = verification_token
    return payload


def register_business_routes(app: FastAPI, store: BusinessStore | None = None) -> None:
    business_store = store or get_business_store()
    stripe_provider = StripeCheckoutProvider()
    email_provider = ResendEmailProvider()
    register_business_tracker_access_routes(
        app,
        store=business_store,
        dashboard_route=os.getenv(
            "PHOENIXGUARD_TRACKER_DASHBOARD_URL",
            TRACKER_DASHBOARD_ROUTE,
        ).strip() or TRACKER_DASHBOARD_ROUTE,
    )
    configured_origins = [
        origin.strip()
        for origin in os.getenv("PHOENIXGUARD_WEB_ORIGINS", "").split(",")
        if origin.strip()
    ]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
            "http://localhost:3210",
            "http://127.0.0.1:3210",
            "http://localhost:3310",
            "http://127.0.0.1:3310",
            *configured_origins,
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Stripe-Signature"],
    )

    @app.get("/v1/business/health")
    def business_health() -> dict[str, Any]:
        stripe_status = stripe_provider.status()
        email_status = email_provider.status()
        return {
            "status": "ok",
            "mode": "production-ready",
            "payments_paused": payments_are_paused(),
            "provider_adapters": {
                "billing": stripe_status.as_dict(),
                "email": email_status.as_dict(),
                "storage": {
                    "provider": "in-memory-development",
                    "configured": False,
                    "missing": ["POSTGRES_DSN"],
                },
            },
        }

    @app.get("/v1/packages")
    def package_catalog() -> dict[str, Any]:
        return {
            "payments_paused": payments_are_paused(),
            "packages": business_store.package_catalog(),
        }

    @app.post("/v1/public/checkout/start")
    def start_checkout(
        request: CheckoutStartRequest | None = Body(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        customer = _require_customer(business_store, authorization)
        if not customer.email_verified:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email verification required before checkout.")
        plan_code = (request.plan_code if request else "hybrid-standard-6h") or "hybrid-standard-6h"
        try:
            package_profile = package_profile_for_plan(plan_code)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported package selection.") from exc
        if not package_profile.public_visible:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported package selection.")
        if package_profile.code == FREE_PREVIEW_PLAN_CODE:
            license_record = business_store.grant_free_preview_license(customer=customer)
            session = business_store.record_checkout_session(
                customer=customer,
                provider_payload={
                    "id": f"free_preview_{customer.id}",
                    "provider": "free-preview",
                    "status": "activated",
                    "plan_code": FREE_PREVIEW_PLAN_CODE,
                },
            )
            return {
                "checkout_url": "",
                "checkout_session_id": session["id"],
                "mode": "free-preview",
                "provider": "free-preview",
                "plan_code": FREE_PREVIEW_PLAN_CODE,
                "license": business_store.license_payload(license_record),
                "runtime_policy": runtime_policy_for_plan(FREE_PREVIEW_PLAN_CODE),
                "package_profile": business_store.package_profile_payload(FREE_PREVIEW_PLAN_CODE),
                "message": "Free preview activated. Disclosure, broker, and device checks are still required before protected tracker access.",
                "risk_warning": "Trading carries risk. PhoenixGuard does not guarantee profit and is not financial advice.",
            }
        if not package_profile.self_service:
            return business_store.stage_review_package_selection(customer=customer, plan_code=package_profile.code)
        if payments_are_paused():
            return business_store.stage_paid_package_selection(customer=customer, plan_code=package_profile.code)
        try:
            checkout = stripe_provider.create_subscription_checkout(
                customer_id=customer.id,
                email=customer.email,
                plan_code=package_profile.code,
            )
            checkout["plan_code"] = package_profile.code
        except ProviderConfigurationError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "stripe_configuration_required",
                    "message": str(exc),
                    "status": stripe_provider.status().as_dict(),
                },
            ) from exc
        except ProviderRequestError as exc:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        session = business_store.record_checkout_session(customer=customer, provider_payload=checkout)
        return {
            "checkout_url": session["url"],
            "checkout_session_id": session["id"],
            "mode": stripe_provider.status().mode,
            "provider": "stripe",
            "plan_code": package_profile.code,
            "package_profile": business_store.package_profile_payload(package_profile.code),
            "risk_warning": "Trading carries risk. PhoenixGuard does not guarantee profit and is not financial advice.",
        }

    @app.post("/v1/auth/register", status_code=status.HTTP_201_CREATED)
    def register(payload: RegisterRequest, http_request: Request) -> dict[str, Any]:
        normalized_email = str(payload.email or "").strip().lower()
        _enforce_rate_limit(
            business_store,
            scope="auth.register.ip",
            key=_client_fingerprint(http_request),
            limit=12,
            window_seconds=60 * 60,
        )
        _enforce_rate_limit(
            business_store,
            scope="auth.register.email",
            key=normalized_email,
            limit=3,
            window_seconds=60 * 60,
        )
        try:
            customer, _token, verification_token = business_store.register_customer(
                email=payload.email,
                full_name=payload.full_name,
                password=payload.password,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        verification_url = os.getenv(
            "PHOENIXGUARD_EMAIL_VERIFICATION_URL",
            "http://127.0.0.1:3000/login?verify_token={token}",
        ).replace("{token}", verification_token)
        email_status = email_provider.status()
        email_sent = False
        email_error: dict[str, Any] | None = None
        try:
            email_provider.send_email_verification(
                to_email=customer.email,
                full_name=customer.full_name,
                verification_url=verification_url,
                verification_code=verification_token,
            )
            email_sent = True
        except ProviderConfigurationError as exc:
            email_error = {
                "code": "email_configuration_required",
                "message": str(exc),
                "status": email_status.as_dict(),
            }
        except ProviderRequestError as exc:
            email_error = {"code": "email_delivery_failed", "message": str(exc)}
        return {
            "customer": _public_customer(customer),
            "email_verification": _email_verification_payload(
                sent=email_sent,
                provider=email_status.provider,
                configuration=email_status.as_dict(),
                error=email_error,
                verification_token=verification_token,
            ),
            "onboarding": business_store.access_gates_for_customer(customer),
        }

    @app.post("/v1/auth/verify-email")
    def verify_email(payload: VerifyEmailRequest, http_request: Request) -> dict[str, Any]:
        token = str(payload.token or payload.verification_token or "").strip()
        _enforce_rate_limit(
            business_store,
            scope="auth.verify.ip",
            key=_client_fingerprint(http_request),
            limit=20,
            window_seconds=15 * 60,
        )
        _enforce_rate_limit(
            business_store,
            scope="auth.verify.token",
            key=token[:24],
            limit=8,
            window_seconds=15 * 60,
        )
        if not token:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email verification token is required.")
        try:
            customer = business_store.verify_customer_email(token=token)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Email verification token not found.") from exc
        access_token = business_store.issue_customer_session_token(customer)
        business_store.clear_rate_limit(scope="auth.verify.token", key=token[:24])
        return {
            "access_token": access_token,
            "token_type": "bearer",
            "customer": _public_customer(customer),
            "onboarding": business_store.access_gates_for_customer(customer),
        }

    @app.post("/v1/auth/verification/resend")
    def resend_email_verification(payload: ResendVerificationRequest, http_request: Request) -> dict[str, Any]:
        normalized_email = str(payload.email or "").strip().lower()
        _enforce_rate_limit(
            business_store,
            scope="auth.verification_resend.ip",
            key=_client_fingerprint(http_request),
            limit=10,
            window_seconds=60 * 60,
        )
        _enforce_rate_limit(
            business_store,
            scope="auth.verification_resend.email",
            key=normalized_email,
            limit=4,
            window_seconds=60 * 60,
        )
        customer, verification_token, retry_after = business_store.resend_email_verification_token(email=normalized_email)
        if retry_after > 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"code": "verification_resend_cooldown", "retry_after_seconds": retry_after},
                headers={"Retry-After": str(retry_after)},
            )
        email_status = email_provider.status()
        email_sent = False
        email_error: dict[str, Any] | None = None
        if customer is not None and verification_token:
            verification_url = os.getenv(
                "PHOENIXGUARD_EMAIL_VERIFICATION_URL",
                "http://127.0.0.1:3000/login?verify_token={token}",
            ).replace("{token}", verification_token)
            try:
                email_provider.send_email_verification(
                    to_email=customer.email,
                    full_name=customer.full_name,
                    verification_url=verification_url,
                    verification_code=verification_token,
                )
                email_sent = True
            except ProviderConfigurationError as exc:
                email_error = {
                    "code": "email_configuration_required",
                    "message": str(exc),
                    "status": email_status.as_dict(),
                }
            except ProviderRequestError as exc:
                email_error = {"code": "email_delivery_failed", "message": str(exc)}
        return {
            "accepted": True,
            "email_verification": _email_verification_payload(
                sent=email_sent,
                provider=email_status.provider,
                configuration=email_status.as_dict(),
                error=email_error,
                verification_token=verification_token or "",
            ),
        }

    @app.post("/v1/auth/login")
    def login(payload: LoginRequest, http_request: Request) -> dict[str, Any]:
        normalized_email = str(payload.email or "").strip().lower()
        _enforce_rate_limit(
            business_store,
            scope="auth.login.ip",
            key=_client_fingerprint(http_request),
            limit=30,
            window_seconds=15 * 60,
        )
        _enforce_rate_limit(
            business_store,
            scope="auth.login.email",
            key=normalized_email,
            limit=8,
            window_seconds=15 * 60,
        )
        authenticated = business_store.authenticate_customer(email=payload.email, password=payload.password)
        if authenticated is None:
            customer = _customer_by_email(business_store, payload.email)
            business_store.audit(
                actor_type="customer" if customer else "anonymous",
                actor_id=customer.id if customer else "unknown",
                action="auth.login_failed",
                target_type="customer",
                target_id=customer.id if customer else "",
            )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown customer or invalid password.")
        customer, token = authenticated
        if not customer.email_verified:
            business_store.audit(
                actor_type="customer",
                actor_id=customer.id,
                action="auth.login_blocked_unverified_email",
                target_type="customer",
                target_id=customer.id,
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email verification required before login.")
        business_store.clear_rate_limit(scope="auth.login.email", key=normalized_email)
        business_store.audit(
            actor_type="customer",
            actor_id=customer.id,
            action="auth.login",
            target_type="customer",
            target_id=customer.id,
        )
        return {
            "access_token": token,
            "token_type": "bearer",
            "customer": _public_customer(customer),
            "onboarding": business_store.access_gates_for_customer(customer),
        }

    @app.get("/v1/me")
    def me(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        customer = _require_customer(business_store, authorization)
        snapshot = business_store.snapshot_customer(customer)
        snapshot["customer"] = _public_customer(customer)
        return snapshot

    @app.get("/v1/onboarding/status")
    def onboarding_status(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        customer = _require_customer(business_store, authorization)
        return business_store.access_gates_for_customer(customer)

    @app.post("/v1/disclosures/accept", status_code=status.HTTP_204_NO_CONTENT)
    def accept_disclosure(authorization: str | None = Header(default=None)) -> Response:
        customer = _require_customer(business_store, authorization)
        if not customer.email_verified:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email verification required before disclosure acceptance.")
        customer.disclosure_accepted = True
        business_store.audit(
            actor_type="customer",
            actor_id=customer.id,
            action="disclosure.accepted",
            target_type="customer",
            target_id=customer.id,
            metadata={"version": "risk-disclosure-2026-06"},
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/v1/broker-accounts", status_code=status.HTTP_201_CREATED)
    def create_broker_account(
        request: BrokerAccountInput,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        customer = _require_customer(business_store, authorization)
        if not customer.email_verified:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email verification required before broker binding.")
        payload = request.model_dump()
        _reject_sensitive_broker_payload(payload)
        try:
            account = business_store.create_broker_account(
                customer_id=customer.id,
                broker_server=request.broker_server,
                mt4_account_number=request.mt4_account_number,
                label=request.label or request.broker_server,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return {
            "id": account.id,
            "broker_server_label": account.broker_server_label,
            "account_number_masked": account.account_number_masked,
            "status": account.status,
        }

    @app.get("/v1/licenses")
    def list_licenses(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        customer = _require_customer(business_store, authorization)
        return {
            "licenses": [business_store.license_payload(license_record) for license_record in business_store.customer_licenses(customer.id)]
        }

    @app.post("/v1/device/register", status_code=status.HTTP_201_CREATED)
    def register_device(request: DeviceRegisterInput) -> dict[str, Any]:
        try:
            device, license_record = business_store.register_device(
                license_key=request.license_key,
                device_fingerprint=request.device_fingerprint,
                device_label=request.device_label or "MT4 connector",
                connector_version=request.connector_version,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="License key not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return {
            "device_id": device.id,
            "license_id": license_record.id,
            "connector_token": device.connector_token,
            "token_type": "bearer",
        }

    @app.post("/v1/device/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
    def device_heartbeat(
        request: HeartbeatInput | None = None,
        authorization: str | None = Header(default=None),
    ) -> Response:
        device = _require_connector(business_store, authorization)
        business_store.record_heartbeat(device=device, payload=(request.model_dump() if request else {}))
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/v1/entitlements/current")
    def current_entitlement(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        device = _require_connector(business_store, authorization)
        return business_store.entitlement_for_device(device)

    @app.get("/v1/commands/latest")
    def latest_command(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        decision = evaluate_tracker_access(business_store, authorization, connector_only=True)
        if decision.auth_type == "none":
            raise tracker_access_http_exception(decision)
        if not decision.allowed:
            return blocked_command_response(decision)
        device = decision.device
        if device is None:
            raise tracker_access_http_exception(decision)
        entitlement = business_store.entitlement_for_device(device)
        return latest_command_for_context(
            entitlement_status=str(entitlement["status"]),
            license_id=str(entitlement["license_id"]),
            device_id=device.id,
            account_bound=bool(entitlement["account_bound"]),
            device_status=device.status,
            update_required=False,
            internal_packet=business_store.current_mock_packet(device=device),
        )

    @app.get("/v1/releases/latest")
    @app.get("/v1/releases/connector/latest")
    def latest_release(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        customer_or_device = business_store.customer_for_token(authorization)
        connector_device = business_store.device_for_connector_token(authorization)
        if customer_or_device is None and connector_device is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required.")
        if isinstance(customer_or_device, Customer):
            gates = business_store.access_gates_for_customer(customer_or_device)
            if not gates["allowed"]:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "access_gates_incomplete", **gates})
        if connector_device is not None:
            entitlement = business_store.entitlement_for_device(connector_device)
            if (
                entitlement["status"] not in {"active", "trialing"}
                or not entitlement.get("account_bound", False)
                or not entitlement.get("disclosure_accepted", False)
                or not entitlement.get("email_verified", False)
            ):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "connector_access_gates_incomplete", "entitlement": entitlement},
                )
        release = business_store.release_payload()
        return release

    @app.post("/v1/webhooks/stripe")
    async def stripe_webhook(
        request: Request,
        stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"),
    ) -> dict[str, Any]:
        payload = await request.body()
        if not business_store.billing_provider.verify_webhook_signature(
            payload=payload,
            signature_header=str(stripe_signature or ""),
        ):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid Stripe signature.")
        try:
            event: object = json.loads(payload.decode("utf-8") or "{}")
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook JSON.") from exc
        event_payload: Mapping[str, Any] = cast(Mapping[str, Any], event) if isinstance(event, Mapping) else {}
        return business_store.apply_stripe_event(event_payload)

    @app.get("/v1/admin/customers")
    def admin_customers(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _require_admin(business_store, authorization)
        return {
            "customers": [
                {
                    "id": customer.id,
                    "email": customer.email,
                    "full_name": customer.full_name,
                    "status": customer.status,
                    "license_count": len(business_store.customer_licenses(customer.id)),
                    "disclosure_accepted": customer.disclosure_accepted,
                }
                for customer in business_store.customers.values()
                if not customer.is_admin
            ],
            "audit_event_count": len(business_store.audit_events),
        }

    @app.post("/v1/admin/licenses/{license_id}/revoke", status_code=status.HTTP_204_NO_CONTENT)
    def admin_revoke_license(
        license_id: str,
        body: Mapping[str, Any] = Body(default={}),
        authorization: str | None = Header(default=None),
    ) -> Response:
        admin = _require_admin(business_store, authorization)
        license_record = business_store.licenses.get(license_id)
        if license_record is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="License not found.")
        license_record.status = "revoked"
        license_record.revoke_reason = str(body.get("reason") or "Revoked by admin.")
        business_store.audit(
            actor_type="admin",
            actor_id=admin.id,
            action="license.revoked",
            target_type="license",
            target_id=license_id,
            metadata={"reason": license_record.revoke_reason},
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/v1/admin/customers/{customer_id}/family-lifetime-license", status_code=status.HTTP_201_CREATED)
    def admin_grant_family_lifetime_license(
        customer_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        admin = _require_admin(business_store, authorization)
        customer = business_store.customers.get(customer_id)
        if customer is None or customer.is_admin:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found.")
        license_record = business_store.grant_internal_family_lifetime_license(customer=customer, admin=admin)
        return {
            "customer": _public_customer(customer),
            "license": business_store.license_payload(license_record),
            "message": "Internal family lifetime license granted. Disclosure, broker binding, device freshness, and command freshness gates still apply.",
        }

    app.state.business_route_handler_names = tuple(
        handler.__name__
        for handler in (
            business_health,
            package_catalog,
            start_checkout,
            register,
            verify_email,
            resend_email_verification,
            login,
            me,
            onboarding_status,
            accept_disclosure,
            create_broker_account,
            list_licenses,
            register_device,
            device_heartbeat,
            current_entitlement,
            latest_command,
            latest_release,
            stripe_webhook,
            admin_customers,
            admin_revoke_license,
            admin_grant_family_lifetime_license,
        )
    )
