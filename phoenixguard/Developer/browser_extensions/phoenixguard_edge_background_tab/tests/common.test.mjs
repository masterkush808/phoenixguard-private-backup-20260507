import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";
import {fileURLToPath} from "node:url";
import {dirname, resolve} from "node:path";

import {
  DECODER_PROGRESS_TIMEOUT_MS,
  SAMPLE_FPS,
  SAMPLE_INTERVAL_MS,
  boundedHeartbeatIdentityObservation,
  canonicalFrameSignaturePayload,
  captureDiscardPolicy,
  captureHealthCheckDue,
  captureRegistryAttestation,
  captureTransportDecision,
  capturableHttpUrl,
  frameIngestEndpoint,
  initialStatus,
  leaseHeartbeatDue,
  lockedTabLifecycleAction,
  mapNormalizedRegionToPixels,
  mediaPlaybackAdvanced,
  meanAbsoluteDifference,
  normalizeConfig,
  normalizeRegionSelection,
  normalizedRoiVector,
  ownerlessSourceRecoveryFence,
  remainingUploadStartDelayMs,
  sanitizeSourceUrl,
  sourceControlClaimEndpoint,
  sourceControlHeartbeatEndpoint,
  sourceControlHeartbeatPayload,
  sourceControlKillEndpoint,
  sourceControlKillPayload,
  sourceControlStatusEndpoint,
  sourceOrigin,
  tabCaptureLineageStillCurrent,
  terminalTabCaptureTarget,
  validateAuthorizedBackgroundTab,
  validateCapturableTab,
  validateConfig
} from "../common.js";

const here = dirname(fileURLToPath(import.meta.url));
const extensionRoot = resolve(here, "..");

test("any explicit HTTP(S) chart tab is capturable while protected schemes are rejected", () => {
  assert.equal(capturableHttpUrl("https://www.tradingview.com/chart/ABC?secret=1"), true);
  assert.equal(capturableHttpUrl("https://pocketoption.com/en/cabinet/"), true);
  assert.equal(capturableHttpUrl("http://localhost:3000/chart"), true);
  for (const url of ["edge://settings", "chrome://extensions", "file:///chart.html", "data:text/html,chart", "javascript:void(0)"]) {
    assert.equal(capturableHttpUrl(url), false, url);
  }
  assert.deepEqual(
    validateCapturableTab({id: 12, active: true, url: "https://www.tradingview.com/chart/x", title: "Chart"}),
    {ok: true, reason: "The active HTTP(S) chart tab can be selected."}
  );
  assert.equal(validateCapturableTab({id: 12, active: false, url: "https://example.com"}).ok, false);
  assert.equal(validateCapturableTab({id: 12, active: true, url: "edge://settings"}).ok, false);
  assert.deepEqual(
    validateAuthorizedBackgroundTab(
      {id: 12, active: false, url: "https://pocketoption.com/en/cabinet/"},
      "https://pocketoption.com"
    ),
    {ok: true, reason: "The authorized HTTP(S) chart tab can recover in the background."}
  );
  assert.equal(
    validateAuthorizedBackgroundTab(
      {id: 12, active: false, url: "https://example.com/chart"},
      "https://pocketoption.com"
    ).ok,
    false
  );
});

test("source URL lineage removes credentials, query, and fragment", () => {
  assert.equal(
    sanitizeSourceUrl("https://user:pass@charts.example.test/view?token=secret#chart"),
    "https://charts.example.test/view"
  );
  assert.equal(sourceOrigin("https://charts.example.test/view?token=secret"), "https://charts.example.test");
  assert.equal(sanitizeSourceUrl("edge://settings"), "");
});

test("ROI selection clamps to viewport and rejects undersized geometry", () => {
  const selected = normalizeRegionSelection({
    rectCss: {x: 900, y: 600, width: -800, height: -500},
    viewportCss: {width: 1000, height: 700},
    devicePixelRatio: 1.5
  });
  assert.equal(selected.ok, true);
  assert.deepEqual(selected.region.rectCss, {x: 100, y: 100, width: 800, height: 500});
  assert.deepEqual(selected.region.normalized, {
    x: 0.1,
    y: 0.14285714,
    width: 0.8,
    height: 0.71428571
  });
  assert.equal(normalizeRegionSelection({
    rectCss: {x: 0, y: 0, width: 100, height: 100},
    viewportCss: {width: 1000, height: 700}
  }).ok, false);
});

