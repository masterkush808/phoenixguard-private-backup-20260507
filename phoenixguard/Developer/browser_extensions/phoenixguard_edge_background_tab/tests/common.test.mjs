import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import test from "node:test";
import {fileURLToPath} from "node:url";
import {dirname, resolve} from "node:path";

import {
  SAMPLE_FPS,
  canonicalFrameSignaturePayload,
  frameIngestEndpoint,
  initialStatus,
  isPocketOptionTitle,
  isPocketOptionUrl,
  meanAbsoluteDifference,
  normalizeConfig,
  sanitizeSourceUrl,
  validateConfig,
  validatePocketOptionTab
} from "../common.js";

const here = dirname(fileURLToPath(import.meta.url));
const extensionRoot = resolve(here, "..");

test("Pocket Option identity requires the real HTTPS host and title", () => {
  assert.equal(isPocketOptionUrl("https://pocketoption.com/en/cabinet/demo-quick-high-low/"), true);
  assert.equal(isPocketOptionUrl("https://api.pocketoption.com/chart"), true);
  assert.equal(isPocketOptionUrl("http://pocketoption.com/chart"), false);
  assert.equal(isPocketOptionUrl("https://pocketoption.com.evil.example/chart"), false);
  assert.equal(isPocketOptionTitle("The Most Innovative Trading Platform and 31 more pages - Microsoft Edge"), true);
  assert.equal(isPocketOptionTitle("Pocket Option - Microsoft Edge"), true);
  assert.equal(isPocketOptionTitle("PhoenixGuard dashboard"), false);

  assert.deepEqual(
    validatePocketOptionTab({
      id: 123,
      url: "https://pocketoption.com/en/cabinet/",
      title: "The Most Innovative Trading Platform - Microsoft Edge"
    }),
    {ok: true, reason: "Pocket Option URL and title verified."}
  );
  assert.equal(validatePocketOptionTab({id: 123, url: "https://example.com", title: "Pocket Option"}).ok, false);
  assert.equal(validatePocketOptionTab({id: 123, url: "https://pocketoption.com", title: "Inbox"}).ok, false);
});

test("source URL lineage strips query parameters and fragments", () => {
  assert.equal(
    sanitizeSourceUrl("https://pocketoption.com/en/cabinet/?token=secret#chart"),
    "https://pocketoption.com/en/cabinet/"
  );
  assert.equal(sanitizeSourceUrl("not a URL"), "");
});

test("local frame-ingest configuration is normalized and bounded", () => {
  const config = normalizeConfig({
    baseUrl: "http://127.0.0.1:8793///",
    sessionId: "pocket-live-8788",
    sourceId: "edge-source",
    token: " secret ",
    timeframe: "m5",
    maxWidth: 99_999,
    jpegQuality: 0.1,
    materialDeltaThreshold: 4,
    heartbeatSec: 2
  });
  assert.equal(config.baseUrl, "http://127.0.0.1:8793");
  assert.equal(config.token, "secret");
  assert.equal(config.timeframe, "M5");
  assert.equal(config.maxWidth, 2560);
  assert.equal(config.jpegQuality, 0.55);
  assert.equal(config.materialDeltaThreshold, 0.08);
  assert.equal(config.heartbeatSec, 15);
  assert.equal(validateConfig(config).ok, true);
  assert.equal(validateConfig({...config, baseUrl: "https://remote.example"}).ok, false);
  assert.equal(validateConfig({...config, token: ""}).ok, false);
  assert.equal(
    frameIngestEndpoint(config),
    "http://127.0.0.1:8793/v1/mobile/frame-ingest/sessions/pocket-live-8788/frames"
  );
});

test("signature canonicalization matches PG_FRAME_INGEST_V1 field order", () => {
  const canonical = canonicalFrameSignaturePayload({
    path: "/v1/mobile/frame-ingest/sessions/pocket-live-8788/frames",
    sessionId: "pocket-live-8788",
    sourceId: "edge-source",
    sequenceId: "edge-tab-5-seq",
    frameId: 42,
    captureEpochMs: 1780000000000,
    frameSha256: "ABCDEF",
    timestamp: "1780000001000",
    nonce: "nonce-42"
  });
  assert.equal(canonical, [
    "PG_FRAME_INGEST_V1",
    "POST",
    "/v1/mobile/frame-ingest/sessions/pocket-live-8788/frames",
    "pocket-live-8788",
    "edge-source",
    "edge-tab-5-seq",
    "42",
    "1780000000000",
    "abcdef",
    "1780000001000",
    "nonce-42"
  ].join("\n"));
});

test("material probe difference is normalized", () => {
  assert.equal(meanAbsoluteDifference(new Uint8Array([0, 10]), new Uint8Array([0, 10])), 0);
  assert.equal(meanAbsoluteDifference(new Uint8Array([0]), new Uint8Array([255])), 1);
  assert.equal(meanAbsoluteDifference(null, new Uint8Array([10])), 1);
});

test("status contract reports quarter-FPS and an immutable no-focus policy", () => {
  const status = initialStatus();
  assert.equal(SAMPLE_FPS, 0.25);
  assert.equal(status.sampleFps, 0.25);
  assert.equal(status.focusPolicy, "never_activate_raise_or_focus_tabs");
});

test("manifest is MV3 and runtime source contains no tab/window focus calls", async () => {
  const manifest = JSON.parse(await readFile(resolve(extensionRoot, "manifest.json"), "utf8"));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(
    [...manifest.permissions].sort(),
    ["activeTab", "offscreen", "storage", "tabCapture"].sort()
  );
  assert.equal(manifest.action.default_popup, undefined);
  assert.equal(manifest.background.type, "module");

  const source = [
    await readFile(resolve(extensionRoot, "common.js"), "utf8"),
    await readFile(resolve(extensionRoot, "service_worker.js"), "utf8"),
    await readFile(resolve(extensionRoot, "offscreen.js"), "utf8")
  ].join("\n");
  assert.match(source, /coordinate_space:\s*"edge_tab_content_v1"/);
  assert.match(source, /browser_chrome_included:\s*false/);
  assert.match(source, /extension_id:\s*chrome\.runtime\.id/);
  assert.match(source, /extension_version:\s*chrome\.runtime\.getManifest\(\)\.version/);
  assert.match(source, /form\.append\("source_url",\s*sanitizeSourceUrl\(lockedTab\.url\)\)/);
  assert.match(source, /sourceId:\s*"edge-background-tab-v3"/);
  for (const forbidden of [
    "chrome.tabs.update",
    "chrome.windows.update",
    "chrome.windows.create",
    "window.focus(",
    "tabs.highlight"
  ]) {
    assert.equal(source.includes(forbidden), false, `forbidden focus-changing API: ${forbidden}`);
  }
});
