from pathlib import Path

import pytest

from phoenixguard.runtime.python_environment_v3 import (
    build_python_environment_status,
    is_live_runtime_python_command,
)


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
        ),
        "Backend/tools/resume_paused_burn_20260623_050000.ps1": ('$python = "python.exe"',),
        "Backend/tools/start_enter_now_floating_gui.ps1": ('$Python = "python"',),
    }

    for relative_path, forbidden_fragments in scripts.items():
        text = _read(relative_path)
        assert "Resolve-PhoenixGuardPythonRuntime" in text
        assert ".VenvPython" in text
        assert ".ProcessPython" not in text
        for fragment in forbidden_fragments:
            assert fragment not in text


def test_python_resolver_points_every_python_path_to_profile_environment() -> None:
    text = _read("Backend/launch/Resolve-PhoenixGuardPython.ps1")
    assert "Get-PhoenixGuardPythonEnvironmentName" in text
    assert "'live' { return '.venv-live' }" in text
    assert "'dev' { return '.venv-dev' }" in text
    assert "$env:PHOENIXGUARD_PYTHON_ENV_NAME = $environmentName" in text
    assert "$venvPython = Join-Path -Path $venvPath -ChildPath 'Scripts\\python.exe'" in text
    assert "PHOENIXGUARD_PYTHON_PROCESS_EXE" not in text
    assert "ProcessPython" not in text
    assert "phoenixguard-python.exe" not in text
    assert "Copy-Item -LiteralPath $basePython" not in text


def test_bootstrap_uses_only_repo_venv_python() -> None:
    bootstrap_text = _read("_pg_bootstrap.py")
    sitecustomize_text = _read("sitecustomize.py")
    assert '"phoenixguard-python.exe"' not in bootstrap_text
    assert '"phoenixguard-python.exe"' not in sitecustomize_text
    assert "pyvenv.cfg" not in bootstrap_text
    assert "multiprocessing.set_executable" not in bootstrap_text
    assert "multiprocessing.set_executable" not in sitecustomize_text
    assert "__PYVENV_LAUNCHER__" not in bootstrap_text
    assert "__PYVENV_LAUNCHER__" not in sitecustomize_text


