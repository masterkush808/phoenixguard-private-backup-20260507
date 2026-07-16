# PhoenixGuard One-Page Architecture Map

## 1) Input

- User provides image/file (chart screenshot or equivalent).
- Entry path: `run_inference(...)` in `main.py`. Live tracker path: `ContinuousWindowTrackerService`
  in `Backend/src/phoenixguard/mobile_api/window_tracker.py` keeps the locked broker/chart surface warm on a
  one-second capture loop.
- It collects frames for the dashboard.
- The service serves the dashboard through the mobile API.

## 2) Model Layer (ordered execution)

1. **Preprocess**

   - `load_any_file_as_image` -> `normalize_for_model` (`preprocess.py`).

2. **CV model (HF endpoint or YOLO fallback path)**

   - `cv_engine.detect(img)` (`cv_module.py`).
   - `MultiModelEnsemble` (`Backend/src/phoenixguard/vision/multi_model_ensemble.py`) can keep YOLO/ViT/SAM
     loaded and bag YOLO over same-coordinate contrast/sharpness views.
   - Boxes that survive cross-view agreement are then boosted.

3. **Structured chart-state extraction**

   - Heuristic/CV-native chart-state payload is built from live chart structure.
   - Window tracker extracts candle tracks, global/local/impulse boxes, historical structure legs,
     projected sniper/trigger/target zones, and significant support/resistance zones.
   - Smart-money context is attached to each box play: order-block retests, fair-value gaps,
     liquidity sweeps/pools, market-structure shift, S/R entry and target levels, and a per-box SMC
     score.

4. **Memory retrieval + context injection**

   - `_get_memory_bank()` load (`main.py` + `memory_ingest.py`).
   - `embed_description(chart_state, image=img)` -> `search(top_k=5)`.
   - Build few-shot context + compute memory boost.

5. **Regression forecasting**

   - `chronos.forecast_3m(chart_state, quantiles)` (`regression_module.py`).

6. **Style personalization update**

   - `personal.update_style(... )` (`personalization.py`).

7. **RL policy inference**

   - `rl_engine.infer(fused_features, memory_recall_top1_sim, memory_recall_direction)`
     (`rl_module.py`).

## 3) Feature Fusion Layer

- Fused vector combines:
  - CV detection stats,
  - chart-structure priors,
  - smart-money concepts,
  - historically significant support/resistance context,
  - Chronos quantiles,
  - personalization style vector.
- Built by `fused_feature_vector(...)` in `main.py`.

## 4) Diagnostic Gate Layer (13 core gates + support gates)

- Executed by `gates_engine.run_all(...)` in `skill_gates.py`.
- Gates include:

  1. Probability + conformal calibration
  2. Discrete FSM
  3. Algorithmic heap signal ranking
  4. Meta stacking
  5. Context retrieval
  6. Ops stability
  7. UI analytics
  8. Meta constraints
  9. Regression error estimation
  10. Knowledge representation (ontology coherence)
  11. Candle group context
  12. Formal automata state progression
  13. Predictive analytics fusion

- Gates are diagnostics only in the live path. They can warn, explain, and feed dashboards/logs, but
  they cannot select side, create HOLD, inflate confidence, choose expiry, veto a tracker-backed
  setup, or trigger execution.
- Supporting diagnostic checks:
  - continuation strength,
  - macro/local alignment,
  - memory regime agreement,
  - opposition pressure,
  - execution readiness telemetry,
  - forecast calibration,
  - interval efficiency,
  - regime stability,
  - transition alignment.
- Router weighting:
  - `LinearRouter(13->13)` scales gate scores for explainability/offline review.
  - MoE route weights are metadata only for live execution.

## 5) Ensemble Decision Layer

- `ensemble.infer(...)` in `ensemble.py` fuses:
  - RL calibrated probabilities,
  - memory similarity,
  - forecast interval constraints.
