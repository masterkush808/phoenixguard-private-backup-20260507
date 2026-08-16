# PhoenixGuard Universal Edge Chart Region

<!-- markdownlint-disable MD013 -->

Current unpacked release: **0.3.12**.

This unpacked Microsoft Edge MV3 extension streams one explicitly selected rectangle from any normal HTTP(S) chart tab to the local PhoenixGuard V3 frame-ingest service. Once the badge reads `LIVE`, the chart tab may be inactive, covered, or inside a minimized Edge window while its individual tab stream continues. PhoenixGuard never raises Edge, activates the selected tab, or clicks the chart.

The extension is study-only. It does not click BUY or SELL and grants no execution permission.

The canonical PhoenixGuard launcher does not start Edge or open a dashboard tab. It waits briefly for this already-installed extension to reclaim its already-authorized stream, then leaves every existing browser window and tab untouched. Dashboard opening is an explicit operator opt-in.

## Operator controls

- `Ctrl+Shift+7`: immediately lock the active HTTP(S) tab's full viewport through the same provisional stream and source-lease promotion contract. It does not open or inject the ROI selector.
- `Ctrl+Shift+8`: select or switch the chart region in the active Edge tab.
- `Ctrl+Shift+9`: global kill switch. Stops active and provisional capture.
- Toolbar icon: select or switch the chart region.
- Selector: drag over the chart, then press **Use this chart region** or `Enter`. On a scaled Windows desktop, `F` selects the full viewport deterministically and `Enter` confirms it. `Esc` cancels and preserves an existing source.

Edge reserves `Ctrl+Shift+B` for its Favorites bar, so PhoenixGuard does not attempt to override it. Shortcuts can be remapped at `edge://extensions/shortcuts`.

## What remains visible in the background

The extension uses Edge `tabCapture`, not desktop screenshots. It consumes the explicit capture grant inside an offscreen extension document and crops every frame to the selected normalized ROI. This allows the chosen web tab to remain covered or inactive.

While a chart capture is locked, the extension marks only that tab as non-discardable so Edge Memory Saver cannot unload it. The tab's original discard setting is restored when capture stops or switches. The offscreen capture verifies a live `MediaStreamTrack` against Edge's `tabCapture` registry every ten seconds. Unchanged chart pixels remain valid while decoded-frame count or media time keeps advancing. If both stop for more than 90 seconds, upload fails closed even when the track remains registered.

An arbitrary desktop rectangle is different: once another window covers it, a desktop screenshot sees the covering application. Native applications require a separate per-window Windows Graphics Capture source.

## Security and truth boundaries

- Only an explicit toolbar click or Edge command can grant the temporary `activeTab` access used for selection.
- Selection accepts only normal `http://` or `https://` tabs. Edge settings, extension pages, files, data URLs, and JavaScript URLs are rejected.
- There are no persistent website host permissions and no always-injected content script.
- Host access is limited to `http://127.0.0.1/*` and `http://localhost/*` for PhoenixGuard ingest.
- The selector runs only in the top frame and sends geometry. The capture worker reads only exact visible pair/timeframe labels plus their CSS bboxes for the bracketed identity proof; it does not upload broader DOM content or credentials.
- Query strings, fragments, and URL credentials are removed from source lineage.
- Pair/timeframe acceleration is advisory and capture-bound: the service worker observes the visible pair and timeframe controls immediately before and after the exact video-to-canvas sample. `identity_observation_v3` is emitted only when values, CSS bboxes, viewport, tab, origin, and sequence all agree; backend pixel evidence remains authoritative.
- The light lease heartbeat also samples the locked tab's pair/timeframe independently of image upload. This bounded observation is revocation-only: it can immediately hide an older pair's overlays and decision while the new chart is classified, but it can never create completed-study, overlay, direction, timing, or entry authority.
- Every confirmed ROI obtains a server source generation and private lease. Superseded or killed leases stop locally on HTTP `409`/`410`; they cannot silently keep publishing.
- If only the local API worker restarts and reports an exact ownerless `NO_SOURCE` fence, the still-live, already-authorized tab stream conditionally reclaims its lease. The compare-and-swap refuses recovery when another source owns the session or the operator killed it.
- No provisional frame is uploaded while the selection overlay is visible.
- Heartbeats renew only a currently attested tab-capture lease with bounded decoder progress. Upload pauses on a stopped/muted track, missing capture registration, discarded tab, explicitly frozen tab, or decoder stall beyond 90 seconds; a merely inactive, covered, or minimized tab remains eligible while media delivery advances.
- No tab/window activation, restore, topmost, focus, execution, or broker-click API is present. The only tab mutation is the temporary `autoDiscardable: false` capture guard described above.

## Read-only readiness check

From the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\Developer\browser_extensions\phoenixguard_edge_background_tab\Test-ExtensionReadiness.ps1"
```

For JSON evidence:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\Developer\browser_extensions\phoenixguard_edge_background_tab\Test-ExtensionReadiness.ps1" -Json
```

Do not install or reload while readiness reports `FAIL`.

## Existing-tab launch and recovery

