# Final Grade A Intelligence Report

Date: 2026-06-11

## Summary

PhoenixGuard V3 was hardened around the existing `FINAL_LIVE` doctrine:
observation is not execution, study is not execution, and only a fresh validated
`PG_EXECUTION_PACKET_V3` may reach calibrated broker action.

This pass did not create V4. It tightened V3 authority, provenance, sequence
readiness, shooter safety, model-role evidence, runtime dataflow trace, and
operator launch documentation.

## Implemented

### Canonical Execution Authority

- `main.py::run_inference` is explicitly marked as offline/manual analysis.
- Manual inference payloads now advertise `execution_authority: NONE`,
  `can_publish_execution_packet: false`, and `can_trigger_shooter: false`.
- Legacy observer/dashboard/manual analysis paths remain diagnostic only.
- `execute_v3_packet_trade` validates executable V3 packets before any live
  BUY/SELL side click.
- `ShooterActionSequencerV2.execute` validates packet authority before broker
  side action.
- Startup calibration packets are forced to time-only behavior and cannot click
  BUY or SELL.

### Packet V3 Hardening

- `build_execution_packet_v3` no longer fabricates a synthetic complete
  sequence context.
- Execution packets now carry mandatory provenance:
  `frame_id`, `capture_count`, `state_version`, `sequence_id`,
  `source_lock_id`, `model_health_id`, `chart_transform_id`,
  `created_epoch_ms`, and `valid_until_epoch_ms`.
- `validate_execution_packet_v3` verifies provenance identity, packet timing,
  resolved sequence identity, TTL, and broker-click-safe identity.
- Validation now rejects executable packets when sequence confidence, historical
  box context, progression context, or entry progression are missing/weak.

### SequenceContextV3

- Added canonical sequence schema marker `PG_SEQUENCE_CONTEXT_V3`.
- Added `status` alias for sequence readiness while preserving existing
  `sequence_status`.
- Readiness reports now expose explicit `COMPLETE` or `INCOMPLETE` status.
- Packet validation requires real sequence evidence rather than latest-candle
  fallback behavior.

### Model Role Evidence

- `ReasoningArbitratorV3` now normalizes role outputs into a full evidence
  contract.
- Role votes include model name, role, side vote, play vote, regime vote,
  confidence, frames used, freshness, evidence text, and risk warning.
- Existing supplied role outputs are normalized instead of blindly trusted.

### Runtime Trace And Certification Gates

- Mobile API runtime trace now emits a `PG_DATAFLOW_CONTRACT_TRACE_V3` map for:
  BrokerSourceLockV3, LatestFrameBufferV3, ChartSegmentationV3,
  CandleObjectTrackerV3, MarketObjectTrackerV3, SequenceContextV3,
  MultiModelRoleOutputsV3, RegimeEngineV3, MarketPlayEngineV3,
  PriceLocationEngineV3, VisualPlayMemoryBank, PairBehaviorProfileV3,
  SkillContributionAggregatorV3, ReasoningArbitratorV3, ModelCouncilV3,
  STUDY_PACKET, PG_EXECUTION_PACKET_V3, RuntimeTraceV3,
  Dashboard/FloatingStateV2, and ShooterActionSequencerV2.
- Runtime trace now exposes certification gate state for source lock, frame
  freshness, sequence context, model warm state, overlay truth, Model Council
  trace, packet contract, shooter persistence, and burn-in.
- `Backend/tools/runtime_trace_v3.py` prints the new dataflow summary.
- Certification reporting now includes Broker Source Lock, Wrong Surface
  Rejection, and Overlay Mode Wiring.

### README Operations

- `README.md` now contains a precise PowerShell kill/reset/launch workflow.
- Read-only launch with `-DisableShooter` is documented.
- Post-launch runtime trace, sequence trace, and integrity verification commands
  are documented without malformed placeholder URLs.
- Shooter safety doctrine and packet authority language are documented.

## Files Changed In This Pass

