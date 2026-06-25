from __future__ import annotations

from dataclasses import dataclass
import inspect
import time
from typing import Any, Mapping, cast

from fastapi import FastAPI, Header, HTTPException, status
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from .commands import build_status_command
from .store import BusinessStore, Customer, Device, License, Subscription


TRACKER_ACCESS_SCHEMA_VERSION = "PG_BUSINESS_TRACKER_ACCESS_V1"
TRACKER_HEALTH_SCHEMA_VERSION = "PG_BUSINESS_TRACKER_HEALTH_V1"
TRACKER_DASHBOARD_ROUTE = "/v1/mobile/window-tracker/dashboard"
TRACKER_DEFAULT_SESSION_ID = "pocket-live-8788"

GATE_REGISTRATION = "registration"
GATE_EMAIL = "email"
GATE_SUBSCRIPTION = "subscription"
GATE_DISCLOSURE = "disclosure"
GATE_BROKER = "broker"
GATE_DEVICE = "device"
TRACKER_GATE_ORDER = (
    GATE_REGISTRATION,
    GATE_EMAIL,
    GATE_SUBSCRIPTION,
    GATE_DISCLOSURE,
    GATE_BROKER,
    GATE_DEVICE,
)


@dataclass(frozen=True)
class TrackerGate:
    name: str
    passed: bool
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TrackerAccessDecision:
    allowed: bool
    auth_type: str
    customer: Customer | None
    license_record: License | None
    device: Device | None
    subscription: Subscription | None
    gates: Mapping[str, TrackerGate]
    entitlement: Mapping[str, Any]

    @property
    def blocked_gates(self) -> list[str]:
        return [
            gate_name
            for gate_name in TRACKER_GATE_ORDER
            if not self.gates[gate_name].passed
        ]

    @property
    def primary_reason(self) -> str:
        for gate_name in self.blocked_gates:
            reason = self.gates[gate_name].reason
            if reason:
                return reason
        return ""

    def as_dict(self) -> dict[str, Any]:
        customer = self.customer
        license_record = self.license_record
        device = self.device
        return {
            "schema_version": TRACKER_ACCESS_SCHEMA_VERSION,
            "allowed": self.allowed,
            "status": "allowed" if self.allowed else "blocked",
            "auth_type": self.auth_type,
            "customer_id": customer.id if customer is not None else "",
            "customer_email": customer.email if customer is not None else "",
            "license_id": license_record.id if license_record is not None else "",
            "device_id": device.id if device is not None else "",
            "blocked_gates": self.blocked_gates,
            "reason": self.primary_reason,
            "gates": {
                gate_name: self.gates[gate_name].as_dict()
                for gate_name in TRACKER_GATE_ORDER
            },
            "entitlement": dict(self.entitlement),
        }


def evaluate_tracker_access(
    store: BusinessStore,
    authorization: str | None,
    *,
    connector_only: bool = False,
    now_epoch: float | None = None,
) -> TrackerAccessDecision:
    """Resolve whether a customer or connector can enter the live tracker surface."""

    current = float(now_epoch if now_epoch is not None else time.time())
    device = store.device_for_connector_token(authorization)
    customer = store.customer_for_token(authorization) if device is None and not connector_only else None
    auth_type = "connector" if device is not None else "customer" if customer is not None else "none"

    license_record: License | None = None
    subscription: Subscription | None = None
    if device is not None:
        license_record = store.licenses.get(device.license_id)
        customer = store.customers.get(device.customer_id)
    elif customer is not None:
        license_record = _preferred_customer_license(store, customer.id, now_epoch=current)
        if license_record is not None:
            device = _preferred_license_device(store, license_record.id)

    if license_record is not None:
        subscription = store.subscriptions.get(license_record.subscription_id)

    gates = _build_gates(
        store=store,
        customer=customer,
        license_record=license_record,
        subscription=subscription,
        device=device,
        auth_type=auth_type,
        connector_only=connector_only,
        now_epoch=current,
    )
    allowed = all(gate.passed for gate in gates.values())
    entitlement = _entitlement_payload(store, device)
    return TrackerAccessDecision(
        allowed=allowed,
        auth_type=auth_type,
        customer=customer,
        license_record=license_record,
        device=device,
        subscription=subscription,
        gates=gates,
        entitlement=entitlement,
    )


def tracker_access_http_exception(decision: TrackerAccessDecision) -> HTTPException:
    status_code = status.HTTP_401_UNAUTHORIZED if decision.auth_type == "none" else status.HTTP_403_FORBIDDEN
    return HTTPException(status_code=status_code, detail=decision.as_dict())


def blocked_command_response(decision: TrackerAccessDecision) -> dict[str, Any]:
    status_code, reason = _command_status_for_blocked_access(decision)
    return {
        "status": status_code,
        "command": build_status_command(
            status_code,
            reason=reason,
        ),
        "tracker_access": decision.as_dict(),
    }


