from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8", errors="replace")


def _exists(relative_path: str) -> bool:
    return (ROOT / relative_path).exists()


def _check(name: str, passed: bool, detail: str = "") -> dict[str, object]:
    return {"name": name, "passed": passed, "detail": detail}


def verify_release_readiness() -> dict[str, object]:
    linux_bootstrap = _text("Developer/deployment/linux_cloud_brain_bootstrap.sh")
    frame_ingest = _text("Backend/src/phoenixguard/mobile_api/frame_ingest.py")
    edge_agent = _text("Developer/deployment/edge_frame_agent.py")
    app = _text("Backend/src/phoenixguard/mobile_api/app.py")
    dashboard = _text("Frontend/dashboard/static/window_tracker_dashboard.html")
    pyright = _text("pyrightconfig.json")
    readme = _text("Developer/deployment/README.md")
    business_packages = _text("Backend/src/phoenixguard/business/packages.py")
    business_api = _text("Backend/src/phoenixguard/business/api.py")
    cloudflare_security = _text("Developer/deployment/cloudflare_security/main.tf") if _exists("Developer/deployment/cloudflare_security/main.tf") else ""

    checks = [
        _check("live_windows_lock_exists", _exists("requirements/locks/live-win-py311.txt")),
        _check("live_linux_lock_exists", _exists("requirements/locks/live-linux-py311.txt")),
        _check(
            "linux_bootstrap_uses_linux_lock",
            "requirements/locks/live-linux-py311.txt" in linux_bootstrap and "live-win-py311.txt" not in linux_bootstrap,
        ),
        _check("cloud_watchdog_script_exists", _exists("Developer/deployment/phoenixguard_cloud_watchdog.py")),
        _check("cloud_watchdog_unit_exists", _exists("Developer/deployment/phoenixguard-cloud-watchdog.service")),
        _check("linux_bootstrap_enables_watchdog", "phoenixguard-cloud-watchdog.service" in linux_bootstrap),
        _check("frame_ingest_requires_auth", "Frame ingest is not armed" in frame_ingest and "Invalid frame ingest token" in frame_ingest),
        _check("frame_ingest_has_decode_safety", "PHOENIXGUARD_FRAME_INGEST_MAX_PIXELS" in frame_ingest and "Animated frame uploads are not allowed" in frame_ingest),
        _check("frame_ingest_has_hmac_signature_and_nonce_replay_defense", "PG_FRAME_INGEST_V1" in frame_ingest and "hmac.compare_digest" in frame_ingest and "SIGNATURE_NONCES" in frame_ingest),
        _check("frame_ingest_security_audit_log_exists", "PG_SECURITY_AUDIT_V1" in frame_ingest and "PHOENIXGUARD_SECURITY_AUDIT_LOG" in frame_ingest),
        _check("edge_frame_agent_can_sign_uploads", "X-PhoenixGuard-Signature" in edge_agent and "HMAC-SHA256-V1" in edge_agent),
        _check("frame_ingest_commits_runtime_after_acceptance", "commit=False" in frame_ingest and "commit=True" in frame_ingest),
        _check("api_has_origin_and_host_controls", "CORSMiddleware" in app and "TrustedHostMiddleware" in app),
        _check(
            "dashboard_overlay_payload_does_not_shadow_objects",
            "function commitOperatorState(payload)" in dashboard
            and "state.overlays = safeList(operatorState.overlays);" in dashboard
            and "session.overlays || liveState.overlays" not in dashboard
            and "liveState.overlays || session.overlays" not in dashboard,
        ),
        _check("cloudflare_security_template_exists", "cloudflare_zero_trust_access_application" in cloudflare_security and "http_ratelimit" in cloudflare_security and "http_request_firewall_custom" in cloudflare_security),
        _check("model_asset_manifest_tool_exists", _exists("Developer/deployment/model_asset_manifest.py") and "PG_MODEL_ASSET_MANIFEST_V1" in _text("Developer/deployment/model_asset_manifest.py")),
        _check("business_internal_family_lifetime_is_admin_only", "INTERNAL_FAMILY_LIFETIME_PLAN_CODE" in business_packages and "public_visible=False" in business_packages and "family-lifetime-license" in business_api),
        _check("overlay_contract_validator_exists", _exists("Backend/tools/validate_overlay_contract_v3.py")),
        _check("v3_integrity_verifier_exists", _exists("Backend/tools/verify_v3_integrity.py")),
        _check("deployment_verifier_exists", _exists("Developer/deployment/verify_universal_frame_feed.py")),
        _check("pyright_excludes_runtime_payloads", '"reports"' in pyright and '".codex_runtime"' in pyright and '"logs"' in pyright),
        _check("deployment_readme_documents_watchdog", "phoenixguard_cloud_watchdog.py" in readme and "phoenixguard-cloud-watchdog.service" in readme),
    ]
    ok = all(bool(row["passed"]) for row in checks)
    return {"schema_version": "PG_RELEASE_READINESS_V1", "ok": ok, "checks": checks}


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify PhoenixGuard release/deployment contract readiness.")
    parser.add_argument("--out", default="", help="Optional JSON report path.")
    args = parser.parse_args()
    report = verify_release_readiness()
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if bool(report["ok"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
