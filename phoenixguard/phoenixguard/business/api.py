from __future__ import annotations

import json
from typing import Any, Mapping

from fastapi import Body, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .commands import latest_command_for_context
from .store import BusinessStore, Customer, Device, get_business_store


class LoginRequest(BaseModel):
    email: str = Field(default="operator@808fx.mock")
    password: str | None = Field(default=None, description="Accepted only for mock UI flow; never persisted.")


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


def _token_for_customer(store: BusinessStore, customer_id: str) -> str:
    for token, mapped_customer_id in store.tokens.items():
        if mapped_customer_id == customer_id:
            return token
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Mock token missing.")


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
    }


def _require_customer(store: BusinessStore, authorization: str | None) -> Customer:
    customer = store.customer_for_token(authorization)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Mock customer bearer token required.")
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


def register_business_routes(app: FastAPI, store: BusinessStore | None = None) -> None:
    business_store = store or get_business_store()

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "http://localhost:3001",
            "http://127.0.0.1:3001",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Stripe-Signature"],
    )

    @app.get("/v1/business/health")
    def business_health() -> dict[str, Any]:
        return {
            "status": "ok",
            "mode": "mock",
            "provider_adapters": {
                "billing": "stripe-mock-with-signature-path",
                "email": "resend-mock",
                "storage": "in-memory-seed",
            },
        }

    @app.post("/v1/public/checkout/start")
    def start_checkout() -> dict[str, Any]:
        return {
            "checkout_url": "https://billing.phoenixguard.mock/checkout/session/mock_808fx_standard",
            "mode": "test",
            "provider": "stripe-mock",
            "risk_warning": "Trading carries risk. PhoenixGuard does not guarantee profit and is not financial advice.",
        }

    @app.post("/v1/auth/login")
    def login(request: LoginRequest) -> dict[str, Any]:
        customer = _customer_by_email(business_store, request.email)
        if customer is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown mock customer.")
        token = _token_for_customer(business_store, customer.id)
        business_store.audit(
            actor_type="customer",
            actor_id=customer.id,
            action="auth.mock_login",
            target_type="customer",
            target_id=customer.id,
        )
        return {"access_token": token, "token_type": "bearer", "customer": _public_customer(customer)}

    @app.get("/v1/me")
    def me(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        customer = _require_customer(business_store, authorization)
        snapshot = business_store.snapshot_customer(customer)
        snapshot["customer"] = _public_customer(customer)
        return snapshot

    @app.post("/v1/disclosures/accept", status_code=status.HTTP_204_NO_CONTENT)
    def accept_disclosure(authorization: str | None = Header(default=None)) -> Response:
        customer = _require_customer(business_store, authorization)
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
        payload = request.model_dump()
        _reject_sensitive_broker_payload(payload)
        account = business_store.create_broker_account(
            customer_id=customer.id,
            broker_server=request.broker_server,
            mt4_account_number=request.mt4_account_number,
            label=request.label or request.broker_server,
        )
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
        device = _require_connector(business_store, authorization)
        entitlement = business_store.entitlement_for_device(device)
        if not entitlement.get("disclosure_accepted", False):
            return {
                "status": "NO_EXECUTION_PACKET",
                "command": {
                    "schema_version": "PG_MT4_EXECUTION_COMMAND_V2",
                    "status": "NO_EXECUTION_PACKET",
                    "execution_authority": False,
                    "reason": "Risk disclosure acceptance required before executable commands.",
                },
            }
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
        if customer_or_device is None and business_store.device_for_connector_token(authorization) is None:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required.")
        release = business_store.release_payload()
        if isinstance(customer_or_device, Customer):
            business_store.email_provider.send(
                customer=customer_or_device,
                template_key="release_download_ready",
                metadata={"release_id": release["id"]},
            )
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
            event = json.loads(payload.decode("utf-8") or "{}")
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook JSON.") from exc
        return business_store.apply_stripe_event(event if isinstance(event, Mapping) else {})

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