def test_runtime_environment_rejects_wrong_repo_python(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    wrong_python = tmp_path / "Python311" / "python.exe"
    wrong_python.parent.mkdir(parents=True)
    wrong_python.write_text("", encoding="utf-8")
    monkeypatch.setenv("PHOENIXGUARD_PYTHON_ENV_NAME", ".venv-dev")
    monkeypatch.setenv("VIRTUAL_ENV", str(ROOT / ".venv-dev"))
    monkeypatch.setenv("PHOENIXGUARD_PYTHON_EXE", str(wrong_python))

    status = build_python_environment_status(ROOT)

    assert status["ok"] is False
    assert "PHOENIXGUARD_PYTHON_EXE is not configured PhoenixGuard python" in status["reason"]


def test_certification_visual_tools_use_repo_venv_for_child_python() -> None:
    for relative_path in (
        "Backend/tools/run_final_10h_production_certification.py",
        "Backend/tools/capture_overlay_anchor_screenshots_v3.py",
        "Backend/tools/certify_overlay_visual_truth_v3.py",
    ):
        text = _read(relative_path)
        assert "PHOENIXGUARD_PYTHON_EXE" in text
        assert 'Scripts" / "python.exe"' in text
        assert "PHOENIXGUARD_PYTHON_PROCESS_EXE" not in text
        assert "phoenixguard-python.exe" not in text
        assert "[sys.executable" not in text


def test_profile_installers_use_separate_locked_environments() -> None:
    expected_envs = {
        "Backend/scripts_runtime/env/install_live.ps1": ".venv-live",
        "Backend/scripts_runtime/env/install_dev.ps1": ".venv-dev",
        "Backend/scripts_runtime/env/install_training.ps1": ".venv-training",
        "Backend/scripts_runtime/env/install_business.ps1": ".venv-business",
        "Backend/scripts_runtime/env/install_docs.ps1": ".venv-docs",
    }
    for relative_path, environment_name in expected_envs.items():
        text = _read(relative_path)
        assert f"Join-Path $ProjectRoot '{environment_name}'" in text
        assert "py -3.11 -m venv $venvPath" in text
        assert "Remove-Item -LiteralPath $venvPath -Recurse -Force" not in text
        assert "phoenixguard_repo_paths.pth" in text


def test_final_certification_periodic_overlay_capture_is_lazy_loaded() -> None:
    text = _read("Backend/tools/run_final_10h_production_certification.py")
    assert '"CLEAN_LIVE,SUPPLY_DEMAND,TRENDLINES"' in text
    assert '"CLEAN_LIVE,SUPPLY_DEMAND,TRENDLINES,TRIGGER,FULL_HISTORY_READ"' not in text


def test_single_venv_runtime_verifier_documents_runtime_state_not_environment() -> None:
    text = _read("Backend/tools/verify_single_venv_runtime.py")
    assert "EXTRA_ENVIRONMENT_DIR_NAMES" in text
    assert '".venv-live"' not in text
    assert '".venv-dev"' not in text
    assert '".venv-training"' not in text
    assert '".venv-business"' not in text
    assert "runtime_dir_is_environment=False" in text
    assert "process_scan_status" in text
    assert "port_scan_status" in text
    assert "Refusing to remove unexpected environment path" in text


@pytest.mark.parametrize(
    "command_line",
    (
        r'python.exe "C:\repo\Backend\launch\start_phoenixguard_24_7_tracker.py"',
        r'python.exe "C:\repo\Backend\launch\start_phoenixguard_mobile_api.py"',
        r'python.exe "C:\repo\Backend\launch\shooter.py" signal',
        r"python.exe -m phoenixguard.runtime.model_council_daemon",
        r'python.exe "C:\repo\Backend\tools\phoenixguard_disk_growth_guard.py" --apply',
        r"python.exe -m uvicorn phoenixguard.mobile_api.app:app --port 8793",
        r'python.exe "C:\repo\Backend\tools\phoenixguard_mt4_file_bridge.py"',
    ),
)
def test_live_runtime_process_scope_includes_only_stack_entrypoints(command_line: str) -> None:
    assert is_live_runtime_python_command(command_line) is True


@pytest.mark.parametrize(
    "command_line",
    (
        r".venv-dev\Scripts\python.exe -m pyright Backend\src\phoenixguard\mobile_api\app.py",
        r".venv-dev\Scripts\python.exe -m pytest Backend\tests\test_window_tracker_service.py",
        r".venv-dev\Scripts\python.exe -m compileall Backend\src\phoenixguard",
        r".venv-dev\Scripts\python.exe -",
        r"python.exe C:\Users\developer\.vscode\extensions\pylance\language_server.py",
    ),
)
def test_live_runtime_process_scope_excludes_pylance_and_development_tools(command_line: str) -> None:
    assert is_live_runtime_python_command(command_line) is False


def test_kill_switch_targets_named_stack_roles_without_repo_wide_python_fallback() -> None:
    text = _read("Developer/developer_tools/phoenixguard_kill_switch.py")

    assert '"phoenixguard_disk_growth_guard.py"' in text
    assert 'command = [sys.executable, str(cleaner), "--apply", "--delete"]' in text
    assert 'repo_text in command and "phoenixguard" in command' not in text
    assert "if row.name.lower() in STACK_PROCESS_NAMES:" not in text


def test_canonical_dashboard_launchers_use_v3_window_tracker_dashboard_route() -> None:
    for relative_path in (
        "Backend/launch/launch_phoenixguard_live_ready.ps1",
        "Backend/launch/start_phoenixguard_full_local.ps1",
    ):
        text = _read(relative_path)
        assert "/v3/mobile/window-tracker/dashboard/$SessionId" in text
        assert "/v1/mobile/window-tracker/dashboard/$SessionId" not in text


def test_canonical_live_launcher_enables_validated_native_display_capture_fallback() -> None:
    text = _read("Backend/launch/launch_phoenixguard_live_ready.ps1")

    assert "$env:PHOENIXGUARD_DISPLAY_ALLOW_NATIVE_CAPTURE_FALLBACK = '1'" in text
    assert "display_native_capture_fallback_enabled = $env:PHOENIXGUARD_DISPLAY_ALLOW_NATIVE_CAPTURE_FALLBACK" in text


def test_canonical_live_launcher_bounds_native_threads_and_avoids_codex_session_pruning() -> None:
    text = _read("Backend/launch/launch_phoenixguard_live_ready.ps1")
    resolver_index = text.index("Resolve-PhoenixGuardPythonRuntime")

    assert "[Environment]::GetEnvironmentVariable($Name, 'Process')" in text
    assert "[string]::IsNullOrWhiteSpace([string]$currentValue)" in text
    assert "[Environment]::SetEnvironmentVariable($Name, $Value, 'Process')" in text
    for name, value in (
        ("OMP_NUM_THREADS", "2"),
        ("MKL_NUM_THREADS", "2"),
        ("OPENBLAS_NUM_THREADS", "2"),
        ("NUMEXPR_NUM_THREADS", "2"),
        ("TOKENIZERS_PARALLELISM", "false"),
        ("PHOENIXGUARD_CHRONOS_CPU_THREADS", "2"),
        ("PHOENIXGUARD_BACKGROUND_WARMUP_ON_LAUNCH", "1"),
    ):
        fragment = f"Set-PhoenixGuardDefaultProcessEnvironment -Name '{name}' -Value '{value}'"
        assert fragment in text
        assert text.index(fragment) < resolver_index

    final_live_fragment = "$env:PHOENIXGUARD_PROFILE = 'FINAL_LIVE'"
    python_profile_fragment = (
        "Set-PhoenixGuardDefaultProcessEnvironment "
        "-Name 'PHOENIXGUARD_PYTHON_PROFILE' -Value 'live'"
    )
    assert text.index(final_live_fragment) < resolver_index
    assert text.index(python_profile_fragment) < resolver_index
    assert "$env:PHOENIXGUARD_DISK_GUARD_INCLUDE_CODEX_SESSIONS = '0'" in text
    assert "$env:PHOENIXGUARD_DISK_GUARD_INCLUDE_CODEX_SESSIONS = '1'" not in text
    assert "$env:PHOENIXGUARD_DISK_GUARD_MAX_BYTES = '512MB'" in text
    assert "$env:PHOENIXGUARD_DISK_GUARD_LOW_WATER_BYTES = '384MB'" in text
    assert "$env:PHOENIXGUARD_OVERLAY_PERSIST_DEBUG = '0'" in text
    assert "$env:PHOENIXGUARD_OVERLAY_GEOMETRY_DUMPS = '0'" in text
    assert "$env:PHOENIXGUARD_UVICORN_ACCESS_LOG = '0'" in text
    assert "$env:PHOENIXGUARD_PERSIST_CHILD_STDIO = '0'" in text


def test_canonical_live_launchers_keep_display_and_forecast_frames_atomic() -> None:
    live_ready = _read("Backend/launch/launch_phoenixguard_live_ready.ps1")
    full_local = _read("Backend/launch/start_phoenixguard_full_local.ps1")

    assert "$env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT = '0'" in live_ready
    assert "else { '0' }" in next(
        line
        for line in full_local.splitlines()
        if "PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT =" in line
    )


def test_live_launch_paths_disable_source_bytecode_cache_growth() -> None:
    powershell_launchers = (
        "Backend/launch/launch_phoenixguard_live_ready.ps1",
        "Backend/launch/start_phoenixguard_full_local.ps1",
    )
    python_entrypoints = (
        "Backend/launch/start_phoenixguard_24_7_tracker.py",
        "Backend/launch/start_phoenixguard_mobile_api.py",
    )

    for relative_path in powershell_launchers:
        text = _read(relative_path)
        bytecode_guard = "$env:PYTHONDONTWRITEBYTECODE = '1'"
        assert bytecode_guard in text
        assert text.index(bytecode_guard) < text.index("Resolve-PhoenixGuardPythonRuntime")

    for relative_path in python_entrypoints:
        text = _read(relative_path)
        environment_guard = 'os.environ["PYTHONDONTWRITEBYTECODE"] = "1"'
        interpreter_guard = "sys.dont_write_bytecode = True"
        bootstrap_import = "from _pg_bootstrap import ensure_project_paths"
        assert environment_guard in text
        assert interpreter_guard in text
        assert text.index(environment_guard) < text.index(bootstrap_import)
        assert text.index(interpreter_guard) < text.index(bootstrap_import)


def test_all_production_tracker_and_shooter_defaults_are_thirty_seconds() -> None:
    expected_fragments = {
        "Backend/src/phoenixguard/mobile_api/app.py": (
            "_WINDOW_TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC = 30.0",
        ),
        "Backend/src/phoenixguard/mobile_api/window_tracker.py": (
            "_TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC = 30.0",
        ),
        "Backend/launch/start_phoenixguard_24_7_tracker.py": (
            'PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC", "30.0"',
        ),
        "Backend/launch/start_phoenixguard_24_7_tracker.ps1": (
            "else { 30.0 }",
        ),
        "Backend/launch/start_live_dashboard.ps1": (
            "else { 30.0 }",
        ),
        "Backend/launch/launch_live_ready.ps1": (
            "else { 30.0 }",
        ),
        "Backend/launch/launch_full_then_shooter.ps1": (
            "else { 30.0 }",
        ),
        "Backend/launch/launch_phoenixguard_live_ready.ps1": (
            "PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC) { [double]$env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC } else { 30.0 }",
            "PHOENIXGUARD_SHOOTER_POLL_SEC) { [double]$env:PHOENIXGUARD_SHOOTER_POLL_SEC } else { 30.0 }",
        ),
        "Backend/launch/start_phoenixguard_full_local.ps1": (
            "PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC) { [double]$env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC } else { 30.0 }",
            "PHOENIXGUARD_SHOOTER_POLL_SEC) { [double]$env:PHOENIXGUARD_SHOOTER_POLL_SEC } else { 30.0 }",
        ),
        "Backend/launch/deploy/windows/Start-PhoenixGuardVmMonitor.ps1": (
            "PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC' -DefaultValue '30.0'",
            "PHOENIXGUARD_SHOOTER_POLL_SEC' -DefaultValue '30.0'",
        ),
        "Backend/launch/deploy/windows/phoenixguard.vm-monitor.env.ps1": (
            "PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC = '30.0'",
            "PHOENIXGUARD_SHOOTER_POLL_SEC = '30.0'",
        ),
        "Backend/launch/deploy/windows/phoenixguard.vm-monitor.env.example.ps1": (
            "PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC = '30.0'",
            "PHOENIXGUARD_SHOOTER_POLL_SEC = '30.0'",
        ),
        "Backend/tools/start_entry_allowance_burn_orchestrator.ps1": (
            "PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC) { [double]$env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC } else { 30.0 }",
            "PHOENIXGUARD_SHOOTER_POLL_SEC) { [double]$env:PHOENIXGUARD_SHOOTER_POLL_SEC } else { 30.0 }",
        ),
        "Backend/launch/shooter.py": (
            "DEFAULT_POLL_SECONDS = 30.0",
        ),
        "Backend/tools/enter_now_floating_gui.py": (
            '"--poll-ms", type=int, default=30000',
        ),
        "Backend/tools/start_enter_now_floating_gui.ps1": (
            "[int]$PollMilliseconds = 30000",
        ),
        "Backend/tools/certify_process_topology_v3.py": (
            "--capture-interval 30 --no-open-dashboard",
            "--poll 30.0 --heartbeat 4.0",
        ),
        "Backend/tools/certify_shooter_persistence_v3.py": (
            "--poll 30.0 --heartbeat 4.0",
        ),
        "Backend/launch/deploy/windows/WINDOWS_VM_CONTINUOUS_MONITOR.md": (
            r".\.venv-live\Scripts\python.exe Backend\launch\shooter.py",
            "--poll 30.0 --heartbeat 4.0",
        ),
        "Developer/developer_tools/phoenixguard_kill_switch.py": (
            '"--capture-interval-sec", type=float, default=30.0',
            '"--shooter-poll-sec", type=float, default=30.0',
        ),
        "Developer/deployment/windows_worker_bootstrap.ps1": (
            "[double]$CaptureIntervalSec = 30.0",
        ),
    }

    for relative_path, fragments in expected_fragments.items():
        text = _read(relative_path)
        for fragment in fragments:
            assert fragment in text, f"{relative_path} lost the 30-second production cadence"


def test_dashboard_browser_launcher_quotes_chrome_profile_paths_with_spaces() -> None:
    text = _read("Backend/launch/start_phoenixguard_full_local.ps1")

    assert "ConvertTo-PhoenixGuardProcessArgumentString -Arguments $browserArguments" in text
    assert "Start-Process -FilePath $browserPath -ArgumentList $browserArgumentString" in text
    assert "Start-Process -FilePath $browserPath -ArgumentList (Get-PhoenixGuardDashboardBrowserArguments" not in text
