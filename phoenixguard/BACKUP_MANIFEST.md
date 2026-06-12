# PhoenixGuard Private Backup Manifest

This repository is a private PhoenixGuard code backup created before cleanup on 2026-05-07 and updated after cleanup/organization.

Included: source code, tests, scripts, docs, frontend assets, deployment templates, root calibration/config files, and lightweight data fixtures.

Excluded intentionally: virtualenv, runtime session/log folders, Hugging Face cache, model binaries, raw memory datasets, user runtime profiles, encrypted preferences, local deployment secrets, pycache, pytest cache, and other generated artifacts.

## Final Hardened Trader Dashboard Snapshot

Saved on 2026-05-08 after restoring the verified private backup and hardening the live dashboard launcher.

- Default dashboard/API port: `8793`.
- Default live session: `pocket-live-8788`.
- Base capture cadence: `0.5s`.
- Adaptive capture bounds: `0.5s` minimum, `2.0s` maximum.
- Signal freshness contract: PhoenixGuard now publishes `published_epoch`,
  `published_at`, `signal_age_sec`, `freshness_score`,
  `freshness_window_sec`, and `pipeline_latency_sec` so Shooter measures age
  from the completed Phoenix decision, not from pre-inference capture start.
- Pipeline timing contract: PhoenixGuard records per-stage capture/inference
  timings and logs the slowest capture stage.
- Live report timing: PhoenixGuard reuses a fresh live report for `20s` and
  defers report refresh around executable states so report generation does not
  starve Shooter freshness.
- Shooter polling default: `0.05s`; published-signal age window default: `8s`.
- Execution mode on dashboard start: `shadow`.
- Memory projection remains enabled as advisory context, but it is not a
  required live-click gate by default.
- Live execution gate remains controlled by PhoenixGuard broker/identity/actionability checks.
- Verification: `.venv\Scripts\python.exe -m pytest` passed with `563 passed`.
- PowerShell launcher validation: `start_live_dashboard.ps1` parses cleanly and has no PSScriptAnalyzer errors/warnings.
- Dashboard smoke launch: isolated port `8795` returned health `ok` with
  `capture_interval_sec=0.5`, adaptive min `0.5`, adaptive max `2.0`, then the
  smoke listener was stopped.

Launch:

```powershell
.\.venv\Scripts\Activate.ps1
.\start_live_dashboard.ps1 -ForceRestart
python shooter.py signal `
  --session-id pocket-live-8788 `
  --base-url http://127.0.0.1:8793 `
  --poll 0.05 `
  --max-signal-age 8
```
