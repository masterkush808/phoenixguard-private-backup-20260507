# PhoenixGuard V3 CPU Streaming Architecture

Status: implementation contract for the local Windows live profile
Scope: PhoenixGuard V3 only
Primary constraint: CPU-only workstation, 6 physical cores, 12 logical cores,
approximately 8 GiB RAM

## 1. Outcome

PhoenixGuard continuously observes the locked broker chart while keeping the
existing V3 study, decision, packet-validation, and handoff authority intact.
Continuous observation does not mean continuous full-model inference. A small
CPU observer samples the chart, rejects duplicated pixels, summarizes intrabar
motion, and admits only a bounded set of material keyframes into the expensive
V3 lane.

The operator surface continues to answer exactly three questions:

1. Where did the market come from and how did that history behave?
2. Which direction was being studied and what is being studied now?
3. What is the best decision to do right now?

The third answer names the best current action: enter a validated BUY/SELL,
watch a pullback or rally into a current entry area, hold/protect a confirmed
trade, avoid chasing an expired move, or wait when evidence is genuinely
insufficient. Stream activity is evidence, never a substitute for the
validated V3 execution packet. The local launcher keeps direct broker clicks
disabled; an accepted action is a validated handoff package for the configured
downstream boundary.

## 2. Non-negotiable invariants

- V3 remains the only architecture and schema lineage.
- The locked HWND, stable pair/timeframe selector fingerprint, chart geometry,
  and crop coordinate space belong to every observation.
- Frame order is monotonic inside one stream generation.
- A pair, timeframe, HWND, capture-size, or chart-geometry change starts a new
  generation and invalidates pending work from the previous generation.
- The forming candle may supply watch-state evidence, but no forming-candle
  observation may impersonate a completed candle.
- Repeated observations are correlated measurements, not independent ensemble
  votes.
- No queue is allowed to grow with time. There is one pending slot and one
  explicitly tracked in-flight keyframe; motion is still measured while heavy
  study is busy, but new material/heartbeat work is coalesced.
- Raw video is not persisted. Only the latest bounded frames, small grayscale
  fingerprints, significant keyframes, and derived temporal facts exist.
- Stream health never grants entry permission.
- Old results cannot overwrite a newer generation.

## 3. Data plane

```text
locked Pocket Option HWND
        |
        v
local Windows capture producer (0.25 FPS CPU-safe baseline)
        |
        v
CPUStreamObserver
  - stable content digest
  - identity and geometry epoch
  - 128 x 72 grayscale fingerprint
  - duplicate/rest/motion classification
  - bounded temporal evidence
        |
        +-- duplicate/rest noise --> counters only, then discard
        |
        +-- material change / heartbeat / reset
                    |
                    v
             latest-keyframe slot
             (capacity exactly one)
                    |
                    v
        existing V3 capture-and-study worker
          - surface/source validation
          - chart/candle reconstruction
          - object and geometry study
          - trend/regression/countertrend study
          - model council and strategy book
          - immutable execution packet validation
                    |
                    v
       operator workspace and existing SSE state stream
                    |
                    v
       Q1 history | Q2 direction | Q3 entry decision
```

## 4. Two cadence domains

### 4.1 Observation cadence

The local native producer defaults to 0.25 FPS on constrained CPU-only hosts and
can be raised to 8 FPS only when measured capacity permits. Its job is only to
notice what changed and maintain bounded temporal facts. The supported range is
0.25 to 8 FPS; 0.25 FPS is the empirically certified baseline for the current workstation.

At each observation the core records:

- stable BLAKE/SHA content identity;
- capture and monotonic timestamps;
- stream ID, generation, and sequence number;
- source identity and image geometry;
- mean normalized pixel change;
- changed-pixel ratio;
- motion/rest classification;
- consecutive duplicate/rest count;
- time since last accepted keyframe;
- total observed, accepted, duplicate, reset, and dropped frames;
- effective observation and acceptance rate.

### 4.2 Study cadence

The expensive V3 pipeline runs only when one of these gates opens:

- a material visual change exceeds both noise and minimum-keyframe timing
  guards;
- the source identity or geometry changes and a new generation must be
  established;
- the five-second observer heartbeat requires a truth refresh and neither a
  pending nor an in-flight heavy study exists;
- the snapshot watchdog detects that the producer stopped advancing.

This separation prevents the full ensemble, OCR, object study, and forecasting
lanes from running four times per second.

## 5. Bounded resource contract

