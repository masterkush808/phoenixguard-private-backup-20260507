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
    BillingConfigurationError,
    BillingPayloadError,
    BillingProviderError,
    BillingService,
    BillingSignatureError,
    StripeCheckoutSessionClient,
    StripeWebhookVerifier,
)
from .email import EmailConfigurationError, EmailProviderError
from .license import ConnectorContext, LicenseService
from .onboarding import (
    BrokerSecretError,
    CustomerOnboardingService,
    EmailProviderConfigurationError,
    EmailVerificationProvider,
    build_email_verification_provider_from_env,
    reject_sensitive_broker_payload,
)
from .repository import AuthorizationError, ConflictError, MockBusinessRepository, NotFoundError


class CustomerRegisterInput(BaseModel):
    email: str = Field(min_length=3)
    full_name: str = Field(min_length=1)
    country_code: str | None = None
    phone: str | None = None


class EmailVerificationInput(BaseModel):
    verification_token: str = Field(min_length=16)


class EmailVerificationResendInput(BaseModel):
    email: str = Field(min_length=3)


class DisclosureAcceptInput(BaseModel):
    version: str | None = None
    license_id: str | None = None


class BrokerAccountInput(BaseModel):
    broker_server: str = Field(min_length=1)
    mt4_account_number: str = Field(min_length=1)
    label: str | None = None

    model_config = {"extra": "allow"}


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


class CheckoutStartInput(BaseModel):
    customer_email: str = Field(min_length=3)
    customer_id: str | None = None
    plan_code: str = Field(default="business", min_length=1)


