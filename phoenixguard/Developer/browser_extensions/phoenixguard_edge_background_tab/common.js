export const SAMPLE_INTERVAL_MS = 4_000;
export const SAMPLE_FPS = 0.25;
export const DEFAULT_CONFIG = Object.freeze({
  baseUrl: "http://127.0.0.1:8793",
  sessionId: "pocket-live-8788",
  sourceId: "edge-background-tab-v3",
  token: "",
  signingSecret: "",
  symbol: "",
  timeframe: "M5",
  maxWidth: 1920,
  jpegQuality: 0.82,
  materialDeltaThreshold: 0.006,
  heartbeatSec: 30
});

const POCKET_TITLE_TOKENS = [
  "pocket option",
  "the most innovative trading platform"
];

export function cleanText(value) {
  return String(value ?? "").trim();
}

export function isPocketOptionUrl(rawUrl) {
  try {
    const url = new URL(cleanText(rawUrl));
    const hostname = url.hostname.toLowerCase().replace(/\.$/, "");
    return url.protocol === "https:" && (
      hostname === "pocketoption.com" || hostname.endsWith(".pocketoption.com")
    );
  } catch {
    return false;
  }
}

export function isPocketOptionTitle(rawTitle) {
  const title = cleanText(rawTitle).toLowerCase();
  return POCKET_TITLE_TOKENS.some((token) => title.includes(token));
}

export function sanitizeSourceUrl(rawUrl) {
  try {
    const url = new URL(cleanText(rawUrl));
    url.search = "";
    url.hash = "";
    return url.toString();
  } catch {
    return "";
  }
}

export function validatePocketOptionTab(tab) {
  if (!tab || !Number.isInteger(tab.id) || tab.id <= 0) {
    return {ok: false, reason: "The active Edge tab has no stable tab ID."};
  }
  if (!isPocketOptionUrl(tab.url)) {
    return {ok: false, reason: "Open an HTTPS pocketoption.com trading tab before locking."};
  }
  if (!isPocketOptionTitle(tab.title)) {
    return {ok: false, reason: "The active tab title does not identify the Pocket Option trading page."};
  }
  return {ok: true, reason: "Pocket Option URL and title verified."};
}

function clampNumber(value, fallback, minimum, maximum) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(maximum, Math.max(minimum, parsed));
}

export function normalizeConfig(raw = {}) {
  const merged = {...DEFAULT_CONFIG, ...(raw || {})};
  const baseUrl = cleanText(merged.baseUrl).replace(/\/+$/, "");
  const sessionId = cleanText(merged.sessionId);
  const sourceId = cleanText(merged.sourceId);
  return {
    baseUrl,
    sessionId,
    sourceId,
    token: cleanText(merged.token),
    signingSecret: cleanText(merged.signingSecret),
    symbol: cleanText(merged.symbol).toUpperCase(),
    timeframe: cleanText(merged.timeframe).toUpperCase(),
    maxWidth: Math.round(clampNumber(merged.maxWidth, DEFAULT_CONFIG.maxWidth, 640, 2560)),
    jpegQuality: clampNumber(merged.jpegQuality, DEFAULT_CONFIG.jpegQuality, 0.55, 0.95),
    materialDeltaThreshold: clampNumber(
      merged.materialDeltaThreshold,
      DEFAULT_CONFIG.materialDeltaThreshold,
      0.001,
      0.08
    ),
    heartbeatSec: Math.round(clampNumber(merged.heartbeatSec, DEFAULT_CONFIG.heartbeatSec, 15, 120))
  };
}

export function validateConfig(config) {
  const normalized = normalizeConfig(config);
  let url;
  try {
    url = new URL(normalized.baseUrl);
  } catch {
    return {ok: false, reason: "PhoenixGuard server URL is invalid."};
  }
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(url.hostname)) {
    return {ok: false, reason: "This local extension accepts only http://127.0.0.1 or http://localhost."};
  }
  if (!/^[A-Za-z0-9._-]+$/.test(normalized.sessionId)) {
    return {ok: false, reason: "Session ID may contain only letters, numbers, dot, underscore, and dash."};
  }
  if (!/^[A-Za-z0-9._-]+$/.test(normalized.sourceId)) {
    return {ok: false, reason: "Source ID may contain only letters, numbers, dot, underscore, and dash."};
  }
  if (!normalized.token) {
    return {ok: false, reason: "A PhoenixGuard frame-ingest token is required."};
  }
  return {ok: true, reason: "Configuration ready.", config: normalized};
}

export function frameIngestConfigEndpoint(config) {
  return `${normalizeConfig(config).baseUrl}/v1/mobile/frame-ingest/config`;
}

export function frameIngestEndpoint(config) {
  const normalized = normalizeConfig(config);
  return `${normalized.baseUrl}/v1/mobile/frame-ingest/sessions/${encodeURIComponent(normalized.sessionId)}/frames`;
}

export function canonicalFrameSignaturePayload({
  path,
  sessionId,
  sourceId,
  sequenceId,
  frameId,
  captureEpochMs,
  frameSha256,
  timestamp,
  nonce
}) {
  return [
    "PG_FRAME_INGEST_V1",
    "POST",
    cleanText(path),
    cleanText(sessionId),
    cleanText(sourceId),
    cleanText(sequenceId),
    String(Number(frameId) || 0),
    String(Number(captureEpochMs) || 0),
    cleanText(frameSha256).toLowerCase(),
    cleanText(timestamp),
    cleanText(nonce)
  ].join("\n");
}

export function meanAbsoluteDifference(previous, current) {
  if (!previous || !current || previous.length !== current.length || current.length === 0) {
    return 1;
  }
  let total = 0;
  for (let index = 0; index < current.length; index += 1) {
    total += Math.abs(Number(current[index]) - Number(previous[index]));
  }
  return total / (current.length * 255);
}

export function makeSequenceId(tabId, nowMs = Date.now()) {
  const epoch = Math.max(0, Number(nowMs) || 0).toString(36);
  return `edge-tab-${Number(tabId) || 0}-${epoch}`;
}

export function initialStatus(overrides = {}) {
  return {
    schemaVersion: "PG_EDGE_TAB_CAPTURE_STATUS_V1",
    phase: "idle",
    message: "Click the extension once while the Pocket Option tab is active.",
    lockedTabId: 0,
    lockedTitle: "",
    lockedUrl: "",
    sequenceId: "",
    sampleFps: SAMPLE_FPS,
    uploadMinIntervalSec: 0,
    sampledFrames: 0,
    queuedFrames: 0,
    replacedFrames: 0,
    acceptedFrames: 0,
    rejectedFrames: 0,
    frameId: 0,
    lastAcceptedAt: "",
    lastError: "",
    focusPolicy: "never_activate_raise_or_focus_tabs",
    updatedAt: new Date().toISOString(),
    ...overrides
  };
}