test("normalized ROI maps deterministically into current video pixels", () => {
  assert.deepEqual(
    mapNormalizedRegionToPixels({x: 0.1, y: 0.2, width: 0.5, height: 0.4}, 1920, 1080),
    {x: 192, y: 216, width: 960, height: 432, sourceWidth: 1920, sourceHeight: 1080}
  );
  const bounded = mapNormalizedRegionToPixels({x: 0.99, y: 0.99, width: 1, height: 1}, 100, 100);
  assert.deepEqual(bounded, {x: 99, y: 99, width: 1, height: 1, sourceWidth: 100, sourceHeight: 100});
});

test("local frame-ingest and source-control configuration is bounded", () => {
  const config = normalizeConfig({
    baseUrl: "http://127.0.0.1:8793///",
    sessionId: "pocket-live-8788",
    sourceId: "edge-chart-region-v3",
    token: " secret ",
    symbol: "CHF/JPY OTC",
    timeframe: "M5",
    maxWidth: 99_999,
    jpegQuality: 0.1,
    materialDeltaThreshold: 4,
    heartbeatSec: 2
  });
  assert.equal(config.baseUrl, "http://127.0.0.1:8793");
  assert.equal(config.token, "secret");
  assert.equal(config.symbol, "");
  assert.equal(config.timeframe, "");
  assert.equal(config.maxWidth, 2560);
  assert.equal(config.jpegQuality, 0.55);
  assert.equal(config.materialDeltaThreshold, 0.08);
  assert.equal(config.heartbeatSec, 15);
  assert.equal(validateConfig(config).ok, true);
  assert.equal(validateConfig({...config, baseUrl: "https://remote.example"}).ok, false);
  assert.equal(validateConfig({...config, token: ""}).ok, false);
  assert.equal(frameIngestEndpoint(config), "http://127.0.0.1:8793/v1/mobile/frame-ingest/sessions/pocket-live-8788/frames");
  assert.equal(sourceControlClaimEndpoint(config), "http://127.0.0.1:8793/v1/mobile/frame-ingest/sessions/pocket-live-8788/source-control/claim");
  assert.equal(sourceControlStatusEndpoint(config), "http://127.0.0.1:8793/v1/mobile/frame-ingest/sessions/pocket-live-8788/source-control");
  assert.equal(sourceControlKillEndpoint(config), "http://127.0.0.1:8793/v1/mobile/frame-ingest/sessions/pocket-live-8788/source-control/kill");
  assert.deepEqual(sourceControlKillPayload({
    sourceId: " edge-chart-region-v3 ",
    sequenceId: " edge-roi-5-seq ",
    sourceGeneration: 7.9,
    sourceLeaseId: " lease-private ",
    reason: " Operator kill "
  }), {
    source_id: "edge-chart-region-v3",
    sequence_id: "edge-roi-5-seq",
    source_generation: 7,
    source_lease_id: "lease-private",
    reason: "Operator kill"
  });
});

test("an already-authorized stream can recover only from an exact ownerless API-restart fence", () => {
  const ownerless = {
    state_revision: 0,
    state: "NO_SOURCE",
    source_id: "",
    source_generation: 0,
    source_type: "",
    coordinate_space: "",
    selection_id: "",
    sequence_id: ""
  };
  const recovery = ownerlessSourceRecoveryFence(ownerless);
  assert.equal(recovery.ok, true);
  assert.equal(recovery.reason, "ownerless_api_restart");
  assert.deepEqual(recovery.expectedSourceControl, ownerless);

  assert.equal(ownerlessSourceRecoveryFence({...ownerless, state: "KILLED"}).ok, false);
  assert.equal(ownerlessSourceRecoveryFence({...ownerless, state: "LIVE", source_id: "edge-new", source_generation: 2}).ok, false);
  assert.equal(ownerlessSourceRecoveryFence({...ownerless, sequence_id: "another-owner"}).ok, false);
  assert.equal(ownerlessSourceRecoveryFence({state: "NO_SOURCE"}).reason, "incomplete_source_fence");
});