def create_business_app(
    *,
    repository: MockBusinessRepository | None = None,
    auth_provider: MockBusinessAuthProvider | None = None,
    license_service: LicenseService | None = None,
    billing_service: BillingService | None = None,
    checkout_client: StripeCheckoutSessionClient | None = None,
    email_provider: EmailVerificationProvider | None = None,
    onboarding_service: CustomerOnboardingService | None = None,
    stripe_webhook_secret: str = MOCK_STRIPE_WEBHOOK_SECRET,
) -> FastAPI:
    repo = repository or MockBusinessRepository.seeded()
    auth = auth_provider or MockBusinessAuthProvider()
    licenses = license_service or LicenseService(repository=repo, auth_provider=auth)
    email = email_provider or build_email_verification_provider_from_env()
    onboarding = onboarding_service or CustomerOnboardingService(
        repository=repo,
        auth_provider=auth,
        email_provider=email,
    )
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
    app.state.business_checkout_client = checkout_client
    app.state.business_email_provider = email
    app.state.business_onboarding_service = onboarding

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

    @app.exception_handler(BillingConfigurationError)
    async def _billing_configuration_error_handler(request: Request, exc: BillingConfigurationError) -> JSONResponse:
        return _error_response(request, status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))

    @app.exception_handler(BillingProviderError)
    async def _billing_provider_error_handler(request: Request, exc: BillingProviderError) -> JSONResponse:
        return _error_response(request, status.HTTP_502_BAD_GATEWAY, str(exc))

    @app.exception_handler(EmailConfigurationError)
    async def _email_configuration_error_handler(request: Request, exc: EmailConfigurationError) -> JSONResponse:
        return _error_response(request, status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))

    @app.exception_handler(EmailProviderError)
    async def _email_provider_error_handler(request: Request, exc: EmailProviderError) -> JSONResponse:
        return _error_response(request, status.HTTP_502_BAD_GATEWAY, str(exc))

    @app.exception_handler(BrokerSecretError)
    async def _broker_secret_error_handler(request: Request, exc: BrokerSecretError) -> JSONResponse:
        return _error_response(request, status.HTTP_400_BAD_REQUEST, str(exc))

    @app.exception_handler(EmailProviderConfigurationError)
    async def _onboarding_email_provider_error_handler(
        request: Request,
        exc: EmailProviderConfigurationError,
    ) -> JSONResponse:
        return _error_response(request, status.HTTP_503_SERVICE_UNAVAILABLE, str(exc))

    @app.post("/v1/auth/register", status_code=status.HTTP_201_CREATED)
    def register_customer(request: Request, payload: CustomerRegisterInput) -> dict[str, Any]:
        return _onboarding_service(request).register_customer(
            email=payload.email,
            full_name=payload.full_name,
            country_code=payload.country_code,
            phone=payload.phone,
            ip_address=_client_ip(request),
        )

    @app.post("/v1/auth/verify-email")
    def verify_email(request: Request, payload: EmailVerificationInput) -> dict[str, Any]:
        return _onboarding_service(request).verify_email(
            verification_token=payload.verification_token,
            ip_address=_client_ip(request),
        )

    @app.post("/v1/auth/verification/resend")
    def resend_email_verification(
        request: Request,
        payload: EmailVerificationResendInput,
    ) -> dict[str, Any]:
        return _onboarding_service(request).resend_email_verification(
            email=payload.email,
            ip_address=_client_ip(request),
        )

    @app.post("/v1/public/checkout/start", status_code=status.HTTP_201_CREATED)
    def start_checkout(
        request: Request,
        payload: CheckoutStartInput,
    ) -> dict[str, Any]:
        return _checkout_client(request).create_session(
            customer_email=payload.customer_email,
            customer_id=payload.customer_id,
            plan_code=payload.plan_code,
            metadata={"source": "phoenixguard_business_api"},
        )

    @app.post("/v1/disclosures/accept", status_code=status.HTTP_204_NO_CONTENT)
    def accept_disclosure(
        request: Request,
        payload: DisclosureAcceptInput | None = Body(default=None),
        principal: CustomerPrincipal = Depends(_require_active_customer_principal),
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
        principal: CustomerPrincipal = Depends(_require_active_customer_principal),
    ) -> dict[str, Any]:
        reject_sensitive_broker_payload(payload.model_dump())
        return _license_service(request).create_broker_account(
            customer_id=principal.customer_id,
            broker_server=payload.broker_server,
            mt4_account_number=payload.mt4_account_number,
            label=payload.label,
        )

    @app.get("/v1/licenses")
    def list_licenses(
        request: Request,
        principal: CustomerPrincipal = Depends(_require_active_customer_principal),
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

    @app.get("/v1/commands/latest")
    def latest_command(
        request: Request,
        context: ConnectorContext = Depends(_require_connector_context),
    ) -> dict[str, Any]:
        return _license_service(request).latest_command_for_connector(context=context)

    @app.get("/v1/tracker/access")
    def tracker_access(
        request: Request,
        principal: CustomerPrincipal = Depends(_require_active_customer_principal),
    ) -> dict[str, Any]:
        return _license_service(request).tracker_access_for_customer(customer_id=principal.customer_id)

    @app.get("/v1/releases/latest")
    def latest_release(
        request: Request,
        principal: CustomerPrincipal = Depends(_require_active_customer_principal),
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

    app.state.business_route_handler_names = tuple(
        handler.__name__
        for handler in (
            _request_id_middleware,
            _auth_error_handler,
            _authorization_error_handler,
            _not_found_error_handler,
            _conflict_error_handler,
            _billing_signature_error_handler,
            _billing_payload_error_handler,
            _billing_configuration_error_handler,
            _billing_provider_error_handler,
            _email_configuration_error_handler,
            _email_provider_error_handler,
            _broker_secret_error_handler,
            _onboarding_email_provider_error_handler,
            register_customer,
            verify_email,
            resend_email_verification,
            start_checkout,
            accept_disclosure,
            create_broker_account,
            list_licenses,
            register_device,
            device_heartbeat,
            current_entitlement,
            latest_command,
            tracker_access,
            latest_release,
            stripe_webhook,
        )
    )
    return app


create_app = create_business_app


def _auth_provider(request: Request) -> MockBusinessAuthProvider:
    return request.app.state.business_auth_provider


def _license_service(request: Request) -> LicenseService:
    return request.app.state.business_license_service


def _billing_service(request: Request) -> BillingService:
    return request.app.state.business_billing_service


def _onboarding_service(request: Request) -> CustomerOnboardingService:
    return request.app.state.business_onboarding_service


def _checkout_client(request: Request) -> StripeCheckoutSessionClient:
    configured = request.app.state.business_checkout_client
    if configured is not None:
        return configured
    return StripeCheckoutSessionClient.from_env()


def _require_active_customer_principal(
    request: Request,
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> CustomerPrincipal:
    principal = _auth_provider(request).authenticate_customer_header(authorization)
    _license_service(request).require_active_customer(principal.customer_id)
    return principal


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