- `README.md`
- `main.py`
- `shooter.py`
- `Backend/src/phoenixguard/decision/reasoning_arbitrator_v3.py`
- `Backend/src/phoenixguard/execution/packet_v3.py`
- `Backend/src/phoenixguard/execution/sequence_context.py`
- `Backend/src/phoenixguard/execution/shooter_action_sequencer.py`
- `Backend/src/phoenixguard/mobile_api/app.py`
- `Backend/tools/runtime_trace_v3.py`
- `Backend/tools/certification_common_v3.py`
- `Backend/tests/__init__.py`
- `Backend/tests/support/__init__.py`
- `Backend/tests/support/v3_packet_samples.py`
- `Backend/tests/test_execution_packet_schema_v3.py`
- `Backend/tests/test_market_intelligence_v3.py`
- `Backend/tests/test_market_reality_engine.py`
- `Backend/tests/test_manual_inference_queue.py`
- `Backend/tests/test_runtime_telemetry_v3.py`
- `Backend/tests/test_shooter_action_sequencer.py`
- `Backend/tests/test_shooter_v3_runtime.py`
- `Backend/tests/test_simulation_paper_execution.py`
- `Backend/tests/test_v3_language_contracts.py`

## Tests Passed

```text
.\.venv\Scripts\python.exe -m pytest Backend/tests/test_execution_packet_schema_v3.py Backend/tests/test_model_council_v3.py Backend/tests/test_market_reality_engine.py Backend/tests/test_market_intelligence_v3.py Backend/tests/test_v3_language_contracts.py Backend/tests/test_simulation_paper_execution.py Backend/tests/test_shooter_action_sequencer.py -q
144 passed in 17.77s
```

```text
.\.venv\Scripts\python.exe -m pytest Backend/tests/test_shooter_v3_runtime.py -q
42 passed in 9.07s
```

```text
.\.venv\Scripts\python.exe -m pytest Backend/tests/test_cache_observability_v3.py Backend/tests/test_runtime_telemetry_v3.py Backend/tests/test_manual_inference_queue.py -q
33 passed in 54.45s
```

```text
.\.venv\Scripts\python.exe -m compileall -q main.py shooter.py Backend\src\phoenixguard\decision Backend\src\phoenixguard\execution Backend\src\phoenixguard\mobile_api Backend\src\phoenixguard\runtime Backend\tools\runtime_trace_v3.py Backend\tools\certification_common_v3.py
PASS
```

```text
.\.venv\Scripts\python.exe Backend\tools\verify_v3_integrity.py
Overall: PASS
```

Integrity verifier highlights:

- V3 manifest: PASS
- Tracker API: PASS
- Model Council V3: PASS
- Market Reality Engine: PASS
- Execution Packet V3: PASS
- V3 Language Contracts: PASS
- Shooter Action Sequencer: PASS
- Floating State V2: PASS
- Calibration Manifest: PASS
- Observability V3: PASS
- Calibration: PASS
- Legacy Trigger Paths: PASS - DISABLED
- FINAL_LIVE canonical launch profile: PASS
- Runtime Cache: PASS

## Certification Gate Status

- Gate 1, Source Lock: implemented in trace/certification reporting.
- Gate 2, Frame Freshness: implemented in trace/certification reporting.
- Gate 3, Sequence Context: enforced in packet validation and trace reporting.
- Gate 4, Model Warm State: implemented in trace/certification reporting.
- Gate 5, Overlay Truth: implemented in trace/certification reporting.
- Gate 6, Model Council Trace: implemented in trace/certification reporting.
- Gate 7, Packet Contract: enforced by packet builder, validator, and tests.
- Gate 8, Shooter Persistence: covered by sequencer and shooter runtime tests.
- Gate 9, Burn-In: gate is represented, but a live two-hour run was not
  executed during this pass.

## Live-Ready Rerun Addendum

Date/time: 2026-06-11, Asia/Calcutta

Additional blockers fixed during the live rerun:

- Display-only refresh no longer overwrites authoritative capture fields.
- Model Council study/execution endpoints now prefer the file-backed tracker
  session so the API process cannot lose packet authority in split launch mode.
- Compact direct-read state now preserves V3 study/execution packets,
  `promotion_trace`, and `SequenceContextV3`.