test("source heartbeat is lease-bound and full viewport ROI stays explicit", () => {
  const config = normalizeConfig({
    baseUrl: "http://127.0.0.1:8793",
    sessionId: "pocket-live-8788"
  });
  assert.equal(
    sourceControlHeartbeatEndpoint(config),
    "http://127.0.0.1:8793/v1/mobile/frame-ingest/sessions/pocket-live-8788/source-control/heartbeat"
  );
  assert.deepEqual(
    normalizedRoiVector({x: 0, y: 0, width: 1, height: 1}),
    [0, 0, 1, 1]
  );
  assert.deepEqual(normalizedRoiVector({x: 0.8, y: 0, width: 0.3, height: 1}), []);
  const heartbeat = sourceControlHeartbeatPayload({
    sourceId: "edge-chart-region-v3",
    sequenceId: "sequence-808",
    sourceGeneration: 4,
    sourceLeaseId: "lease-4",
    captureEpochMs: 1_780_000_000_000,
    sourceRenderFresh: true,
    materialChangePending: true,
    roiNormalized: {x: 0, y: 0, width: 1, height: 1},
    roiSourcePixels: {x: 0, y: 0, width: 1920, height: 1080},
    sourceSurfaceWidth: 1920,
    sourceSurfaceHeight: 1080,
    transportFrameAgeMs: 12,
    decoderFrameAgeMs: 24,
    captureHealthReason: "capture_confirmed",
    captureStatus: "active",
    presentedFrames: 808,
    mediaTime: 42.5,
    identityObservationV3: {
      schema_version: "PG_EDGE_TAB_IDENTITY_HEARTBEAT_V3",
      revocation_only: true,
      symbol: "usd/cad otc",
      timeframe: "m5",
      sequence_id: "sequence-808",
      locked_tab_id: 443372230,
      locked_origin: "https://pocketoption.com/en/cabinet/?token=private",
      observed_epoch_ms: 1_780_000_000_001,
      direction: "BUY",
      permission_allowed: true,
      study_authority: true,
      overlay_authority: true,
      decision_authority: true
    }
  });
  assert.equal(heartbeat.source_lease_id, "lease-4");
  assert.equal(heartbeat.material_change_pending, true);
  assert.deepEqual(heartbeat.roi_normalized, [0, 0, 1, 1]);
  assert.equal(heartbeat.source_surface_width, 1920);
  assert.equal(heartbeat.decoder_frame_age_ms, 24);
  assert.deepEqual(heartbeat.identity_observation_v3, {
    schema_version: "PG_EDGE_TAB_IDENTITY_HEARTBEAT_V3",
    revocation_only: true,
    symbol: "USD/CAD OTC",
    timeframe: "M5",
    sequence_id: "sequence-808",
    locked_tab_id: 443372230,
    locked_origin: "https://pocketoption.com",
    observed_epoch_ms: 1_780_000_000_001,
    study_authority: false,
    overlay_authority: false,
    decision_authority: false
  });
  assert.equal("direction" in heartbeat.identity_observation_v3, false);
  assert.equal("permission_allowed" in heartbeat.identity_observation_v3, false);
  assert.equal(
    boundedHeartbeatIdentityObservation(
      {...heartbeat.identity_observation_v3, sequence_id: "superseded"},
      "sequence-808"
    ),
    null
  );
});

test("signature canonicalization matches PG_FRAME_INGEST_V1 field order", () => {
  const canonical = canonicalFrameSignaturePayload({
    path: "/v1/mobile/frame-ingest/sessions/pocket-live-8788/frames",
    sessionId: "pocket-live-8788",
    sourceId: "edge-chart-region-v3",
    sequenceId: "edge-roi-5-seq",
    frameId: 42,
    captureEpochMs: 1780000000000,
    frameSha256: "ABCDEF",
    timestamp: "1780000001000",
    nonce: "nonce-42"
  });
  assert.equal(canonical, [
    "PG_FRAME_INGEST_V1", "POST", "/v1/mobile/frame-ingest/sessions/pocket-live-8788/frames",
    "pocket-live-8788", "edge-chart-region-v3", "edge-roi-5-seq", "42", "1780000000000",
    "abcdef", "1780000001000", "nonce-42"
  ].join("\n"));
});

test("material probe difference is normalized", () => {
  assert.equal(meanAbsoluteDifference(new Uint8Array([0, 10]), new Uint8Array([0, 10])), 0);
  assert.equal(meanAbsoluteDifference(new Uint8Array([0]), new Uint8Array([255])), 1);
  assert.equal(meanAbsoluteDifference(null, new Uint8Array([10])), 1);
});

