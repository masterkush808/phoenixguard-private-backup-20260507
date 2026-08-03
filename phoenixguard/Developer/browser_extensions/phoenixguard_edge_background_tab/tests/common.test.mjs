import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";
import {fileURLToPath} from "node:url";
import {dirname, resolve} from "node:path";

import {
  SAMPLE_FPS,
  canonicalFrameSignaturePayload,
  captureDiscardPolicy,
  capturableHttpUrl,
  frameIngestEndpoint,
  initialStatus,
  mapNormalizedRegionToPixels,
  mediaPlaybackAdvanced,
  meanAbsoluteDifference,
  normalizeConfig,
  normalizeRegionSelection,
  sanitizeSourceUrl,
  sourceControlClaimEndpoint,
  sourceControlKillEndpoint,
  sourceControlKillPayload,
  sourceOrigin,
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
    timeframe: "",
    maxWidth: 99_999,
    jpegQuality: 0.1,
    materialDeltaThreshold: 4,
    heartbeatSec: 2
  });
  assert.equal(config.baseUrl, "http://127.0.0.1:8793");
  assert.equal(config.token, "secret");
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
  assert.equal(SAMPLE_FPS, 0.25);
  assert.equal(status.schemaVersion, "PG_EDGE_REGION_CAPTURE_STATUS_V3");
  assert.equal(status.captureMode, "tab_region");
  assert.equal(status.sourceRenderFresh, false);
  assert.equal(status.focusPolicy, "never_activate_raise_or_focus_tabs");
});

test("manifest is least-privilege MV3 with explicit select and global kill commands", async () => {
  const manifest = JSON.parse(await readFile(resolve(extensionRoot, "manifest.json"), "utf8"));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(
    [...manifest.permissions].sort(),
    ["activeTab", "offscreen", "scripting", "storage", "tabCapture"].sort()
  );
  assert.deepEqual([...manifest.host_permissions].sort(), ["http://127.0.0.1/*", "http://localhost/*"].sort());
  assert.equal(manifest.commands["select-chart-region"].suggested_key.windows, "Ctrl+Shift+8");
  assert.equal(manifest.commands["stop-chart-capture"].suggested_key.windows, "Ctrl+Shift+9");
  assert.equal(manifest.commands["stop-chart-capture"].global, true);
  assert.equal(JSON.stringify(manifest).includes("Ctrl+Shift+B"), false);
  assert.equal(manifest.action.default_popup, undefined);
  assert.equal(manifest.background.type, "module");
});

test("runtime contains ROI lease lineage and no focus-changing API", async () => {
  const source = (await Promise.all([
    "common.js", "service_worker.js", "offscreen.js", "roi_selector.js", "options.js"
  ].map((name) => readFile(resolve(extensionRoot, name), "utf8")))).join("\n");
  assert.match(source, /coordinate_space:\s*"edge_tab_roi_v1"/);
  assert.match(source, /source_type:\s*"browser_tab_roi_capture"/);
  assert.match(source, /form\.append\("source_generation"/);
  assert.match(source, /form\.append\("source_lease_id"/);
  assert.match(source, /source_render_fresh/);
  assert.match(source, /getVideoPlaybackQuality/);
  assert.match(source, /autoDiscardable: false/);
  assert.match(source, /autoDiscardable: true/);
  assert.match(source, /PREPARE_CAPTURE_CANDIDATE_V1/);
  assert.match(source, /COMMIT_CAPTURE_REGION_V1/);
  assert.match(source, /STOP_ALL_CAPTURE_V1/);
  for (const forbidden of [
    "chrome.windows.update", "chrome.windows.create", "window.focus(", "tabs.highlight", "<all_urls>",
    "chrome.tabs.update(tabId, {active", "chrome.tabs.update(id, {active"
  ]) {
    assert.equal(source.includes(forbidden), false, `forbidden focus or broad-access primitive: ${forbidden}`);
  }
});
