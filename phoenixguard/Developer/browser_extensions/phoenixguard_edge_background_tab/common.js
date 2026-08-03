export const SAMPLE_INTERVAL_MS = 4_000;
export const SAMPLE_FPS = 0.25;
export const SELECTION_TIMEOUT_MS = 60_000;
export const SOURCE_FREEZE_TIMEOUT_MS = 15_000;
export const MIN_REGION_CSS_WIDTH = 320;
export const MIN_REGION_CSS_HEIGHT = 180;

export const DEFAULT_CONFIG = Object.freeze({
  baseUrl: "http://127.0.0.1:8793",
  sessionId: "pocket-live-8788",
  sourceId: "edge-chart-region-v3",
  token: "",
  signingSecret: "",
  symbol: "",
  timeframe: "",
  maxWidth: 1920,
  jpegQuality: 0.82,
  materialDeltaThreshold: 0.006,
  heartbeatSec: 30
});

export function cleanText(value) {
  return String(value ?? "").trim();
}

export function capturableHttpUrl(rawUrl) {
  try {
    const url = new URL(cleanText(rawUrl));
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}

export function sourceOrigin(rawUrl) {
  try {
    const url = new URL(cleanText(rawUrl));
    return capturableHttpUrl(url.toString()) ? url.origin : "";
  } catch {
    return "";
  }
}

export function sanitizeSourceUrl(rawUrl) {
  try {
    const url = new URL(cleanText(rawUrl));
    if (!capturableHttpUrl(url.toString())) return "";
    url.username = "";
    url.password = "";
    url.search = "";
    url.hash = "";
    return url.toString();
  } catch {
    return "";
  }
}

export function validateCapturableTab(tab) {
  if (!tab || !Number.isInteger(tab.id) || tab.id <= 0) {
    return {ok: false, reason: "The active Edge tab has no stable tab ID."};
  }
  if (tab.active !== true) {
    return {ok: false, reason: "Make the chart tab active before selecting its region."};
  }
  if (!capturableHttpUrl(tab.url)) {
    return {ok: false, reason: "PhoenixGuard can select only a normal HTTP or HTTPS chart tab."};
  }
  return {ok: true, reason: "The active HTTP(S) chart tab can be selected."};
}

function clampNumber(value, fallback, minimum, maximum) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(maximum, Math.max(minimum, parsed));
}

function roundedFraction(value) {
  return Number(Math.min(1, Math.max(0, value)).toFixed(8));
}

export function normalizeRegionSelection(raw = {}) {
  const viewportWidth = Number(raw.viewportCss?.width);
  const viewportHeight = Number(raw.viewportCss?.height);
  if (!Number.isFinite(viewportWidth) || !Number.isFinite(viewportHeight) || viewportWidth < 1 || viewportHeight < 1) {
    return {ok: false, reason: "The chart viewport dimensions are unavailable."};
  }

  const rawX = Number(raw.rectCss?.x);
  const rawY = Number(raw.rectCss?.y);
  const rawWidth = Number(raw.rectCss?.width);
  const rawHeight = Number(raw.rectCss?.height);
  if (![rawX, rawY, rawWidth, rawHeight].every(Number.isFinite)) {
    return {ok: false, reason: "The selected chart rectangle is invalid."};
  }

  const firstX = Math.min(rawX, rawX + rawWidth);
  const secondX = Math.max(rawX, rawX + rawWidth);
  const firstY = Math.min(rawY, rawY + rawHeight);
  const secondY = Math.max(rawY, rawY + rawHeight);
  const x = Math.min(viewportWidth, Math.max(0, firstX));
  const y = Math.min(viewportHeight, Math.max(0, firstY));
  const right = Math.min(viewportWidth, Math.max(0, secondX));
  const bottom = Math.min(viewportHeight, Math.max(0, secondY));
  const width = Math.max(0, right - x);
  const height = Math.max(0, bottom - y);
  if (width < MIN_REGION_CSS_WIDTH || height < MIN_REGION_CSS_HEIGHT) {
    return {
      ok: false,
      reason: `Select at least ${MIN_REGION_CSS_WIDTH} by ${MIN_REGION_CSS_HEIGHT} chart pixels.`
    };
  }

  const region = {
    schemaVersion: "PG_EDGE_TAB_ROI_V1",
    rectCss: {x, y, width, height},
    viewportCss: {width: viewportWidth, height: viewportHeight},
    normalized: {
      x: roundedFraction(x / viewportWidth),
      y: roundedFraction(y / viewportHeight),
      width: roundedFraction(width / viewportWidth),
      height: roundedFraction(height / viewportHeight)
    },
    devicePixelRatio: clampNumber(raw.devicePixelRatio, 1, 0.25, 8),
    visualViewport: {
      offsetLeft: Number(raw.visualViewport?.offsetLeft) || 0,
      offsetTop: Number(raw.visualViewport?.offsetTop) || 0,
      width: Number(raw.visualViewport?.width) || viewportWidth,
      height: Number(raw.visualViewport?.height) || viewportHeight,
      scale: clampNumber(raw.visualViewport?.scale, 1, 0.1, 10)
    }
  };
  return {ok: true, reason: "Chart region selected.", region};
}

