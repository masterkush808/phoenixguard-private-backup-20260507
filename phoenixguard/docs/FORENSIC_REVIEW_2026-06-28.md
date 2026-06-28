# PhoenixGuard Forensic Review - 2026-06-28

## Scope

This review covered the production overlay lock, overlay/dashboard truth flow, backend/frontend state
contracts, decision-lane wording, runtime cleanup safety, Windows launch/deploy scripts, dependency
profile wiring, documentation drift, and validation behavior on the current repo.

The production overlay setup was locked before this review:

- Branch: `main`
- Remote: `origin` at `https://github.com/masterkush808/phoenixguard-private-backup-20260507.git`
- Lock commit: `31bdfbe Lock production overlay configuration`
- Overlay behavior commit: `7684f4e Enforce atomic overlay display frame barrier`
- Annotated tag: `production-overlay-v3-atomic-20260628`
- Main branch protection: force pushes disabled, branch deletion disabled, required linear history enabled.

## Current Production Contract

PhoenixGuard V3 live mode is the default production path. The broker window is the source of truth for
fresh chart frames. The Chrome dashboard is the operator surface. The dashboard must display the same
backend-authoritative overlay frame bundle that the tracker produced, without registry fallback,
independent frontend reinterpretation, or stale display-cache promotion.

The local `Backend/launch/shooter.py` process is a package reporter only. It does not click, calibrate,
edit amount, set broker time, or manipulate broker controls. Any downstream external action must
consume and revalidate an accepted V3 allowance package.

## High-Severity Findings And Fixes

| Finding | Root cause | Fix applied |
| --- | --- | --- |
| Frontend heartbeat false positives | Dashboard used backend renderable overlay count as the visible DOM count, so image-rendered artifacts could look like live DOM overlays. | Split DOM overlay count from server artifact overlay count in `window_tracker_dashboard.html`, `app.py`, and `realtime_sync_v3.py`. |
| Compact live sidecar could hide overlay truth | `compact_live_state.json` could be newer than `session.json` while lacking overlay objects/geometry. | Added compact overlay summaries in `window_tracker.py`; API skips compact payloads without overlay summaries when a full session exists. |
| Stale runtime directory fallback | API direct reads could search legacy runtime/data paths and accept old payloads. | `_runtime_data_dir_candidates()` now uses `RUNTIME.data_dir` only unless `PHOENIXGUARD_ALLOW_RUNTIME_DATA_DIR_FALLBACK=1`. |
| Live mode wording leaked into broker-click identity | `live_execution_enabled` or `execution_mode=live` could request broker-click-safe identity. | `ModelCouncilV3` now treats normal live mode as paper-safe reporting; explicit `broker_click`, `broker`, or `live_click` remain legacy-compatible. |
| Runtime cleanup could follow an unintended env override | `clean_v3_runtime_state.py` trusted `PHOENIXGUARD_RUNTIME_DIR`. | Cleanup now refuses to operate outside `runtime/live`. |
| Venv cleanup could delete arbitrary top-level folders named `env` or `venv` | Cleanup only checked folder name. | `verify_single_venv_runtime.py` now requires `pyvenv.cfg` and a Python executable marker before deleting extra env folders. |
| Production bootstrap used broad requirement files | Several launch/deploy scripts installed `requirements.txt`. | Live scripts now prefer `requirements/locks/live-win-py311.txt`; business share prefers `requirements/locks/business-win-py311.txt`. |
| Cloudflare tunnel installer relayed token through elevated command line | Non-elevated relaunch passed `-TunnelToken` as an argument. | Installer now requires an elevated session and refuses token relay through a relaunched command line. |
| Quick tunnel stop could kill PID reuse | Stop script trusted only a stored PID. | Stop script now verifies the PID still belongs to a cloudflared command before terminating it. |

## Medium Findings And Fixes

- Model-strength save fallback marked failed backend saves as saved. It now records backend save
  failure and only treats live patching as temporary runtime state.
- TARGET overlay mode included invalidation boxes that backend clean-live mode does not expose. The
  frontend TARGET allow-list now matches the backend clean-live behavior.
