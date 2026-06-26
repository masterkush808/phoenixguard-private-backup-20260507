# Overlay Uptime Root Cause Forensic - 2026-06-27

## Clear Answer

The overlay outage was not caused by the model failing to detect overlays. The backend had valid overlay objects, but the API was falsely treating normal display-frame fingerprint drift as a new-pair/wrong-surface event while the model/overlay session lagged behind the fast display heartbeat. That made `CLEAN_LIVE` publish empty overlays and kept the UI in "studying new pair" even when the chart had not actually changed pairs.

## Root Cause

- `display_state.json` advances quickly from the live capture loop.
- `session.json` / `compact_live_state.json` advance more slowly because overlay/model reasoning is heavier.
- The compact live-state gate compared the fast display surface signature against the older overlay source signature.
- Normal candle/timer/full-window changes changed the display signature.
- The API interpreted that as pair-switch authority mismatch and returned an empty "studying new pair" overlay response.
- Floating state also read the heavy full session and did not carry compact clean-live overlays, so dashboard/inspector state could disagree with live-state overlays.
- The MT4 bridge used an 8-second timeout against the execution packet endpoint; heavy overlay rebuilds caused false `BRIDGE_ERROR` timeouts even when the correct answer was `NO_EXECUTION_PACKET`.

## Fixes Applied

- Changed the compact overlay authority gate so display-signature mismatch only blanks live overlays when explicit market-selector rebind evidence exists.
- Re-enabled session-level market-selector probing so real pair switches still clear overlays safely.
- Added compact-payload fast paths for Model Council latest/study/execution reads to avoid reading the multi-megabyte session file just to report no executable packet.
- Enriched floating state with clean-live overlay counts and objects so floating/dashboard consumers no longer see zero overlays while live-state has objects.
- Added persisted compact overlay warm-start cache guarded by broker surface signature.
- Raised MT4 bridge timeout from 8 seconds to default 30 seconds, configurable with `PHOENIXGUARD_MT4_BRIDGE_TIMEOUT_SEC`.
- Updated broker source-lock certification to use compact live-state with a longer timeout.
- Hardened the brittle endpoint test TTL from 2 seconds to 120 seconds.

## Verification Evidence

- `CLEAN_LIVE` live-state after restart: 10 overlay objects, 105 total overlays, no empty reason.
- Floating state after restart: 10 overlay objects, 105 total overlays.
- Execution endpoint: safe HTTP 404 `Model Council executable packet not found`, no fake packet.
- MT4 bridge files fresh: `NO_EXECUTION_PACKET`, HTTP 404, schema `PG_MT4_BRIDGE_STATUS_V1`.
- Process topology: PASS.
- Broker source lock: PASS.
- Overlay contract: PASS, 10 overlays, 0 invalid.
- Overlay precision audit: no missing transforms, no unanchored boxes, no stale frame IDs, no label collisions.

## Tests Run

- `python -m pyright Backend/src/phoenixguard/mobile_api/app.py Backend/tests/test_cache_observability_v3.py`
- `python -m compileall -q Backend/src/phoenixguard/mobile_api/app.py Backend/tests/test_cache_observability_v3.py`
- `python -m pytest -q Backend/tests/test_cache_observability_v3.py::test_compact_live_state_returns_studying_new_pair_when_surface_outruns_overlay_authority Backend/tests/test_cache_observability_v3.py::test_compact_live_state_does_not_reuse_studying_new_pair_cache_after_overlay_recovers Backend/tests/test_cache_observability_v3.py::test_model_council_latest_execution_packet_endpoints_return_v3_packet Backend/tests/test_v3_integrity.py::test_floating_state_endpoint_uses_clean_contract`
- `python Backend/tools/validate_overlay_contract_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788 --mode CLEAN_LIVE --timeout 90`
- `python Backend/tools/certify_broker_source_lock_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788 --timeout 60`
- `python Backend/tools/audit_overlay_precision_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788 --mode CLEAN_LIVE`
- `python Backend/tools/certify_process_topology_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788 --data-dir .codex_runtime/data_live`

## Caveat

Full-repo Pyright was started but did not return within 5 minutes while the live stack was running. Changed Python files passed Pyright with 0 errors and 0 warnings.