| Resource | V3 CPU default | Hard behavior |
|---|---:|---|
| Native capture target | 0.25 FPS default | Configurable, clamped to 0.25-8 FPS |
| Full-frame ring | 2 frames | Oldest is overwritten |
| Fingerprint ring | 48 frames at 128 x 72 | Oldest is overwritten |
| Pending heavy keyframes | 1 | Newest replaces stale pending work before claim |
| In-flight heavy keyframes | 1 | Explicit lifecycle marker; always cleared in `finally` |
| Minimum keyframe interval | 250 ms | Faster material events coalesce |
| Observer heartbeat | 5 s | Admits a refresh without busy inference |
| Snapshot watchdog | 15 s | Falls back to the proven timer path |
| Native math threads | 2 per configured pool | Leaves CPU headroom for capture and UI |
| Raw video persistence | 0 | Prohibited |

The full-frame ring and the full-window handoff are both guarded by the maximum
accepted input pixel count, so a small focus crop cannot hide an unbounded 8K
window allocation. The grayscale ring is tiny. Session JSON and artifacts are
not written at the raw observation cadence.

## 6. Stream identity and geometry

Every frame is interpreted inside this tuple:

```text
(stream_id, generation, frame_seq, hwnd, selector_fingerprint, pair, timeframe,
 capture_width, capture_height, chart_roi, geometry_epoch)
```

The tuple prevents the visible failure where geometry from one pair is drawn
over another pair after a tab switch. When identity or geometry changes:

1. increment generation;
2. clear temporal comparison buffers;
3. revoke the old pending keyframe;
4. classify the first new frame as a reset/keyframe, not directional motion;
5. require downstream results to carry the same generation before publication.

Stable cryptographic content digests replace Python's process-local `hash()` so
lineage remains comparable across threads and restarts.

The selector fingerprint uses the broker's stable bright pair/timeframe header
mask inside the exact locked chart-focus pixels. Full-window geometry remains
authoritative, but browser chrome and the animated payout/tab strip are never
part of the selector fingerprint. A changed focus fingerprint resets the stream
generation and forces pair/timeframe re-confirmation before an actionable
publication, even when cached session text still names the old pair.

## 7. Temporal evidence contract

The lightweight observer may publish facts such as:

- `duplicate`: the capture-surface pixels are effectively unchanged; this is
  observer-health evidence, not proof that the market itself is resting;
- `rest`: small coherent movement below material-change threshold;
- `motion`: visible chart movement that is not yet a model conclusion;
- `material_change`: an event worthy of the heavy study lane;
- `heartbeat`: bounded truth refresh in a visually quiet market;
- `identity_reset`: a new pair/source/timeframe context;
- `geometry_reset`: capture dimensions or coordinate space changed.

These are observation facts. They are deliberately not named bullish, bearish,
buy, or sell. Direction comes from the existing candle reconstruction and V3
ensemble so pixel movement, scrolling, timer animation, and broker chrome do not
become fake trading votes.

## 8. Backpressure and concurrency

The producer and study worker are independent:

- The producer owns its native capture backend and never shares an MSS instance
  across threads.
- The observer is protected by a session-local lock.
- This 8 GiB profile permits exactly one native CPU producer across the service;
  other sessions remain on bounded snapshot fallback.
- There is exactly one pending keyframe slot and one in-flight marker.
- Publishing a newer keyframe atomically replaces an unclaimed older slot.
- While heavy study owns a keyframe, material and heartbeat frames continue to
  update bounded temporal metrics but do not create an inference backlog.
- Pair, timeframe, HWND, focus, or geometry resets still break through the busy
  gate and invalidate the prior generation.
- The study worker atomically claims the slot before processing it.
- A generation token is checked again before committing results.
- Stop, emergency-stop, or service close signals terminate the producer and
  clear its pending slot.

This is bounded coalescing: latency and memory stay fixed even when a heavy
study takes longer than the observation interval.

### Cross-process stream truth

The tracker and HTTP API are separate processes, so live counters cannot rely
on process-local memory or wait for a heavy keyframe commit. The tracker writes
one atomic, replace-in-place `cpu_stream_v3.json` status sidecar at startup,
approximately once per second, on degradation/recovery, and on stop.

- The sidecar contains counters, bounded-ring health, lineage summaries, state,
  and the current failure reason; it never contains pixels or video.
- Schema and session identity must match before the API accepts it.
- The API overlays the record onto compact session and operator reads without
  loading the large historical session document.
- Telemetry older than five seconds becomes `stale_snapshot_fallback` with
  `available=false`; it can never masquerade as a live stream.
- The public stream DTO retains only proof-safe CPU-only, bounded-ring, bounded
  memory, monotonic counter, lineage sequence, and explicit no-click fields. It
  strips frame hashes, pixels, local paths, window identity, geometry internals,
  raw direction, and every private authority field.
