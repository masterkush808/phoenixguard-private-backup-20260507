# PhoenixGuard V3 Architecture Map

> The canonical deep-study contract is the
> [PhoenixGuard V3 Market Study Blueprint](PHOENIXGUARD_V3_MARKET_STUDY_BLUEPRINT.md). PhoenixGuard
> studies every newly proven closed candle continuously; study history does not require a manual
> baseline or a fixed-length operator run.

## 1) Input

- User provides image/file (chart screenshot or equivalent).
- Entry path: `run_inference(...)` in `main.py`. Live tracker path: `ContinuousWindowTrackerService`
  in `Backend/src/phoenixguard/mobile_api/window_tracker.py` keeps the locked broker/chart surface warm on a
  completion-scheduled loop. The canonical launcher default is 30 seconds, with bounded adaptive
  scheduling allowed. Durable study progress advances only on a new, identity-proven closed-candle
  event; repeated frames are idempotent. The CPU observer treats byte-identical frames as capture
  health rather than market rest; an explicitly locked chart may use a rate-limited,
  identity-verified visible recovery that synthesizes no input and re-admits at most one recovery
  keyframe.
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

5. **Regression evidence contributor**

   - `chronos.forecast_3m(chart_state, quantiles)` (`regression_module.py`).
   - Its output is diagnostic evidence. The public study remains historical and the contributor
     cannot grant entry or execution permission.

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

## 4) Continuous Closed-Candle Research Layer

The V3 study lane consumes only closed candles with one declared coordinate space and chronological
order domain. It is independent of execution authority and is bounded at every level.

| Capability | V3 implementation | Contract |
| --- | --- | --- |
| Hierarchical multi-resolution motifs | `study/motif_lattice_v3.py` composes single-candle micro-events, 3-5 candle atoms, 7-12 candle compounds, and full swing/rest regimes. | Maximum depth four and maximum 2,048 nodes per level; descriptive historical geometry only. |
| Time-to-event evidence | The motif module builds Kaplan-Meier-style curves for next swing, direction change, and rest end, with explicit right censoring and Greenwood intervals. | Historical duration distribution only; no future deadline, independence, or causal claim. |
| Adaptive feature ontology | `study/adaptive_feature_ontology_v3.py` proposes features in a shadow namespace, records versioned temporal/leakage gates, promotes only passing revisions, and supports audited rollback. | A passing gate proves eligibility for public study, not causation or predictive value. |
| Exact historical path reconstruction | The motif module reconstructs anchor-known median-range paths with MFE, MAE, efficiency, state transitions, and time in state. | Future candles never influence normalization; reconstructed paths remain historical examples. |
| Joint Path-Clock Liquidity Field | `study/path_clock_liquidity_v3.py` estimates stop-before-target survival over normalized path, remaining contract clock, and a five-axis liquidity state. `study/path_clock_liquidity_store_v3.py` owns restart-safe anchors, trajectories, freezes, replay calibration, and the compact Pair DNA partition. The tracker admits clock evidence only through a source-backed or one-rollover boundary certificate bound to the resolver key, sequence, and row. | New anchors require 900-7,200 seconds. Capture time is never candle time, gaps are censored rather than interpolated, active anchors keep learning through the final clock, raw paths stay in the bounded side store, and timing may veto but never grant entry permission. |
| Cross-pair association graph | `study/cross_pair_association_v3.py` compares exact shared closed timestamps in compatible normalized spaces with a Granger-style variance-reduction proxy and mutual information. `study/cross_pair_coordinator_v3.py` atomically retains bounded normalized returns so independently arriving pair updates can be synchronized without fabricating a peer. | **Explicitly non-causal.** Circular-shift significance supports association only; it does not prove influence, direction, or a tradable lead. Until a genuine compatible peer and support exist, the result remains `INSUFFICIENT_SYNCHRONIZED_PAIR` or `INSUFFICIENT_SUPPORT`. |
| Online regime drift | `study/concept_drift_v3.py` applies adjacent-window KS evidence with multiplicity control and a mean-shift floor, then creates deterministic regime partition IDs. | A partition boundary describes a distribution change; it does not predict market direction. |
| Study claim proof certificates | `study/study_claim_proof_v3.py` binds a claim to ordered closed-candle IDs, coordinate space, order domain, bounded inputs, and derivation hashes. | A valid digest proves derivation integrity only; it does not authenticate the market source, prove causation, or authorize a trade. |

`MarketStudyServiceV3` runs these capabilities from restart-safe continuous history and publishes
bounded study keys beside Pair DNA and the exact candle ledger: `motif_lattice`, `survival_network`,
`path_reconstruction`, `path_clock_liquidity`, `adaptive_feature_ontology`, `concept_drift`, `regime_partition`,
`cross_pair_association`, and `claim_proofs`. Ontology and drift rebuild deterministically from the
retained closed-candle evidence; the cross-pair coordinator owns its separate bounded atomic state.
These services do not create a new product version, a second decision lane, or an execution
shortcut.

## 5) Diagnostic Gate Layer (13 core gates + support gates)

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

## 6) Ensemble Decision Layer

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
- Live broker timing is map-aware and JPCLF-aware instead of fixed to an M3/M5 shortcut.
- `window_tracker.py` builds `jpclf_aware_timing_v3` with a hard 15-minute duration floor.
- The profile keeps the large Global/Local target horizon when the path is clear and scores
  buy-high/sell-low extreme risk.
- It maps significant opposing S/R forces from history.
- It only blocks or compresses when price is at a global/local peak/trough or an unbroken opposing
  level.
- A lower-history SELL is no longer treated as an automatic wait when the live picture is already
  firing: `current_flow_continuation_ready` can override the lower-history

## 7) Final Action + Outputs

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

## 8) Online Adaptation Loop

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