test("media playback progress proves fresh transport even when video callbacks are throttled", () => {
  assert.deepEqual(
    mediaPlaybackAdvanced({mediaTime: 10, decodedFrames: 20}, {mediaTime: 10.25, decodedFrames: 20}),
    {advanced: true, mediaTime: 10.25, decodedFrames: 20}
  );
  assert.deepEqual(
    mediaPlaybackAdvanced({mediaTime: 10.25, decodedFrames: 20}, {mediaTime: 10.25, decodedFrames: 21}),
    {advanced: true, mediaTime: 10.25, decodedFrames: 21}
  );
  assert.equal(
    mediaPlaybackAdvanced({mediaTime: 10.25, decodedFrames: 21}, {mediaTime: 10.25, decodedFrames: 21}).advanced,
    false
  );
});

test("background transport tolerates unchanged pixels only while decoder progress remains bounded", () => {
  const now = 1_000_000;
  assert.deepEqual(
    captureTransportDecision({
      trackReadyState: "live",
      trackMuted: false,
      captureStatus: "active",
      checkedAtMs: now,
      lastConfirmedAtMs: now,
      tabDiscarded: false,
      tabFrozen: false,
      decoderProgressAgeMs: 30_000
    }, now + 30_000),
    {healthy: true, reason: "capture_confirmed", confirmationAgeMs: 30_000, checkAgeMs: 30_000}
  );
  assert.equal(captureTransportDecision({
    trackReadyState: "live",
    captureStatus: "unknown",
    checkedAtMs: now + 10_000,
    lastConfirmedAtMs: now,
    decoderProgressAgeMs: 60_000
  }, now + 40_000).reason, "capture_confirmation_grace");
  assert.equal(captureTransportDecision({
    trackReadyState: "live",
    captureStatus: "unknown",
    checkedAtMs: now + 50_000,
    lastConfirmedAtMs: now,
    decoderProgressAgeMs: 0
  }, now + 50_000).healthy, false);
  const registeredCapture = {
    trackReadyState: "live",
    trackMuted: false,
    captureStatus: "active",
    checkedAtMs: now,
    lastConfirmedAtMs: now,
    tabDiscarded: false,
    tabFrozen: false
  };
  assert.equal(captureTransportDecision({
    ...registeredCapture,
    decoderProgressAgeMs: DECODER_PROGRESS_TIMEOUT_MS
  }, now).healthy, true);
  assert.deepEqual(captureTransportDecision({
    ...registeredCapture,
    decoderProgressAgeMs: DECODER_PROGRESS_TIMEOUT_MS + 1
  }, now), {
    healthy: false,
    reason: "decoder_stalled",
    confirmationAgeMs: 0,
    checkAgeMs: 0
  });
  assert.equal(captureTransportDecision({
    ...registeredCapture,
    decoderProgressAgeMs: -1
  }, now).reason, "decoder_unconfirmed");
  for (const unhealthy of [
    {trackReadyState: "ended", captureStatus: "active", decoderProgressAgeMs: 0},
    {trackReadyState: "live", trackMuted: true, captureStatus: "active", decoderProgressAgeMs: 0},
    {trackReadyState: "live", captureStatus: "active", tabDiscarded: true, decoderProgressAgeMs: 0},
    {trackReadyState: "live", captureStatus: "active", tabFrozen: true, decoderProgressAgeMs: 0},
    {trackReadyState: "live", captureStatus: "missing", decoderProgressAgeMs: 0}
  ]) {
    assert.equal(captureTransportDecision(unhealthy, now).healthy, false, JSON.stringify(unhealthy));
  }
  assert.equal(captureHealthCheckDue(now, 0), true);
  assert.equal(captureHealthCheckDue(now, now - 9_999), false);
  assert.equal(captureHealthCheckDue(now, now - 10_000), true);
  assert.equal(leaseHeartbeatDue(now, 0, 0, 30), true);
  assert.equal(leaseHeartbeatDue(now, now - 30_000, 0, 30), true);
  assert.equal(leaseHeartbeatDue(now, now - 30_000, now - 5_000, 30), false);
});

