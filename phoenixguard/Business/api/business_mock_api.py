from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field


DISCLOSURE_VERSION = "risk-disclosure-2026-06"
ACTIVE_CUSTOMER_EMAIL = "customer@phoenixguard.test"
EXPIRED_CUSTOMER_EMAIL = "expired@phoenixguard.test"
ADMIN_EMAIL = "admin@phoenixguard.test"
MOCK_PASSWORD = "mock-password"
TRACKER_SESSION_ID = "mock-tracker-active"


def _new_disclosure_history() -> list[dict[str, Any]]:
    return []


class LoginRequest(BaseModel):
    email: str
    password: str = MOCK_PASSWORD


class DisclosureRequest(BaseModel):
    version: str = DISCLOSURE_VERSION
    accepted: bool = True


class BrokerAccountRequest(BaseModel):
    broker_server: str = Field(min_length=2)
    mt4_account_number: str = Field(min_length=2)
    label: str | None = None


class DeviceRegisterRequest(BaseModel):
    license_key: str = Field(min_length=2)
    device_fingerprint: str = Field(min_length=2)
    device_label: str | None = None
    connector_version: str = "mock-connector-2026.06"


@dataclass
class MockPrincipal:
    email: str
    role: str
    customer_id: str
    license_id: str
    license_key: str
    license_status: str
    disclosure_accepted: bool = False
    broker_account_bound: bool = False
    device_status: str = "active"
    broker_server_hash: str = ""
    mt4_account_number_hash: str = ""
    last_heartbeat_epoch: float = 0.0
    connector_version: str = "mock-connector-2026.06"
    disclosure_history: list[dict[str, Any]] = field(default_factory=_new_disclosure_history)


def _hash_identifier(value: str) -> str:
    digest = hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _default_principals() -> dict[str, MockPrincipal]:
    return {
        ACTIVE_CUSTOMER_EMAIL: MockPrincipal(
            email=ACTIVE_CUSTOMER_EMAIL,
            role="customer",
            customer_id="cus_mock_active",
            license_id="lic_mock_active",
            license_key="PG-ACTIVE-TEST",
            license_status="active",
        ),
        EXPIRED_CUSTOMER_EMAIL: MockPrincipal(
            email=EXPIRED_CUSTOMER_EMAIL,
            role="customer",
            customer_id="cus_mock_expired",
            license_id="lic_mock_expired",
            license_key="PG-EXPIRED-TEST",
            license_status="expired",
        ),
        ADMIN_EMAIL: MockPrincipal(
            email=ADMIN_EMAIL,
            role="admin",
            customer_id="adm_mock_001",
            license_id="",
            license_key="",
            license_status="admin",
            disclosure_accepted=True,
        ),
    }


def _token_for(email: str, prefix: str = "user") -> str:
    digest = hashlib.sha256(f"phoenixguard:{prefix}:{email}".encode("utf-8")).hexdigest()[:24]
    return f"mock-{prefix}-{digest}"


def _status_command(status_code: str, reason: str) -> dict[str, Any]:
    now = time.time()
    return {
        "schema_version": "PG_MT4_EXECUTION_COMMAND_V2",
        "status": status_code,
        "packet_id": f"status-{status_code.lower()}-{int(now * 1000)}",
        "created_epoch": now,
        "valid_until_epoch": now + 15.0,
        "execution_authority": False,
        "reason": reason,
    }


def _execution_command(principal: MockPrincipal) -> dict[str, Any]:
    now = time.time()
    unsigned: dict[str, Any] = {
        "schema_version": "PG_MT4_EXECUTION_COMMAND_V2",
        "status": "EXECUTION_PACKET",
        "packet_id": f"mock-exec-{int(now * 1000)}",
        "stream_sequence": int(now),
        "license_id": principal.license_id,
        "customer_id": principal.customer_id,
        "side": "BUY",
        "symbol": "EURUSD",
        "timeframe": "M1",
        "created_epoch": now,
        "valid_until_epoch": now + 2.0,
        "execution_authority": True,
        "risk_controls": {
            "user_controlled_risk": True,
            "broker_password_required": False,
            "max_duration_seconds": 60,
        },
    }
    command_hash = hashlib.sha256(repr(sorted(unsigned.items())).encode("utf-8")).hexdigest()
    return {
        **unsigned,
        "command_hash": command_hash,
        "signature_alg": "mock-ed25519-detached",
        "signature": f"mocksig:{command_hash[:32]}",
        "public_key": "mock-public-key-do-not-use-for-production",
    }


def _public_principal(principal: MockPrincipal) -> dict[str, Any]:
    return {
        "email": principal.email,
        "role": principal.role,
        "customer_id": principal.customer_id,
        "license_id": principal.license_id,
        "license_status": principal.license_status,
        "disclosure_accepted": principal.disclosure_accepted,
        "broker_account_bound": principal.broker_account_bound,
        "device_status": principal.device_status,
        "tracker_session_id": TRACKER_SESSION_ID if principal.role == "customer" else "",
    }