- Gate scores remain attached as diagnostics.
- Consensus rules enforce:
  - confidence threshold,
  - memory similarity requirement (or no-memory mode),
  - interval safety.
- Live tracker decisions additionally pass SMC and significant S/R evidence into
  `Backend/src/phoenixguard/decision/decision_kernel.py`, where those signals join the structure family before
  the final trade-mode
- Live broker timing is map-aware instead of fixed to an M3/M5 shortcut.
- `window_tracker.py` builds an `opposing_force_timing_v1` profile.
- The profile keeps the large Global/Local target horizon when the path is clear and scores
  buy-high/sell-low extreme risk.
- It maps significant opposing S/R forces from history.
- It only blocks or compresses when price is at a global/local peak/trough or an unbroken opposing
  level.
- A lower-history SELL is no longer treated as an automatic wait when the live picture is already
  firing: `current_flow_continuation_ready` can override the lower-history

## 6) Final Action + Outputs

- Final action: `BUY` / `SELL` / `HOLD`, with `HOLD` as the default until the tracker, decision
  kernel, timing engine, execution governor, calibration manifest, latency budget, cooldown, and
  broker state agree.
- Position sizing is outside the live control loop. The broker amount is preserved exactly as
  visible and PhoenixGuard does not calibrate or edit it.
- Explainability artifacts:
  - gate scores,
  - approximate SHAP contributions,
  - memory similarity,
  - forecast stats.
- Live authority flow:
  - tracker reasons over the full frame and dual BUY/SELL hypotheses,
  - Model Council authorizes only study or a V3 execution packet,
  - packet validation checks freshness, timing, source lock, model health, and runtime integrity,
  - the local shooter process reports accepted allowance packages only and never clicks or edits the broker.
- Visualization:
  - chart overlay,
  - always-live tracker dashboard at `/v1/mobile/window-tracker/dashboard/{session_id}`,
  - Study Map cells for Global, Local, Impulse, SMC, Live S/R, and Candles,
  - inspector rows for every overlay/projection/support/resistance box including SMC score, tags,
    entry level, target level, and summary,
  - Timing Lock rows/cards showing recommended expiry, timing class, hold intent, history-area
    label, entry-area score, opposing-force count/risk, and global-extreme risk,
  - confidence gauge,
  - optional skill dashboard.

## 7) Online Adaptation Loop

- If memory recall exists, RL receives recall-driven update batches.
- Router weights update from feedback history.
- Personalization vector evolves from user feedback + memory-bank DPO pairs.
- Allowed or externally handled packages may keep a trade record containing:
  - entry price proxy
  - decision kernel
  - trend-follow context, trigger/target event context, target-before-invalidation race outcome,
    hazard timing, direction alignment, target runway, and SMC/S/R context
  - timing profile
  - settlement memory
- Broker amount is not part of the strategy loop and must remain read-only. The local package
  reporter does not adjust expiry/time, click a direction, or preserve broker controls by touching
  them; downstream external bridges must revalidate the allowance package before any action.
- Live execution location gate is wick-aware and history-aware:
  - BUY requires a mapped significant support/studied-low context and is blocked in upper historical
    highs.
  - SELL requires a mapped significant resistance/studied-high context and is blocked in lower
    historical lows unless the strict live-flow continuation override proves the current sell is
    already moving with target runway.
  - Forward projection reports the current history area, preferred entry area, and nearest opposing
    force before any live trigger is allowed.
- Expired trades are classified as won/lost/flat when chart-proxy data is available and appended to
  `trade_outcomes.jsonl` for future timing review.
- Live tracker deployment uses the Windows VM scripts in `Backend/launch/deploy/windows/`:
  - `Start-PhoenixGuardVmMonitor.ps1` supervises API, tracker, and package reporter.
  - `phoenixguard.vm-monitor.env.ps1` pins `PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC=30.0`.
  - `Start-PhoenixGuardQuickTunnel.ps1` can publish the local API through Cloudflare quick tunnel
    when a public browser URL is needed.
