# Repo Hygiene Report

## Execution-Path Files

| File                                        | Classification | Notes                                                                               |
| ------------------------------------------- | -------------- | ----------------------------------------------------------------------------------- |
| `Backend/launch/shooter.py`                 | ACTIVE_CORE    | V3 packet reader and accepted allowance-package reporter; no broker-click authority |
| `Backend/src/phoenixguard/decision/model_council_v3.py` | ACTIVE_CORE    | candidate promotion, study packet, executable packet creation                       |
| `Backend/src/phoenixguard/mobile_api/window_tracker.py` | ACTIVE_CORE    | tracker session state, council publication, packet lookup                           |
| `Backend/src/phoenixguard/mobile_api/app.py`            | ACTIVE_CORE    | HTTP endpoint wiring for council/latest, study/latest, execution/latest             |
| `Backend/src/phoenixguard/execution/packet_v3.py`       | ACTIVE_CORE    | executable packet builder and validator                                             |
| `Backend/tools/phoenixguard_mt4_file_bridge.py`     | ACTIVE_SUPPORT | package-aware MT4 command bridge and allowance revalidator                          |
| `Backend/launch/mt4/PhoenixGuard_MT4_Executioner.mq4`      | ACTIVE_SUPPORT | MT4 external execution consumer with intraday/swing package controls                |
| `Backend/launch/start_phoenixguard_full_local.ps1` | ACTIVE_SUPPORT | local process launcher and profile summary                                          |
| `Backend/tools/diagnose_v3_execution_path.py`       | ACTIVE_SUPPORT | endpoint comparison tool                                                            |

## Quarantine Decision

No files were quarantined in this pass. The repo has many untracked and modified files, and several
legacy-looking paths are still referenced by tests or launchers. Quarantining without a full
import/reference pass would risk breaking the live tracker.

## Next Hygiene Pass

Run an import/reference analysis before permanently deleting anything. Do not create a quarantine
or backup archive; delete only files proven generated or unused. The duplicate classes to inspect are:

- packet builders: `rg "build_execution_packet|PG_EXECUTION_PACKET"`
- signal parsers: `rg "execution_action|candidate_action|latest_signal"`
- trigger publishers: `rg "TRIGGER|SNIPER_READY|WAIT_FOR_TRIGGER"`
- shooter parsers: `rg "fetch_latest_model_council_packet|_extract_model_council_packet"`
- cooldown managers: `rg "cooldown|trade_discipline|locked_until"`
- test signal generators: `rg "--test-signal|generate_test_signal|startup_test_entry"`
