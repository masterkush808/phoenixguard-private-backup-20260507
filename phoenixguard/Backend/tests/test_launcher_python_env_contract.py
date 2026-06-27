from pathlib import Path

import pytest

from phoenixguard.runtime.python_environment_v3 import build_python_environment_status


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_launchers_require_repo_venv_python_instead_of_global_python() -> None:
    scripts = {
        "Backend/launch/start_phoenixguard_24_7_tracker.ps1": ("python @launcherArgs",),
        "Backend/launch/start_phoenixguard_mobile_api.ps1": (
            "py -3.11 -m venv",
            "Activate.ps1",
            "python -m pip",
            "python Backend\\launch\\start_phoenixguard_mobile_api.py",
        ),
        "Backend/launch/launch_phoenixguard_live_ready.ps1": ("py -3.11 -m venv",),
        "Backend/launch/start_phoenixguard_full_local.ps1": (
            "py -3.11 -m venv",
            "Activate.ps1",
        ),
        "Business/api/start_business_mock_local.ps1": ("python -m uvicorn",),
        "Business/api/start_phoenixguard_share.ps1": (
            "py -3.11 -m venv",
            "Activate.ps1",
            "python -m pip",
            "python $ShareRunnerPath",
        ),
        "Backend/launch/deploy/windows/Start-PhoenixGuardVmMonitor.ps1": (
            "py -3.11 -m venv",
            "Start-Process -FilePath $PythonPath",
        ),
        "Backend/tools/resume_paused_burn_20260623_050000.ps1": ('$python = "python.exe"',),
        "Backend/tools/start_enter_now_floating_gui.ps1": ('$Python = "python"',),
    }

    for relative_path, forbidden_fragments in scripts.items():
        text = _read(relative_path)
        assert "Resolve-PhoenixGuardPythonRuntime" in text
        assert ".ProcessPython" in text
        for fragment in forbidden_fragments:
            assert fragment not in text


def test_python_resolver_points_process_python_to_repo_venv() -> None:
    text = _read("Backend/launch/Resolve-PhoenixGuardPython.ps1")
    assert "$venvPython = Join-Path -Path $venvPath -ChildPath 'Scripts\\python.exe'" in text
    assert "$processPython = Join-Path -Path $scriptsPath -ChildPath 'phoenixguard-python.exe'" in text
    assert "$env:PHOENIXGUARD_PYTHON_PROCESS_EXE = $processPython" in text


def test_bootstrap_process_host_does_not_follow_pyvenv_base_python() -> None:
    bootstrap_text = _read("_pg_bootstrap.py")
    sitecustomize_text = _read("sitecustomize.py")
    assert '"phoenixguard-python.exe"' in bootstrap_text
    assert '"phoenixguard-python.exe"' in sitecustomize_text
    assert "pyvenv.cfg" not in bootstrap_text
    assert "multiprocessing.set_executable(process_text)" in bootstrap_text
    assert "multiprocessing.set_executable(process_text)" in sitecustomize_text


def test_runtime_environment_rejects_wrong_process_host(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    wrong_process_host = tmp_path / "Python311" / "python.exe"
    wrong_process_host.parent.mkdir(parents=True)
    wrong_process_host.write_text("", encoding="utf-8")
    monkeypatch.setenv("PHOENIXGUARD_PYTHON_PROCESS_EXE", str(wrong_process_host))

    status = build_python_environment_status(ROOT)

    assert status["ok"] is False
    assert "PHOENIXGUARD_PYTHON_PROCESS_EXE is not repo .venv process host" in status["reason"]


def test_certification_visual_tools_use_repo_process_host_for_child_python() -> None:
    for relative_path in (
        "Backend/tools/run_final_10h_production_certification.py",
        "Backend/tools/capture_overlay_anchor_screenshots_v3.py",
        "Backend/tools/certify_overlay_visual_truth_v3.py",
    ):
        text = _read(relative_path)
        assert "PHOENIXGUARD_PYTHON_PROCESS_EXE" in text
        assert "phoenixguard-python.exe" in text
        assert "[sys.executable" not in text


def test_final_certification_periodic_overlay_capture_is_lazy_loaded() -> None:
    text = _read("Backend/tools/run_final_10h_production_certification.py")
    assert '"CLEAN_LIVE,SUPPLY_DEMAND,TRENDLINES"' in text
    assert '"CLEAN_LIVE,SUPPLY_DEMAND,TRENDLINES,TRIGGER,FULL_HISTORY_READ"' not in text