Launch the local stack from the repository root with the canonical final V3
launcher. `-NoBrowser` is explicit proof that launch must leave every existing
Edge window and tab untouched:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\Backend\launch\launch_phoenixguard_live_ready.ps1" -NoBrowser
```

The launch is headless with respect to Edge: it neither creates a new window
nor opens another Pocket Option or dashboard tab. Open the printed dashboard
URL yourself only when a dashboard tab is explicitly wanted. Runtime state,
logs, and the extension token default under
`%LOCALAPPDATA%\PhoenixGuard\runtime\live`; `PHOENIXGUARD_RUNTIME_DIR`,
`PHOENIXGUARD_DATA_DIR`, and `PHOENIXGUARD_LOGS_DIR` are explicit overrides.

After updating this unpacked extension, open `edge://extensions` once and press
**Reload** on PhoenixGuard Universal Edge Chart Region. Return to the already
open chart and select it once; do not open a second broker tab. On later API or
stack restarts, leave that chart tab and Edge running. The live offscreen
capture conditionally reclaims an ownerless local source lease and continues
without focusing, restoring, or reopening Edge. If the badge stays `OFF`, use
the toolbar or `Ctrl+Shift+7`/`Ctrl+Shift+8`; if it reads `FRZ`, verify the tab
was not discarded or navigated to another origin before reselecting it. The
kill switch remains `Ctrl+Shift+9`.

Version acceptance requires a newly accepted frame after reload or recovery:
`external_frame_feed.source_lineage.extension_version` must match
`manifest.json`. An older saved session can legitimately retain the previous
frame's version until a new frame arrives; it is not proof that Edge is still
running the old release.

## One-time installation

1. Use a dedicated, signed-out local Edge profile with sync disabled.
2. Open `edge://extensions`, enable **Developer mode**, and choose **Load unpacked**.
3. Select `Developer\browser_extensions\phoenixguard_edge_background_tab`.
4. Confirm the extension shows `activeTab`, `alarms`, `offscreen`, `scripting`, `storage`, `tabCapture`, and loopback-only host access. Remove it if broader access appears.
5. If PhoenixGuard origin enforcement is enabled, add only `chrome-extension://<this-extension-id>` to both allowed-origin settings before launching the stack. Never use a wildcard.
6. Open **Extension options**. Keep the server at `http://127.0.0.1:8793`, use the live session, and enter the locally generated token. On Windows the default path is `%LOCALAPPDATA%\PhoenixGuard\runtime\live\edge_tab_capture.token`; `PHOENIXGUARD_RUNTIME_DIR` can override it. Never commit or share it.
7. Select **Test server**. It must report that ingest is armed and `edge_tab_roi_v1` leases are supported.
8. Open `edge://extensions/shortcuts` and confirm Lock Full Viewport, Select/Switch, and Kill are assigned.

## Use

1. Open TradingView, Pocket Option, or another HTTP(S) chart in Edge.
2. Make that chart tab active. Press `Ctrl+Shift+7` to lock its full viewport without a selector, or press `Ctrl+Shift+8` to open exact-region selection.
3. For exact-region selection, drag over the chart viewport. Include the candles and price geometry needed by PhoenixGuard; the minimum is 320 × 180 CSS pixels. `F` selects the full viewport inside the interactive selector.
4. Confirm the rectangle. The selector disappears before the first candidate frame is eligible for ingest. For a full-tab chart, press `Ctrl+Shift+7` instead: the trusted Edge command reads and commits the full viewport without opening the selector.
5. Wait for the badge to progress from `ROI` to `LIVE`.
6. Switch tabs or applications, cover Edge, or minimize its window and continue working normally.
7. To change the chart or site, activate the new chart and press `Ctrl+Shift+8`. The previous source remains authoritative until the candidate is confirmed. Cancelling preserves it.
8. Press `Ctrl+Shift+9` whenever capture must stop immediately.

Pair and timeframe are re-proved from every newly selected chart. Legacy saved hints are cleared automatically and are never uploaded by the universal ROI capture path.

## Fail-closed behavior

Capture stops or refuses promotion when:

- the tab is closed, discarded, or navigates to another origin;
- the selected origin changes;
- the selector expires after 60 seconds;
- the candidate stream cannot be acquired;
- the source claim is rejected;
- the tab capture track ends;
- the current source lease is superseded or killed;
- the operator invokes the kill switch.

If a different-tab candidate fails, the already active source is preserved. Same-tab reselection pauses sampling so the selection overlay can never contaminate model input.

## Acceptance test

Use paper/demo study only during validation.

1. Run Node tests and the readiness check.
2. Select a TradingView region and wait for `LIVE`.
3. Cover Edge with another application for at least 90 seconds. Confirm frame IDs and accepted timestamps advance without any Edge foreground event.
4. Minimize Edge for at least 90 seconds. Confirm capture-health attestations, frame IDs, and accepted timestamps advance and the tab remains non-discardable. Decoder age may advance on an unchanged chart without making the transport stale.
5. Make another Edge tab active for at least 90 seconds. Confirm the locked tab ID, selection ID, source generation, and sequence remain unchanged.
6. Start a switch to another chart and cancel it. Confirm the original source resumes with no candidate pixels accepted.
7. Confirm a new chart. Confirm a new sequence, selection, source generation, and lease become authoritative.
8. Reload or change routes inside the selected origin. Confirm the same capture lease survives and visual pair/timeframe identity is re-proved. Navigate to another origin and confirm capture stops.
9. Restart only the PhoenixGuard API while the Edge capture remains live. Confirm the same tab/selection/sequence conditionally reclaims the ownerless server lease without opening or focusing any browser UI.
10. Invoke `Ctrl+Shift+9`. Confirm the badge reads `OFF`, server source control reports killed, the original tab discard setting is restored, and no later frame is accepted.

Pass requires zero focus-changing events, no selector pixels in ingest, monotonic frames under one source lease, honest `FRZ` reporting, and hard stop after kill or lease loss.
