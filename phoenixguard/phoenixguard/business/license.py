from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Mapping

from .auth import ConnectorPrincipal, MockBusinessAuthProvider, hash_connector_token
from .commands import build_status_command, latest_command_for_context
from .repository import (
    AuthorizationError,
    DeviceRecord,
    LicenseRecord,
    MOCK_DISCLOSURE_VERSION,
    MockBusinessRepository,
    Mt4Account,
    NotFoundError,
    ReleaseBuild,
    iso_datetime,
    deterministic_id,
)


@dataclass(frozen=True, slots=True)
class ConnectorContext:
    principal: ConnectorPrincipal
    device: DeviceRecord
    license_record: LicenseRecord


class LicenseService:
    """Business license and entitlement service for the mock FastAPI API."""

    def __init__(
        self,
        *,
        repository: MockBusinessRepository,
        auth_provider: MockBusinessAuthProvider,
    ) -> None:
        self._repository = repository
        self._auth_provider = auth_provider

    @property
    def repository(self) -> MockBusinessRepository:
        return self._repository

    def require_active_customer(self, customer_id: str) -> None:
        self._repository.ensure_customer_active(customer_id)

    def accept_disclosure(
        self,
        *,
        customer_id: str,
        version: str | None,
        license_id: str | None,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        self._repository.ensure_customer_active(customer_id)
        self._repository.accept_disclosure(
            customer_id=customer_id,
            version=version,
            license_id=license_id,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self._repository.append_audit_event(
            actor_type="customer",
            actor_id=customer_id,
            action="risk_disclosure.accepted",
            target_type="risk_disclosure",
            target_id=version or MOCK_DISCLOSURE_VERSION,
            ip_address=ip_address,
            metadata={"license_id": license_id},
        )

    def create_broker_account(
        self,
        *,
        customer_id: str,
        broker_server: str,
        mt4_account_number: str,
        label: str | None,
    ) -> dict[str, Any]:
        self._repository.ensure_customer_active(customer_id)
        account, created = self._repository.create_or_get_broker_account(
            customer_id=customer_id,
            broker_server=broker_server,
            mt4_account_number=mt4_account_number,
            label=label,
        )
        binding = self._repository.bind_account_to_first_unbound_active_license(
            customer_id=customer_id,
            account_id=account.id,
        )
        self._repository.append_audit_event(
            actor_type="customer",
            actor_id=customer_id,
            action="broker_account.created" if created else "broker_account.reused",
            target_type="mt4_account",
            target_id=account.id,
            ip_address=None,
            metadata={
                "binding_id": binding.id if binding is not None else None,
                "license_id": binding.license_id if binding is not None else None,
            },
        )
        return {
            "broker_account_id": account.id,
            "broker_server_label": account.broker_server_label,
            "account_number_masked": account.account_number_masked,
            "status": account.status,
            "created": created,
            "binding": _binding_payload(binding) if binding is not None else None,
        }

    def list_customer_licenses(self, *, customer_id: str) -> dict[str, Any]:
        self._repository.ensure_customer_active(customer_id)
        licenses = self._repository.list_licenses_for_customer(customer_id)
        accepted_current_disclosure = self._repository.has_accepted_current_disclosure(customer_id)
        return {
            "customer_id": customer_id,
            "accepted_current_disclosure": accepted_current_disclosure,
            "licenses": [
                self._license_payload(license_record)
                for license_record in sorted(licenses, key=lambda item: item.created_at)
            ],
        }

    def register_device(
        self,
        *,
        license_key: str,
        device_fingerprint: str,
        device_label: str | None,
        connector_version: str,
    ) -> dict[str, Any]:
        license_record = self._repository.get_license_by_key(license_key)
        self._repository.ensure_customer_active(license_record.customer_id)
        device = self._repository.upsert_device(
            customer_id=license_record.customer_id,
            license_id=license_record.id,
            device_fingerprint=device_fingerprint,
            device_label=device_label,
            connector_version=connector_version,
        )
        connector_token = self._auth_provider.issue_connector_token(
            customer_id=license_record.customer_id,
            license_id=license_record.id,
            device_id=device.id,
        )
        device = self._repository.update_device_token_hash(
            device.id,
            hash_connector_token(connector_token),
        )
        entitlement = self.resolve_entitlement(device=device, license_record=license_record)
        self._repository.append_audit_event(
            actor_type="connector",
            actor_id=device.id,
            action="device.registered",
            target_type="license",
            target_id=license_record.id,
            ip_address=None,
            metadata={"connector_version": connector_version},
        )
        return {
            "device_id": device.id,
            "customer_id": license_record.customer_id,
            "license_id": license_record.id,
            "connector_token": connector_token,
            "entitlement": entitlement,
        }

    def connector_context(self, principal: ConnectorPrincipal) -> ConnectorContext:
        device, license_record = self._repository.validate_connector_device(
            customer_id=principal.customer_id,
            license_id=principal.license_id,
            device_id=principal.device_id,
            connector_token_hash=hash_connector_token(principal.token),
        )
        self._repository.ensure_customer_active(principal.customer_id)
        return ConnectorContext(
            principal=principal,
            device=device,
            license_record=license_record,
        )

    def heartbeat(
        self,
        *,
        context: ConnectorContext,
        connector_version: str | None,
        ea_version: str | None,
        mt4_terminal_build: str | None,
        status: str | None,
        detail: str | None,
        ip_address: str | None,
    ) -> None:
        device = self._repository.update_device_last_seen(
            context.device.id,
            connector_version=connector_version,
        )
        license_record = self._repository.get_license(context.license_record.id)
        entitlement = self.resolve_entitlement(device=device, license_record=license_record)
        self._repository.record_heartbeat(
            license_id=license_record.id,
            device_id=device.id,
            connector_version=connector_version or device.connector_version,
            ea_version=ea_version,
            mt4_terminal_build=mt4_terminal_build,
            status=status or str(entitlement["status"]),
            detail=detail or str(entitlement.get("reason") or ""),
            ip_address=ip_address,
        )

    def current_entitlement(self, *, context: ConnectorContext) -> dict[str, Any]:
        return self.resolve_entitlement(
            device=context.device,
            license_record=context.license_record,
        )

    def latest_command_for_connector(self, *, context: ConnectorContext) -> dict[str, Any]:
        device = self._repository.update_device_last_seen(context.device.id)
        license_record = self._repository.get_license(context.license_record.id)
        entitlement = self.resolve_entitlement(device=device, license_record=license_record)
        customer_id = license_record.customer_id
        account_bound = self._repository.license_has_active_account_binding(license_record.id)
        entitlement_status = str(entitlement["status"])
        if entitlement_status == "grace" and entitlement.get("reason") == "ACCOUNT_NOT_BOUND":
            command_entitlement_status = "grace"
        elif entitlement_status not in {"active", "trialing"}:
            command_entitlement_status = "expired"
        else:
            command_entitlement_status = entitlement_status
        if command_entitlement_status == "expired":
            return latest_command_for_context(
                entitlement_status=command_entitlement_status,
                license_id=license_record.id,
                device_id=device.id,
                account_bound=account_bound,
                device_status=device.status,
                update_required=entitlement.get("status") == "update_required",
                internal_packet=None,
            )
        if not self._repository.has_accepted_current_disclosure(customer_id):
            return {
                "status": "SERVICE_UNAVAILABLE",
                "command": build_status_command(
                    "SERVICE_UNAVAILABLE",
                    reason="Risk disclosure acceptance required before command delivery.",
                ),
            }
        return latest_command_for_context(
            entitlement_status=command_entitlement_status,
            license_id=license_record.id,
            device_id=device.id,
            account_bound=account_bound,
            device_status=device.status,
            update_required=entitlement.get("status") == "update_required",
            internal_packet=None,
        )

    def tracker_access_for_customer(self, *, customer_id: str) -> dict[str, Any]:
        self._repository.ensure_customer_active(customer_id)
        if not self._repository.has_accepted_current_disclosure(customer_id):
            raise AuthorizationError("risk_disclosure_required")
        eligible = [
            license_record
            for license_record in self._repository.list_licenses_for_customer(customer_id)
            if self._is_release_eligible_license(license_record)
        ]
        if not eligible:
            raise AuthorizationError("no_tracker_eligible_license")
        license_record = eligible[0]
        if not self._repository.license_has_active_account_binding(license_record.id):
            raise AuthorizationError("broker_account_binding_required")
        return {
            "access": "granted",
            "customer_id": customer_id,
            "license_id": license_record.id,
            "gates": {
                "email_verified": True,
                "disclosure_accepted": True,
                "active_subscription_or_license": True,
                "broker_account_bound": True,
            },
        }

    def resolve_entitlement(
        self,
        *,
        device: DeviceRecord,
        license_record: LicenseRecord,
    ) -> dict[str, Any]:
        status, reason = self._resolve_entitlement_state(device=device, license_record=license_record)
        payload: dict[str, Any] = {
            "status": status,
            "license_id": license_record.id,
            "plan_code": license_record.plan_code,
            "expires_at": iso_datetime(license_record.expires_at),
        }
        if reason:
            payload["reason"] = reason
        self._repository.record_entitlement_snapshot(
            license_id=license_record.id,
            device_id=device.id,
            status=status,
            reason=reason,
            snapshot_json=payload,
        )
        return payload

    def latest_release_for_customer(self, *, customer_id: str, channel: str = "stable") -> dict[str, Any]:
        self._repository.ensure_customer_active(customer_id)
        licenses = self._repository.list_licenses_for_customer(customer_id)
        eligible = [
            license_record
            for license_record in licenses
            if self._is_release_eligible_license(license_record)
        ]
        if not eligible:
            raise AuthorizationError("no_release_eligible_license")
        if not self._repository.has_accepted_current_disclosure(customer_id):
            raise AuthorizationError("risk_disclosure_required")
        release = self._repository.latest_release(channel=channel)
        signature = deterministic_id("sig", customer_id, release.id, eligible[0].id, length=24).removeprefix("sig_")
        return {
            "release_id": release.id,
            "channel": release.channel,
            "ea_version": release.ea_version,
            "connector_version": release.connector_version,
            "minimum_connector_version": release.minimum_connector_version,
            "sha256_manifest": release.sha256_manifest,
            "published_at": iso_datetime(release.published_at),
            "manifest": dict(release.manifest_json),
            "signed_download_url": (
                f"https://downloads.phoenixguard.example.test/{release.id}/"
                f"{customer_id}?signature={signature}"
            ),
        }

    def _resolve_entitlement_state(
        self,
        *,
        device: DeviceRecord,
        license_record: LicenseRecord,
    ) -> tuple[str, str | None]:
        now = self._repository.now
        try:
            customer = self._repository.get_customer(device.customer_id)
        except NotFoundError:
            return "revoked", "CUSTOMER_NOT_FOUND"
        if customer.status != "active" or customer.email_verified_at is None:
            return "revoked", "EMAIL_NOT_VERIFIED"
        if device.status == "revoked":
            return "revoked", "DEVICE_REVOKED"
        if license_record.status == "revoked":
            return "revoked", "LICENSE_REVOKED"
        if license_record.status == "expired" or _is_expired(license_record.expires_at, now):
            return "expired", "LICENSE_EXPIRED"
        if license_record.status in {"past_due", "grace", "unpaid"}:
            return "grace", "PAYMENT_GRACE"
        release = self._repository.latest_release(channel="stable")
        if _version_is_less(
            device.connector_version,
            release.minimum_connector_version,
        ):
            return "update_required", "UPDATE_REQUIRED"
        if not self._repository.license_has_active_account_binding(license_record.id):
            return "grace", "ACCOUNT_NOT_BOUND"
        if license_record.status == "trialing":
            return "trialing", None
        return "active", None

    def _license_payload(self, license_record: LicenseRecord) -> dict[str, Any]:
        accounts = self._repository.list_accounts_for_license(license_record.id)
        devices = self._repository.list_devices_for_license(license_record.id)
        return {
            "license_id": license_record.id,
            "customer_id": license_record.customer_id,
            "subscription_id": license_record.subscription_id,
            "plan_code": license_record.plan_code,
            "status": license_record.status,
            "expires_at": iso_datetime(license_record.expires_at),
            "revoked_at": iso_datetime(license_record.revoked_at),
            "revoke_reason": license_record.revoke_reason,
            "bound_accounts": [_account_payload(account) for account in accounts],
            "devices": [_device_payload(device) for device in devices],
        }

    def _is_release_eligible_license(self, license_record: LicenseRecord) -> bool:
        if license_record.status not in {"active", "trialing"}:
            return False
        subscription_status = self._repository.subscription_status_for_license(license_record)
        if subscription_status is not None and subscription_status not in {"active", "trialing"}:
            return False
        return not _is_expired(license_record.expires_at, self._repository.now)


def _is_expired(expires_at: datetime | None, now: datetime) -> bool:
    return expires_at is not None and expires_at <= now


def _version_is_less(current: str | None, minimum: str | None) -> bool:
    if not minimum:
        return False
    if not current:
        return True
    current_parts = _version_tuple(current)
    minimum_parts = _version_tuple(minimum)
    width = max(len(current_parts), len(minimum_parts))
    padded_current = current_parts + (0,) * (width - len(current_parts))
    padded_minimum = minimum_parts + (0,) * (width - len(minimum_parts))
    return padded_current < padded_minimum


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = [int(part) for part in re.findall(r"\d+", str(value or ""))]
    return tuple(parts or [0])


def _account_payload(account: Mt4Account) -> dict[str, Any]:
    return {
        "broker_account_id": account.id,
        "broker_server_label": account.broker_server_label,
        "account_number_masked": account.account_number_masked,
        "status": account.status,
    }


def _device_payload(device: DeviceRecord) -> dict[str, Any]:
    return {
        "device_id": device.id,
        "device_label": device.device_label,
        "connector_version": device.connector_version,
        "status": device.status,
        "registered_at": iso_datetime(device.registered_at),
        "last_seen_at": iso_datetime(device.last_seen_at),
        "revoked_at": iso_datetime(device.revoked_at),
    }


def _binding_payload(binding: object | None) -> dict[str, Any] | None:
    if binding is None:
        return None
    license_id = getattr(binding, "license_id")
    return {
        "binding_id": getattr(binding, "id"),
        "license_id": license_id,
        "status": getattr(binding, "status"),
    }
