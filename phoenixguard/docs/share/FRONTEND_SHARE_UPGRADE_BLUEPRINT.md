# Frontend Share Upgrade Blueprint

<!-- markdownlint-disable MD013 -->

## Objective

Upgrade the shared PhoenixGuard surface to the newest premium UX while
preserving your current techniques and backend
protections. The final surface must:

- Use the exact product name: the 808Fx Standard System Hybrid.
- Keep backend implementation details hidden from public UI copy.
- Improve readability and eliminate text overflow/out-of-box rendering.
- Keep Feedback and RL learning controls discoverable only when users

intentionally open Feedback.

- Add a dedicated Binary Options Timing button/panel with full timing playbook

and run-specific overlay snapshots.

- Upgrade Visual Desk and Heatmap to be actionable, not visually noisy.
- Add completion notifications that follow modern browser UX standards.

## Current Surface Map (As-Is)

### Share app entry and routing

- share entrypoint and app composition: share_phoenixguard.py
- share blocks and tabs: share_phoenixguard.py
- share head script injection: share_phoenixguard.py
- share css composition: share_phoenixguard.py

### Core UI render builders reused by share mode

- signal overview builder: main.py
- decision gauge builder: main.py
- heatmap summary builder: main.py
- compare desk builder: main.py
- timeframe overlay gallery builder: main.py
- UI JS and behavior hooks: main.py
- UI CSS rules and breakpoints: main.py

### Feedback/RL path currently present

- share feedback gating flags and defaults: share_phoenixguard.py
- share feedback submit path and mutation guard logic: share_phoenixguard.py
- full feedback learning flow tests: Backend/tests/test_feedback_learning_flow.py

### Share surface tests already available

- payload and hero assertions: Backend/tests/test_share_surface.py

## Gap Analysis vs Requested Upgrades

1. Name and brand text mismatch

- Current alias is 808FxStandardSystemHybrid without spaces.
- Required text is the 808Fx Standard System Hybrid.

1. Signal Overview readability

- Layout is better than plain text, but explanation remains a long dense

paragraph and key timing details are missing.

1. Backend disclosure in UI

- Some user-facing strings still reference internal mechanics (for example YOLO,

model council internals, per-model

details).

- Requirement: hide backend specifics in shared public wording.

1. Feedback and RL visibility behavior

- Feedback tab exists, but no explicit lazy reveal strategy that keeps learning

controls hidden until tab intent is

clear.

1. Binary options timing UX

- Timing computation exists in backend result payload, but no dedicated shared

panel/button for full timing playbook

with overlays.

1. Notification layer missing

- No browser Notification API integration for run completion.
- No standardized in-app toast fallback for denied permissions.

1. Decision gauge fit and layout crossing

- Current Plotly gauge can visually clash depending on viewport and adjacent

card height.

1. Visual desk and heatmap clarity

- Existing controls exist, but semantic storytelling is still complex and can

appear noisy for non-operator users.

1. Overflow and clipping quality

- Several cards use strict containers without strict overflow-wrap strategy for

dynamic strings.

## Target UX Architecture (To-Be)

### A. Surface identity and trust language

- Use the 808Fx Standard System Hybrid in hero, title, auth button copy, and

status copy.

- Replace internal-tech terms with intent-based language:
  - Replace internal model references with Hybrid Node, Structure Lens,

  Execution Lens, Specialist Council.

  - Keep technical traces in logs only, not public panel copy.

### B. Signal Overview 2.0

- Keep compact top-line action card.
- Split into three clear blocks:

  1. Signal State (Action, Confidence, Execution, Gate pass count)
  1. Trade Framing (Expected move, Position size, Memory alignment, MTF state)
  1. Timing Summary (Entry state, ETA range, Expiry fit)

- Explanation text becomes bullet lines (max 3 concise statements).

### C. Binary Timing Playbook button and panel

- Add dedicated button near run/overview section:
  - Label: Binary Timing Playbook
- On click/toggle, show a structured panel with:
  - Entry state: READY, WATCH, PREMATURE, LATE
  - ETA candles and minutes low-mid-high
  - Expiry and entry buffer
  - Timing score and fit score
  - Top timing rationale bullets
  - Overlay snapshots from current run (higher/lower or split compare assets)

### D. Feedback/RL intentional reveal

- Keep feedback features hidden by default from casual browsing.
- Reveal only when Feedback tab is selected.
- On first open in session, show learning disclosure card and then enable

controls.

- Keep mutation guard language operator-only; use non-technical user copy.

### E. Run completion notifications

- Layer 1: Browser notifications after successful run and model council

completion.

- Layer 2: In-app toast fallback when permissions denied.
- Include action summary and confidence in notification body.
- Avoid sensitive backend details in notification text.

### F. Decision confidence visual update

- Replace or restyle current gauge to avoid collision:
  - Option 1: compact radial ring with fixed 1:1 container.
  - Option 2: segmented confidence bar with direction tone.
- Ensure fixed min/max height and no overlap at all responsive breakpoints.

### G. Visual Desk and Heatmap modernization

- Keep existing engine layers but simplify control language:
  - Opportunity, Entry, Continuation, Reversal, Context, Fusion.
- Add presets:
  - Quick View
  - Entry Focus
  - Continuation Focus
  - Full Diagnostic
- Add confidence legend with practical interpretation text.
- Reduce visual clutter in default state; advanced layers opt-in.

### H. Transitioning component with clear goals and benefits

- Build a short transition rail that explains:
  - What user should do next
  - Why this matters
  - Expected benefit
- This should guide navigation between Signal Overview, Visual Desk, Timing

