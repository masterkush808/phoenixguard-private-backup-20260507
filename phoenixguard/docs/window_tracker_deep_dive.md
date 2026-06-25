# Window Tracker Deep Dive (archived summary)

This file has been shortened and the full deep-dive has been moved to:

- `docs/archive/window_tracker_deep_dive.md`

Operator note: the archived document contains implementation-level details, artifacts, and internal payload contracts. Use the archived copy when performing deep maintenance or migration work.

If you need a brief operational summary, see `docs/README.md` (operator runbook) and the live dashboard routes under the mobile API.

### 3.1. `WindowsWindowCaptureBackend`

This component is responsible for desktop window discovery and capture.

- It enumerates visible Windows desktop windows via `EnumWindows`.
- It keeps only windows with:
  - non-empty title
  - width and height at least 64 pixels
  - optional title substring match when `window_query` is provided
- It returns descriptors with:
  - `hwnd`
  - `title`
  - `bbox`
  - `width`
  - `height`
  - `is_minimized`

Capture order:

1. Try `PrintWindow` / offscreen capture first.
2. If the offscreen image exists and is not visually blank, use it.
3. Otherwise fall back to `ImageGrab.grab` against the descriptor bounding box.

The blank-image guard treats very low-variance captures as blank.

### 3.2. `PhoenixGuardWindowTrackingAdapter`

This is the chart-reader side.

It does three major jobs:

1. Detect the timeframe selector from the top-left chart UI.
2. Detect the market label from the chart header.
3. Auto-crop the price area and hand the cropped image into the main PhoenixGuard chart-structure extraction logic.

The adapter builds template banks for:

- timeframe chips: `M1`, `M3`, `M5`, `M15`, `M30`, `H1`, `H4`, `D1`
- market glyphs: `A-Z` and `/`

The adapter then:

- extracts a binary text mask from candidate regions
- template-matches timeframe labels and market glyphs
- reconstructs the market label text from glyph boxes
- runs `auto_crop_price_area_with_meta(...)`
- calls the existing `main._extract_chart_structure(...)`

Returned values:

- `chart_image`: the precise analysis crop
- `chart_geometry`: geometry + detected market/timeframe + local crop metadata
- `sequence_state`: visible candle and box structure state

### 3.3. `SignalObserverService`

This is the continuous observer that already exists separately from the tracker.

The tracker creates an observer session for each tracker session and uses it as the underlying decision engine.

The observer:

- accepts exactly 4 images per bundle
- normalizes them through the pipeline adapter
- runs best-play analysis
- computes:
  - market phase
  - thesis
  - directional watch
  - armed state
  - actionable state
- renders signals with freshness decay over time

### 3.4. `ContinuousWindowTrackerService`

This is the orchestrator that joins:

- window discovery
- chart capture
- candle tracking
- observer bundling
- signal stabilization
- artifact writing
- RL context recording and resolution
- dashboard session state

## 4. Layout profiles

The tracker supports an upfront surface crop before fine chart analysis.

Defined profiles:

- `auto`
  - no fixed normalized crop
- `pocket_option_browser`
  - intended for Pocket Option in a browser layout
  - normalized bbox: `[0.132, 0.128, 0.888, 0.995]`
- `pocket_option_compact`
  - intended for layouts without the wide browser sidebar
  - normalized bbox: `[0.028, 0.128, 0.888, 0.995]`

Why this exists:

- The raw browser window contains non-chart chrome such as the left sidebar, top pair strip, and right order panel.
- The layout profile crop reduces that raw window to a more chart-centered surface before deeper parsing begins.

## 5. Window selection and self-capture protection

The tracker does not simply use the first matched title. It scores candidate windows.

Scoring inputs:

- hard blocked title tokens:
  - `phoenixguard`
  - `808fx standard system`
  - `window-tracker/dashboard`
  - `localhost`
  - `127.0.0.1`
- soft blocked tokens:
  - `.pdf`
  - `architecture`
  - `readme`
  - `github`
  - `docs`
  - `swagger`
  - `redoc`
- market alias match against the requested session market
- presence of an FX pair in the title
- `Pocket Option` / `pocketoption` / `OTC` hints
- explicit `window_query` match
- previous locked `hwnd`
- minimized state
- desk-sized dimensions