test("service-worker registry attestation is lineage-bound and background-safe", () => {
  const status = {
    lockedTabId: 808,
    sequenceId: "sequence-808",
    lockedTitle: "Chart",
    lockedUrl: "https://pocketoption.com/en/cabinet/"
  };
  const request = {tabId: 808, sequenceId: "sequence-808"};
  const tab = {
    id: 808,
    active: false,
    discarded: false,
    frozen: false,
    status: "complete",
    title: "CAD/JPY OTC",
    url: "https://pocketoption.com/en/cabinet/?pair=CADJPY"
  };
  const live = captureRegistryAttestation(status, request, tab, [{tabId: 808, status: "active"}], 42_000);
  assert.equal(live.ok, true);
  assert.equal(live.captureStatus, "active");
  assert.equal(live.tabFrozen, false);
  assert.equal(live.tabOrigin, "https://pocketoption.com");
  assert.equal(live.tabUrl, "https://pocketoption.com/en/cabinet/");
  assert.equal(captureRegistryAttestation(status, request, tab, [], 42_000).captureStatus, "missing");
  assert.equal(captureRegistryAttestation(status, {...request, sequenceId: "old"}, tab, [], 42_000).ok, false);
});

test("same-origin pair routes and reloads preserve capture without focus changes", () => {
  const origin = "https://pocketoption.com";
  assert.equal(
    lockedTabLifecycleAction({status: "loading"}, `${origin}/en/cabinet/demo-quick-high-low/`, origin),
    "preserve"
  );
  assert.equal(
    lockedTabLifecycleAction({url: `${origin}/en/cabinet/?pair=CADJPY`}, `${origin}/en/cabinet/?pair=CADJPY`, origin),
    "preserve"
  );
  assert.equal(
    lockedTabLifecycleAction({url: "https://example.test/chart"}, "https://example.test/chart", origin),
    "stop"
  );
  assert.equal(lockedTabLifecycleAction({frozen: true}, `${origin}/chart`, origin), "hold");
  assert.equal(lockedTabLifecycleAction({discarded: true}, `${origin}/chart`, origin), "stop");
  assert.equal(lockedTabLifecycleAction({frozen: false}, `${origin}/chart`, origin), "preserve");
});

test("a delayed terminal event cannot cancel a replacement capture on the same tab", () => {
  const status = {
    lockedTabId: 0,
    candidateTabId: 808,
    selectionId: "selection-new"
  };
  const stopped = {tabId: 808, status: "stopped"};

  assert.equal(
    terminalTabCaptureTarget(stopped, [{tabId: 808, status: "pending"}], status),
    ""
  );
  assert.equal(
    terminalTabCaptureTarget(stopped, [{tabId: 808, status: "active"}], status),
    ""
  );
  assert.equal(terminalTabCaptureTarget(stopped, [], status), "");
  assert.equal(
    terminalTabCaptureTarget(stopped, [{tabId: 909, status: "active"}], status),
    ""
  );
  assert.equal(
    terminalTabCaptureTarget(
      stopped,
      [],
      {lockedTabId: 808, sequenceId: "sequence-current"}
    ),
    "locked"
  );
  assert.equal(
    terminalTabCaptureTarget({tabId: 808, status: "active"}, [], status),
    ""
  );

  assert.equal(tabCaptureLineageStillCurrent("candidate", status, status), true);
  assert.equal(
    tabCaptureLineageStillCurrent("candidate", status, {...status, selectionId: "selection-newer"}),
    false
  );
  assert.equal(
    tabCaptureLineageStillCurrent(
      "locked",
      {lockedTabId: 808, sequenceId: "sequence-a"},
      {lockedTabId: 808, sequenceId: "sequence-b"}
    ),
    false
  );
});

test("upload pacing counts backend analysis time from request start", () => {
  assert.equal(remainingUploadStartDelayMs(1_000, 0, 10_000), 0);
  assert.equal(remainingUploadStartDelayMs(12_000, 10_000, 10_000), 8_000);
  assert.equal(remainingUploadStartDelayMs(35_000, 10_000, 10_000), 0);
});

test("capture discard policy is temporary and preserves an existing protected tab", () => {
  assert.deepEqual(captureDiscardPolicy(true), {
    originalAutoDiscardable: true,
    protectionUpdate: {autoDiscardable: false},
    restorationUpdate: {autoDiscardable: true}
  });
  assert.deepEqual(captureDiscardPolicy(false), {
    originalAutoDiscardable: false,
    protectionUpdate: null,
    restorationUpdate: null
  });
});