- Study packet freshness now respects `valid_until_epoch` instead of rejecting a
  fresh study packet only because a newer display/capture frame exists.
- Persisted scenario generation is disabled in the live execution hot path by
  default unless `PHOENIXGUARD_ENABLE_LIVE_SCENARIO_GENERATION=1` is explicitly
  set.
- The live-ready launcher gates shooter arming on RuntimeTraceV3 authority and
  fresh performance trace before starting `shooter.py`.
- The burn-in monitor now supports consecutive stale-frame gating through
  `--max-consecutive-stale-frames`.

Additional tests passed:

```text
tests/test_live_visual_state_v3.py::test_compact_session_payload_preserves_v3_authority_packets_and_sequence
tests/test_window_tracker_service.py::test_tracker_scenario_generation_runs_when_enabled
tests/test_window_tracker_service.py::test_tracker_scenario_generation_stays_disabled_in_live_hot_path
tests/test_window_tracker_service.py::test_tracker_display_only_refresh_does_not_replace_authority_frame
4 passed
```

```text
tests/test_runtime_telemetry_v3.py Backend/tests/test_realtime_performance_v3.py
tests/test_model_council_v3.py::test_high_frequency_two_candle_lane_publishes_fixed_600s_packet
tests/test_high_frequency_candle_predictor.py
12 passed
```

```text
tests/test_shooter_v3_runtime.py
42 passed
```

Live launch evidence:

- `Backend/launch/launch_phoenixguard_live_ready.ps1 -NoBrowser -WarmupSeconds 20` completed
  readiness, runtime authority, and freshness gates.
- Shooter started in `LIVE_READY` mode against
  `http://127.0.0.1:8793`.
- Runtime trace after launch showed source lock PASS, sequence PASS, study PASS,
  packet contract PASS, and shooter WAITING when no fresh executable packet was
  available.
- Shooter handshake rejected an expired executable packet with
  `RUNTIME_INTEGRITY: PACKET_EXPIRED` and `will_click=false`, confirming expired
  packets are not consumed for broker action.

Two-hour burn-in status:

- Active monitor PID: `26876`.
- Report target:
  `reports/FINAL_FULL_SYSTEM_ACTIVATED_BURN_IN_RERUN14_REPORT.md`.
- Live samples are being written to `.codex_runtime/burn_in/`.
- First activated sample: source lock PASS, sequence PASS, study PASS, shooter
  process present, and `will_click=false` while waiting for a fresh executable
  packet.

## Remaining Blockers With Evidence

### Burn-In Completion Pending

Evidence: the two-hour burn-in is now running in the operator environment. Final
verdict depends on the monitor completing the full 7200 seconds without process
loss, wrong-surface rejection, risk-limit stop, or unsafe stale packet
consumption.

### Live Broker Click Outcome Not Yet Proven

Evidence: the shooter is armed but only clicks after a fresh validated
`PG_EXECUTION_PACKET_V3`. During the observed startup window it correctly refused
expired authority and did not click.

## Final Verdict

PhoenixGuard V3 now has stricter discipline of connection across live authority:
packet provenance is mandatory, sequence context cannot be faked, shooter action
is packet-validated, manual inference is isolated as non-authoritative, model
role outputs are traceable, and runtime trace exposes the canonical dataflow and
certification gates.

The remaining Grade A operational proof is the live two-hour burn-in.

## Overlay And Activated Burn-In Addendum

Date/time: 2026-06-12 00:55 Asia/Calcutta

Additional live blockers fixed:

- Frontend overlay rendering no longer hides valid backend V3 overlay objects
  while display heartbeat frames advance faster than model overlay frames.
- Dashboard EventSource updates now merge enriched live-state payloads instead
  of replacing them with raw tracker payloads that omit overlay objects.
- Surface artifact rendering now falls back cleanly when a full-overlay artifact
  is briefly unavailable, preventing the dashboard from staying on a stale
  "live surface updating" state.
- Backend compact live state now drops expired `PG_EXECUTION_PACKET_V3` objects
  and sanitizes broker execution state to non-actionable waiting.
- Shooter now rejects expired snapshot execution packets before evaluating any
  gate.
