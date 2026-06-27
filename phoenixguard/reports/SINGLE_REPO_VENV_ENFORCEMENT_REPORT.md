# PhoenixGuard Single Repo Venv Enforcement Report

Date: 2026-06-27

## Clear Answer

PhoenixGuard is now hardened to use the original repo environment only:

```text
phoenixguard\.venv
phoenixguard\.venv\Scripts\python.exe
phoenixguard\.venv\Scripts\phoenixguard-python.exe
```

Long-running runtime processes use `phoenixguard-python.exe` as the process host inside the same `.venv`.

## Root Cause Found

`_pg_bootstrap.py` could derive the child-process executable from `.venv\pyvenv.cfg`, which points to the base Windows Python executable. That made some runtime metadata report a base-Python child process path even when the launcher was started from the repo `.venv`.

## Fixes Applied

- `_pg_bootstrap.py` now prefers `.venv\Scripts\phoenixguard-python.exe` for child processes.
- `sitecustomize.py` now pins:
  - `PHOENIXGUARD_PYTHON_EXE=.venv\Scripts\python.exe`
  - `PHOENIXGUARD_PYTHON_PROCESS_EXE=.venv\Scripts\phoenixguard-python.exe`
  - `PHOENIXGUARD_PYVENV_LAUNCHER=.venv\Scripts\python.exe`
- `python_environment_v3.py` now rejects a mismatched `PHOENIXGUARD_PYTHON_PROCESS_EXE`.
- Active docs no longer instruct normal operators to run `Activate.ps1`.
- The duplicate plain dashboard Chrome window was stopped; the dedicated dashboard profile window was kept.
- Generated root caches removed:
  - `.pytest_cache`
  - `__pycache__`

## What Was Preserved

`.codex_runtime` was preserved because it is runtime state, logs, locks, screenshots, and browser profile storage. It is not a Python environment and deleting it while the tracker is active would destroy live evidence and runtime state.

## Runtime Evidence

The restarted runtime environment endpoint reported:

```json
{
  "ok": true,
  "reason": "repo .venv runtime active",
  "phoenixguard_python_exe": "C:\\Users\\thaba\\OneDrive\\Documents\\The 808 Vision 2026\\phoenixguard\\.venv\\Scripts\\python.exe",
  "phoenixguard_python_process_exe": "C:\\Users\\thaba\\OneDrive\\Documents\\The 808 Vision 2026\\phoenixguard\\.venv\\Scripts\\phoenixguard-python.exe"
}
```

Active runtime roles after restart:

```text
tracker: phoenixguard-python.exe, port 8793, capture interval 15 seconds
api: phoenixguard-python.exe
shooter reporter: phoenixguard-python.exe, poll 15 seconds
MT4 bridge: phoenixguard-python.exe, poll 15 seconds
dashboard: one Chrome dashboard profile
```

## Validation

```text
compileall changed files: PASS
pyright changed files: PASS, 0 errors
pytest environment/launcher contracts: PASS, 29 passed
pip check: PASS
verify_dependency_profile.py --profile dev: PASS
runtime_trace_v3.py: PASS alignment, models 7/7, SequenceContext COMPLETE
```

## Remaining Note

`sys_base_prefix` still points to the installed Windows Python because a venv is built on top of a base interpreter. That is expected and is not a second active environment. The enforced runtime values now keep PhoenixGuard execution inside the repo `.venv`.
