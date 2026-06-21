from __future__ import annotations

from typing import Any
import uuid

from fastapi import Body, Depends, FastAPI, Header, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .auth import (
    BusinessAuthError,
    ConnectorPrincipal,
    CustomerPrincipal,
    MOCK_STRIPE_WEBHOOK_SECRET,
    MockBusinessAuthProvider,
)
from .billing import (
    BillingPayloadError,
    BillingService,
    BillingSignatureError,
    StripeWebhookVerifier,
)
from .license import ConnectorContext, LicenseService
from .repository import AuthorizationError, ConflictError, MockBusinessRepository, NotFoundError


class DisclosureAcceptInput(BaseModel):
    version: str | None = None
    license_id: str | None = None


class BrokerAccountInput(BaseModel):
    broker_server: str = Field(min_length=1)
    mt4_account_number: str = Field(min_length=1)
    label: str | None = None


class DeviceRegisterInput(BaseModel):
    license_key: str = Field(min_length=1)
    device_fingerprint: str = Field(min_length=1)
    device_label: str | None = None
    connector_version: str = Field(min_length=1)


class DeviceHeartbeatInput(BaseModel):
    connector_version: str | None = None
    ea_version: str | None = None
    mt4_terminal_build: str | None = None
    status: str | None = None
    detail: str | None = None


