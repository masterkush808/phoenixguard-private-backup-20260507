export const SAMPLE_INTERVAL_MS = 4_000;
export const SAMPLE_FPS = 0.25;
export const SELECTION_TIMEOUT_MS = 60_000;
export const DECODER_PROGRESS_TIMEOUT_MS = 90_000;
export const CAPTURE_HEALTH_POLL_MS = 10_000;
export const CAPTURE_HEALTH_GRACE_MS = 45_000;
export const TRANSPORT_HEARTBEAT_INTERVAL_MS = 8_000;
export const MIN_REGION_CSS_WIDTH = 320;
export const MIN_REGION_CSS_HEIGHT = 180;

export const DEFAULT_CONFIG = Object.freeze({
  baseUrl: "http://127.0.0.1:8793",
  sessionId: "pocket-live-8788",
  sourceId: "edge-chart-region-v3",
  token: "f14e606096141188dea0b69c4cf7bcdab8d2f449a8a16d647e4573acdb489516",
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

export function validateAuthorizedBackgroundTab(tab, expectedOrigin = "") {
  if (!tab || !Number.isInteger(tab.id) || tab.id <= 0) {
    return {ok: false, reason: "The authorized chart tab no longer exists."};
  }
  if (!capturableHttpUrl(tab.url)) {
    return {ok: false, reason: "The authorized chart tab is no longer an HTTP(S) chart."};
  }
  const actualOrigin = sourceOrigin(tab.url);
  const requiredOrigin = cleanText(expectedOrigin);
  if (requiredOrigin && actualOrigin !== requiredOrigin) {
    return {ok: false, reason: "The authorized chart tab changed origin."};
  }
  // A persisted user-authorized binding must remain recoverable while its tab
  // is in the background. Requiring `tab.active` here would silently delete
  // the binding whenever Edge reloads the extension from its Extensions tab.
  return {ok: true, reason: "The authorized HTTP(S) chart tab can recover in the background."};
}

export function terminalTabCaptureTarget(info = {}, capturedTabs = [], currentStatus = {}) {
  const eventStatus = cleanText(info.status).toLowerCase();
  const tabId = Number(info.tabId || 0);
  if (tabId <= 0 || !["stopped", "error"].includes(eventStatus)) return "";

  // onStatusChanged identifies only the tab, not the stream instance. Edge can
  // deliver a terminal event for an older stream after a replacement stream on
  // the same tab is already pending or active. Never let that stale event tear
  // down the replacement candidate.
  const replacementIsLive = Array.isArray(capturedTabs) && capturedTabs.some((capture) => (
    Number(capture?.tabId || 0) === tabId &&
    ["pending", "active"].includes(cleanText(capture?.status).toLowerCase())
  ));
  if (replacementIsLive) return "";
  // Candidate teardown is owned by the exact MediaStreamTrack and the bounded
  // selector timeout. Edge's tab-level event has no capture lineage and can
  // arrive before a same-tab replacement appears in getCapturedTabs().
  if (Number(currentStatus?.candidateTabId || 0) === tabId) return "";
  if (Number(currentStatus?.lockedTabId || 0) === tabId) return "locked";
  return "";
}

export function tabCaptureLineageStillCurrent(target, observedStatus = {}, currentStatus = {}) {
  if (target === "locked") {
    return Number(observedStatus.lockedTabId || 0) > 0 &&
      Number(currentStatus.lockedTabId || 0) === Number(observedStatus.lockedTabId || 0) &&
      cleanText(observedStatus.sequenceId) !== "" &&
      cleanText(currentStatus.sequenceId) === cleanText(observedStatus.sequenceId);
  }
  if (target === "candidate") {
    return Number(observedStatus.candidateTabId || 0) > 0 &&
      Number(currentStatus.candidateTabId || 0) === Number(observedStatus.candidateTabId || 0) &&
      cleanText(observedStatus.selectionId) !== "" &&
      cleanText(currentStatus.selectionId) === cleanText(observedStatus.selectionId);
  }
  return false;
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
    // A universal ROI can be rebound to another pair or timeframe without a
    // configuration change. Persisted identity hints therefore cannot be
    // authoritative: the pixels selected for this capture must re-prove both.
    // Keeping the keys blank preserves the wire/config schema while migrating
    // older extension storage safely.
    symbol: "",
    timeframe: "",
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

export function sourceControlStatusEndpoint(config) {
  const normalized = normalizeConfig(config);
  return `${normalized.baseUrl}/v1/mobile/frame-ingest/sessions/${encodeURIComponent(normalized.sessionId)}/source-control`;
}

export function ownerlessSourceRecoveryFence(source = {}) {
  const requiredFields = [
    "state_revision",
    "state",
    "source_id",
    "source_generation",
    "source_type",
    "coordinate_space",
    "selection_id",
    "sequence_id"
  ];
  if (!source || typeof source !== "object" || requiredFields.some((field) => !(field in source))) {
    return {ok: false, reason: "incomplete_source_fence", expectedSourceControl: null};
  }
  const state = cleanText(source.state).toUpperCase();
  const generation = Math.max(0, Math.trunc(Number(source.source_generation) || 0));
  const ownerless = state === "NO_SOURCE" &&
    !cleanText(source.source_id) &&
    !cleanText(source.sequence_id) &&
    generation === 0;
  if (!ownerless) {
    return {
      ok: false,
      reason: state === "KILLED" ? "source_killed" : "source_owned",
      expectedSourceControl: null
    };
  }
  return {
    ok: true,
    reason: "ownerless_api_restart",
    expectedSourceControl: Object.fromEntries(requiredFields.map((field) => [field, source[field]]))
  };
}

export function sourceControlHeartbeatEndpoint(config) {
  const normalized = normalizeConfig(config);
  return `${normalized.baseUrl}/v1/mobile/frame-ingest/sessions/${encodeURIComponent(normalized.sessionId)}/source-control/heartbeat`;
}

export function normalizedRoiVector(value = {}) {
  const raw = Array.isArray(value)
    ? value.slice(0, 4)
    : [value?.x, value?.y, value?.width, value?.height];
  const numbers = raw.map((item) => Number(item));
  if (
    numbers.length !== 4 ||
    numbers.some((item) => !Number.isFinite(item)) ||
    numbers[0] < 0 ||
    numbers[1] < 0 ||
    numbers[2] <= 0 ||
    numbers[3] <= 0 ||
    numbers[0] + numbers[2] > 1.00000001 ||
    numbers[1] + numbers[3] > 1.00000001
  ) return [];
  return numbers.map(roundedFraction);
}

export function boundedHeartbeatIdentityObservation(value = {}, expectedSequenceId = "") {
  const row = value && typeof value === "object" ? value : {};
  const symbol = cleanText(row.symbol).toUpperCase();
  const timeframe = cleanText(row.timeframe).toUpperCase();
  const sequenceId = cleanText(row.sequence_id);
  const expectedSequence = cleanText(expectedSequenceId);
  const lockedTabId = Math.max(0, Math.trunc(Number(row.locked_tab_id) || 0));
  const lockedOrigin = sourceOrigin(row.locked_origin);
  const observedEpochMs = Math.max(0, Math.trunc(Number(row.observed_epoch_ms) || 0));
  if (
    cleanText(row.schema_version) !== "PG_EDGE_TAB_IDENTITY_HEARTBEAT_V3" ||
    row.revocation_only !== true ||
    !/^[A-Z]{3}\/[A-Z]{3}(?: OTC)?$/.test(symbol) ||
    !/^(?:S3|S5|S10|S15|S30|M1|M2|M3|M4|M5|M10|M15|M20|M30|M45|H1|H2|H3|H4|H6|H8|H12|D1|D2|D3|W1|W2|MN1|MN3|MN6)$/.test(timeframe) ||
    !sequenceId ||
    !expectedSequence ||
    sequenceId !== expectedSequence ||
    lockedTabId <= 0 ||
    !lockedOrigin ||
    observedEpochMs <= 0
  ) return null;
  return {
    schema_version: "PG_EDGE_TAB_IDENTITY_HEARTBEAT_V3",
    revocation_only: true,
    symbol,
    timeframe,
    sequence_id: sequenceId,
    locked_tab_id: lockedTabId,
    locked_origin: lockedOrigin,
    observed_epoch_ms: observedEpochMs,
    study_authority: false,
    overlay_authority: false,
    decision_authority: false
  };
}

export function sourceControlHeartbeatPayload({
  sourceId,
  sequenceId,
  sourceGeneration,
  sourceLeaseId,
  captureEpochMs,
  sourceRenderFresh,
  materialChangePending,
  roiNormalized,
  roiSourcePixels,
  sourceSurfaceWidth,
  sourceSurfaceHeight,
  transportFrameAgeMs,
  decoderFrameAgeMs,
  captureHealthReason,
  captureStatus,
  presentedFrames,
  mediaTime,
  identityObservationV3
}) {
  return {
    source_id: cleanText(sourceId),
    sequence_id: cleanText(sequenceId),
    source_generation: Math.max(0, Math.trunc(Number(sourceGeneration) || 0)),
    source_lease_id: cleanText(sourceLeaseId),
    capture_epoch_ms: Math.max(0, Math.trunc(Number(captureEpochMs) || 0)),
    source_render_fresh: sourceRenderFresh === true,
    material_change_pending: materialChangePending === true,
    roi_normalized: normalizedRoiVector(roiNormalized),
    roi_source_pixels: roiSourcePixels && typeof roiSourcePixels === "object" ? {...roiSourcePixels} : {},
    source_surface_width: Math.max(0, Math.trunc(Number(sourceSurfaceWidth) || 0)),
    source_surface_height: Math.max(0, Math.trunc(Number(sourceSurfaceHeight) || 0)),
    transport_frame_age_ms: Math.max(0, Math.trunc(Number(transportFrameAgeMs) || 0)),
    decoder_frame_age_ms: Math.max(0, Math.trunc(Number(decoderFrameAgeMs) || 0)),
    capture_health_reason: cleanText(captureHealthReason).slice(0, 96),
    capture_status: cleanText(captureStatus).slice(0, 48),
    presented_frames: Math.max(0, Math.trunc(Number(presentedFrames) || 0)),
    media_time: Math.max(0, Number(mediaTime) || 0),
    identity_observation_v3: boundedHeartbeatIdentityObservation(
      identityObservationV3,
      sequenceId
    )
  };
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

export function captureTransportDecision(raw = {}, nowMs = Date.now()) {
  const now = Number(nowMs);
  const checkedAtMs = Number(raw.checkedAtMs || 0);
  const lastConfirmedAtMs = Number(raw.lastConfirmedAtMs || 0);
  const trackReadyState = cleanText(raw.trackReadyState).toLowerCase();
  const captureStatus = cleanText(raw.captureStatus).toLowerCase();
  const decoderProgressAgeMs = Number(raw.decoderProgressAgeMs);
  const confirmationAgeMs = Number.isFinite(now) && lastConfirmedAtMs > 0
    ? Math.max(0, now - lastConfirmedAtMs)
    : -1;
  const checkAgeMs = Number.isFinite(now) && checkedAtMs > 0
    ? Math.max(0, now - checkedAtMs)
    : -1;

  if (trackReadyState !== "live") {
    return {healthy: false, reason: "track_not_live", confirmationAgeMs, checkAgeMs};
  }
  if (raw.trackMuted === true) {
    return {healthy: false, reason: "track_muted", confirmationAgeMs, checkAgeMs};
  }
  if (raw.tabDiscarded === true) {
    return {healthy: false, reason: "tab_discarded", confirmationAgeMs, checkAgeMs};
  }
  if (raw.tabFrozen === true) {
    return {healthy: false, reason: "tab_frozen", confirmationAgeMs, checkAgeMs};
  }
  if (["stopped", "error", "missing"].includes(captureStatus)) {
    return {healthy: false, reason: `capture_${captureStatus}`, confirmationAgeMs, checkAgeMs};
  }
  let healthyReason = "capture_confirmed";
  if (!["active", "pending"].includes(captureStatus)) {
    // A service worker can be suspended between messages. Keep a short bounded
    // grace period after the last positive tabCapture attestation so one missed
    // worker response cannot turn a healthy background stream stale.
    if (confirmationAgeMs < 0 || confirmationAgeMs > CAPTURE_HEALTH_GRACE_MS) {
      return {healthy: false, reason: "capture_unconfirmed", confirmationAgeMs, checkAgeMs};
    }
    healthyReason = "capture_confirmation_grace";
  }
  if (!Number.isFinite(decoderProgressAgeMs) || decoderProgressAgeMs < 0) {
    return {healthy: false, reason: "decoder_unconfirmed", confirmationAgeMs, checkAgeMs};
  }
  if (decoderProgressAgeMs > DECODER_PROGRESS_TIMEOUT_MS) {
    return {healthy: false, reason: "decoder_stalled", confirmationAgeMs, checkAgeMs};
  }
  return {healthy: true, reason: healthyReason, confirmationAgeMs, checkAgeMs};
}

export function captureRegistryAttestation(status = {}, request = {}, tab = {}, capturedTabs = [], nowMs = Date.now()) {
  const tabId = Number(request.tabId || 0);
  const sequenceId = cleanText(request.sequenceId);
  const checkedAtMs = Number.isFinite(Number(nowMs)) ? Number(nowMs) : Date.now();
  if (
    tabId <= 0 ||
    tabId !== Number(status.lockedTabId || 0) ||
    !sequenceId ||
    sequenceId !== cleanText(status.sequenceId)
  ) {
    return {
      ok: false,
      captureStatus: "missing",
      checkedAtMs,
      error: "The requested capture lineage is no longer the locked chart source."
    };
  }
  const capture = Array.isArray(capturedTabs)
    ? capturedTabs.find((item) => Number(item?.tabId || 0) === tabId) || null
    : null;
  return {
    ok: true,
    checkedAtMs,
    captureStatus: cleanText(capture?.status || "missing").toLowerCase(),
    tabDiscarded: tab?.discarded === true,
    tabFrozen: tab?.frozen === true,
    tabStatus: cleanText(tab?.status),
    tabTitle: cleanText(tab?.title || status.lockedTitle).slice(0, 180),
    tabUrl: sanitizeSourceUrl(tab?.url || status.lockedUrl).slice(0, 2048),
    tabOrigin: sourceOrigin(tab?.url || status.lockedUrl)
  };
}

export function captureHealthCheckDue(nowMs, checkedAtMs) {
  const now = Number(nowMs);
  const checked = Number(checkedAtMs);
  if (!Number.isFinite(now) || !Number.isFinite(checked) || checked <= 0) return true;
  return now - checked >= CAPTURE_HEALTH_POLL_MS;
}

export function leaseHeartbeatDue(nowMs, lastAcceptedAtMs, lastQueuedAtMs, heartbeatSec) {
  const now = Number(nowMs);
  const accepted = Math.max(0, Number(lastAcceptedAtMs) || 0);
  const queued = Math.max(0, Number(lastQueuedAtMs) || 0);
  const intervalMs = Math.max(1, Number(heartbeatSec) || DEFAULT_CONFIG.heartbeatSec) * 1000;
  if (!Number.isFinite(now)) return false;
  return now - Math.max(accepted, queued) >= intervalMs;
}

export function lockedTabLifecycleAction(changeInfo = {}, tabUrl = "", lockedOrigin = "") {
  if (changeInfo.discarded === true) return "stop";
  const nextOrigin = sourceOrigin(tabUrl);
  if (changeInfo.url && (!nextOrigin || nextOrigin !== cleanText(lockedOrigin))) return "stop";
  if (changeInfo.frozen === true) return "hold";
  if (
    changeInfo.status === "loading" ||
    changeInfo.frozen === false ||
    changeInfo.discarded === false ||
    Boolean(changeInfo.url)
  ) return "preserve";
  return "ignore";
}

export function remainingUploadStartDelayMs(nowMs, lastUploadStartedAtMs, minIntervalMs) {
  const now = Number(nowMs);
  const lastStarted = Number(lastUploadStartedAtMs);
  const interval = Math.max(0, Number(minIntervalMs) || 0);
  if (!Number.isFinite(now) || !Number.isFinite(lastStarted) || lastStarted <= 0) return 0;
  return Math.max(0, lastStarted + interval - now);
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
    decoderFrameAgeMs: -1,
    visualChangeAgeMs: -1,
    sourceRenderFresh: false,
    captureHealthReason: "capture_unconfirmed",
    captureStatus: "unknown",
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
