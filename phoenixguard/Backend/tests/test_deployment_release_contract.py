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


def test_release_readiness_and_cloud_watchdog_are_documented() -> None:
    deployment_readme = _read("Developer/deployment/README.md")
    runbook = _read("docs/deployment/PHOENIXGUARD_WORLDWIDE_DEPLOYMENT_RUNBOOK.md")

    assert "verify_release_readiness.py" in deployment_readme
    assert "phoenixguard_cloud_watchdog.py" in deployment_readme
    assert "phoenixguard-cloud-watchdog.service" in deployment_readme
    assert "live-linux-py311.txt" in runbook
    assert "live-win-py311.txt" in runbook


def test_frame_ingest_deployment_hardening_stays_enabled() -> None:
    frame_ingest = _read("Backend/src/phoenixguard/mobile_api/frame_ingest.py")

    assert "PHOENIXGUARD_FRAME_INGEST_MAX_PIXELS" in frame_ingest
    assert "Animated frame uploads are not allowed" in frame_ingest
    assert "Frame upload format is not allowed" in frame_ingest
    assert "commit=False" in frame_ingest
    assert "commit=True" in frame_ingest


def test_windows_vm_monitor_process_cleanup_is_scoped_to_repo_runtime_or_session() -> None:
    monitor = _read("Backend/launch/deploy/windows/Start-PhoenixGuardVmMonitor.ps1")

    assert "$projectRootNeedle" in monitor
    assert "$runtimeRootNeedle" in monitor
    assert "$sessionNeedle" in monitor
    assert "$matchesThisRepo" in monitor
    assert "$matchesRuntime -or $matchesSession" in monitor