Selection behavior:

- Hard-blocked windows are treated as non-viable even if their title matches.
- This prevents the tracker from locking onto its own dashboard page or localhost API page.
- If nothing viable remains, the session moves into `waiting_for_window` or guarded state with a warning string in `lock_warning`.

## 6. Session creation and defaults

A tracker session is created by `ContinuousWindowTrackerService.create_session(...)`.

Important tracker defaults:

- `window_query`: `Pocket Option`
- `layout_profile`: `auto`
- `capture_interval_sec`: `10.0`
- `rl_track_interval_sec`: `30.0`

Important observer policy defaults injected by the tracker:

- `single_surface_mode = True`
- `min_actionable_confidence = 0.54`
- `min_directional_confidence = 0.44`
- `min_thesis_confidence = 0.42`
- `signal_cooldown_sec = 8.0`

Important session state fields initialized at creation:

- lock and window state:
  - `locked_hwnd`
  - `locked_title`
  - `lock_state`
  - `lock_score`
  - `lock_reason`
  - `lock_warning`
- capture state:
  - `capture_count`
  - `frame_index`
  - `last_capture_at`
  - `last_capture_epoch`
  - `last_frame_signature`
- artifact paths:
  - `last_frame_path`
  - `last_chart_path`
  - `last_display_chart_path`
  - `last_tracker_overlay_path`
  - `last_tracker_chart_overlay_path`
- crop metadata:
  - `last_chart_region`
  - `last_display_chart_region`
- market / timeframe state:
  - `market`
  - `detected_market`
  - `market_confidence`
  - `market_source`
  - `market_confirmed`
  - `detected_timeframe`
  - `timeframe_source`
  - `timeframe_confidence`
- tracking state:
  - `tracked_candles`
  - `next_track_id`
  - `tracking_summary`
- memory state:
  - `market_memory`
  - `pair_memory`
- latest live read:
  - `latest_signal`
  - `latest_bundle_id`
- RL state:
  - `rl_pending`
  - `rl_recent_resolutions`
  - `rl_feedback_count`
  - `rl_online_update_count`
  - `rl_last_resolved_at`

## 7. Capture cycle in detail

The synchronous one-shot path is `capture_once(session_id)`.
For continuously running sessions, the runtime now uses a three-stage worker pipeline:

- capture worker
- perception worker
- decision worker

### 7.1. Resolve the target window

The tracker calls `_resolve_window_descriptor(payload)`.

Possible outcomes:

- no matching window: session becomes `waiting_for_window`
- guarded / wrong window type: session warns and does not capture
- viable window selected: continue

### 7.2. Capture the raw window

The selected descriptor is captured into `raw_image`.

The frame signature is hashed from a grayscale thumbnail.

If the signature is unchanged from the previous frame:

- the tracker updates freshness and lock metadata
- it skips the expensive downstream run

This avoids reprocessing duplicate frames.

### 7.3. Write the raw frame artifact

Each session stores frames under:

- `window_tracker/sessions/<session_id>/frames/`

Artifacts are numbered with a six-digit prefix:

- `000001_window.png`
- `000001_chart.png`
- `000001_chart_display.png`
- `000001_chart_overlay.png`
- `000001_overlay.png`

### 7.4. Build the tracking surface

The tracker applies `_prepare_tracking_surface(...)`.

This step:

- resolves the effective layout profile
- runs adaptive chart-surface detection first
- falls back to the profile bbox when adaptive confidence is weak
- returns:
  - `prepared_image`: the profile-cropped chart surface
  - `surface_region`: metadata about that crop

If `auto` is used and no profile bbox exists, the full raw image passes through.

### 7.5. Extract chart state

The tracking adapter runs on `prepared_image`.

Internally it:

1. detects timeframe
2. detects market label
3. auto-crops the price panel with `auto_crop_price_area_with_meta`
4. calls the existing PhoenixGuard chart-structure extractor

The price-panel crop helper uses:

- Hough line detection when OpenCV is available
- a numpy row-variance fallback otherwise

The goal is to isolate the main price panel and ignore indicator subpanels.

