# PhoenixGuard Single Environment Runtime Root Report

Date: 2026-06-26

## Clear Answer

PhoenixGuard now defaults to the repo virtual environment and repo runtime root:

- Python executable: `.venv\Scripts\python.exe`
- Runtime root: `.codex_runtime`
- Live data: `.codex_runtime\data_live`
- Live logs: `.codex_runtime\logs_live`
- Tracker status: `.codex_runtime\tracker_status.json`

The external stale runtime directory was deleted:

`C:\Users\thaba\AppData\Local\PhoenixGuard\codex_runtime`

Before deletion it contained 7,472 files, 38 directories, and about 1,878.07 MB.

## Files Changed

- `Backend/launch/launch_phoenixguard_live_ready.ps1`
- `Backend/launch/start_phoenixguard_full_local.ps1`
- `Backend/launch/start_phoenixguard_24_7_tracker.ps1`
- `Backend/launch/start_phoenixguard_24_7_tracker.py`
- `Backend/launch/start_phoenixguard_mobile_api.ps1`
- `Backend/launch/start_phoenixguard_mobile_api.py`
- `Backend/launch/deploy/windows/Start-PhoenixGuardVmMonitor.ps1`
- `Backend/launch/deploy/windows/phoenixguard.vm-monitor.env.example.ps1`
- `Backend/launch/deploy/windows/phoenixguard.vm-monitor.env.ps1`
- `Backend/launch/deploy/windows/WINDOWS_VM_CONTINUOUS_MONITOR.md`
- `Backend/tools/certification_common_v3.py`
- `Backend/tools/run_entry_allowance_burn.py`
- `Backend/src/phoenixguard/mobile_api/app.py`
- `README.md`

## Validation

- PowerShell launcher parse check: PASS
- `python -m compileall` on changed runtime/launcher/backend paths: PASS
- `python -m pyright`: PASS, 0 errors, 0 warnings
- `python -m pip check`: PASS
- `python -m pipdeptree --warn fail`: PASS
- `python Backend/tools/verify_v3_integrity.py`: PASS
- Focused regression tests: PASS, 102 passed

Full `python -m pytest -q` was attempted, but the command timed out after 15 minutes without a final result.

## Remaining Risk

Windows may still show a child process under the base Python path when `.venv\Scripts\python.exe` is used because of the Windows venv redirector. This is not a second PhoenixGuard environment when `PHOENIXGUARD_PYTHON_EXE`, `VIRTUAL_ENV`, and launcher paths point to the repo `.venv`.