test("status contract reports ROI capture, freshness, and immutable no-focus policy", () => {
  const status = initialStatus();
  assert.equal(SAMPLE_FPS, 1.0);
  assert.equal(SAMPLE_INTERVAL_MS, 1_000);
  assert.equal(DECODER_PROGRESS_TIMEOUT_MS, 90_000);
  assert.equal(status.schemaVersion, "PG_EDGE_REGION_CAPTURE_STATUS_V3");
  assert.equal(status.captureMode, "tab_region");
  assert.equal(status.sourceRenderFresh, false);
  assert.equal(status.focusPolicy, "never_activate_raise_or_focus_tabs");
});

test("manifest and package are version-locked, least-privilege MV3, and documented", async () => {
  const manifest = JSON.parse(await readFile(resolve(extensionRoot, "manifest.json"), "utf8"));
  const packageMetadata = JSON.parse(await readFile(resolve(extensionRoot, "package.json"), "utf8"));
  const readme = await readFile(resolve(extensionRoot, "README.md"), "utf8");
  assert.equal(manifest.manifest_version, 3);
  assert.equal(manifest.version, "0.3.15");
  assert.equal(packageMetadata.version, manifest.version);
  assert.equal(readme.includes(`Current unpacked release: **${manifest.version}**.`), true);
  assert.deepEqual(
    [...manifest.permissions].sort(),
    ["activeTab", "alarms", "offscreen", "scripting", "storage", "tabs", "tabCapture"].sort()
  );
  assert.deepEqual(
    [...manifest.host_permissions].sort(),
    ["http://*/*", "https://*/*", "http://127.0.0.1/*", "http://localhost/*"].sort()
  );
  assert.equal(manifest.commands["lock-full-viewport"].suggested_key.windows, "Ctrl+Shift+7");
  assert.equal(manifest.commands["lock-full-viewport"].global, undefined);
  assert.equal(manifest.commands["select-chart-region"].suggested_key.windows, "Ctrl+Shift+8");
  assert.equal(manifest.commands["stop-chart-capture"].suggested_key.windows, "Ctrl+Shift+9");
  assert.equal(manifest.commands["stop-chart-capture"].global, true);
  assert.equal(JSON.stringify(manifest).includes("Ctrl+Shift+B"), false);
  assert.equal(manifest.action.default_popup, undefined);
  assert.equal(manifest.background.type, "module");
});

test("identity metadata brackets the exact captured frame without persisted pair defaults", async () => {
  const worker = await readFile(resolve(extensionRoot, "service_worker.js"), "utf8");
  const offscreen = await readFile(resolve(extensionRoot, "offscreen.js"), "utf8");

  assert.equal(worker.includes("OBSERVE_LOCKED_TAB_IDENTITY_V3"), true);
  assert.equal(worker.includes("market_bbox_css"), true);
  assert.equal(worker.includes("timeframe_bbox_css"), true);
  assert.equal(offscreen.includes('schema_version: "PG_EDGE_TAB_IDENTITY_OBSERVATION_V3"'), true);
  assert.equal(offscreen.includes("capture_bracket_consistent: true"), true);
  assert.equal(offscreen.includes("identity_observation_v3: frame.identityObservationV3 || null"), true);
  assert.equal(offscreen.includes('schema_version: "PG_EDGE_TAB_IDENTITY_HEARTBEAT_V3"'), true);
  assert.equal(offscreen.includes("void heartbeatSource(session"), true);
  assert.equal(offscreen.includes('form.append("symbol", "")'), true);
  assert.equal(offscreen.includes('form.append("timeframe", "")'), true);
});