def create_business_app(
    *,
    repository: MockBusinessRepository | None = None,
    auth_provider: MockBusinessAuthProvider | None = None,
    license_service: LicenseService | None = None,
    billing_service: BillingService | None = None,
    stripe_webhook_secret: str = MOCK_STRIPE_WEBHOOK_SECRET,
) -> FastAPI:
    repo = repository or MockBusinessRepository.seeded()
    auth = auth_provider or MockBusinessAuthProvider()
    licenses = license_service or LicenseService(repository=repo, auth_provider=auth)
    billing = billing_service or BillingService(
        repository=repo,
        stripe_verifier=StripeWebhookVerifier(secret=stripe_webhook_secret),
    )

    app = FastAPI(
        title="PhoenixGuard Commercial API Skeleton",
        version="0.1.0",
        description="Mock/test-mode commercial API for onboarding, licensing, releases, and billing webhooks.",
    )
    app.state.business_repository = repo
    app.state.business_auth_provider = auth
    app.state.business_license_service = licenses
    app.state.business_billing_service = billing

    @app.middleware("http")
    async def _request_id_middleware(request: Request, call_next: Any) -> Response:
        request_id = request.headers.get("X-Request-ID") or f"req_{uuid.uuid4().hex[:12]}"
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(BusinessAuthError)
    async def _auth_error_handler(request: Request, exc: BusinessAuthError) -> JSONResponse:
        return _error_response(request, status.HTTP_401_UNAUTHORIZED, str(exc))

    @app.exception_handler(AuthorizationError)
    async def _authorization_error_handler(request: Request, exc: AuthorizationError) -> JSONResponse:
        return _error_response(request, status.HTTP_403_FORBIDDEN, str(exc))

    @app.exception_handler(NotFoundError)
    async def _not_found_error_handler(request: Request, exc: NotFoundError) -> JSONResponse:
        return _error_response(request, status.HTTP_404_NOT_FOUND, str(exc))

    @app.exception_handler(ConflictError)
    async def _conflict_error_handler(request: Request, exc: ConflictError) -> JSONResponse:
        return _error_response(request, status.HTTP_409_CONFLICT, str(exc))

    @app.exception_handler(BillingSignatureError)
    async def _billing_signature_error_handler(request: Request, exc: BillingSignatureError) -> JSONResponse:
        return _error_response(request, status.HTTP_400_BAD_REQUEST, str(exc))

    @app.exception_handler(BillingPayloadError)
    async def _billing_payload_error_handler(request: Request, exc: BillingPayloadError) -> JSONResponse:
        return _error_response(request, status.HTTP_400_BAD_REQUEST, str(exc))

    @app.post("/v1/disclosures/accept", status_code=status.HTTP_204_NO_CONTENT)
    def accept_disclosure(
        request: Request,
        payload: DisclosureAcceptInput | None = Body(default=None),
        principal: CustomerPrincipal = Depends(_require_customer_principal),
    ) -> Response:
        body = payload or DisclosureAcceptInput()
        _license_service(request).accept_disclosure(
            customer_id=principal.customer_id,
            version=body.version,
            license_id=body.license_id,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("User-Agent"),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/v1/broker-accounts", status_code=status.HTTP_201_CREATED)
    def create_broker_account(
        request: Request,
        payload: BrokerAccountInput,
        principal: CustomerPrincipal = Depends(_require_customer_principal),
    ) -> dict[str, Any]:
        return _license_service(request).create_broker_account(
            customer_id=principal.customer_id,
            broker_server=payload.broker_server,
            mt4_account_number=payload.mt4_account_number,
            label=payload.label,
        )

    @app.get("/v1/licenses")
    def list_licenses(
        request: Request,
        principal: CustomerPrincipal = Depends(_require_customer_principal),
    ) -> dict[str, Any]:
        return _license_service(request).list_customer_licenses(customer_id=principal.customer_id)

    @app.post("/v1/device/register", status_code=status.HTTP_201_CREATED)
    def register_device(
        request: Request,
        payload: DeviceRegisterInput,
    ) -> dict[str, Any]:
        return _license_service(request).register_device(
            license_key=payload.license_key,
            device_fingerprint=payload.device_fingerprint,
            device_label=payload.device_label,
            connector_version=payload.connector_version,
        )

    @app.post("/v1/device/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
    def device_heartbeat(
        request: Request,
        payload: DeviceHeartbeatInput | None = Body(default=None),
        context: ConnectorContext = Depends(_require_connector_context),
    ) -> Response:
        body = payload or DeviceHeartbeatInput()
        _license_service(request).heartbeat(
            context=context,
            connector_version=body.connector_version,
            ea_version=body.ea_version,
            mt4_terminal_build=body.mt4_terminal_build,
            status=body.status,
            detail=body.detail,
            ip_address=_client_ip(request),
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/v1/entitlements/current")
    def current_entitlement(
        request: Request,
        context: ConnectorContext = Depends(_require_connector_context),
    ) -> dict[str, Any]:
        return _license_service(request).current_entitlement(context=context)

    @app.get("/v1/releases/latest")
    def latest_release(
        request: Request,
        principal: CustomerPrincipal = Depends(_require_customer_principal),
    ) -> dict[str, Any]:
        return _license_service(request).latest_release_for_customer(customer_id=principal.customer_id)

    @app.post("/v1/webhooks/stripe")
    async def stripe_webhook(request: Request) -> dict[str, Any]:
        payload = await request.body()
        return _billing_service(request).handle_stripe_webhook(
            payload=payload,
            signature_header=request.headers.get("Stripe-Signature"),
            ip_address=_client_ip(request),
        )

    return app


create_app = create_business_app


def _auth_provider(request: Request) -> MockBusinessAuthProvider:
    return request.app.state.business_auth_provider


def _license_service(request: Request) -> LicenseService:
    return request.app.state.business_license_service


def _billing_service(request: Request) -> BillingService:
    return request.app.state.business_billing_service


def _require_customer_principal(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> CustomerPrincipal:
    return _auth_provider(request).authenticate_customer_header(authorization)


def _require_connector_context(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> ConnectorContext:
    principal: ConnectorPrincipal = _auth_provider(request).authenticate_connector_header(authorization)
    return _license_service(request).connector_context(principal)


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client is not None else None


def _error_response(request: Request, status_code: int, error: str) -> JSONResponse:
    request_id = str(getattr(request.state, "request_id", "") or request.headers.get("X-Request-ID") or "req_unknown")
    return JSONResponse(
        status_code=status_code,
        content={"error": error, "request_id": request_id},
        headers={"X-Request-ID": request_id},
    )
