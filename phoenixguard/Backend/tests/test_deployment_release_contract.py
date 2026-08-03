from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_linux_cloud_brain_uses_linux_live_lock_and_watchdog() -> None:
    bootstrap = _read("Developer/deployment/linux_cloud_brain_bootstrap.sh")

    assert (ROOT / "requirements" / "locks" / "live-linux-py311.txt").exists()
    assert "requirements/locks/live-linux-py311.txt" in bootstrap
    assert "requirements/locks/live-win-py311.txt" not in bootstrap
    assert "phoenixguard-cloud-watchdog.service" in bootstrap
    assert "phoenixguard_cloud_watchdog.py" in bootstrap
    assert "systemctl enable --now phoenixguard-cloud-watchdog.service" in bootstrap
    assert "PHOENIXGUARD_FRAME_INGEST_REQUIRE_SIGNATURE=1" in bootstrap
    assert "PHOENIXGUARD_FEED_SIGNING_SECRET_ADMIN" in bootstrap


def test_release_readiness_and_cloud_watchdog_are_documented() -> None:
    deployment_readme = _read("Developer/deployment/README.md")
    runbook = _read("docs/deployment/PHOENIXGUARD_WORLDWIDE_DEPLOYMENT_RUNBOOK.md")

    assert "verify_release_readiness.py" in deployment_readme
    assert "phoenixguard_cloud_watchdog.py" in deployment_readme
    assert "phoenixguard-cloud-watchdog.service" in deployment_readme
    assert "live-linux-py311.txt" in runbook
    assert "live-win-py311.txt" in runbook
    assert (ROOT / "Developer" / "deployment" / "cloudflare_security" / "main.tf").exists()
    assert (ROOT / "Developer" / "deployment" / "model_asset_manifest.py").exists()


def test_frame_ingest_deployment_hardening_stays_enabled() -> None:
    frame_ingest = _read("Backend/src/phoenixguard/mobile_api/frame_ingest.py")

    assert "PHOENIXGUARD_FRAME_INGEST_MAX_PIXELS" in frame_ingest
    assert "Animated frame uploads are not allowed" in frame_ingest
    assert "Frame upload format is not allowed" in frame_ingest
    assert "PG_FRAME_INGEST_V1" in frame_ingest
    assert "SIGNATURE_NONCES" in frame_ingest
    assert "PG_SECURITY_AUDIT_V1" in frame_ingest
    assert "_reserve_feed_runtime(" in frame_ingest
    assert "feed_reservation_committed = True" in frame_ingest
    assert "_rollback_feed_runtime_reservation(feed_reservation)" in frame_ingest


def test_edge_agent_and_verifier_send_signed_frame_uploads() -> None:
    edge_agent = _read("Developer/deployment/edge_frame_agent.py")
    feed_verifier = _read("Developer/deployment/verify_universal_frame_feed.py")

    assert "X-PhoenixGuard-Signature" in edge_agent
    assert "HMAC-SHA256-V1" in edge_agent
    assert "X-PhoenixGuard-Signature" in feed_verifier
    assert "--signing-secret" in feed_verifier


def test_internal_family_lifetime_plan_is_hidden_and_admin_scoped() -> None:
    packages = _read("Backend/src/phoenixguard/business/packages.py")
    business_api = _read("Backend/src/phoenixguard/business/api.py")

    assert "INTERNAL_FAMILY_LIFETIME_PLAN_CODE" in packages
    assert "public_visible=False" in packages
    assert "family-lifetime-license" in business_api
    assert "admin_grant_family_lifetime_license" in business_api


def test_windows_vm_monitor_process_cleanup_is_scoped_to_repo_runtime_or_session() -> None:
    monitor = _read("Backend/launch/deploy/windows/Start-PhoenixGuardVmMonitor.ps1")

    assert "$projectRootNeedle" in monitor
    assert "$runtimeRootNeedle" in monitor
    assert "$sessionNeedle" in monitor
    assert "$matchesThisRepo" in monitor
    assert "$matchesRuntime -or $matchesSession" in monitor