- Legacy `Frontend/assets/js/overlay_skeleton.js` used the raw registry endpoint. It now reads
  `/v1/mobile/live/state/v3/{session_id}?compact=1` and normalizes overlays from that contract.
- The market tracker fallback schema still used `PG_V3_OVERLAY_OBJECT`. It now matches
  `PG_V3_OVERLAY_OBJECT_V1`.
- VM/share scheduled task defaulted toward SYSTEM startup behavior. It now defaults to current-user
  logon and requires explicit `-RunAtStartupAsSystem` for SYSTEM startup registration.

## Documentation And Requirement Updates

- `README.md` now describes the package reporter as non-clicking in the launch and burn-in sections.
- `docs/active_execution_paths.md` now describes package-reporter validation instead of shooter gate
  reachability.
- `docs/architecture/ARCHITECTURE_MAP.md` removed stale calibrated-fire-command and click wording.
- `docs/architecture/PhoenixGuard_System_Blueprint.md` now uses `runtime/live` artifact paths and
  states that local live mode keeps broker-click execution disabled.
- `Backend/launch/deploy/windows/WINDOWS_VM_CLOUDFLARE_TUNNEL.md` now points to the locked business
  requirements profile.
- `requirements/README.md` defines the current dependency-profile authority.

## Positive Architecture Elements

- The atomic overlay display frame barrier is the correct root fix for backend/frontend mismatch:
  broker screenshot, overlay image, model artifacts, and display state must advance as one coherent
  frame bundle.
- The V3 overlay contract and mode profiles give the dashboard a stable vocabulary for global, local,
  SMC/council, supply/demand, trendline, trigger, target, replay, and broker layers.
- The package-reporter split is the right safety boundary. It preserves live monitoring while keeping
  local broker manipulation retired.
- Runtime trace, performance trace, visual health, and live-state endpoints provide enough evidence to
  debug stale frames, overlay gaps, packet promotion, and source-lock problems without guessing.
- Split requirement profiles are appropriate for this repo because live, business, training, docs, and
  dev dependencies have different risk and weight profiles.

## Remaining Recommendations

- Add a smaller Pyright profile for the production hot path. Full strict Pyright over the repo is too
  slow for quick launch certification and should be separated from live preflight checks.
- Add a frontend heartbeat test harness that checks DOM overlay count, server artifact overlay count,
  and backend renderable count independently.
- Keep broker/source screenshots and dashboard screenshots in the same evidence manifest so every
  future overlay complaint compares one backend frame id and one frontend render id.
- Continue retiring or archiving old V1/V2 docs that imply direct broker-click behavior.
- Add a scheduled dependency-lock refresh process for each profile so lock files stay current without
  widening the live runtime package set accidentally.

## Validation Notes

The review intentionally avoids claiming that every file was manually rewritten. The repo is large, so
the audit used targeted source searches, agent passes across backend/frontend/docs/deploy layers,
compile/type/test validation, and direct root-cause patches for the mismatches found.

Final validation performed during this review:

- `pip check`: passed.
- `python -m compileall -q Backend/src/phoenixguard Backend/launch Backend/tools Business/api Frontend/dashboard`: passed.
- PowerShell parser check for the changed launch/deploy scripts: passed.
- JS syntax check for `Frontend/assets/js/overlay_skeleton.js` and
  `Frontend/dashboard/static/floating_windows/model_strength_controls.js`: passed.
- `npm --prefix Business/web run typecheck`: passed.
- Targeted Pyright on changed backend files: passed with 0 errors and 0 warnings.
- Targeted pytest set covering frontend heartbeat, realtime sync, compact live-state cache,
  floating-state contract, and model-council live/broker-click wording: 25 passed.
- `verify_single_venv_runtime.py --json`: passed; active PhoenixGuard processes were using the
  repo `.venv`.

Full strict Pyright over the entire repository remains too slow for quick forensic turns and timed out
during exploration. The recommended follow-up is to add a smaller production Pyright profile for the
hot path and reserve full strict analysis for a scheduled CI lane.