- Shooter now pins to the tracker-locked broker HWND via `--window-hwnd`.
- Synthesized compatibility `STUDY_PACKET` objects can no longer inherit an
  `EXECUTABLE` state; they remain `WATCHING` until a fresh
  `PG_EXECUTION_PACKET_V3` exists.

Files changed in this addendum:

- `Frontend/dashboard/static/window_tracker_dashboard.html`
- `phoenixguard/mobile_api/live_state_v3.py`
- `Backend/src/phoenixguard/mobile_api/window_tracker.py`
- `Backend/tools/certification_common_v3.py`
- `Backend/tools/certify_v3_full_system_burn_in.py`
- `Backend/tools/capture_dashboard_visual_v3.py`
- `Backend/launch/launch_phoenixguard_live_ready.ps1`
- `Backend/launch/shooter.py`
- `Backend/tests/test_shooter_v3_runtime.py`
- `Backend/tests/test_live_visual_state_v3.py`
- `Backend/tests/test_window_tracker_service.py`
- `Backend/tests/test_certification_common_v3.py`
- `README.md`

Focused tests passed:

```text
tests/test_shooter_v3_runtime.py::test_synthesized_study_packet_never_inherits_executable_authority
tests/test_shooter_v3_runtime.py::test_find_pocket_option_window_prefers_locked_hwnd
tests/test_shooter_v3_runtime.py::test_shooter_writes_handshake_for_study_packet
tests/test_shooter_v3_runtime.py::test_extract_model_council_packet_ignores_expired_snapshot_execution_packet
tests/test_live_visual_state_v3.py::test_compact_session_payload_drops_expired_execution_authority
tests/test_window_tracker_service.py::test_model_council_packet_lookup_ignores_expired_execution_packet
tests/test_ui_copy_hardening.py::test_tracker_dashboard_uses_backend_overlay_objects_for_live_overlays
tests/test_certification_common_v3.py::test_wmic_python_process_fallback_preserves_comma_arguments
8 passed
```

Frontend visual verification:

```text
tools/capture_dashboard_visual_v3.py
verdict: PASS
hard_mismatches: none
visible_image: true
full_overlay_image: true
overlay_rendered: true
updating_state_visible: false
screenshot: reports/dashboard_overlay_verify/dashboard_pocket-live-8788_20260612_010241.png
```

Runtime evidence at restart:

- Source lock: `VALID`
- Locked broker HWND: `132820`
- Sequence status: `READY`
- Overlay objects: `51`
- Renderable overlays: `8`
- Execution packet: absent; no stale execution packet exposed
- Broker execution state: non-actionable waiting for fresh
  `PG_EXECUTION_PACKET_V3`
- Shooter handshake: selected HWND `132820`, preferred HWND `132820`,
  `window_matches_preferred=true`, `packet_type=STUDY_PACKET`,
  `execution_state=WATCHING`, `will_click=false`

Current two-hour activated burn-in:

- Monitor parent PID: `11756`
- Monitor child PID: `14320`
- Shooter parent PID: `17328`
- Shooter child PID: `29708`
- Report target:
  `reports/FINAL_FULL_SYSTEM_ACTIVATED_BURN_IN_OVERLAYFIXED_FINAL_REPORT.md`
- Monitor logs:
  `reports/burn_in_runtime/final_2h_activated_monitor_stdout.log`
  and `reports/burn_in_runtime/final_2h_activated_monitor_stderr.log`
- Shooter logs:
  `reports/burn_in_runtime/live_ready_final_shooter_relaunch_stdout.log`
  and `reports/burn_in_runtime/live_ready_final_shooter_relaunch_stderr.log`

Remaining blocked condition:

- The live system is ready and armed, but the shooter is correctly waiting
  because Model Council has not published a fresh executable V3 packet during
  this verification sample. The current blocker is market/runtime maturity, not
  overlay rendering, source lock, stale packet exposure, or shooter surface
  selection.

## Calibration And Stable Overlay Addendum

Date/time: 2026-06-12 03:22 Asia/Calcutta

Final live-action safety corrections:

- Stopped the live shooter before calibration work so no further broker clicks
  could occur while coordinates were unsafe.
