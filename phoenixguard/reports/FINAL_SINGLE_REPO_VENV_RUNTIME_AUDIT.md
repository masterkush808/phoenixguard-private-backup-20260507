# PhoenixGuard Single Repo Venv Runtime Audit

Date: 2026-06-27

## Clear Answer

PhoenixGuard is currently running from the single project environment:

```text
C:\Users\thaba\OneDrive\Documents\The 808 Vision 2026\phoenixguard\.venv\Scripts\python.exe
```

No secondary PhoenixGuard virtual environments were found in the project. No active PhoenixGuard Python process was found running from global Python.

## Live Runtime Evidence

Active Python processes all use the repo `.venv`:

```text
tracker: .venv\Scripts\python.exe Backend\launch\start_phoenixguard_24_7_tracker.py --port 8793 --capture-interval 15
api: .venv\Scripts\python.exe Backend\launch\start_phoenixguard_mobile_api.py
mt4 bridge: .venv\Scripts\python.exe Backend\tools\phoenixguard_mt4_file_bridge.py --poll-sec 15
shooter reporter: .venv\Scripts\python.exe Backend\launch\shooter.py signal --poll 15
10h monitor: .venv\Scripts\python.exe Backend\tools\run_final_10h_production_certification.py --sample-sec 15
```

The runtime Python environment endpoint returned:

```text
ok: true
reason: repo .venv runtime active
sys_executable: phoenixguard\.venv\Scripts\python.exe
sys_prefix: phoenixguard\.venv
virtual_env: phoenixguard\.venv
site_packages: phoenixguard\.venv and phoenixguard\.venv\Lib\site-packages
```

`sys_base_prefix` points to the Windows Python installation because that is how a normal Windows virtual environment records its base interpreter. PhoenixGuard packages are loaded from the repo `.venv` site-packages, not global site-packages.

## Environment Folder Audit

Found:

```text
phoenixguard\.venv
```

Not found:

```text
phoenixguard\.venv-live
phoenixguard\.venv-dev
phoenixguard\.venv-training
phoenixguard\.venv-business
```

`Backend\scripts_runtime\env` is a scripts folder for environment installers. It is not a Python environment and must not be deleted as a venv.

## Runtime State Clarification

`.codex_runtime` is not a Python environment. It is the active PhoenixGuard runtime state, log, lock, heartbeat, screenshot, overlay, and certification evidence directory. The current live stack writes and reads:

```text
.codex_runtime\data_live
.codex_runtime\logs_live
.codex_runtime\frontend_heartbeat_v3
.codex_runtime\10h_cert
.codex_runtime\phoenixguard_stack.lock.json
.codex_runtime\tracker_status.json
```

Deleting `.codex_runtime` while the stack is running would remove live state and evidence, and can cause stale or missing overlay/session symptoms. Cleanups should use `Backend\tools\clean_v3_runtime_state.py` through `.venv\Scripts\python.exe` during a planned restart.

## Dependency Verification

Commands run with the repo `.venv`:

```text
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pipdeptree --warn fail
.\.venv\Scripts\python.exe Backend\tools\verify_dependency_profile.py --profile dev --json
.\.venv\Scripts\python.exe Backend\tools\verify_dependency_profile.py --profile live --json
.\.venv\Scripts\python.exe Backend\tools\certify_process_topology_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788 --port 8793
```

Results:

```text
pip check: PASS
pipdeptree --warn fail: PASS
dependency profile dev: PASS
dependency profile live: PASS
process topology: PASS
```

## Guardrails Confirmed

The following files enforce the single repo venv path:

```text
Backend\launch\Resolve-PhoenixGuardPython.ps1
_pg_bootstrap.py
sitecustomize.py
Backend\src\phoenixguard\runtime\python_environment_v3.py
Backend\tests\test_launcher_python_env_contract.py
```

Launchers now resolve `VenvPython` and reject or avoid global Python paths. The current runtime uses port `8793`.

## Remaining Caveat

Windows still shows the venv process as being based on `Python311` internally because `.venv` is built from that base interpreter. That is normal venv metadata, not a second PhoenixGuard package environment.
