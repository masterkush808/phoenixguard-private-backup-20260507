# PhoenixGuard V3 Repo Language Hardening Initial Audit

Generated: 2026-06-23

## Preservation Checkpoint

- Repo root: `C:\Users\thaba\OneDrive\Documents\The 808 Vision 2026`
- Checkpoint commit: `14f4c7b` (`checkpoint: working state before repo language hardening`)
- Checkpoint tag: `v3-working-before-language-hardening`
- Backup directory: `phoenixguard/_backups/language_hardening_20260623_092724`
- Backup manifest: `phoenixguard/_backups/language_hardening_20260623_092724/BACKUP_MANIFEST.txt`

## Backup Coverage

Copied:

- `808_shooter_boxes.json`
- `user_calibration_manifest.json`
- `config/shooter_broker_timing_profile.json`
- `launch_phoenixguard_live_ready.ps1`
- `start_phoenixguard_full_local.ps1`
- `phoenixguard/V3_CANONICAL_MANIFEST.json` from actual path `phoenixguard/phoenixguard/V3_CANONICAL_MANIFEST.json`
- `reports/`
- `tests/fixtures/`
- `.codex_runtime/`

Missing at audit time:

- outer-root `V3_CANONICAL_MANIFEST.json` (canonical manifest exists under `phoenixguard/phoenixguard/`)
- `runtime_trace_v3.json`
- `runtime_trace_v3.log`
- `tracker_status.json`
- `shooter_handshake.json`
- `logs_live/`

## Uploaded Diagnostic Surface

Source: `C:\Users\thaba\.codex\attachments\e7fa67b2-25a4-46e1-ab85-2ef7748d939a\pasted-text.txt`

Total diagnostics: `3489`

Top rule families:

- `reportUnknownMemberType`: 908
- `reportUnknownArgumentType`: 706
- `reportUnknownVariableType`: 702
- `reportPrivateUsage`: 255
- `reportUnknownLambdaType`: 199
- `reportUnknownParameterType`: 189
- `reportMissingParameterType`: 166
- `reportArgumentType`: 75
- `reportAttributeAccessIssue`: 71
- `reportTypedDictNotRequiredAccess`: 38
- markdown lint (`MD032`, `MD022`, `MD012`, `MD013`, `MD034`): 74 total
- CSS compatibility/prefix ordering: 7

Top file concentrations:

- `tests/test_full_suite.py`: 1001
- `tests/test_sequence_projection.py`: 199
- `tests/test_shooter_v3_runtime.py`: 158
- `tests/vision/test_enhanced_vision_phase1.py`: 136
- `tests/test_real_models.py`: 106
- `tests/test_tracker_bootstrap.py`: 100
- `tests/test_runtime_recovery.py`: 94
- `tests/test_v3_overlay_contract.py`: 86
- `scripts/build_sequence_teacher_manifest.py`: 81
- `generate_phoenixguard_pdf.py`: 19
- `phoenixguard/mobile_api/static/window_tracker_dashboard.html`: 7

## Configuration Issue Found Before Code Fixes

`pyrightconfig.json` currently sets many diagnostics to `"none"`, including unknown type, unused import/function/variable, optional access, argument type, and private usage checks. This conflicts with the requested hardening rule of not relying on global suppression. The hardening pass must replace this with a maintainable configuration after the concrete error families are addressed.

## Initial Risk Boundaries

- Do not alter `808_shooter_boxes.json`, `user_calibration_manifest.json`, or broker timing profile values.
- Do not weaken `PG_EXECUTION_PACKET_V3`, V3 execution authority, or shooter live-click gates.
- Do not delete active V3 runtime, model, calibration, or architecture files.
- Treat FastAPI route handlers as externally used through decorators even if static analyzers flag them as unused.
- Treat ReportLab/Torch stubs as possible third-party typing limits; isolate any unavoidable casts narrowly.

