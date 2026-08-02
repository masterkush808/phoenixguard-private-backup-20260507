# PhoenixGuard Edge Background Tab

<!-- markdownlint-disable MD013 -->

This unpacked Microsoft Edge MV3 extension captures one explicitly selected Pocket Option tab and sends bounded frames to the local PhoenixGuard frame-ingest endpoint. After the one-time lock gesture, tab capture is independent of desktop window focus: PhoenixGuard does not need to raise Edge over the application you are using.

The extension is not a broker automation tool. It does not click BUY or SELL, does not read other browser profiles, and does not grant execution permission. It posts chart frames only to `http://127.0.0.1` or `http://localhost`.

## Important boundary

The native PhoenixGuard source captures an Edge **window**. If Pocket Option shares that window with other tabs, selecting another tab replaces its pixels and native HWND capture cannot see Pocket Option. An "always active" visibility extension cannot change that fact.

This extension uses Edge `tabCapture` instead. The operator must click the extension once while the verified HTTPS Pocket Option tab is active. After the badge reports `LIVE`, that locked tab may be covered by other windows or become an inactive tab while its individual media stream continues. Closing the tab, navigating it away from Pocket Option, reloading the extension, or exiting Edge ends the stream and requires a fresh operator lock.

## Safety model

- Install it only in a dedicated, local Edge profile such as `PhoenixGuard Broker`.
- Do not sign that profile into a Microsoft account and do not enable extension sync.
- Do not load it into your everyday Edge profile.
- Use Edge's normal **Load unpacked** workflow. Do not use registry policy, force-install policy, `--load-extension`, or scripts that start Edge.
- The extension requires only `activeTab`, `tabCapture`, `offscreen`, and local `storage` permissions.
- Host access is restricted to HTTPS Pocket Option pages and the loopback PhoenixGuard API.
- The explicit toolbar click is the authority boundary. The extension cannot silently choose an unrelated tab.
- Keep the unpacked folder at this stable repository path. Review changes and rerun readiness before pressing **Reload** in Edge.
- Treat the frame-ingest token and optional signing secret as local credentials. Never commit, screenshot, paste into chat, or copy them into this directory.

## Read-only readiness check

From the repository root, run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\Developer\browser_extensions\phoenixguard_edge_background_tab\Test-ExtensionReadiness.ps1"
```

The check validates MV3, the exact permission and host allowlists, all referenced files, absence of remote or dynamic script loading, and file hashes. It does **not** open Edge, install an extension, change a profile, write policy, or modify the registry.

For machine-readable evidence:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ".\Developer\browser_extensions\phoenixguard_edge_background_tab\Test-ExtensionReadiness.ps1" -Json
```

Do not install while readiness reports `FAIL`.

## One-time installation in an isolated Edge profile

1. Start Edge yourself and create a new local profile named `PhoenixGuard Broker`. Keep it signed out and leave sync disabled.
2. In that dedicated profile only, open `edge://extensions`.
3. Enable **Developer mode**.
4. Select **Load unpacked** and choose this exact folder:

   `Developer\browser_extensions\phoenixguard_edge_background_tab`

5. Confirm the extension details show only the permissions and hosts listed in the safety model above. If Edge shows broader access, remove it and stop.
6. Copy the extension ID shown by `edge://extensions`. If PhoenixGuard origin enforcement is enabled, `chrome-extension://<that-id>` must be present in both `PHOENIXGUARD_ALLOWED_ORIGINS` and `PHOENIXGUARD_FRAME_INGEST_ALLOWED_ORIGINS` **before** the stack starts. Never use a wildcard extension origin.
7. Open **Extension options**. Keep the server URL at `http://127.0.0.1:8793`, use session `pocket-live-8788`, and enter the locally generated frame-ingest token. The canonical local launcher writes that credential to `runtime\live\edge_tab_capture.token`; read it locally and never paste it into chat or commit it.
8. Leave the signing secret blank unless the API readiness contract reports that signatures are required and its CORS preflight explicitly permits the extension's HMAC headers. A partial signing setup must fail closed; do not weaken origin or signature validation to make it connect.
9. Open the HTTPS Pocket Option trading page. Verify both its URL and title identify Pocket Option.
10. Click the PhoenixGuard extension icon once. The badge must progress to `LIVE`. A second click deliberately stops capture.

Installation is profile-scoped. Removing the extension or deleting the dedicated profile reverses the installation without changing the everyday Edge profile.

## Operating rules

- Keep the locked Pocket Option tab open. It may be inactive or occluded after the badge reaches `LIVE`.
- Use another Edge window or another application for normal work.
- Do not assume a pinned or sleeping tab is streaming; the extension badge and frame-ingest status are authoritative.
- After an Edge restart, extension reload, tab close, or Pocket Option navigation, return to the Pocket Option tab and click once to establish a new stream.
- Do not run both an old foreground-restoring capture helper and this source. PhoenixGuard background-only mode must remain enabled.
- If background frames stop, PhoenixGuard must report stale/unavailable. It must never recover by raising the broker window.

## Acceptance test: tab switching and occlusion

Run this test with paper/demo use only. Do not place a live-money trade during capture validation.

### Preconditions

1. Readiness reports `PASS`.
2. PhoenixGuard is running with `PHOENIXGUARD_BACKGROUND_CAPTURE_ONLY=1`, foreground capture fallback disabled, and locked-window restore disabled.
3. The extension options point to the local API and the expected session. The frame-ingest readiness endpoint is armed, and any configured origin/signature checks pass from this exact extension ID.
4. Pocket Option is locked and the extension badge reads `LIVE`.
5. Record the current external-frame feed sequence ID, frame ID, accepted-frame count, last-capture time, and the Pocket Option window handle.

### Scenario A: occluded window

1. Put a different application in front of the entire Edge window for 90 seconds. Do not click or expose the Pocket Option window.
2. Continue working normally in the foreground application.
3. Confirm that the Pocket Option window never becomes foreground or topmost.
4. Confirm the same external source and sequence remain locked, frame IDs increase monotonically, accepted-frame count advances, and capture age remains within the configured heartbeat window.

### Scenario B: inactive tab

1. In the dedicated broker window, switch from the already locked Pocket Option tab to a harmless local or blank tab for 90 seconds.
2. Leave Pocket Option inactive. Do not click the extension again.
3. Confirm the badge remains `LIVE`, the locked tab ID does not change, and PhoenixGuard frames continue to identify the original Pocket Option source.
4. Return to Pocket Option and verify continuity: no sequence reset, duplicate frame ID, or source substitution occurred.

### Scenario C: fail-closed identity change

1. With the stream live, navigate the locked tab away from `https://pocketoption.com` or close it.
2. Confirm the extension stops, clears the locked tab, and reports `OFF` or an error.
3. Confirm PhoenixGuard becomes stale/unavailable after its bounded timeout and does not bind to the blank tab, another Edge tab, or the operator's foreground application.

### Pass criteria

- Zero Edge foreground, restore, topmost, or new-window events caused by PhoenixGuard.
- One explicit Pocket Option tab ID, source ID, and sequence throughout Scenarios A and B.
- Monotonic frame IDs and capture timestamps; no replay or cross-tab substitution.
- Continued accepted frames or explicit bounded stale status. Silent reuse of old pixels is a failure.
- Scenario C stops the source and fails closed.
- No broker clicks and no execution authority in extension frame metadata.

If any criterion fails, remove or stop the extension and continue with PhoenixGuard in background-only unavailable/stale mode. Never re-enable foreground recovery to make this test pass.