Playbook, and Feedback.

### I. No overflow guarantee

- Enforce robust CSS for dynamic text:
  - overflow-wrap:anywhere
  - word-break:break-word
  - min-width:0 for grid children
  - max-height plus scroll for long diagnostics where needed
- Validate at desktop and mobile widths.

## Implementation Workstreams and File-Level Changes

### Workstream 1: Naming and brand alignment

- Update alias/title constants and hero copy in share_phoenixguard.py.
- Update auth button and metadata title copy in share head injection.
- Keep internal identifiers untouched where they are not user-facing.

### Workstream 2: Hide backend details in shared UI

- Update user-facing copy builders in main.py:
  - build_signal_overview_html
  - build_model_council_html
  - _build_heatmap_summary_html
- Preserve internal payload fields but remove explicit backend-tech wording from

displayed text.

### Workstream 3: Signal Overview refactor

- Refactor build_signal_overview_html in main.py to structured sections.
- Add timing summary row sourced from timing_signal payload.
- Keep existing chips but shorten labels and clamp text lengths.

### Workstream 4: Binary Timing Playbook component

- Add new renderer in main.py:
  - build_timing_playbook_html(result)
- Wire into share surface in share_phoenixguard.py:
  - New button/toggle and panel output target.
  - Include overlay snapshot references from multi_timeframe entries.

### Workstream 5: Feedback-first reveal flow

- Add tab-select hook behavior for Feedback in share_phoenixguard.py.
- Defer rendering of feedback controls until tab entry event.
- Keep SHARE_ENABLE_FEEDBACK and SHARE_ENABLE_LEARNING_MUTATIONS behavior

unchanged.

### Workstream 6: Completion notifications

- Extend UI_HEAD in main.py with notification service:
  - Permission request on explicit user action.
  - Notify on signal completion and council completion.
  - In-app toast fallback.
- Trigger from share callbacks via status markers or hidden event payload in

returned HTML.

### Workstream 7: Decision confidence component update

- Replace _build_decision_gauge_from_result output with compact fixed-size chart

style.

- Keep same confidence source values.
- Set strict container dimensions in CSS to prevent crossing over.

### Workstream 8: Visual desk and heatmap simplification

- Update compare and heatmap summary wording in main.py:
  - _build_compare_desk_html
  - _build_heatmap_summary_html
- Add layer presets and cleaner defaults in UI JS behavior.
- Keep all current layer generation techniques and calibration logic intact.

### Workstream 9: Overflow and responsive hardening

- Update CSS in main.py and share extra css in share_phoenixguard.py.
- Apply min-width:0 and overflow-wrap:anywhere to cards, chips, and notes.
- Add explicit mobile checks for critical cards.

### Workstream 10: Transition goals/benefits rail

- Add renderer in main.py for guided transition copy.
- Inject into share layout near adaptive guidance.

## Data and Learning Safety Rules

- Keep host-only logging and mutation controls exactly as currently designed.
- Keep side-effect-free mode default for public share unless operator explicitly

enables learning mutation.

- Keep feedback submission journaling and hash-chain logs unchanged.
- Any expanded UI copy must avoid disclosing internal model stack details.

## Environment and Launch Strategy

### Recommended share defaults for public demo with controlled feedback access

- Keep side effects off by default.
- Enable feedback review path, keep learning mutation optional.

Suggested operator config before launch:

- PHOENIXGUARD_SHARE_ENABLE_FEEDBACK=1
- PHOENIXGUARD_SHARE_ENABLE_LEARNING_MUTATIONS=0 for review-only mode
- PHOENIXGUARD_SHARE_SIDE_EFFECT_FREE=1 for guarded inference mode

### Optional operator mode for trusted users

- PHOENIXGUARD_SHARE_ENABLE_LEARNING_MUTATIONS=1
- Keep audit logs and rate limits active.

## Testing and Validation Plan

### Unit and behavior tests

- Extend Backend/tests/test_share_surface.py for:
  - brand name exact string assertions
  - timing playbook button and panel presence
  - hidden backend wording checks
  - feedback lazy reveal checks

- Extend Backend/tests/test_feedback_learning_flow.py for:
  - feedback tab reveal gating behavior
  - notification payload safety wording

### UI quality checks

- Desktop resolutions: 1920x1080, 1536x864, 1366x768.
- Mobile/tablet widths: 820, 768, 480.
- Validate no text overflow outside cards.
- Validate no component crossing around confidence visual and signal metrics.

### Security and disclosure checks

- Confirm no user-facing text exposes model class names, backend parsers, or

internal runtime signatures.

- Confirm audit logs still capture operational details on host side.

## Rollout Plan

Phase 1

- Naming alignment, signal overview formatting, overflow fixes.

Phase 2

- Binary Timing Playbook panel and overlay snapshot integration.

Phase 3

- Feedback intent reveal and completion notifications.

Phase 4

- Heatmap/visual desk simplification presets and transition rail.

Phase 5

- Regression tests, final copy pass, and production share launch rehearsal.

## Definition of Done

- Shared UI displays the exact name the 808Fx Standard System Hybrid.
- Signal Overview is structured and scannable without dense paragraph confusion.
- Timing playbook is accessible through its own button with full run details and

overlay snaps.

- Feedback and RL controls appear intentionally through Feedback interaction

only.

- Browser notifications fire on run completion with safe non-sensitive copy.
- No card text escapes container bounds at supported breakpoints.
- Heatmap defaults are understandable and actionable, with advanced layers still

available.

- Backend internals remain hidden in public-facing UI text while host-side

logging remains intact.
