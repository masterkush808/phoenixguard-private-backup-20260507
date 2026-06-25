from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_launchers_require_repo_venv_python_instead_of_global_python() -> None:
    scripts = {
        "Backend/launch/start_phoenixguard_24_7_tracker.ps1": ("python @launcherArgs",),
        "Backend/launch/start_phoenixguard_mobile_api.ps1": (
            "python -m pip",
            "python Backend\\launch\\start_phoenixguard_mobile_api.py",
        ),
        "Business/api/start_business_mock_local.ps1": ("python -m uvicorn",),
        "Business/api/start_phoenixguard_share.ps1": (
            "python -m pip",
            "python $ShareRunnerPath",
        ),
        "Backend/tools/resume_paused_burn_20260623_050000.ps1": ('$python = "python.exe"',),
        "Backend/tools/start_enter_now_floating_gui.ps1": ('$Python = "python"',),
    }

    for relative_path, forbidden_fragments in scripts.items():
        text = _read(relative_path)
        assert "Scripts\\python.exe" in text
        for fragment in forbidden_fragments:
            assert fragment not in text
