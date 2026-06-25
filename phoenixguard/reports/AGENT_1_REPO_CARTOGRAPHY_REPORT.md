# Agent 1 Repo Cartography Report

## CLEAR ANSWER

PhoenixGuard was cartographed as a staged V3 migration, not a blind folder move. The active runtime package is now `Backend/src/phoenixguard`, runtime tools are under `Backend/tools`, tests are under `Backend/tests`, dashboard/static assets are under `Frontend`, commercial web/API files are under `Business`, and training/development scripts are under `Developer`.

## CONFIDENCE LEVEL

0.91

## KEY CAVEATS

Root launchers and compatibility wrapper modules remain at repo root intentionally so existing operator commands, tests, and Windows launch paths do not break. Runtime/model/cache folders were preserved, not deleted.

## FILES STUDIED

- `Backend/src/phoenixguard/V3_CANONICAL_MANIFEST.json`
- `Backend/src/phoenixguard/paths.py`
- `launch_phoenixguard_live_ready.ps1`
- `start_phoenixguard_full_local.ps1`
- `start_phoenixguard_24_7_tracker.py`
- `start_phoenixguard_mobile_api.py`
- `shooter.py`
- `Backend/src/phoenixguard/mobile_api/app.py`
- `Backend/src/phoenixguard/mobile_api/window_tracker.py`
- `Backend/tools/verify_v3_integrity.py`
- `Backend/tests`
- `Frontend/dashboard/static`
- `Business/api`
- `Developer`
- `docs`

## CLASSIFICATION SUMMARY

- ACTIVE_BACKEND: `Backend/src/phoenixguard`, `Backend/tools`, `Backend/scripts_runtime`, `Backend/scripts_data`, root launchers, root compatibility modules.
- ACTIVE_FRONTEND: `Frontend/dashboard/static`, `Frontend/assets`, `Frontend/package.json`.
- ACTIVE_DEVELOPER: `Developer/model_training`, `Developer/model_exports`, `Developer/datasets`, `Developer/sequence_teacher`, `Developer/developer_tools`.
- ACTIVE_BUSINESS: `Business/api`, `Business/web`, `Business/business_docs`.
- ACTIVE_TEST: `Backend/tests`, `Backend/tests/fixtures`.
- ACTIVE_CONFIG: `pyproject.toml`, `pyrightconfig.json`, `pytest.ini`, `.gitignore`, `Backend/typings`.
- ACTIVE_DOC: `README.md`, `docs`, tracked restructure reports.
- GENERATED_RUNTIME: `.codex_runtime`, `logs`, `.pytest_cache`, `__pycache__`, runtime evidence.
- ACTIVE_MODEL_ASSET: `models`, `.hf_cache`, `adapters`, `memory_bank`, `yolov8n.pt`.
- ACTIVE_CALIBRATION: ignored local calibration artifacts were preserved.

## FIXES APPLIED

Moved files into the four-folder layout and added a marker-based path contract through `phoenixguard.paths` so code can resolve `PROJECT_ROOT`, `BACKEND_ROOT`, `FRONTEND_ROOT`, `BUSINESS_ROOT`, and `DEVELOPER_ROOT`.

## TESTS RUN

See `reports/AGENT_8_VALIDATION_REPORT.md` and `reports/FINAL_TEST_VALIDATION_REPORT.md`.

## REMAINING RISKS

External Windows shortcuts or scheduled tasks outside git may still reference old paths. Root compatibility launchers remain to reduce that risk.
