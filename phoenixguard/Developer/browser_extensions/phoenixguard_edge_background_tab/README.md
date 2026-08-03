# PhoenixGuard Universal Edge Chart Region

<!-- markdownlint-disable MD013 -->

This unpacked Microsoft Edge MV3 extension streams one explicitly selected rectangle from any normal HTTP(S) chart tab to the local PhoenixGuard V3 frame-ingest service. Once the badge reads `LIVE`, the chart tab may be inactive, covered, or inside a minimized Edge window while its individual tab stream continues. PhoenixGuard never raises Edge, activates the selected tab, or clicks the chart.

The extension is study-only. It does not click BUY or SELL and grants no execution permission.

## Operator controls

- `Ctrl+Shift+8`: select or switch the chart region in the active Edge tab.
- `Ctrl+Shift+9`: global kill switch. Stops active and provisional capture.
- Toolbar icon: select or switch the chart region.
- Selector: drag over the chart, then press **Use this chart region** or `Enter`. `Esc` cancels and preserves an existing source.

Edge reserves `Ctrl+Shift+B` for its Favorites bar, so PhoenixGuard does not attempt to override it. Shortcuts can be remapped at `edge://extensions/shortcuts`.

## What remains visible in the background

The extension uses Edge `tabCapture`, not desktop screenshots. It consumes the explicit capture grant inside an offscreen extension document and crops every frame to the selected normalized ROI. This allows the chosen web tab to remain covered or inactive.

While a chart capture is locked, the extension marks only that tab as non-discardable so Edge Memory Saver cannot unload it. The tab's original discard setting is restored when capture stops or switches. Offscreen freshness observes both video-frame callbacks and decoded media progress, so a hidden offscreen document cannot falsely report `FRZ` merely because Edge throttled one callback mechanism.

An arbitrary desktop rectangle is different: once another window covers it, a desktop screenshot sees the covering application. Native applications require a separate per-window Windows Graphics Capture source.

## Security and truth boundaries

- Only an explicit toolbar click or Edge command can grant the temporary `activeTab` access used for selection.
- Selection accepts only normal `http://` or `https://` tabs. Edge settings, extension pages, files, data URLs, and JavaScript URLs are rejected.
- There are no persistent website host permissions and no always-injected content script.
- Host access is limited to `http://127.0.0.1/*` and `http://localhost/*` for PhoenixGuard ingest.
- The selector runs only in the top frame and sends geometry, never page DOM or credentials.
- Query strings, fragments, and URL credentials are removed from source lineage.
- Every confirmed ROI obtains a server source generation and private lease. Superseded or killed leases stop locally on HTTP `409`/`410`; they cannot silently keep publishing.
- No provisional frame is uploaded while the selection overlay is visible.
- Upload heartbeats do not prove the source is rendering. The badge reports `FRZ` and uploads pause when Edge stops presenting fresh video frames.
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

## One-time installation

1. Use a dedicated, signed-out local Edge profile with sync disabled.
2. Open `edge://extensions`, enable **Developer mode**, and choose **Load unpacked**.
3. Select `Developer\browser_extensions\phoenixguard_edge_background_tab`.
4. Confirm the extension shows `activeTab`, `offscreen`, `scripting`, `storage`, `tabCapture`, and loopback-only host access. Remove it if broader access appears.
5. If PhoenixGuard origin enforcement is enabled, add only `chrome-extension://<this-extension-id>` to both allowed-origin settings before launching the stack. Never use a wildcard.
6. Open **Extension options**. Keep the server at `http://127.0.0.1:8793`, use the live session, and enter the locally generated token from `runtime\live\edge_tab_capture.token`. Never commit or share it.
7. Select **Test server**. It must report that ingest is armed and `edge_tab_roi_v1` leases are supported.
8. Open `edge://extensions/shortcuts` and confirm Select/Switch and Kill are assigned.

## Use

1. Open TradingView, Pocket Option, or another HTTP(S) chart in Edge.
2. Make that chart tab active and press `Ctrl+Shift+8`.
3. Drag over the exact chart viewport. Include the candles and price geometry needed by PhoenixGuard; the minimum is 320 × 180 CSS pixels.
4. Confirm the rectangle. The selector disappears before the first candidate frame is eligible for ingest.
5. Wait for the badge to progress from `ROI` to `LIVE`.
6. Switch tabs or applications, cover Edge, or minimize its window and continue working normally.
7. To change the chart or site, activate the new chart and press `Ctrl+Shift+8`. The previous source remains authoritative until the candidate is confirmed. Cancelling preserves it.
8. Press `Ctrl+Shift+9` whenever capture must stop immediately.

Symbol and timeframe hints are optional and blank by default. Keep them blank when switching between pairs unless the hint is certainly correct; PhoenixGuard must re-prove chart identity instead of inheriting stale pair metadata.

## Fail-closed behavior

Capture stops or refuses promotion when:

- the tab is closed, navigated, or reloaded;
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
4. Minimize Edge for at least 90 seconds. Confirm decoded media progress, frame IDs, and accepted timestamps advance and the tab remains non-discardable.
5. Make another Edge tab active for at least 90 seconds. Confirm the locked tab ID, selection ID, source generation, and sequence remain unchanged.
6. Start a switch to another chart and cancel it. Confirm the original source resumes with no candidate pixels accepted.
7. Confirm a new chart. Confirm a new sequence, selection, source generation, and lease become authoritative.
8. Reload the selected tab. Confirm capture stops and requires a new selection.
9. Invoke `Ctrl+Shift+9`. Confirm the badge reads `OFF`, server source control reports killed, the original tab discard setting is restored, and no later frame is accepted.

Pass requires zero focus-changing events, no selector pixels in ingest, monotonic frames under one source lease, honest `FRZ` reporting, and hard stop after kill or lease loss.