- The forming-chart read uses a bounded cadence-aware freshness budget: at
  least 8 seconds, four target periods, or three observed periods (whichever is
  slower), capped at 45 seconds. `available=true` and `RUNNING` remain required;
  a stopped or unavailable producer still fails closed. This budget never
  refreshes completed-candle truth or entry permission.
- `broker_click_authority=false`, one-slot capacity, CPU target, and the
  event-gated model policy are reasserted at the read boundary even if a file is
  stale or malformed.
- Capacity rejection and construction failure publish the same bounded record,
  so the GUI receives the actual reason even if the producer never starts.

This is runtime observability, not market evidence and not decision authority.

## 9. State machine

```text
DISABLED
  -> STARTING       local Windows capture enabled and session locked
  -> OBSERVING      stream frames advancing
  -> KEYFRAME_READY material event or heartbeat admitted
  -> STUDY_BUSY     existing V3 worker owns latest keyframe
  -> OBSERVING      publication complete

OBSERVING / STUDY_BUSY
  -> RESETTING      pair, HWND, timeframe, or geometry changed
  -> OBSERVING      new generation established

any active state
  -> DEGRADED       native producer stale or capture failed
  -> SNAPSHOT_ONLY  proven timer capture keeps V3 alive
  -> OBSERVING      producer recovers with a new generation

any active state
  -> STOPPED        session stop, emergency stop, or service shutdown
```

## 10. Operator contract

Streaming adds one compact freshness line beneath the three answers, for
example:

```text
Continuous observation live - 0.25 FPS target - latest frame 1.7 s ago
```

Its expanded diagnostics may show generation, sequence, observed/accepted/drop
counts, motion score, and fallback state. Those diagnostics are collapsed by
default. There is no raw video player, frame-card grid, or second decision
surface.

The three answers own meaning:

- Q1 summarizes major trend origin, completed swings/rests, and historical
  regression behavior.
- Q2 distinguishes the previously studied direction from the current major and
  inner movement, including a valid countertrend/sniper setup.
- Q3 makes one explicit entry decision and states the immediate evidence or
  invalidation needed to change it.

The three textual answers and stream strip commit as soon as the compact
operator response arrives. The larger broker bitmap may finish transfer and
decode afterward. Overlay geometry and its individual entry-area controls stay
on the last committed bitmap until the new exact-frame image is ready, so UI
latency improves without mixing frames.

### Path-clock timing on the stream

Every newly proven closed candle also advances the bounded Joint Path-Clock
Liquidity Field V3. The lightweight frame observer does not create timing
samples: only the closed-candle study worker may admit or advance an anchor.

The stream's `captured_epoch` is observation lineage, not a candle-close time.
For screenshot-only charts, the resolver must prove one forming-to-closed event
while consecutive captures bracket one exact timeframe boundary. Only that
boundary is certified, and the certificate must bind the same pair, timeframe,
event key, sequence, and visible candle row. Initial screenshots, duplicate
heartbeats, missed boundaries, and multi-candle reacquisitions remain censored.
Broker/source close times take precedence when present.

- a new anchor requires an explicit contract duration from 900 through 7,200
  seconds;
- the duration is aligned to an actually observable candle close;
- an active anchor continues through its final 899 seconds so late liquidity
  sweeps remain learnable, but that late state cannot open a new entry;
- raw trajectories and replay freezes use a dedicated bounded side store;
- the streaming DTO carries only a mature, current-lineage timing summary;
- missing, immature, stale, or cross-generation timing is omitted rather than
  displayed as zero confidence;
- a mature timing read can delay or veto Q3 but can never grant permission.

The same timing gate runs during the initial three-question synthesis and every
subsequent stream refresh. A heartbeat therefore cannot erase a timing veto,
and timing from an old pair or geometry generation cannot survive a chart
switch.

## 11. Action boundary

`BUY_NOW` or `SELL_NOW` can be published only after existing V3 packet
validation. The stream cannot bypass:

- source and chart lock;
- completed-candle lineage where required by the strategy contract;
- direction agreement and countertrend exception rules;
- risk, expiry, freshness, and packet schema validation;
- replay and duplicate-action protection;
- downstream acknowledgement rules.

`DO_NOT_ENTER` must carry a useful reason rather than a generic waiting message.
Examples include wrong source, geometry reset, insufficient completed history,
conflicting direction, expired packet, missing entry zone, or risk rejection.

## 12. Failure behavior