### 7.6. Resolve two chart regions

The tracker keeps two different chart regions on purpose.

#### Analysis region

`_resolve_chart_region(...)` maps the precise analysis crop back into raw-window coordinates.

This is the model-facing crop:

- it is tight
- it exists for accurate candle parsing
- it is stored in `last_chart_path`

#### Display region

`_resolve_display_chart_region(...)` keeps the wider surface that the frontend should show.

This is the UI-facing chart surface:

- it preserves more context
- it avoids the “chopped candles” problem
- it is stored in `last_display_chart_path`

The dashboard `Trading Ground` surface prefers the display chart path, not the tight analysis crop.

### 7.7. Track candles across frames

The tracker assigns persistent candle IDs with `_assign_track_ids(...)`.

Matching factors:

- x-center distance
- bbox IoU
- color agreement bonus

Per tracked candle it stores:

- `track_id`
- `bbox`
- `color`
- `parse_conf`
- `body_height_pct`
- `upper_wick_pct`
- `lower_wick_pct`
- `close_pos_in_range`
- `x_center`
- `rank`
- `seen_count`
- `age_frames`

This is the tracker’s object continuity layer.

### 7.8. Build four observer uploads

The tracker converts one live chart into four synthetic uploads for the observer.

Order is fixed:

1. `higher_1.png`
   - full locked window
2. `higher_2.png`
   - precise chart analysis crop
3. `lower_1.png`
   - focus crop around the current box / latest candles
4. `lower_2.png`
   - tighter focus crop around the same live focus area

Focus selection:

- prefer `sequence_state.current_box.bbox`
- else use the latest visible candle bbox

Focus crop size changes with visible candle density:

- more visible candles -> slightly narrower detail crop
- fewer visible candles -> slightly wider detail crop

This lets the observer consume a quartet without requiring manual multi-timeframe screenshots.

### 7.9. Submit and wait for the observer bundle

The tracker submits the 4 synthetic uploads into the paired observer session.

Observer settings are adjusted live:

- `label_density`
- `history_depth`
- `debug_depth`
- `projection_focus`
- `higher_timeframe`
- `lower_timeframe`

If the tracker detects a timeframe chip on the chart, both observer timeframes are set to that same detected timeframe.

The tracker then blocks on `wait_for_bundle(...)` until the observer completes or times out.

## 8. Observer signal construction

The observer signal is built from:

- pipeline result
- best-play analysis
- recent signal history
- policy

The observer computes:

### 8.1. Market phase

From chart and sequence probabilities:

- `continuation`
- `pullback`
- `reversal`
- `fakeout`
- `consolidation`
- `transition`

### 8.2. Thesis state

The thesis is not just the current model action.
It is a weighted support calculation over recent signal history plus current confidence plus memory similarity plus phase bias.

Outputs include:

- `thesis_action`
- `thesis_confidence`
- `thesis_state`
- `thesis_age`
- `thesis_buy_support`
- `thesis_sell_support`
- `thesis_conviction`

### 8.3. Directional watch state

This decides whether there is enough directional support to show a watch-state directional bias even when the signal is not fully executable.

Outputs include:

- `directional_watch_ready`
- `directional_threshold`

### 8.4. Signal arm state

In `single_surface_mode`, the observer can promote a setup into an “armed” state before full execute permission is present.

Outputs include:

- `signal_armed`
- `signal_armed_action`
- `signal_armed_score`
- `signal_armed_threshold`
- `signal_armed_state`
- `signal_armed_reverse_guard`

### 8.5. Actionable state

The observer marks a signal actionable when:

- candidate direction is `BUY` or `SELL`
- timing is ready
- adaptive confidence clears threshold
- either:
  - gate is confirmed and execution permission is `EXECUTE`
  - or single-surface mode allows armed promotion

Outputs include:

- `action`
- `base_action`
- `candidate_action`
- `actionable`
- `transition`
- `alert`
- `summary`
- `reasons`

### 8.6. Freshness decay

Observer signals decay toward `HOLD` as they age.

`_render_signal(...)` multiplies confidence by freshness and demotes stale signals when:

- age exceeds `stale_after_sec`
- freshness drops below `min_freshness_score`
- or effective confidence no longer clears the required threshold