export function mapNormalizedRegionToPixels(normalized, sourceWidth, sourceHeight) {
  const width = Math.max(1, Math.floor(Number(sourceWidth) || 0));
  const height = Math.max(1, Math.floor(Number(sourceHeight) || 0));
  const x0 = Math.max(0, Math.min(width - 1, Math.floor((Number(normalized?.x) || 0) * width)));
  const y0 = Math.max(0, Math.min(height - 1, Math.floor((Number(normalized?.y) || 0) * height)));
  const rightPixel = ((Number(normalized?.x) || 0) + (Number(normalized?.width) || 0)) * width;
  const bottomPixel = ((Number(normalized?.y) || 0) + (Number(normalized?.height) || 0)) * height;
  const x1 = Math.max(x0 + 1, Math.min(width, Math.ceil(rightPixel - 1e-7)));
  const y1 = Math.max(y0 + 1, Math.min(height, Math.ceil(bottomPixel - 1e-7)));
  return {x: x0, y: y0, width: x1 - x0, height: y1 - y0, sourceWidth: width, sourceHeight: height};
}

export function normalizeConfig(raw = {}) {
  const merged = {...DEFAULT_CONFIG, ...(raw || {})};
  const baseUrl = cleanText(merged.baseUrl).replace(/\/+$/, "");
  return {
    baseUrl,
    sessionId: cleanText(merged.sessionId),
    sourceId: cleanText(merged.sourceId),
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

export function sourceControlClaimEndpoint(config) {
  const normalized = normalizeConfig(config);
  return `${normalized.baseUrl}/v1/mobile/frame-ingest/sessions/${encodeURIComponent(normalized.sessionId)}/source-control/claim`;
}

export function sourceControlKillEndpoint(config) {
  const normalized = normalizeConfig(config);
  return `${normalized.baseUrl}/v1/mobile/frame-ingest/sessions/${encodeURIComponent(normalized.sessionId)}/source-control/kill`;
}

export function sourceControlKillPayload({sourceId, sequenceId, sourceGeneration, sourceLeaseId, reason}) {
  return {
    source_id: cleanText(sourceId),
    sequence_id: cleanText(sequenceId),
    source_generation: Math.max(0, Math.trunc(Number(sourceGeneration) || 0)),
    source_lease_id: cleanText(sourceLeaseId),
    reason: cleanText(reason || "Capture stopped by the operator.").slice(0, 240)
  };
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
  if (!previous || !current || previous.length !== current.length || current.length === 0) return 1;
  let total = 0;
  for (let index = 0; index < current.length; index += 1) {
    total += Math.abs(Number(current[index]) - Number(previous[index]));
  }
  return total / (current.length * 255);
}

export function mediaPlaybackAdvanced(previous = {}, current = {}) {
  const previousMediaTime = Number(previous.mediaTime);
  const currentMediaTime = Number(current.mediaTime);
  const previousDecodedFrames = Number(previous.decodedFrames);
  const currentDecodedFrames = Number(current.decodedFrames);
  const mediaTimeAdvanced = Number.isFinite(currentMediaTime) && currentMediaTime >= 0 &&
    (!Number.isFinite(previousMediaTime) || currentMediaTime > previousMediaTime + 1e-6);
  const decodedFramesAdvanced = Number.isFinite(currentDecodedFrames) && currentDecodedFrames >= 0 &&
    (!Number.isFinite(previousDecodedFrames) || currentDecodedFrames > previousDecodedFrames);
  return {
    advanced: mediaTimeAdvanced || decodedFramesAdvanced,
    mediaTime: Number.isFinite(currentMediaTime) && currentMediaTime >= 0
      ? currentMediaTime
      : Number.isFinite(previousMediaTime) ? previousMediaTime : -1,
    decodedFrames: Number.isFinite(currentDecodedFrames) && currentDecodedFrames >= 0
      ? currentDecodedFrames
      : Number.isFinite(previousDecodedFrames) ? previousDecodedFrames : -1
  };
}

export function captureDiscardPolicy(autoDiscardable) {
  const originalAutoDiscardable = autoDiscardable !== false;
  return {
    originalAutoDiscardable,
    protectionUpdate: originalAutoDiscardable ? {autoDiscardable: false} : null,
    restorationUpdate: originalAutoDiscardable ? {autoDiscardable: true} : null
  };
}

export function makeSequenceId(tabId, nowMs = Date.now()) {
  const epoch = Math.max(0, Number(nowMs) || 0).toString(36);
  return `edge-roi-${Number(tabId) || 0}-${epoch}`;
}

export function initialStatus(overrides = {}) {
  return {
    schemaVersion: "PG_EDGE_REGION_CAPTURE_STATUS_V3",
    phase: "idle",
    message: "Press Ctrl+Shift+8 on any HTTP(S) chart tab to select a region.",
    captureMode: "tab_region",
    lockedTabId: 0,
    lockedTitle: "",
    lockedUrl: "",
    lockedOrigin: "",
    selectionId: "",
    candidateTabId: 0,
    candidateTitle: "",
    candidateUrl: "",
    candidateOrigin: "",
    sequenceId: "",
    region: null,
    cropPixels: null,
    sampleFps: SAMPLE_FPS,
    uploadMinIntervalSec: 0,
    sampledFrames: 0,
    queuedFrames: 0,
    replacedFrames: 0,
    acceptedFrames: 0,
    rejectedFrames: 0,
    frameId: 0,
    lastAcceptedAt: "",
    lastVideoFrameAt: "",
    lastVisualChangeAt: "",
    transportFrameAgeMs: -1,
    visualChangeAgeMs: -1,
    sourceRenderFresh: false,
    sourceGeneration: 0,
    sourceLeaseActive: false,
    discardProtectionActive: false,
    lockedTabOriginalAutoDiscardable: true,
    candidateTabOriginalAutoDiscardable: true,
    lastError: "",
    focusPolicy: "never_activate_raise_or_focus_tabs",
    updatedAt: new Date().toISOString(),
    ...overrides
  };
}
