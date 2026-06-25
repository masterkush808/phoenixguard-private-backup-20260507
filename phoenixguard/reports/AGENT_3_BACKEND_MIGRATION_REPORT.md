# Agent 3 Backend Migration Report

## CLEAR ANSWER

Backend runtime code was migrated to `Backend` while preserving the V3 authority chain and root launcher compatibility.

## CONFIDENCE LEVEL

0.93

## KEY CAVEATS

Root `main.py`, `shooter.py`, and launch scripts remain in place intentionally. They are entrypoints, not duplicate active packages.

## FILES STUDIED

- `Backend/src/phoenixguard`
- `Backend/tools`
- `Backend/tests`
- `Backend/scripts_runtime`
- `Backend/scripts_data`
- `Backend/launch`
- `pyproject.toml`
- `pytest.ini`
- `pyrightconfig.json`

## FIXES APPLIED

- Moved `phoenixguard/` to `Backend/src/phoenixguard`.
- Moved `tools/` to `Backend/tools`.
- Moved `tests/` to `Backend/tests`.
- Moved runtime/data scripts to `Backend/scripts_runtime` and `Backend/scripts_data`.
- Moved deploy and MT4 support under `Backend/launch`.
- Added package metadata for the src layout.
- Updated V3 manifest paths.
- Updated launcher `PYTHONPATH` handling to include `Backend/src`, `Backend`, and repo root.
- Updated backend static serving to read dashboard files from `Frontend`.

## TESTS RUN

- `python Backend/tools/verify_v3_integrity.py` passed.
- Compile and focused pytest passed.
- Full pytest rerun is recorded in final validation.

## REMAINING RISKS

Root wrappers should stay until external shortcuts, docs, and scheduled tasks are confirmed migrated.
