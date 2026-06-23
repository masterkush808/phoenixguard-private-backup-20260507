from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Mapping


FREE_PREVIEW_PLAN_CODE = "hybrid-free-2h"
STANDARD_PLAN_CODE = "hybrid-standard-6h"
PROFESSIONAL_PLAN_CODE = "hybrid-professional-24x7"
SCALE_REVIEW_PLAN_CODE = "scale-review"
DEFAULT_PAID_PLAN_CODE = STANDARD_PLAN_CODE


@dataclass(frozen=True, slots=True)
class PackageProfile:
    code: str
    name: str
    tier: str
    price_label: str
    billing_kind: str
    daily_runtime_hours: int
    runtime_label: str
    license_status: str
    subscription_status: str
    license_duration_days: int
    max_devices: int
    max_broker_accounts: int
    heartbeat_freshness_seconds: int
    command_freshness_seconds: int
    stale_market_data_seconds: int
    release_channel: str
    certification_level: str
    self_service: bool = True
    payment_required: bool = False

    @property
    def daily_runtime_seconds(self) -> int:
        return max(0, int(self.daily_runtime_hours) * 3600)

    def runtime_policy(self) -> dict[str, Any]:
        return {
            "daily_runtime_hours": self.daily_runtime_hours,
            "daily_runtime_seconds": self.daily_runtime_seconds,
            "runtime_label": self.runtime_label,
            "tier": self.tier,
            "max_devices": self.max_devices,
            "max_broker_accounts": self.max_broker_accounts,
            "heartbeat_freshness_seconds": self.heartbeat_freshness_seconds,
            "command_freshness_seconds": self.command_freshness_seconds,
            "stale_market_data_seconds": self.stale_market_data_seconds,
            "release_channel": self.release_channel,
        }

    def phoenix_guard_settings(self) -> dict[str, Any]:
        return {
            "profile_code": self.code,
            "operating_tier": self.tier,
            "release_channel": self.release_channel,
            "runtime_limit_seconds_daily": self.daily_runtime_seconds,
            "heartbeat_max_age_seconds": self.heartbeat_freshness_seconds,
            "command_max_age_seconds": self.command_freshness_seconds,
            "stale_market_data_max_age_seconds": self.stale_market_data_seconds,
            "requires_verified_email": True,
            "requires_risk_disclosure": True,
            "requires_broker_binding": True,
            "requires_fresh_device_heartbeat": True,
            "live_execution_default_enabled": False,
        }

    def public_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "name": self.name,
            "tier": self.tier,
            "price_label": self.price_label,
            "billing_kind": self.billing_kind,
            "self_service": self.self_service,
            "payment_required": self.payment_required,
            "runtime_policy": self.runtime_policy(),
            "phoenix_guard_settings": self.phoenix_guard_settings(),
            "certification_level": self.certification_level,
        }


PACKAGE_PROFILES: dict[str, PackageProfile] = {
    FREE_PREVIEW_PLAN_CODE: PackageProfile(
        code=FREE_PREVIEW_PLAN_CODE,
        name="Free Preview",
        tier="free-preview",
        price_label="$0 starter access",
        billing_kind="free-preview",
        daily_runtime_hours=2,
        runtime_label="2 hours daily preview",
        license_status="trialing",
        subscription_status="trialing",
        license_duration_days=30,
        max_devices=1,
        max_broker_accounts=1,
        heartbeat_freshness_seconds=600,
        command_freshness_seconds=30,
        stale_market_data_seconds=20,
        release_channel="preview",
        certification_level="preview-certified",
    ),
    STANDARD_PLAN_CODE: PackageProfile(
        code=STANDARD_PLAN_CODE,
        name="Standard Access",
        tier="standard",
        price_label="$20 per month",
        billing_kind="subscription",
        daily_runtime_hours=6,
        runtime_label="6 hours daily",
        license_status="active",
        subscription_status="active",
        license_duration_days=30,
        max_devices=1,
        max_broker_accounts=1,
        heartbeat_freshness_seconds=300,
        command_freshness_seconds=20,
        stale_market_data_seconds=12,
        release_channel="stable",
        certification_level="standard-certified",
        payment_required=True,
    ),
    PROFESSIONAL_PLAN_CODE: PackageProfile(
        code=PROFESSIONAL_PLAN_CODE,
        name="Professional Access",
        tier="professional",
        price_label="$100 per month",
        billing_kind="subscription",
        daily_runtime_hours=24,
        runtime_label="24/7 eligible runtime",
        license_status="active",
        subscription_status="active",
        license_duration_days=30,
        max_devices=2,
        max_broker_accounts=2,
        heartbeat_freshness_seconds=180,
        command_freshness_seconds=15,
        stale_market_data_seconds=8,
        release_channel="stable",
        certification_level="professional-certified",
        payment_required=True,
    ),
    SCALE_REVIEW_PLAN_CODE: PackageProfile(
        code=SCALE_REVIEW_PLAN_CODE,
        name="Scale Review",
        tier="review",
        price_label="Review before rollout",
        billing_kind="manual-review",
        daily_runtime_hours=0,
        runtime_label="Custom controls",
        license_status="pending_review",
        subscription_status="pending_review",
        license_duration_days=0,
        max_devices=0,
        max_broker_accounts=0,
        heartbeat_freshness_seconds=0,
        command_freshness_seconds=0,
        stale_market_data_seconds=0,
        release_channel="review",
        certification_level="review-required",
        self_service=False,
        payment_required=True,
    ),
}

PLAN_ALIASES = {
    "hybrid-standard": STANDARD_PLAN_CODE,
    "business": STANDARD_PLAN_CODE,
    "standard": STANDARD_PLAN_CODE,
    "pro": PROFESSIONAL_PLAN_CODE,
    "professional": PROFESSIONAL_PLAN_CODE,
    "free": FREE_PREVIEW_PLAN_CODE,
    "preview": FREE_PREVIEW_PLAN_CODE,
}


def normalize_plan_code(plan_code: str | None) -> str:
    raw = str(plan_code or DEFAULT_PAID_PLAN_CODE).strip().lower()
    return PLAN_ALIASES.get(raw, raw)


def package_profile_for_plan(plan_code: str | None) -> PackageProfile:
    normalized = normalize_plan_code(plan_code)
    profile = PACKAGE_PROFILES.get(normalized)
    if profile is None:
        raise KeyError(f"unsupported_package_profile:{normalized}")
    return profile


def package_catalog_payload() -> list[dict[str, Any]]:
    return [profile.public_payload() for profile in PACKAGE_PROFILES.values()]


def runtime_policy_for_plan(plan_code: str | None) -> dict[str, Any]:
    try:
        return package_profile_for_plan(plan_code).runtime_policy()
    except KeyError:
        return PACKAGE_PROFILES[SCALE_REVIEW_PLAN_CODE].runtime_policy()


def phoenix_guard_settings_for_plan(plan_code: str | None) -> dict[str, Any]:
    try:
        return package_profile_for_plan(plan_code).phoenix_guard_settings()
    except KeyError:
        return PACKAGE_PROFILES[SCALE_REVIEW_PLAN_CODE].phoenix_guard_settings()


def payments_are_paused(env: Mapping[str, str] | None = None) -> bool:
    values = env if env is not None else os.environ
    raw = str(values.get("PHOENIXGUARD_PAYMENTS_PAUSED", "1") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}