## 9. Tracker-local stabilization after the observer result

The observer output is not used raw.
The tracker applies `_stabilize_tracker_signal(...)` afterward.

This is where the tracker asserts authority over live chart structure.

### 9.1. Pair-memory reliability

The tracker only trusts pair memory if:

- the current market is confirmed from the chart surface
- the memory market matches the current market

If pair memory is not reliable, the tracker is allowed to override more aggressively with local chart evidence.

### 9.2. Pressure action

`_derive_tracker_pressure_action(...)` derives a local directional pressure from:

- candidate direction
- candidate confidence
- phase bias
- phase confidence
- recent price momentum
- latest candle color

This produces:

- `BUY`
- `SELL`
- or `HOLD`

### 9.3. Trend lock

The current tracker version adds `_compute_tracker_trend_state(...)`.

This trend lock inspects the mapped visible candles directly and scores:

- total travel from oldest to latest visible close proxy
- recent travel over the last small window
- ratio of upward steps vs downward steps
- green vs red candle ratio
- latest candle body impulse
- current box direction
- phase bias / continuation / reversal context
- previous tracker trend action and strength

Outputs include:

- `tracker_trend_action`
- `tracker_trend_strength`
- `tracker_trend_opposing_strength`
- `tracker_trend_score_gap`
- `tracker_regime_state`
- `tracker_trend_locked`
- `tracker_reversal_ready`
- `tracker_countertrend_blocked`
- `tracker_trend_travel`
- `tracker_trend_recent_travel`

Behavior:

- If the visible chart is strongly bullish, a raw upstream `SELL` can be blocked.
- If the visible chart is strongly bearish, a raw upstream `BUY` can be blocked.
- A true reversal must show enough reversal probability and enough counter-move evidence before the countertrend side is allowed through.

When blocked:

- live action is demoted to `HOLD/watch`
- execution permission is rewritten to `WAIT_FOR_TREND_REALIGNMENT`
- the signal reason list is annotated with the block explanation

### 9.4. Why the tracker can disagree with the observer

The observer is built from synthetic four-view inference and recent session history.
The tracker sees additional local candle continuity that the observer does not:

- object-level candle IDs
- recent visible price travel
- exact latest box location
- local trend lock on the live mapped candle stream

That is why the tracker can deliberately neutralize or rewrite a raw observer direction.

## 10. Tracking summary fields

The tracker builds a condensed local state under `tracking_summary`.

Important fields:

- chart stats:
  - `visible_candle_count`
  - `recent_candle_count`
  - `geometry_confidence`
  - `spacing_consistency`
  - `box_sequence_agreement`
  - `path_clarity`
- market / timeframe:
  - `detected_market`
  - `market_source`
  - `market_confidence`
  - `detected_timeframe`
  - `timeframe_source`
  - `timeframe_confidence`
- latest local price features:
  - `latest_candle_color`
  - `latest_price_proxy`
  - `recent_price_proxy`
  - `recent_price_momentum`
  - `latest_body_height_pct`
  - `latest_close_pos_in_range`
  - `latest_candle_anchor`
- structural box data:
  - `current_box`
  - `primary_next_box`
  - `box_history_tail`
- tracked objects:
  - `active_track_count`
  - `latest_track_id`
  - `tracked_candles`

## 11. Artifacts and overlays

The tracker writes both model artifacts and display artifacts.

### 11.1. Raw window artifact

- `*_window.png`
- full captured application window

### 11.2. Analysis chart artifact

- `*_chart.png`
- tight internal chart crop used for analysis

### 11.3. Display chart artifact

- `*_chart_display.png`
- wider frontend chart surface shown in `Trading Ground`

### 11.4. Chart overlay artifact

- `*_chart_overlay.png`
- overlay drawn on the display chart surface
- includes:
  - tracked candle boxes
  - current box highlight
  - direction arrow
  - chart focus rectangle

### 11.5. Window overlay artifact

- `*_overlay.png`
- overlay drawn on the full raw window
- boxes and arrows are offset into raw-window coordinates

### 11.6. Artifact resolution behavior

`latest_artifact_path(...)` resolves:

- `chart`
  - `last_display_chart_path`
  - fallback to `last_chart_path`
- `window`
  - `last_frame_path`
- `chart-overlay`
  - `last_tracker_chart_overlay_path`
- `overlay`
  - local tracker overlay first
  - else observer overlay / fusion / sheet fallback

## 12. Dashboard behavior

The dashboard page is a local HTML surface that polls the tracker session.

Main image modes:

- `Locked Window`
- `Trading Ground`
- `Overlay Rail`

Frontend fetches:

- session JSON from `GET /v1/mobile/window-tracker/sessions/{sessionId}`
- image artifacts from `GET /v1/mobile/window-tracker/sessions/{sessionId}/artifacts/latest-*`

Polling:

- the dashboard initializes in `window` mode
- it polls periodically via `refreshDashboard()`
- if runtime appears stale, it tries to restart the session worker

The dashboard merges:

- `payload.latest_signal`
- `payload.tracking_summary`
- `payload.latest_intelligence`
- `payload.pair_memory`
- `payload.rl_runtime`

### 12.1. Exact execution wording

The frontend intentionally uses exact phrases for execution-grade states:

- `BUY NOW`
- `SELL NOW`

Armed-but-not-yet-execute states use:

- `BUY ARMED`
- `SELL ARMED`

Neutral non-execution wording uses:

- `STAND ASIDE`
- `SETUP BUILDING`
- related non-execute states

This prevents softer wording from being confused with immediate execution commands.

### 12.2. Dashboard study model

The frontend also computes a client-side display model from the incoming signal and intelligence payload:

- dominant study
- global vs local study alignment
- dual-track vs promoted execution states
- entry trigger wording
- plan summary
- flip risk
- memory display
- invalidation wording
- spatial map wording

This does not replace the backend signal.
It is a presentation layer built from backend state.

### 12.3. Footer diagnostics

The dashboard footer explicitly shows:

- active layout profile
- capture age
- display chart size
- analysis focus size and crop method
- visible / tracked candles
- latest candle color
- RL blend
- resolved sample count

## 13. RL / learning loop

The tracker has its own reinforcement-learning feedback path separate from the observer.

### 13.1. What is recorded

For selected frames, the tracker records:

- image hash
- state vector
- prior probabilities
- module reliability
- policy result
- predicted action
- memory similarity
- memory recall direction

This is handled by `_record_tracker_rl_context(...)`.

### 13.2. State vector contents

The RL state vector includes a large mixture of:

- signal one-hot encodings:
  - action
  - candidate action
  - thesis action
  - phase bias
  - latest candle color
- confidence features:
  - candidate confidence
  - effective confidence
  - adaptive threshold
  - best-play confidence
  - raw confidence
- gating and timing:
  - gate strength
  - timing score
  - execute permission
  - gate state
  - timing ready
- phase features:
  - phase confidence
  - continuation / pullback / reversal / fakeout probabilities
  - consolidation flag
- regime features:
  - regime stability
  - regime flip rate
- local chart metrics:
  - geometry confidence
  - path clarity
  - spacing consistency
  - box sequence agreement
  - visible candle count
  - recent candle count
  - latest / recent price proxy
  - recent price momentum
  - latest candle body metrics
  - latest candle anchor
- memory features:
  - pair-memory confidence
  - pair-memory age
  - previous action alignment
  - previous thesis alignment
- timeframe normalization

### 13.3. How outcomes resolve

Pending RL entries are revisited after enough future frames arrive.

Resolution is timeframe-aware:

- `M1` resolves faster
- `H4` and `D1` require more frames

The tracker computes proxy move outcomes and records:

- actual outcome
- reason
- price-proxy delta
- travel proxy percentage
- candles elapsed
- market minutes elapsed
- market-time label
- entry and exit anchors

These results appear in the dashboard learning/runtime areas.

## 14. Public tracker payload

`_public_session_payload(...)` merges tracker-local state with observer session state.

Important output sections:

- top-level tracker session fields
- `latest_signal`
  - observer latest signal overlaid by tracker-local overrides
- `recent_bundles`
  - observer bundle history
- `latest_intelligence`
  - structured observer result summary