def _license_payload(principal: MockPrincipal) -> dict[str, Any]:
    now = time.time()
    is_active = principal.license_status in {"active", "trialing", "grace"}
    expires = now + 86400.0 if is_active else now - 86400.0
    return {
        "license_id": principal.license_id,
        "license_key": principal.license_key,
        "status": principal.license_status,
        "plan_code": "test-active" if is_active else "test-expired",
        "expires_at_epoch": expires,
        "is_active": is_active,
        "requires_disclosure_acceptance": not principal.disclosure_accepted,
        "requires_broker_account_binding": not principal.broker_account_bound,
    }


def create_app() -> FastAPI:
    principals = _default_principals()
    user_tokens: dict[str, str] = {}
    connector_tokens: dict[str, str] = {}

    app = FastAPI(
        title="PhoenixGuard Business Mock API",
        version="0.1.0",
        description="Local-only mock API for commercial portal and integration QA.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.state.mock_business_principals = principals
    app.state.mock_business_user_tokens = user_tokens
    app.state.mock_business_connector_tokens = connector_tokens

    def current_principal(request: Request, *, admin_required: bool = False) -> MockPrincipal:
        auth = request.headers.get("authorization", "")
        scheme, _, token = auth.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required.")
        email = user_tokens.get(token) or connector_tokens.get(token)
        if not email or email not in principals:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")
        principal = principals[email]
        if admin_required and principal.role != "admin":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin access denied.")
        return principal

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "mock": True,
            "live_bridge_touched": False,
            "tracker_session_id": TRACKER_SESSION_ID,
        }

    @app.post("/v1/auth/login")
    def login(payload: LoginRequest) -> dict[str, Any]:
        email = payload.email.strip().lower()
        if payload.password != MOCK_PASSWORD or email not in principals:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid mock credentials.")
        token = _token_for(email)
        user_tokens[token] = email
        principal = principals[email]
        return {
            "access_token": token,
            "token_type": "bearer",
            "user": _public_principal(principal),
            "requires_disclosure_acceptance": principal.role == "customer" and not principal.disclosure_accepted,
            "requires_broker_account_binding": principal.role == "customer" and not principal.broker_account_bound,
        }

    @app.get("/v1/me")
    def me(request: Request) -> dict[str, Any]:
        return {"user": _public_principal(current_principal(request))}

    @app.post("/v1/disclosures/accept", status_code=status.HTTP_204_NO_CONTENT)
    def accept_disclosure(request: Request, payload: DisclosureRequest) -> Response:
        principal = current_principal(request)
        if principal.role != "customer":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only customers accept risk disclosures.")
        if not payload.accepted:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Disclosure acceptance is required.")
        principal.disclosure_accepted = True
        principal.disclosure_history.append(
            {
                "version": payload.version,
                "accepted_epoch": time.time(),
            }
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.post("/v1/broker-accounts", status_code=status.HTTP_201_CREATED)
    def bind_broker_account(request: Request, payload: BrokerAccountRequest) -> dict[str, Any]:
        principal = current_principal(request)
        if principal.role != "customer":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only customers bind broker accounts.")
        if not principal.disclosure_accepted:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Accept the risk disclosure first.")
        principal.broker_account_bound = True
        principal.broker_server_hash = _hash_identifier(payload.broker_server)
        principal.mt4_account_number_hash = _hash_identifier(payload.mt4_account_number)
        return {
            "broker_account_id": f"ba_{principal.customer_id}",
            "customer_id": principal.customer_id,
            "broker_server_hash": principal.broker_server_hash,
            "mt4_account_number_hash": principal.mt4_account_number_hash,
            "status": "bound",
            "label": payload.label or "Mock MT4 account",
        }

    @app.get("/v1/licenses")
    def licenses(request: Request) -> dict[str, Any]:
        principal = current_principal(request)
        if principal.role != "customer":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin users do not own customer licenses.")
        return {"licenses": [_license_payload(principal)]}

    @app.post("/v1/device/register", status_code=status.HTTP_201_CREATED)
    def register_device(payload: DeviceRegisterRequest) -> dict[str, Any]:
        license_key = payload.license_key.strip().upper()
        principal = next((item for item in principals.values() if item.license_key == license_key), None)
        if principal is None or principal.role != "customer":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown mock license key.")
        principal.connector_version = payload.connector_version
        token = _token_for(principal.email, prefix=f"connector:{payload.device_fingerprint}")
        connector_tokens[token] = principal.email
        return {
            "connector_token": token,
            "token_type": "bearer",
            "device_id": f"dev_{principal.customer_id}",
            "license": _license_payload(principal),
        }

    @app.post("/v1/device/heartbeat", status_code=status.HTTP_204_NO_CONTENT)
    def heartbeat(request: Request) -> Response:
        principal = current_principal(request)
        if principal.role != "customer":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customer device token required.")
        principal.last_heartbeat_epoch = time.time()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/v1/entitlements/current")
    def entitlement(request: Request) -> dict[str, Any]:
        principal = current_principal(request)
        if principal.role != "customer":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customer entitlement required.")
        license_payload = _license_payload(principal)
        return {
            "status": license_payload["status"],
            "license_id": principal.license_id,
            "plan_code": license_payload["plan_code"],
            "expires_at_epoch": license_payload["expires_at_epoch"],
            "account_bound": principal.broker_account_bound,
            "disclosure_accepted": principal.disclosure_accepted,
        }

    @app.get("/v1/commands/latest")
    def latest_command(request: Request) -> dict[str, Any]:
        principal = current_principal(request)
        if principal.role != "customer":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin users cannot poll connector commands.")
        if principal.license_status not in {"active", "trialing", "grace"}:
            command = _status_command("LICENSE_EXPIRED", "License is not active.")
            return {"status": "LICENSE_EXPIRED", "command": command}
        if not principal.disclosure_accepted:
            command = _status_command("SERVICE_UNAVAILABLE", "Risk disclosure must be accepted before command delivery.")
            return {"status": "SERVICE_UNAVAILABLE", "command": command}
        if not principal.broker_account_bound:
            command = _status_command("ACCOUNT_NOT_BOUND", "Bind a broker account before command delivery.")
            return {"status": "ACCOUNT_NOT_BOUND", "command": command}
        return {"status": "EXECUTION_PACKET", "command": _execution_command(principal)}

    @app.get("/v1/releases/latest")
    def latest_release(request: Request) -> dict[str, Any]:
        principal = current_principal(request)
        if principal.role != "customer":
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Customer release entitlement required.")
        return {
            "channel": "mock",
            "version": "2026.06.mock",
            "download_url": "https://downloads.phoenixguard.test/mock/connector.zip",
            "license_status": principal.license_status,
        }

    @app.get("/v1/admin/customers")
    def admin_customers(request: Request) -> dict[str, Any]:
        current_principal(request, admin_required=True)
        customers = [item for item in principals.values() if item.role == "customer"]
        return {"customers": [_public_principal(item) for item in customers]}

    @app.get("/v1/tracker/status")
    def tracker_status() -> dict[str, Any]:
        return {
            "alive": True,
            "mode": "mock",
            "session_id": TRACKER_SESSION_ID,
            "tracking_enabled": True,
            "live_bridge_touched": False,
        }

    @app.get("/v1/mobile/window-tracker/sessions/{session_id}/health")
    def tracker_session_health(session_id: str) -> dict[str, Any]:
        return {
            "alive": session_id == TRACKER_SESSION_ID,
            "mode": "mock",
            "session_id": session_id,
            "tracking_enabled": session_id == TRACKER_SESSION_ID,
        }

    @app.get("/v1/mobile/window-tracker/sessions/{session_id}")
    def tracker_session(session_id: str) -> dict[str, Any]:
        if session_id != TRACKER_SESSION_ID:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown mock tracker session.")
        return {
            "session_id": session_id,
            "status": "running",
            "tracking_enabled": True,
            "tracker_alive": True,
            "latest_signal": {"status": "mock_active", "action": "HOLD", "confidence": 0.0},
            "broker_source": {"valid": True, "wrong_surface": False, "lock_id": "mock-lock"},
            "broker_execution_state": {"status": "mock_disabled", "side": "HOLD", "execution_authority": False},
            "artifacts": {},
        }

    @app.get("/v3/mobile/window-tracker/dashboard", response_class=HTMLResponse)
    @app.get("/v3/mobile/window-tracker/dashboard/{session_id}", response_class=HTMLResponse)
    @app.get("/v1/mobile/window-tracker/dashboard", response_class=HTMLResponse)
    @app.get("/v1/mobile/window-tracker/dashboard/{session_id}", response_class=HTMLResponse)
    def tracker_dashboard(session_id: str = TRACKER_SESSION_ID) -> HTMLResponse:
        if session_id != TRACKER_SESSION_ID:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unknown mock tracker session.")
        html = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>PhoenixGuard Mock Tracker GUI</title>
  </head>
  <body>
    <main data-testid="tracker-gui">
      <h1>PhoenixGuard Mock Tracker GUI</h1>
      <p data-testid="tracker-status">alive: running</p>
      <p data-testid="tracker-session-id">{session_id}</p>
    </main>
  </body>
</html>"""
        return HTMLResponse(content=html)

    app.state.mock_business_route_handlers = {
        "healthz": healthz,
        "login": login,
        "me": me,
        "accept_disclosure": accept_disclosure,
        "bind_broker_account": bind_broker_account,
        "licenses": licenses,
        "register_device": register_device,
        "heartbeat": heartbeat,
        "entitlement": entitlement,
        "latest_command": latest_command,
        "latest_release": latest_release,
        "admin_customers": admin_customers,
        "tracker_status": tracker_status,
        "tracker_session_health": tracker_session_health,
        "tracker_session": tracker_session,
        "tracker_dashboard": tracker_dashboard,
    }
    return app


app = create_app()