| Failure | Required behavior |
|---|---|
| Window hidden or native capture unavailable | Use non-activating `PrintWindow` first. For an explicitly locked chart only, a bounded byte-identical streak may invoke the existing identity-verified visible-capture recovery, which restores that exact HWND without synthesizing clicks or keys. Recovery attempts are rate-limited; only a recovered frame already classified as non-duplicate/non-rejected may force one study keyframe, without resetting generation. Failures retain the snapshot fallback and remain observable. |
| Pair/tab changes | Start a new generation and revoke old geometry/results |
| Producer faster than inference | Replace only unclaimed pending work; coalesce while in flight; never queue |
| Duplicate/static capture | Update health counters; avoid a heavy model run, never relabel duplicate pixels as market rest, and never overwrite a supported decision with fallback WAIT. A locked-chart stream may attempt the bounded visible recovery above. |
| Browser/SSE reconnect | Rehydrate latest contract; do not restart producer |
| Heavy worker exception | Record error, release busy flag, keep producer alive |
| Stream thread exits | Snapshot watchdog retains observation and reports degraded state |
| Tracker/API process split | Read the schema-bound one-file status sidecar; reject it after five seconds and fall back closed |
| Shutdown races stream construction | Service-wide stop token blocks registration/start and closes the unregistered backend |
| Late result from old generation | Reject publication |
| Memory pressure | Rings remain fixed; no video or unbounded artifact writes |
| Emergency stop | Stop producer, clear pending keyframe, revoke entry authority |

## 13. Configuration

Canonical local-live defaults:

```text
PHOENIXGUARD_CPU_STREAM_ENABLED=1
PHOENIXGUARD_CPU_STREAM_FPS=0.25
PHOENIXGUARD_CPU_STREAM_SNAPSHOT_FALLBACK_SEC=15.0
PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_THRESHOLD=3
PHOENIXGUARD_DUPLICATE_FRAME_RECOVERY_MIN_INTERVAL_SEC=15
OMP_NUM_THREADS=2
MKL_NUM_THREADS=2
OPENBLAS_NUM_THREADS=2
NUMEXPR_NUM_THREADS=2
```

The normal capture interval remains the low-frequency fallback rather than the
observation cadence. Explicit operator environment overrides are respected.

## 14. Verification gates

The change is complete only when all of these pass:

1. Observer unit tests prove stable digests, monotonic sequence, duplicate
   rejection, rest/material classification, heartbeat acceptance, identity and
   geometry reset, bounded rings, and reset behavior.
2. Tracker tests prove one producer for the CPU profile, atomic start reservation,
   event-driven wake-up, pending/in-flight cleanup, busy-study coalescing,
   non-focus-stealing capture, bounded locked-chart duplicate recovery, timer
   fallback, and public stream metadata.
3. Existing window-tracker, packet, source-lock, geometry, and execution tests
   remain green.
4. Operator tests prove exactly three questions and a sanitized stream-health
   contract with no new execution authority.
5. Browser tests prove desktop/mobile layout, collapsed diagnostics, live SSE
   refresh, and reduced-motion behavior.
6. A synthetic benchmark reports observer latency, effective FPS, accepted
   keyframe ratio, and bounded ring sizes on the repository live interpreter.
7. The canonical stack is relaunched and `/health`, `/v3`, session state, SSE,
   process topology, and multi-frame advancement are checked against the live
   broker window.

## 15. Safe relaunch ownership

The canonical Windows launcher must prove that the previous PhoenixGuard stack
is gone before it deletes disposable runtime state or binds the new API. Normal
cleanup uses process command lines plus the resolved repository root. On a
managed Windows profile where CIM/WMI process inspection is denied, the
launcher uses the live interpreter's `psutil` fallback and requires both:

- the exact resolved PhoenixGuard repository in the executable or command;
- a fixed PhoenixGuard runtime token such as the tracker, API, reporter, disk
  guard, or MT4 bridge entrypoint.

The fallback excludes itself and every invoking ancestor, validates a fresh V3
runtime lock when port 8793 is active, kills only the attributed process trees,
rescans for surviving attributed processes, and independently proves port 8793
closed. Any uncertainty aborts before runtime deletion. This keeps relaunch
recoverable without broad Python termination and prevents duplicate tracker,
bridge, reporter, or disk-guard loops.

## 16. Deliberately deferred work

The following can improve capture efficiency later but are not dependencies for
the CPU-first V3 delivery:

- Windows Graphics Capture or Desktop Duplication dirty-rectangle ingestion;
- shared-memory frame transport for a remote capture process;
- optical flow or learned scene-change inference;
- raw-video retention;
- GPU model acceleration.

Any later capture backend must implement the same observer, identity,
generation, backpressure, and packet-authority contracts. It may change how
pixels arrive; it may not change what grants a trade action.
