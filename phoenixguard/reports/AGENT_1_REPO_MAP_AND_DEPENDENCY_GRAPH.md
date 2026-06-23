# Agent 1 Repo Map And Dependency Graph

CLEAR ANSWER

PhoenixGuard V3 is organized around active runtime files in `main.py`, `shooter.py`, `start_phoenixguard_24_7_tracker.py`, `start_phoenixguard_mobile_api.py`, `phoenixguard/mobile_api`, `phoenixguard/decision`, `phoenixguard/execution`, `phoenixguard/runtime`, `phoenixguard/vision`, scripts, tests, tools, docs, and web assets.

CONFIDENCE LEVEL

0.83

KEY CAVEATS

The repo contains ignored runtime state, generated caches, and historical outputs. They were classified conservatively and not deleted because the run did not reach full clean verification.

FILES STUDIED

`pyrightconfig.json`, `requirements.txt`, `phoenixguard/V3_CANONICAL_MANIFEST.json`, `.gitignore`, `tools/verify_v3_integrity.py`, `shooter.py`, `main.py`, `phoenixguard/mobile_api/*`, `phoenixguard/execution/*`, `phoenixguard/decision/*`, `tests/*`, `scripts/*`, `tools/*`, `web/package.json`.

ERRORS FOUND

Active files and generated/runtime files are interleaved in the workspace. `.venv`, `.next`, `.codex_runtime`, reports, and runtime logs can pollute broad verification commands.

FIXES APPLIED

Added `_backups/` and `.backups/` to `.gitignore`. Created backup directory `_backups/language_hardening_20260623_092724`. No production files were deleted.

TESTS RUN

`git status --short`, `git diff --stat`, repo manifest inspection, import/runtime integrity via `tools/verify_v3_integrity.py`.

REMAINING RISKS

No complete import graph artifact was generated beyond Pyright diagnostics and manual classification. Further dead-file deletion should wait until Pyright and full tests are clean.