test("runtime contains ROI lease lineage and no focus-changing API", async () => {
  const sources = await Promise.all([
    "common.js", "service_worker.js", "offscreen.js", "roi_selector.js", "options.js"
  ].map((name) => readFile(resolve(extensionRoot, name), "utf8")));
  const source = sources.join("\n");
  const serviceWorkerSource = sources[1];
  const offscreenSource = sources[2];
  assert.match(source, /coordinate_space:\s*"edge_tab_roi_v1"/);
  assert.match(source, /source_type:\s*"browser_tab_roi_capture"/);
  assert.match(source, /form\.append\("source_generation"/);
  assert.match(source, /form\.append\("source_lease_id"/);
  assert.match(source, /source_render_fresh/);
  assert.match(source, /getVideoPlaybackQuality/);
  assert.match(serviceWorkerSource, /CAPTURE_HEALTH_CHECK_V1/);
  assert.match(serviceWorkerSource, /lockedTabLifecycleAction/);
  assert.match(offscreenSource, /captureTransportDecision/);
  assert.match(offscreenSource, /contractTimer = setTimeout\(\(\) => void refreshServerContract\(\), delay\)/);
  assert.equal(offscreenSource.includes("No freshly presented tab video frame inside the bounded freshness window."), false);
  assert.match(source, /autoDiscardable: false/);
  assert.match(source, /autoDiscardable: true/);
  assert.match(source, /PREPARE_CAPTURE_CANDIDATE_V1/);
  assert.match(source, /COMMIT_CAPTURE_REGION_V1/);
  assert.match(source, /STOP_ALL_CAPTURE_V1/);
  assert.match(serviceWorkerSource, /command === "lock-full-viewport"/);
  assert.match(serviceWorkerSource, /queryFullViewportSelection/);
  assert.match(serviceWorkerSource, /rectCss:\s*\{x:\s*0,\s*y:\s*0,\s*width:\s*window\.innerWidth,\s*height:\s*window\.innerHeight\}/);
  assert.match(serviceWorkerSource, /if \(!fullViewport\) await injectSelector\(tab\.id\)/);
  assert.match(serviceWorkerSource, /commitCandidateRegion\(currentStatus, selection, \{dismissSelectorUi: false\}\)/);
  assert.match(serviceWorkerSource, /extensionVersion:\s*EXTENSION_VERSION/);
  assert.match(serviceWorkerSource, /await chrome\.tabCapture\.getCapturedTabs\(\)/);
  assert.match(serviceWorkerSource, /terminalTabCaptureTarget\(info, capturedTabs, status\)/);
  assert.match(offscreenSource, /extension_version:\s*session\.extensionVersion/);
  assert.equal(offscreenSource.includes("chrome.runtime.getManifest"), false);
  assert.match(offscreenSource, /form\.append\("symbol",\s*""\)/);
  assert.match(offscreenSource, /form\.append\("timeframe",\s*""\)/);
  assert.match(offscreenSource, /identity_hint_policy:\s*"visual_reproof_required"/);
  for (const forbidden of [
    "chrome.windows.update", "chrome.windows.create", "window.focus(", "tabs.highlight", "<all_urls>",
    "chrome.tabs.update(tabId, {active", "chrome.tabs.update(id, {active"
  ]) {
    assert.equal(source.includes(forbidden), false, `forbidden focus or broad-access primitive: ${forbidden}`);
  }
});

test("authorized chart binding recovers after an API or tab-capture interruption", async () => {
  const worker = await readFile(resolve(extensionRoot, "service_worker.js"), "utf8");

  assert.match(worker, /AUTHORIZED_BINDING_KEY = "authorizedCaptureBindingV1"/);
  assert.match(worker, /saveAuthorizedBinding\(status, normalized\.region\)/);
  assert.match(worker, /recoverAuthorizedCaptureOnce/);
  assert.match(worker, /validateAuthorizedBackgroundTab\(tab, binding\.origin\)/);
  assert.doesNotMatch(worker, /recoverAuthorizedCaptureOnce[\s\S]*?validateCapturableTab\(tab\)/);
  assert.match(worker, /chrome\.tabCapture\.getMediaStreamId\(\{targetTabId: tab\.id\}\)/);
  assert.match(worker, /commitCandidateRegion\([\s\S]*binding\.region/);
  assert.match(worker, /chrome\.alarms\.onAlarm\.addListener/);
  assert.match(worker, /scheduleAuthorizedRecovery\(1000\)/);
  assert.match(worker, /await clearAuthorizedBinding\(\)/);
  assert.equal(worker.includes("chrome.tabs.update(tab.id, {active: true"), false);
  assert.equal(worker.includes("chrome.windows.update"), false);
});

test("ROI selector has a DPI-independent keyboard full-viewport path", async () => {
  const selectorSource = await readFile(resolve(extensionRoot, "roi_selector.js"), "utf8");
  assert.match(selectorSource, /F selects full viewport/);
  assert.match(selectorSource, /root\.tabIndex = -1/);
  assert.match(selectorSource, /event\.key\.toLowerCase\(\) === "f"/);
  assert.match(selectorSource, /setRect\(0, 0, window\.innerWidth, window\.innerHeight\)/);
});