- `shooter.py::load_boxes` now prefers `user_calibration_manifest.json` when it
  is marked `authoritative_execution_source=true`.
- Legacy `808_shooter_boxes.json` can no longer override the user manifest.
- Runtime click targets are generated only from manifest-marked calibrated
  targets: broker focus, expiry field, expiry +/- controls, BUY, and SELL.
- Old split fields such as `hourly_input`, `minute_input`, `second_input`,
  `time_300`, and stale `final_screen` are not eligible unless they are
  explicitly user-calibrated in the manifest.
- Calibration validation now rejects ambiguous non-alias duplicate points
  instead of only warning.
- Dashboard overlay viewport updates are now dimension/signature gated, so live
  heartbeat refreshes do not repeatedly mutate zoom and pan when the visible
  overlay plane has not changed.

Verification:

```text
tests/test_shooter_action_sequencer.py::test_manifest_authoritative_boxes_use_combined_time_without_legacy_split_targets
tests/test_shooter_action_sequencer.py::test_exact_preset_fallback_when_typed_controls_missing
tests/test_shooter_v3_runtime.py::test_load_boxes_prefers_authoritative_user_calibration_manifest
tests/test_shooter_v3_runtime.py::test_validate_calibration_rejects_non_alias_duplicate_points
tests/test_shooter_v3_runtime.py::test_validate_calibration_allows_time_button_alias_overlap
tests/test_ui_copy_hardening.py::test_tracker_dashboard_uses_backend_overlay_objects_for_live_overlays
6 passed
```

Live calibration preview on locked HWND `132820`:

- `broker_screen`: manifest source, abs `(1453, 297)`
- `time_button` / `time_input`: manifest source, abs `(1715, 258)`
- `buy_icon`: manifest source, abs `(1782, 446)`
- `sell_icon`: manifest source, abs `(1724, 494)`
- validation: `true`

Frontend visual verification:

- `Backend/tools/capture_dashboard_visual_v3.py`
- verdict: `PASS`
- screenshot:
  `reports/dashboard_overlay_verify/dashboard_pocket-live-8788_20260612_032150.png`

## Silent Bug Review And Overlay Snap Addendum

Date/time: 2026-06-12 Asia/Calcutta

Five-agent review slices completed:

- Frontend overlay viewport and coordinate projection.
- Backend live-state/overlay artifact parity.
- Shooter/calibration/manual execution safety.
- Test and certification gap review.
- Git/release readiness and staging risk audit.

Implemented hardening:

- Dashboard overlay artifact URLs no longer churn on every capture when the
  artifact path/frame is unchanged.
- Hotspots are cleared while replacement images are pending and are redrawn only
  after the target image load event.
- Old queued viewport scroll restores are cancelled when a newer viewport update
  supersedes them.
- Overlay freshness no longer treats `chart_frame_id` as proof of overlay
  freshness.
- Live-state overlay objects are withheld when the displayed overlay artifact
  frame does not match the overlay-object frame.
- V3 overlay `bounds` remains the canonical coordinate array; dimensions are
  exposed through `bounds_rect`.
- Registry overlay cache default was reduced to avoid long-lived stale overlay
  merges.
- Manual one-shot shooter mode now requires both
  `PHOENIXGUARD_ALLOW_LIVE_BROKER_CLICKS=1` and `--allow-live-click`.
- Shooter pre-click confirmation now rejects changed packet identity, expiry,
  and typed-time target contracts.
- Shooter runtime integrity accepts canonical `created_epoch_sec` and
  `valid_until_epoch_sec` fields.
- Second-precision expiry requests abort unless a seconds-capable calibrated
  path exists.
- Calibration manifest validation rejects out-of-range calibrated points.

Verification added:

- Live-state frame-parity regression for reused stale overlay artifacts.
- Dashboard source regression for stable artifact URLs and load-gated hotspot
  rendering.
- Shooter regression for stale same-side expiry packets.
- Shooter regression for canonical `_epoch_sec` packet fields.
- Manual mode regression for explicit live-click arming.
- Action sequencer regression for second-precision expiry refusal.
- Calibration manifest regression for tampered out-of-range points.