- `tracking_summary`
  - tracker-local chart and candle continuity state
- `pair_memory`
  - current market memory snapshot when confirmed
- `memory_markets`
  - currently remembered markets
- `rl_runtime`
  - pending count
  - feedback count
  - online update count
  - resolved count
  - resolved accuracy
  - average candles to resolution
  - average market minutes to resolution
  - latest resolution
  - pending rows
  - recent resolutions
- runtime liveness fields:
  - `worker_alive`
  - `capture_age_sec`
  - `next_capture_in_sec`
  - `runtime_stale`

## 15. API routes

The relevant routes exposed by the FastAPI app are:

- `GET /v1/mobile/window-tracker/windows`
- `GET /v1/mobile/window-tracker/sessions`
- `POST /v1/mobile/window-tracker/sessions`
- `GET /v1/mobile/window-tracker/sessions/{sessionId}`
- `GET /v1/mobile/window-tracker/sessions/{sessionId}/artifacts/latest-chart`
- `GET /v1/mobile/window-tracker/sessions/{sessionId}/artifacts/latest-window`
- `GET /v1/mobile/window-tracker/sessions/{sessionId}/artifacts/latest-{artifactKind}`
- `GET /v1/mobile/window-tracker/dashboard/{sessionId}`
- `POST /v1/mobile/window-tracker/sessions/{sessionId}/start`
- `POST /v1/mobile/window-tracker/sessions/{sessionId}/stop`
- `POST /v1/mobile/window-tracker/sessions/{sessionId}/capture-once`

## 16. One-command launcher

`Backend/launch/start_live_dashboard.ps1` is the easiest operator path.

What it does:

1. Resolves data and log directories.
2. Checks whether the API is already healthy.
3. Starts `Backend/launch/start_phoenixguard_mobile_api.py` if needed.
4. Waits for `/v1/mobile/health`.
5. Warms the tracker runtime by calling the tracker sessions route.
6. Loads or creates the default tracker session.
7. If the session is not running, starts it.
8. Prints the dashboard URL.
9. Opens the browser unless `-NoBrowser` is used.

Default launch values:

- host: `127.0.0.1`
- port: `8791`
- session ID: `pocket-live-8788`
- data dir: `.codex_runtime\data_live`
- logs dir: `.codex_runtime\logs_live`

Session creation performed by the launcher uses:

- `window_query = "Pocket Option"`
- `layout_profile = "auto"`
- `capture_interval_sec = 10.0`
- observer policy:
  - `single_surface_mode = true`
  - `min_actionable_confidence = 0.58`
  - `min_thesis_confidence = 0.46`
  - `signal_cooldown_sec = 8.0`

## 17. Practical interpretation of tracker state

When the tracker is healthy:

- `lock_state` should usually be `stable`
- `worker_alive` should be `true`
- `capture_age_sec` should stay near the configured capture interval
- `latest_signal` should refresh with new bundle IDs
- `tracking_summary.visible_candle_count` and `active_track_count` should remain non-zero

When the tracker is not healthy:

- `waiting_for_window`
  - the target window is missing or not viable
- `guarded`
  - the best match looked like a wrong surface or self-capture target
- stale runtime
  - worker is missing or capture age is too old
- duplicate frames
  - capture loop saw the same signature and skipped a heavy rerun

## 18. Important design constraints

The tracker is strongest when:

- Pocket Option is isolated in its own window
- the tracked market title is visible
- the timeframe chip is visible
- the window remains stable in size and layout
- dashboard and chart are not competing inside the same visible window

The tracker intentionally prefers:

- one locked live surface
- continuous state over one-off snapshots
- trend persistence over flip-flop reactions
- fail-closed behavior on countertrend signals when the visible candle map disagrees

## 19. Current mental model

The cleanest way to think about the tracker is:

- The observer is the general inference engine.
- The tracker is the live chart continuity engine.
- The dashboard is the operator surface.

The observer answers:

- “What does this synthetic quartet imply right now?”

The tracker answers:

- “Does that inference still make sense when anchored to the actual visible candle map across frames?”

The dashboard answers:

- “What is the exact current state, what surface is being watched, and is there a real execution-grade signal or not?”