def register_business_tracker_access_routes(
    app: FastAPI,
    *,
    store: BusinessStore,
    dashboard_route: str = TRACKER_DASHBOARD_ROUTE,
    default_session_id: str = TRACKER_DEFAULT_SESSION_ID,
) -> None:
    @app.get("/app/tracker")
    def tracker_app_access(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        decision = evaluate_tracker_access(store, authorization)
        if not decision.allowed:
            raise tracker_access_http_exception(decision)
        return {
            **decision.as_dict(),
            "dashboard_url": dashboard_route,
            "default_session_id": default_session_id,
        }

    @app.get("/v1/business/tracker/health")
    async def business_tracker_health(
        session_id: str = default_session_id,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        decision = evaluate_tracker_access(store, authorization)
        if not decision.allowed:
            raise tracker_access_http_exception(decision)
        payload, status_code = await aggregate_tracker_health(
            app,
            session_id=session_id,
            decision=decision,
        )
        return JSONResponse(status_code=status_code, content=payload)

    app.state.business_tracker_route_handler_names = (
        tracker_app_access.__name__,
        business_tracker_health.__name__,
    )


async def aggregate_tracker_health(
    app: FastAPI,
    *,
    session_id: str,
    decision: TrackerAccessDecision,
) -> tuple[dict[str, Any], int]:
    components: dict[str, Any] = {}
    component_specs: tuple[tuple[str, str, dict[str, str]], ...] = (
        (
            "mobile_api",
            "/v1/mobile/health",
            {},
        ),
        (
            "tracker_session",
            "/v1/mobile/window-tracker/sessions/{session_id}/health",
            {"session_id": str(session_id or "").strip() or TRACKER_DEFAULT_SESSION_ID},
        ),
    )
    for name, route_path, kwargs in component_specs:
        components[name] = await _read_route_health(app, route_path, kwargs)

    tracker_component = components["tracker_session"]
    if tracker_component["status"] == "ok":
        overall_status = "ok"
        status_code = status.HTTP_200_OK
    elif any(component["status"] == "config-required" for component in components.values()):
        overall_status = "config-required"
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    else:
        overall_status = "unavailable"
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return (
        {
            "schema_version": TRACKER_HEALTH_SCHEMA_VERSION,
            "status": overall_status,
            "session_id": str(session_id or "").strip() or TRACKER_DEFAULT_SESSION_ID,
            "tracker_access": decision.as_dict(),
            "components": components,
        },
        status_code,
    )


def _preferred_customer_license(store: BusinessStore, customer_id: str, *, now_epoch: float) -> License | None:
    licenses = store.customer_licenses(customer_id)
    if not licenses:
        return None

    def sort_key(license_record: License) -> tuple[int, float, str]:
        active = int(_license_subscription_gate_passed(store, license_record, now_epoch=now_epoch))
        return (active, float(license_record.expires_at_epoch or 0.0), license_record.id)

    return max(licenses, key=sort_key)


def _preferred_license_device(store: BusinessStore, license_id: str) -> Device | None:
    devices = [device for device in store.devices.values() if device.license_id == license_id]
    if not devices:
        return None
    return max(
        devices,
        key=lambda device: (
            int(device.status == "active"),
            float(device.last_seen_at_epoch or device.registered_at_epoch or 0.0),
            device.id,
        ),
    )


def _build_gates(
    *,
    store: BusinessStore,
    customer: Customer | None,
    license_record: License | None,
    subscription: Subscription | None,
    device: Device | None,
    auth_type: str,
    connector_only: bool,
    now_epoch: float,
) -> dict[str, TrackerGate]:
    registration_passed = (
        auth_type == "connector"
        and device is not None
        and license_record is not None
        and customer is not None
    ) or (
        not connector_only
        and auth_type == "customer"
        and customer is not None
        and license_record is not None
        and device is not None
    )
    email_passed = bool(
        customer
        and customer.status == "active"
        and _looks_like_email(customer.email)
        and bool(getattr(customer, "email_verified", True))
    )
    subscription_passed = bool(
        license_record is not None
        and subscription is not None
        and _license_subscription_gate_passed(store, license_record, now_epoch=now_epoch)
    )
    disclosure_passed = bool(customer and customer.disclosure_accepted)
    broker_passed = bool(license_record and store.account_bound_for_license(license_record))
    device_passed = bool(
        device
        and device.status == "active"
        and license_record is not None
        and store.device_is_fresh_for_license(device, license_record, now_epoch=now_epoch)
    )

    return {
        GATE_REGISTRATION: TrackerGate(
            GATE_REGISTRATION,
            registration_passed,
            "" if registration_passed else "A registered customer license and connector device are required.",
        ),
        GATE_EMAIL: TrackerGate(
            GATE_EMAIL,
            email_passed,
            "" if email_passed else "An active verified customer email identity is required.",
        ),
        GATE_SUBSCRIPTION: TrackerGate(
            GATE_SUBSCRIPTION,
            subscription_passed,
            "" if subscription_passed else _subscription_gate_reason(
                store,
                license_record,
                subscription,
                now_epoch=now_epoch,
            ),
        ),
        GATE_DISCLOSURE: TrackerGate(
            GATE_DISCLOSURE,
            disclosure_passed,
            "" if disclosure_passed else "Risk disclosure acceptance is required before tracker access.",
        ),
        GATE_BROKER: TrackerGate(
            GATE_BROKER,
            broker_passed,
            "" if broker_passed else "An active broker account binding is required.",
        ),
        GATE_DEVICE: TrackerGate(
            GATE_DEVICE,
            device_passed,
            "" if device_passed else "An active connector device with a fresh heartbeat is required.",
        ),
    }


def _license_subscription_gate_passed(
    store: BusinessStore,
    license_record: License,
    *,
    now_epoch: float,
) -> bool:
    subscription = store.subscriptions.get(license_record.subscription_id)
    if subscription is None:
        return False
    if subscription.status not in {"active", "trialing"}:
        return False
    if license_record.status not in {"active", "trialing", "grace"}:
        return False
    if float(license_record.expires_at_epoch or 0.0) <= now_epoch:
        return False
    return store.runtime_available_for_license(license_record, now_epoch=now_epoch)


def _subscription_gate_reason(
    store: BusinessStore,
    license_record: License | None,
    subscription: Subscription | None,
    *,
    now_epoch: float,
) -> str:
    if license_record is None:
        return "An activated package license is required."
    if subscription is None:
        return "A subscription record is required before tracker access."
    if subscription.status not in {"active", "trialing"}:
        return f"Package payment state is {subscription.status}; active or trialing access is required."
    if license_record.status not in {"active", "trialing", "grace"}:
        return f"License state is {license_record.status}; active access is required."
    if float(license_record.expires_at_epoch or 0.0) <= now_epoch:
        return "The package license period has ended."
    if not store.runtime_available_for_license(license_record, now_epoch=now_epoch):
        return "The daily runtime allowance for this package has been reached."
    return "An active or trialing subscription and unexpired license are required."


def _looks_like_email(value: str) -> bool:
    text = str(value or "").strip()
    return "@" in text and "." in text.rsplit("@", 1)[-1]


def _entitlement_payload(store: BusinessStore, device: Device | None) -> dict[str, Any]:
    if device is None:
        return {}
    try:
        return dict(store.entitlement_for_device(device))
    except Exception:
        return {}


def _command_status_for_blocked_access(decision: TrackerAccessDecision) -> tuple[str, str]:
    blocked = set(decision.blocked_gates)
    if GATE_DEVICE in blocked:
        if "fresh heartbeat" in decision.primary_reason.lower():
            return "SERVICE_UNAVAILABLE", "Fresh connector heartbeat is required before command delivery."
        return "DEVICE_REVOKED", "Active connector device is required before command delivery."
    if GATE_SUBSCRIPTION in blocked:
        return "LICENSE_EXPIRED", "Active subscription is required before command delivery."
    if GATE_BROKER in blocked:
        return "ACCOUNT_NOT_BOUND", "Broker account binding required before command delivery."
    if GATE_DISCLOSURE in blocked:
        return "NO_EXECUTION_PACKET", "Risk disclosure acceptance required before executable commands."
    return "SERVICE_UNAVAILABLE", decision.primary_reason or "Tracker entitlement gates are not satisfied."


async def _read_route_health(app: FastAPI, route_path: str, kwargs: Mapping[str, Any]) -> dict[str, Any]:
    endpoint = _find_get_endpoint(app, route_path)
    if endpoint is None:
        return {
            "status": "config-required",
            "route": route_path,
            "available": False,
            "reason": "health route is not registered on this FastAPI app",
        }
    try:
        result = endpoint(**dict(kwargs))
        if inspect.isawaitable(result):
            result = await result
    except HTTPException as exc:
        return {
            "status": "unavailable",
            "route": route_path,
            "available": False,
            "reason": str(exc.detail),
            "status_code": exc.status_code,
        }
    except Exception as exc:
        return {
            "status": "unavailable",
            "route": route_path,
            "available": False,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    return {
        "status": "ok",
        "route": route_path,
        "available": True,
        "payload": _json_safe(result),
    }


def _find_get_endpoint(app: FastAPI, route_path: str) -> Any | None:
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if route.path != route_path:
            continue
        if "GET" not in route.methods:
            continue
        return route.endpoint
    return None


def _json_safe(value: Any) -> Any:
    if isinstance(value, Mapping):
        value_map = cast(Mapping[str, Any], value)
        return {str(key): _json_safe(item) for key, item in value_map.items()}
    if isinstance(value, (list, tuple)):
        items = cast(list[Any] | tuple[Any, ...], value)
        return [_json_safe(item) for item in items]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)
